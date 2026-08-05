#!/usr/bin/env python3
"""Bulk batch pipeline: process all pending raw dates in 2 mega-batches.

Instead of submitting one batch per date (slow due to queue latency),
this script merges all pending dates into a single Phase-1 batch and
a single Phase-2 batch, dramatically reducing wall-clock time.

Usage:
    python scripts/bulk_batch.py                    # Full pipeline
    python scripts/bulk_batch.py --phase1-only      # Submit phase 1 and exit
    python scripts/bulk_batch.py --phase2-only      # Resume from phase 1 results
    python scripts/bulk_batch.py --status            # Check batch progress
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Tuple

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.grouper import (
    _parse_json_response, _extract_outcome_by_pattern,
    build_grouping_request, build_outcome_request,
)
from pipeline.summarizer import build_summary_request
from pipeline.members import extract_member, load_members, save_members
from pipeline.linker import link_threads
from batch import (
    make_thread_id, build_thread_context, _safe_id,
    update_public_status, collect_batch_results,
)

log = logging.getLogger("bulk_batch")

# Sonnet 4 retired 2026-06-15 (API 404) → Sonnet 5. thinking=disabled on every
# request: Sonnet 5 enables adaptive thinking when omitted, which would eat the
# max_tokens budget and add nondeterminism. See scripts/summarize.py.
DEFAULT_MODEL = "claude-sonnet-5"
RAW_DIR = "data/raw"
THREADS_DIR = "data/threads"
MEMBERS_PATH = "data/members.json"
STATUS_PATH = "data/status.json"
STATE_PATH = "data/batch/bulk_state.json"


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"phase": "init"}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def pending_dates() -> list[str]:
    """Find raw dates that don't have processed threads yet."""
    raw_dates = set()
    for f in os.listdir(RAW_DIR):
        if not f.endswith(".json") or f.startswith("council-") or f.startswith("ndl-"):
            continue
        name = f.replace(".json", "")
        if len(name) == 10 and name[4] == "-":
            raw_dates.add(name)

    done_dates = set()
    if os.path.isdir(THREADS_DIR):
        for f in os.listdir(THREADS_DIR):
            if f.endswith(".json") and not f.endswith(".progress.json"):
                done_dates.add(f.replace(".json", ""))

    return sorted(raw_dates - done_dates)


def load_raw(date_str: str) -> list[dict]:
    """Load meetings from raw file."""
    path = os.path.join(RAW_DIR, f"{date_str}.json")
    with open(path) as f:
        data = json.load(f)
    return data.get("meetings", [])


# ---------------------------------------------------------------------------
# Phase 1: Mega-batch for grouping + outcome
# ---------------------------------------------------------------------------

def build_mega_phase1(dates: list[str], model: str) -> Tuple[list[dict], dict]:
    """Build all Phase 1 requests across all dates.

    Returns (requests, date_meetings_map) where date_meetings_map
    maps date_str -> list of meetings for later assembly.
    """
    import anthropic

    all_requests = []
    date_meetings = {}

    for date_str in dates:
        meetings = load_raw(date_str)
        date_meetings[date_str] = meetings

        for meeting in meetings:
            meeting_id = meeting.get("meetingId", "unknown")
            # Same builders as the daily pipeline — see build_phase1_requests.
            for build, prefix in (
                (build_grouping_request, "group"),
                (build_outcome_request, "outcome"),
            ):
                request = build(meeting, f"{prefix}_{_safe_id(meeting_id)}", model)
                if request is not None:
                    all_requests.append(request)

    return all_requests, date_meetings


# ---------------------------------------------------------------------------
# Phase 2: Mega-batch for summarization
# ---------------------------------------------------------------------------

def build_mega_phase2(
    date_meetings: dict,
    grouping_results: dict,
    model: str,
) -> list[dict]:
    """Build all Phase 2 requests across all dates."""
    all_requests = []

    for date_str, meetings in date_meetings.items():
        for meeting in meetings:
            meeting_id = meeting.get("meetingId", "unknown")
            threads = grouping_results.get(meeting_id, {}).get("threads", [])
            speech_lookup = {s.get("speechOrder", 0): s for s in meeting.get("speeches", [])}

            for i, thread_info in enumerate(threads):
                orders = thread_info.get("speechOrders", [])
                thread_speeches = [speech_lookup[o] for o in orders if o in speech_lookup]
                if not thread_speeches:
                    continue

                all_requests.append(build_summary_request(
                    meeting, thread_info, thread_speeches,
                    f"summary_{_safe_id(meeting_id)}_{i:03d}", model,
                ))

    return all_requests


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble_date(
    date_str: str,
    meetings: list[dict],
    grouping_results: dict,
    summary_results: dict,
    outcome_results: dict,
    members: dict,
) -> list[dict]:
    """Assemble threads for a single date."""
    all_threads = []
    thread_counter = 0

    for meeting in meetings:
        meeting_id = meeting.get("meetingId", "unknown")
        meeting_name = meeting.get("meeting", "")
        house = meeting.get("house", "")
        speech_lookup = {s.get("speechOrder", 0): s for s in meeting.get("speeches", [])}

        grouping = grouping_results.get(meeting_id, {})
        thread_infos = grouping.get("threads", [])
        outcome = outcome_results.get(meeting_id, {
            "result": None, "resolution": None, "status": "ongoing"
        })

        if not thread_infos:
            continue

        for i, thread_info in enumerate(thread_infos):
            thread_counter += 1
            thread_id = make_thread_id(date_str, meeting_id, thread_counter)
            summary_key = f"summary_{_safe_id(meeting_id)}_{i:03d}"
            summary_text = summary_results.get(summary_key)

            if not summary_text:
                continue

            try:
                summary_data = _parse_json_response(summary_text)
            except Exception:
                log.warning("Failed to parse summary for %s", summary_key)
                continue

            ai_speeches = summary_data.get("speeches", [])
            commitments = summary_data.get("commitments", [])

            assembled = []
            for ai_speech in ai_speeches:
                order = ai_speech.get("speechOrder")
                raw = speech_lookup.get(order)
                if not raw:
                    continue
                member = extract_member(raw, existing_members=members)
                if member["id"] not in members:
                    members[member["id"]] = member
                assembled.append({
                    "memberId": member["id"],
                    "tension": ai_speech.get("tension", "確認"),
                    "keywords": ai_speech.get("keywords", [])[:3],
                    "quote": ai_speech.get("quote", ""),
                    "raw": raw.get("speech", ""),
                    "sourceUrl": raw.get("speechURL", ""),
                    "summaries": ai_speech.get("summaries", {
                        "easy": "", "teen": "", "adult": ""
                    }),
                })

            if not assembled:
                continue

            is_last = (thread_info is thread_infos[-1])
            context = build_thread_context(thread_info, meeting)

            source = meeting.get("source", "ndl")
            source_labels = {
                "ndl": "国会会議録",
                "kantei": "首相記者会見",
                "council": "審議会",
            }

            thread = {
                "id": thread_id,
                "date": date_str.replace("-", "."),
                "committee": meeting_name,
                "house": house,
                "topic": thread_info.get("topic", ""),
                "topicTag": thread_info.get("topicTag", ""),
                "topicColor": thread_info.get("topicColor", "#6b7280"),
                "summary": thread_info.get("summary", ""),
                "source": source,
                "sourceLabel": source_labels.get(source, source),
                "context": context,
                "speeches": assembled,
                "outcome": {
                    "result": outcome.get("result") if is_last else None,
                    "resolution": outcome.get("resolution") if is_last else None,
                    "commitments": commitments or [],
                    "status": outcome.get("status", "ongoing"),
                },
            }
            all_threads.append(thread)

    return all_threads


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def submit_batch(client, requests: list[dict], label: str) -> str:
    """Submit requests in chunks (max 10000 per batch)."""
    BATCH_LIMIT = 200
    if len(requests) <= BATCH_LIMIT:
        batch = client.messages.batches.create(requests=requests)
        log.info("%s batch submitted: %s (%d requests)", label, batch.id, len(requests))
        return batch.id

    # Multiple batches needed
    batch_ids = []
    for chunk_start in range(0, len(requests), BATCH_LIMIT):
        chunk = requests[chunk_start:chunk_start + BATCH_LIMIT]
        batch = client.messages.batches.create(requests=chunk)
        batch_ids.append(batch.id)
        log.info("%s batch chunk submitted: %s (%d requests)",
                 label, batch.id, len(chunk))

    # Return comma-separated IDs
    return ",".join(batch_ids)


def wait_for_batches(client, batch_ids_str: str, label: str):
    """Wait for one or more batches to complete."""
    batch_ids = batch_ids_str.split(",")
    for bid in batch_ids:
        log.info("Waiting for %s batch %s...", label, bid)
        while True:
            batch = client.messages.batches.retrieve(bid)
            status = batch.processing_status
            counts = batch.request_counts
            log.info("  %s %s: %s (ok=%d err=%d proc=%d)",
                     label, bid[:20], status,
                     counts.succeeded, counts.errored, counts.processing)
            if status == "ended":
                break
            time.sleep(30)


def collect_all_results(client, batch_ids_str: str) -> dict:
    """Collect results from one or more batches."""
    results = {}
    for bid in batch_ids_str.split(","):
        results.update(collect_batch_results(client, bid))
    return results


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    model: str = DEFAULT_MODEL,
    phase1_only: bool = False,
    phase2_only: bool = False,
    date_from: str | None = None,
    date_until: str | None = None,
):
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    state = load_state()
    dates = pending_dates()

    # Apply date filters
    if date_from:
        dates = [d for d in dates if d >= date_from]
    if date_until:
        dates = [d for d in dates if d <= date_until]

    if not dates:
        log.info("No pending dates to process!")
        return

    log.info("Pending dates: %d", len(dates))

    # --- Phase 1 ---
    if state["phase"] in ("init",) and not phase2_only:
        requests, date_meetings = build_mega_phase1(dates, model)
        log.info("Phase 1: %d requests across %d dates", len(requests), len(dates))

        # Save date_meetings for later
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        dm_path = os.path.join(os.path.dirname(STATE_PATH), "date_meetings.json")
        with open(dm_path, "w") as f:
            json.dump(date_meetings, f, ensure_ascii=False)

        batch_ids = submit_batch(client, requests, "Phase 1")
        state["phase"] = "phase1_submitted"
        state["phase1_batch_ids"] = batch_ids
        state["dates"] = dates
        save_state(state)

        if phase1_only:
            log.info("Phase 1 submitted. Run again to continue.")
            return

    if state["phase"] == "phase1_submitted" and not phase2_only:
        wait_for_batches(client, state["phase1_batch_ids"], "Phase 1")
        state["phase"] = "phase1_done"
        save_state(state)

    # Collect Phase 1 results
    if state["phase"] == "phase1_done" or phase2_only:
        if state["phase"] == "phase1_done":
            log.info("Collecting Phase 1 results...")
            raw_results = collect_all_results(client, state["phase1_batch_ids"])

            # Load date_meetings
            dm_path = os.path.join(os.path.dirname(STATE_PATH), "date_meetings.json")
            with open(dm_path) as f:
                date_meetings = json.load(f)

            # Build id_map for all meetings
            id_map = {}
            for ds, meetings in date_meetings.items():
                for m in meetings:
                    mid = m.get("meetingId", "unknown")
                    id_map[_safe_id(mid)] = mid

            grouping_results = {}
            outcome_results = {}

            for cid, text in raw_results.items():
                try:
                    parsed = _parse_json_response(text)
                except Exception as e:
                    log.warning("Failed to parse %s: %s", cid, e)
                    continue

                if cid.startswith("group_"):
                    hashed = cid[len("group_"):]
                    meeting_id = id_map.get(hashed, hashed)
                    grouping_results[meeting_id] = parsed
                elif cid.startswith("outcome_"):
                    hashed = cid[len("outcome_"):]
                    meeting_id = id_map.get(hashed, hashed)
                    outcome_results[meeting_id] = parsed

            # Pattern-matched outcomes
            for ds, meetings in date_meetings.items():
                for m in meetings:
                    mid = m.get("meetingId", "unknown")
                    if mid not in outcome_results:
                        outcome_results[mid] = _extract_outcome_by_pattern(
                            m.get("speeches", [])
                        )

            # Save intermediate
            inter_path = os.path.join(os.path.dirname(STATE_PATH), "phase1_results.json")
            with open(inter_path, "w") as f:
                json.dump({
                    "grouping": grouping_results,
                    "outcomes": outcome_results,
                }, f, ensure_ascii=False)

            state["phase"] = "phase2_ready"
            save_state(state)

    # Load intermediate results
    inter_path = os.path.join(os.path.dirname(STATE_PATH), "phase1_results.json")
    dm_path = os.path.join(os.path.dirname(STATE_PATH), "date_meetings.json")

    with open(inter_path) as f:
        intermediate = json.load(f)
    with open(dm_path) as f:
        date_meetings = json.load(f)

    grouping_results = intermediate["grouping"]
    outcome_results = intermediate["outcomes"]

    # Filter date_meetings to only requested dates
    if date_from or date_until:
        filtered_dm = {}
        for d, m in date_meetings.items():
            if date_from and d < date_from:
                continue
            if date_until and d > date_until:
                continue
            filtered_dm[d] = m
        date_meetings = filtered_dm
        log.info("Filtered to %d dates for Phase 2", len(date_meetings))

    # --- Phase 2 ---
    if state["phase"] in ("phase2_ready",):
        requests = build_mega_phase2(date_meetings, grouping_results, model)
        log.info("Phase 2: %d requests", len(requests))

        batch_ids = submit_batch(client, requests, "Phase 2")
        state["phase"] = "phase2_submitted"
        state["phase2_batch_ids"] = batch_ids
        save_state(state)

    if state["phase"] == "phase2_submitted":
        wait_for_batches(client, state["phase2_batch_ids"], "Phase 2")
        state["phase"] = "phase2_done"
        save_state(state)

    # --- Assembly ---
    if state["phase"] == "phase2_done":
        log.info("Collecting Phase 2 results...")
        summary_results = collect_all_results(client, state["phase2_batch_ids"])

        members = load_members(MEMBERS_PATH)
        dates = state.get("dates", list(date_meetings.keys()))

        total_threads = 0
        for date_str in sorted(dates):
            meetings = date_meetings.get(date_str, [])
            if not meetings:
                continue

            threads = assemble_date(
                date_str, meetings,
                grouping_results, summary_results, outcome_results,
                members,
            )

            if threads:
                link_threads(threads)
                os.makedirs(THREADS_DIR, exist_ok=True)
                out_path = os.path.join(THREADS_DIR, f"{date_str}.json")
                with open(out_path, "w") as f:
                    json.dump(threads, f, ensure_ascii=False, indent=2)
                log.info("  %s: %d threads → %s", date_str, len(threads), out_path)
                total_threads += len(threads)

        save_members(members, MEMBERS_PATH)
        log.info("Saved %d members", len(members))
        log.info("Total: %d threads across %d dates", total_threads, len(dates))

        state["phase"] = "completed"
        save_state(state)


def show_status():
    import anthropic
    state = load_state()
    print(f"Phase: {state['phase']}")
    print(f"Dates: {len(state.get('dates', []))}")

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    for key in ("phase1_batch_ids", "phase2_batch_ids"):
        ids = state.get(key, "")
        if not ids:
            continue
        for bid in ids.split(","):
            batch = client.messages.batches.retrieve(bid)
            c = batch.request_counts
            print(f"  {key} {bid}: {batch.processing_status} "
                  f"(ok={c.succeeded} err={c.errored} proc={c.processing})")


def main():
    parser = argparse.ArgumentParser(description="Bulk batch pipeline")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--phase1-only", action="store_true")
    parser.add_argument("--phase2-only", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--reset", action="store_true", help="Reset state to start over")
    parser.add_argument("--date-from", default=None, help="Filter: only process dates >= this")
    parser.add_argument("--date-until", default=None, help="Filter: only process dates <= this")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.status:
        show_status()
        return

    if args.reset:
        if os.path.exists(STATE_PATH):
            os.remove(STATE_PATH)
        log.info("State reset")
        return

    run_pipeline(
        model=args.model,
        phase1_only=args.phase1_only,
        phase2_only=args.phase2_only,
        date_from=args.date_from,
        date_until=args.date_until,
    )


if __name__ == "__main__":
    main()
