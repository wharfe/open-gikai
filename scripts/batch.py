#!/usr/bin/env python3
"""Batch API pipeline for GIKAI.

Two-phase batch processing using Anthropic's Message Batches API:
  Phase 1: Topic grouping + outcome extraction (1 batch)
  Phase 2: Per-thread summarization (1 batch, depends on Phase 1)

Usage:
    python scripts/batch.py --date 2025-03-14 run       # Full pipeline
    python scripts/batch.py --date 2025-03-14 status     # Check progress
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.prompts import (
    GROUPING_SYSTEM, GROUPING_PROMPT,
    OUTCOME_SYSTEM, OUTCOME_PROMPT,
    SUMMARY_SYSTEM, SUMMARY_PROMPT,
)
from pipeline.grouper import (
    _is_procedural, _truncate_speech, _format_speech_for_grouping,
    _parse_json_response, _extract_outcome_by_pattern,
)
from pipeline.summarizer import _format_speech_for_summary
from pipeline.members import extract_member, load_members, save_members
from pipeline.linker import link_threads

log = logging.getLogger("batch")

DEFAULT_MODEL = "claude-sonnet-4-20250514"
BATCH_DIR = "data/batch"


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def state_path(output_dir: str, date_str: str) -> str:
    return os.path.join(output_dir, date_str, "state.json")


def load_state(output_dir: str, date_str: str) -> dict:
    p = state_path(output_dir, date_str)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"phase": "init", "phase1_batch_id": None, "phase2_batch_id": None}


def save_state(output_dir: str, date_str: str, state: dict) -> None:
    d = os.path.join(output_dir, date_str)
    os.makedirs(d, exist_ok=True)
    with open(state_path(output_dir, date_str), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Status tracking (public)
# ---------------------------------------------------------------------------

def update_public_status(
    status_path: str,
    date_str: str,
    committees: List[dict],
    phase: str,
) -> None:
    """Update the public status.json with processing progress."""
    if os.path.exists(status_path):
        with open(status_path, "r", encoding="utf-8") as f:
            status = json.load(f)
    else:
        status = {}

    status[date_str] = {
        "updatedAt": datetime.utcnow().isoformat() + "Z",
        "phase": phase,
        "committees": committees,
    }

    os.makedirs(os.path.dirname(status_path), exist_ok=True)
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Phase 1: Submit grouping + outcome batch
# ---------------------------------------------------------------------------

def build_phase1_requests(
    meetings: List[dict],
    model: str,
) -> List[dict]:
    """Build batch requests for grouping and outcome extraction."""
    requests = []

    for meeting in meetings:
        meeting_id = meeting.get("meetingId", "unknown")
        speeches = meeting.get("speeches", [])

        # Grouping request
        substantive = [s for s in speeches if not _is_procedural(s)]
        if substantive:
            formatted = "\n\n".join(_format_speech_for_grouping(s) for s in substantive)
            prompt = GROUPING_PROMPT.format(
                house=meeting.get("house", ""),
                meeting=meeting.get("meeting", ""),
                date=meeting.get("date", ""),
                speeches=formatted,
            )
            requests.append({
                "custom_id": f"group_{meeting_id}",
                "params": {
                    "model": model,
                    "max_tokens": 8192,
                    "system": GROUPING_SYSTEM,
                    "messages": [{"role": "user", "content": prompt}],
                },
            })

        # Outcome request (only if vote patterns detected)
        outcome = _extract_outcome_by_pattern(speeches)
        if outcome and outcome.get("resolution"):
            procedural = []
            for s in speeches:
                role = s.get("speakerRole", "") or ""
                text = s.get("speech", "").strip()
                is_chair = "委員長" in role or "委員長" in text[:30]
                if (is_chair or "附帯決議" in text) and len(text) > 50:
                    procedural.append(f"[{s.get('speaker', '')}] {text}")

            if procedural:
                prompt = OUTCOME_PROMPT.format(
                    house=meeting.get("house", ""),
                    meeting=meeting.get("meeting", ""),
                    date=meeting.get("date", ""),
                    procedural_speeches="\n\n".join(procedural[-10:]),
                )
                requests.append({
                    "custom_id": f"outcome_{meeting_id}",
                    "params": {
                        "model": model,
                        "max_tokens": 1024,
                        "system": OUTCOME_SYSTEM,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                })

    return requests


# ---------------------------------------------------------------------------
# Phase 2: Submit summarization batch
# ---------------------------------------------------------------------------

def build_phase2_requests(
    meetings: List[dict],
    grouping_results: Dict[str, dict],
    model: str,
) -> List[dict]:
    """Build batch requests for per-thread summarization."""
    requests = []

    for meeting in meetings:
        meeting_id = meeting.get("meetingId", "unknown")
        threads = grouping_results.get(meeting_id, {}).get("threads", [])
        speech_lookup = {s.get("speechOrder", 0): s for s in meeting.get("speeches", [])}

        for i, thread_info in enumerate(threads):
            orders = thread_info.get("speechOrders", [])
            thread_speeches = [speech_lookup[o] for o in orders if o in speech_lookup]
            if not thread_speeches:
                continue

            formatted = "\n\n---\n\n".join(
                _format_speech_for_summary(s) for s in thread_speeches
            )
            prompt = SUMMARY_PROMPT.format(
                house=meeting.get("house", ""),
                meeting=meeting.get("meeting", ""),
                topic=thread_info.get("topic", ""),
                speeches=formatted,
            )
            requests.append({
                "custom_id": f"summary_{meeting_id}_{i:03d}",
                "params": {
                    "model": model,
                    "max_tokens": 8192,
                    "system": SUMMARY_SYSTEM,
                    "messages": [{"role": "user", "content": prompt}],
                },
            })

    return requests


# ---------------------------------------------------------------------------
# Result collection
# ---------------------------------------------------------------------------

def wait_for_batch(client: anthropic.Anthropic, batch_id: str, label: str) -> dict:
    """Poll until batch completes."""
    log.info("Waiting for %s batch %s...", label, batch_id)
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        status = batch.processing_status
        counts = batch.request_counts
        log.info(
            "  %s: %s (succeeded=%d, errored=%d, processing=%d)",
            label, status,
            counts.succeeded, counts.errored, counts.processing,
        )
        if status == "ended":
            return batch
        time.sleep(30)


def collect_batch_results(client: anthropic.Anthropic, batch_id: str) -> Dict[str, str]:
    """Collect all results from a completed batch. Returns {custom_id: response_text}."""
    results = {}
    for result in client.messages.batches.results(batch_id):
        cid = result.custom_id
        if result.result.type == "succeeded":
            msg = result.result.message
            results[cid] = msg.content[0].text
        else:
            log.warning("Request %s failed: %s", cid, result.result.type)
    return results


# ---------------------------------------------------------------------------
# Assembly (same logic as summarize.py)
# ---------------------------------------------------------------------------

def make_thread_id(date_str: str, meeting_id: str, index: int) -> str:
    h = hashlib.sha256(meeting_id.encode("utf-8")).hexdigest()[:6]
    return f"t_{date_str.replace('-', '')}_{h}_{index:02d}"


def build_thread_context(thread_info, meeting):
    """Build context from grouper output."""
    description = thread_info.get("contextDescription", "")
    legislation = thread_info.get("legislationName")
    if not description:
        return None
    links = []
    meeting_url = meeting.get("meetingURL")
    if meeting_url:
        source = meeting.get("source", "ndl")
        url_labels = {
            "ndl": "会議録全文（NDL）",
            "kantei": "記者会見全文（首相官邸）",
            "council": "議事録（内閣府）",
        }
        links.append({"label": url_labels.get(source, "原文"), "url": meeting_url})
    if legislation:
        import urllib.parse
        law_name = legislation
        for suffix in ["の一部を改正する法律案", "等の一部を改正する法律案", "法律案", "法案", "改正案"]:
            law_name = law_name.replace(suffix, "")
        law_name = law_name.rstrip("等の") or legislation
        egov_url = "https://www.google.com/search?" + urllib.parse.urlencode(
            {"q": f"{law_name} site:laws.e-gov.go.jp"}
        )
        links.append({"label": f"{law_name}（法令検索）", "url": egov_url})
    return {"description": description, "links": links if links else None}


def assemble_all(
    meetings: List[dict],
    grouping_results: Dict[str, dict],
    summary_results: Dict[str, str],
    outcome_results: Dict[str, dict],
    date_str: str,
    members: Dict[str, dict],
) -> tuple:
    """Assemble all threads from batch results. Returns (threads, committee_statuses)."""
    all_threads = []
    committee_statuses = []
    thread_counter = 0

    for meeting in meetings:
        meeting_id = meeting.get("meetingId", "unknown")
        meeting_name = meeting.get("meeting", "")
        house = meeting.get("house", "")
        speech_lookup = {s.get("speechOrder", 0): s for s in meeting.get("speeches", [])}

        grouping = grouping_results.get(meeting_id, {})
        thread_infos = grouping.get("threads", [])
        outcome = outcome_results.get(meeting_id, {"result": None, "resolution": None, "status": "ongoing"})

        if not thread_infos:
            committee_statuses.append({
                "name": meeting_name, "house": house,
                "status": "failed", "threads": 0, "error": "グルーピング失敗",
            })
            continue

        meeting_threads = []
        for i, thread_info in enumerate(thread_infos):
            thread_counter += 1
            thread_id = make_thread_id(date_str, meeting_id, thread_counter)
            summary_key = f"summary_{meeting_id}_{i:03d}"
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

            # Assemble speeches
            assembled = []
            for ai_speech in ai_speeches:
                order = ai_speech.get("speechOrder")
                raw = speech_lookup.get(order)
                if not raw:
                    continue
                member = extract_member(raw)
                if member["id"] not in members:
                    members[member["id"]] = member
                assembled.append({
                    "memberId": member["id"],
                    "tension": ai_speech.get("tension", "確認"),
                    "keywords": ai_speech.get("keywords", [])[:3],
                    "quote": ai_speech.get("quote", ""),
                    "raw": raw.get("speech", ""),
                    "sourceUrl": raw.get("speechURL", ""),
                    "summaries": ai_speech.get("summaries", {"easy": "", "teen": "", "adult": ""}),
                })

            if not assembled:
                continue

            is_last = (thread_info is thread_infos[-1])
            context = build_thread_context(thread_info, meeting)

            # Determine source from meeting metadata
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
            meeting_threads.append(thread)

        all_threads.extend(meeting_threads)
        committee_statuses.append({
            "name": meeting_name, "house": house,
            "status": "completed", "threads": len(meeting_threads),
        })

    return all_threads, committee_statuses


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    date_str: str,
    model: str = DEFAULT_MODEL,
    raw_dir: str = "data/raw",
    output_dir: str = "data/threads",
    members_path: str = "data/members.json",
    status_path: str = "data/status.json",
    batch_dir: str = BATCH_DIR,
) -> None:
    """Run the full two-phase batch pipeline."""
    raw_path = os.path.join(raw_dir, f"{date_str}.json")
    if not os.path.exists(raw_path):
        log.error("Raw data not found: %s", raw_path)
        sys.exit(1)

    with open(raw_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    meetings = raw_data.get("meetings", [])
    log.info("Processing %d meetings from %s", len(meetings), raw_path)

    client = anthropic.Anthropic()
    state = load_state(batch_dir, date_str)

    # Update initial status
    committee_names = [
        {"name": m.get("meeting", ""), "house": m.get("house", ""), "status": "pending", "threads": 0}
        for m in meetings
    ]
    update_public_status(status_path, date_str, committee_names, "starting")

    # --- Phase 1: Grouping + Outcome ---
    if state["phase"] in ("init", "phase1_submitted"):
        if state["phase"] == "init":
            requests = build_phase1_requests(meetings, model)
            log.info("Submitting Phase 1 batch: %d requests", len(requests))
            batch = client.messages.batches.create(requests=requests)
            state["phase1_batch_id"] = batch.id
            state["phase"] = "phase1_submitted"
            save_state(batch_dir, date_str, state)
            log.info("Phase 1 batch submitted: %s", batch.id)

        # Wait for Phase 1
        update_public_status(status_path, date_str, committee_names, "grouping")
        wait_for_batch(client, state["phase1_batch_id"], "Phase 1 (grouping)")
        state["phase"] = "phase1_done"
        save_state(batch_dir, date_str, state)

    # Collect Phase 1 results
    if state["phase"] == "phase1_done":
        log.info("Collecting Phase 1 results...")
        raw_results = collect_batch_results(client, state["phase1_batch_id"])

        grouping_results = {}
        outcome_results = {}

        for cid, text in raw_results.items():
            try:
                parsed = _parse_json_response(text)
            except Exception as e:
                log.warning("Failed to parse %s: %s", cid, e)
                continue

            if cid.startswith("group_"):
                meeting_id = cid[len("group_"):]
                grouping_results[meeting_id] = parsed
            elif cid.startswith("outcome_"):
                meeting_id = cid[len("outcome_"):]
                outcome_results[meeting_id] = parsed

        # Also get pattern-matched outcomes for meetings without API outcome
        for meeting in meetings:
            mid = meeting.get("meetingId", "unknown")
            if mid not in outcome_results:
                outcome_results[mid] = _extract_outcome_by_pattern(meeting.get("speeches", []))

        # Save intermediate results
        intermediate = {"grouping": grouping_results, "outcomes": outcome_results}
        inter_path = os.path.join(batch_dir, date_str, "phase1_results.json")
        with open(inter_path, "w", encoding="utf-8") as f:
            json.dump(intermediate, f, ensure_ascii=False, indent=2)

        state["phase"] = "phase2_ready"
        save_state(batch_dir, date_str, state)

    # Load Phase 1 results
    inter_path = os.path.join(batch_dir, date_str, "phase1_results.json")
    with open(inter_path, "r", encoding="utf-8") as f:
        intermediate = json.load(f)
    grouping_results = intermediate["grouping"]
    outcome_results = intermediate["outcomes"]

    # --- Phase 2: Summarization ---
    if state["phase"] in ("phase2_ready", "phase2_submitted"):
        if state["phase"] == "phase2_ready":
            requests = build_phase2_requests(meetings, grouping_results, model)
            log.info("Submitting Phase 2 batch: %d requests", len(requests))
            batch = client.messages.batches.create(requests=requests)
            state["phase2_batch_id"] = batch.id
            state["phase"] = "phase2_submitted"
            save_state(batch_dir, date_str, state)
            log.info("Phase 2 batch submitted: %s", batch.id)

        # Wait for Phase 2
        update_public_status(status_path, date_str, committee_names, "summarizing")
        wait_for_batch(client, state["phase2_batch_id"], "Phase 2 (summarization)")
        state["phase"] = "phase2_done"
        save_state(batch_dir, date_str, state)

    # Collect Phase 2 and assemble
    if state["phase"] == "phase2_done":
        log.info("Collecting Phase 2 results...")
        summary_results = collect_batch_results(client, state["phase2_batch_id"])

        members = load_members(members_path)
        all_threads, committee_statuses = assemble_all(
            meetings, grouping_results, summary_results, outcome_results,
            date_str, members,
        )

        # Cross-thread linking
        link_threads(all_threads)

        # Write output
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{date_str}.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_threads, f, ensure_ascii=False, indent=2)
        log.info("Wrote %d threads → %s", len(all_threads), output_path)

        save_members(members, members_path)
        log.info("Saved %d members → %s", len(members), members_path)

        # Update final status
        update_public_status(status_path, date_str, committee_statuses, "completed")

        state["phase"] = "completed"
        save_state(batch_dir, date_str, state)
        log.info("Pipeline complete! %d threads from %d committees",
                 len(all_threads), len([c for c in committee_statuses if c["status"] == "completed"]))


def show_status(date_str: str, batch_dir: str = BATCH_DIR, status_path: str = "data/status.json") -> None:
    """Show current pipeline status."""
    state = load_state(batch_dir, date_str)
    print(f"Date: {date_str}")
    print(f"Phase: {state['phase']}")

    if os.path.exists(status_path):
        with open(status_path, "r", encoding="utf-8") as f:
            status = json.load(f)
        day_status = status.get(date_str, {})
        print(f"Public status: {day_status.get('phase', 'unknown')}")
        for c in day_status.get("committees", []):
            icon = "✅" if c["status"] == "completed" else "⏳" if c["status"] == "pending" else "❌"
            print(f"  {icon} {c['house']}{c['name']}: {c['status']} ({c.get('threads', 0)} threads)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    parser = argparse.ArgumentParser(description="Batch API pipeline for GIKAI")
    parser.add_argument("command", choices=["run", "status"], help="Command to execute")
    parser.add_argument("--date", default=yesterday, help="Date to process (YYYY-MM-DD)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if args.command == "run":
        run(date_str=args.date, model=args.model)
    elif args.command == "status":
        show_status(date_str=args.date)


if __name__ == "__main__":
    main()
