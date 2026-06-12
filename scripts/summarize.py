#!/usr/bin/env python3
"""AI summarization pipeline for GIKAI.

Reads raw NDL speech data, uses Claude API to group speeches by topic
and generate structured summaries, then outputs frontend-ready JSON.

Usage:
    python scripts/summarize.py --date 2025-03-14
    python scripts/summarize.py --date 2025-03-14 --meeting 環境委員会 --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import date, timedelta
from typing import Dict, List, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

# Add scripts/ to path for pipeline imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.grouper import group_meeting, extract_meeting_outcome
from pipeline.summarizer import (
    summarize_thread,
    build_summary_request,
    submit_summary_batch,
    poll_summary_batch,
    fetch_summary_results,
)
from pipeline.members import extract_member, load_members, save_members
from pipeline.linker import link_threads
from pipeline import batch_state as bs

log = logging.getLogger("summarize")

DEFAULT_MODEL = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------

def load_progress(progress_path: str) -> dict:
    """Load progress file for resumability."""
    if os.path.exists(progress_path):
        with open(progress_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": [], "failed": []}


def save_progress(progress: dict, progress_path: str) -> None:
    """Save progress file."""
    os.makedirs(os.path.dirname(progress_path), exist_ok=True)
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# lex-diff cross-linking
# ---------------------------------------------------------------------------

_LEXDIFF_MAP: Optional[Dict[str, dict]] = None

def _get_lexdiff_map() -> Dict[str, dict]:
    """Return the lex-diff mapping, loading lazily."""
    global _LEXDIFF_MAP
    if _LEXDIFF_MAP is None:
        mapping_path = os.path.join(os.path.dirname(__file__), "..", "data", "lexdiff-mapping.json")
        if os.path.exists(mapping_path):
            with open(mapping_path, "r", encoding="utf-8") as f:
                _LEXDIFF_MAP = json.load(f)
                _LEXDIFF_MAP.pop("_comment", None)
        else:
            _LEXDIFF_MAP = {}
    return _LEXDIFF_MAP


def _get_lexdiff_link(law_name: str) -> Optional[dict]:
    """Look up a lex-diff link for a law name."""
    mapping = _get_lexdiff_map()
    if law_name in mapping:
        entry = mapping[law_name]
        return {"label": f"{law_name}（改正差分）", "url": entry["url"]}
    return None


# ---------------------------------------------------------------------------
# Thread assembly
# ---------------------------------------------------------------------------

def make_thread_id(date_str: str, meeting_id: str, index: int) -> str:
    """Generate a stable thread ID."""
    h = hashlib.sha256(meeting_id.encode("utf-8")).hexdigest()[:6]
    return f"t_{date_str.replace('-', '')}_{h}_{index:02d}"


def build_speech_lookup(speeches: List[dict]) -> Dict[int, dict]:
    """Build a lookup from speechOrder to speech record."""
    return {s.get("speechOrder", 0): s for s in speeches}


def build_thread_context(thread_info: dict, meeting: dict) -> Optional[dict]:
    """Build context (background description + links) for a thread."""
    description = thread_info.get("contextDescription", "")
    legislation = thread_info.get("legislationName")

    if not description:
        return None

    links = []

    # Add source URL if available
    meeting_url = meeting.get("meetingURL")
    if meeting_url:
        source = meeting.get("source", "ndl")
        url_labels = {
            "ndl": "会議録全文（NDL）",
            "kantei": "記者会見全文（首相官邸）",
            "council": "議事録（内閣府）",
        }
        links.append({"label": url_labels.get(source, "原文"), "url": meeting_url})

    # Generate e-Gov search link if a law name is mentioned
    if legislation:
        import urllib.parse
        # Simplify to base law name (remove amendment boilerplate)
        law_name = legislation
        for suffix in ["の一部を改正する法律案", "等の一部を改正する法律案", "法律案", "法案", "改正案"]:
            law_name = law_name.replace(suffix, "")
        law_name = law_name.rstrip("等の") or legislation
        # Use Google search scoped to e-Gov (e-Gov SPA doesn't support deep links)
        egov_url = "https://www.google.com/search?" + urllib.parse.urlencode({
            "q": f"{law_name} site:laws.e-gov.go.jp"
        })
        links.append({"label": f"{law_name}（法令検索）", "url": egov_url})

        # lex-diff cross-link (from legislationName)
        lexdiff_link = _get_lexdiff_link(law_name)
        if lexdiff_link:
            links.append(lexdiff_link)

    # lex-diff cross-link fallback: scan description for known law names
    if not legislation and description:
        for lexdiff_name in _get_lexdiff_map():
            if lexdiff_name in description:
                lexdiff_link = _get_lexdiff_link(lexdiff_name)
                if lexdiff_link:
                    links.append(lexdiff_link)
                break

    # Generate bill search link for Shugiin/Sangiin
    house = meeting.get("house", "")
    if legislation and "法案" in legislation:
        if house == "衆議院":
            links.append({
                "label": "議案情報（衆議院）",
                "url": "https://www.shugiin.go.jp/internet/itdb_gian.nsf/html/gian/menu.htm",
            })
        elif house == "参議院":
            links.append({
                "label": "議案情報（参議院）",
                "url": "https://www.sangiin.go.jp/japanese/joho1/kousei/gian/gian.htm",
            })

    return {
        "description": description,
        "links": links if links else None,
    }


def assemble_thread(
    meeting: dict,
    thread_info: dict,
    ai_speeches: List[dict],
    raw_lookup: Dict[int, dict],
    members: Dict[str, dict],
    thread_id: str,
) -> Optional[dict]:
    """Assemble a complete Thread dict from grouping + summarization results."""
    assembled_speeches = []

    for ai_speech in ai_speeches:
        order = ai_speech.get("speechOrder")
        raw = raw_lookup.get(order)
        if not raw:
            log.warning("speechOrder %s not found in raw data", order)
            continue

        # Extract/register member (pass existing for cross-source dedup)
        member = extract_member(raw, existing_members=members)
        member_id = member["id"]
        if member_id not in members:
            members[member_id] = member

        assembled_speeches.append({
            "memberId": member_id,
            "tension": ai_speech.get("tension", "確認"),
            "keywords": ai_speech.get("keywords", [])[:3],
            "quote": ai_speech.get("quote", ""),
            "raw": raw.get("speech", ""),
            "sourceUrl": raw.get("speechURL", ""),
            "summaries": ai_speech.get("summaries", {
                "easy": "",
                "teen": "",
                "adult": "",
            }),
        })

    if not assembled_speeches:
        return None

    date_str = meeting.get("date", "")
    display_date = date_str.replace("-", ".")

    # Build context from grouper output
    context = build_thread_context(
        thread_info, meeting,
    )

    # Determine source from meeting metadata
    raw_source = meeting.get("source", "ndl")
    source_labels = {
        "ndl": "国会会議録",
        "kantei": "首相記者会見",
        "council": "審議会",
    }
    # Normalize council-* sources to "council" for frontend styling,
    # but preserve the detailed label from sourceLabel metadata
    source = "council" if raw_source.startswith("council") else raw_source
    source_label = meeting.get("sourceLabel") or source_labels.get(source, source)

    return {
        "id": thread_id,
        "date": display_date,
        "committee": meeting.get("meeting", ""),
        "house": meeting.get("house", ""),
        "topic": thread_info.get("topic", ""),
        "topicTag": thread_info.get("topicTag", ""),
        "topicColor": thread_info.get("topicColor", "#6b7280"),
        "summary": thread_info.get("summary", ""),
        "source": source,
        "sourceLabel": source_label,
        "context": context,
        "speeches": assembled_speeches,
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_meeting(
    client: anthropic.Anthropic,
    meeting: dict,
    members: Dict[str, dict],
    model: str,
    date_str: str,
    thread_counter: int,
) -> tuple:
    """Process a single meeting through grouping + summarization + outcome.

    Returns (threads_list, updated_thread_counter).
    """
    meeting_id = meeting.get("meetingId", "unknown")
    speeches = meeting.get("speeches", [])
    raw_lookup = build_speech_lookup(speeches)

    # Phase B: Topic grouping
    thread_infos = group_meeting(client, meeting, model=model)
    time.sleep(1)

    # Phase D: Meeting-level outcome (votes, resolutions)
    meeting_outcome = extract_meeting_outcome(client, meeting, model=model)
    time.sleep(1)

    threads = []
    for thread_info in thread_infos:
        thread_counter += 1
        thread_id = make_thread_id(date_str, meeting_id, thread_counter)

        # Gather raw speeches for this thread
        orders = thread_info.get("speechOrders", [])
        thread_speeches = [raw_lookup[o] for o in orders if o in raw_lookup]

        if not thread_speeches:
            log.warning("No speeches found for thread '%s'", thread_info.get("topic"))
            continue

        # Phase C: Summarize speeches in this thread
        try:
            summary_result = summarize_thread(
                client, meeting, thread_info, thread_speeches, model=model,
            )
            ai_speeches = summary_result["speeches"]
            commitments = summary_result["commitments"]
            time.sleep(1)
        except Exception as e:
            log.error("Failed to summarize thread '%s': %s", thread_info.get("topic"), e)
            continue

        thread = assemble_thread(
            meeting, thread_info, ai_speeches, raw_lookup, members, thread_id,
        )
        if thread:
            # Build thread-level outcome
            # Only attach vote result/resolution to the last thread (closest to the vote)
            # All threads get their own commitments and the overall status
            is_last = (thread_info is thread_infos[-1])
            thread["outcome"] = {
                "result": meeting_outcome.get("result") if is_last else None,
                "resolution": meeting_outcome.get("resolution") if is_last else None,
                "commitments": commitments or [],
                "status": meeting_outcome.get("status", "ongoing"),
            }
            threads.append(thread)

    return threads, thread_counter


# ---------------------------------------------------------------------------
# Batch-mode helpers (SUMMARY phase only)
#
# Grouping and outcome extraction stay synchronous because they each emit one
# small call per meeting. The summarization phase emits 3+ calls per meeting
# with large repeated prompts — that's where the 50% Batches API discount
# (stackable with prompt caching) actually moves the needle.
# ---------------------------------------------------------------------------

def make_batch_custom_id(meeting_id: str, thread_idx: int) -> str:
    """Build an ASCII custom_id for a thread's batch request.

    Meeting IDs contain Japanese characters; the Batches API requires
    ``custom_id`` to match ``^[a-zA-Z0-9_-]{1,64}$``. We use a 12-hex-char
    SHA256 prefix which is collision-safe for any plausible batch size.
    """
    h = hashlib.sha256(meeting_id.encode("utf-8")).hexdigest()[:12]
    return f"s_{h}_{thread_idx:02d}"


def build_manifest_meetings(prepared_meetings: list, model: str) -> list:
    """Build sidecar ``meetings[]`` from prepared meetings.

    Captures the FULL thread_info (assemble_thread needs topicTag/topicColor/
    summary/etc.) plus a per-thread input_hash so a resumed batch result can be
    verified against re-fetched raw before being assembled.
    """
    meetings = []
    for prep in prepared_meetings:
        threads = []
        for idx, p in enumerate(prep["pending"]):
            request = build_summary_request(
                p["meeting"], p["thread_info"], p["thread_speeches"],
                p["custom_id"], model,
            )
            threads.append({
                "custom_id": p["custom_id"],
                "thread_idx": idx,
                "thread_info": p["thread_info"],
                "speechOrders": p["thread_info"].get("speechOrders", []),
                "input_hash": bs.compute_input_hash(request["params"]),
            })
        meetings.append({
            "meeting_id": prep["meeting_id"],
            "outcome": prep["outcome"],
            "threads": threads,
        })
    return meetings


def prepare_meeting_for_batch(
    client,
    meeting: dict,
    model: str,
) -> dict:
    """Run grouping + outcome extraction for one meeting (synchronous).

    Returns a dict with the data needed to (a) build the per-thread batch
    requests and (b) re-assemble threads after the batch returns.
    """
    meeting_id = meeting.get("meetingId", "unknown")
    speeches = meeting.get("speeches", [])
    raw_lookup = build_speech_lookup(speeches)

    thread_infos = group_meeting(client, meeting, model=model)
    time.sleep(1)
    meeting_outcome = extract_meeting_outcome(client, meeting, model=model)
    time.sleep(1)

    pending = []
    for idx, thread_info in enumerate(thread_infos):
        orders = thread_info.get("speechOrders", [])
        thread_speeches = [raw_lookup[o] for o in orders if o in raw_lookup]
        if not thread_speeches:
            log.warning(
                "No speeches for thread '%s' in %s",
                thread_info.get("topic"), meeting_id,
            )
            continue
        pending.append({
            "custom_id": make_batch_custom_id(meeting_id, idx),
            "meeting": meeting,
            "thread_info": thread_info,
            "thread_speeches": thread_speeches,
            "raw_lookup": raw_lookup,
        })

    return {
        "meeting_id": meeting_id,
        "thread_infos": thread_infos,
        "outcome": meeting_outcome,
        "pending": pending,
    }


def run_batch_phase(
    client,
    meetings: List[dict],
    progress: dict,
    members: Dict[str, dict],
    model: str,
    date_str: str,
    thread_counter: int,
    batch_timeout_seconds: int = 5400,  # 90 min
    batch_poll_seconds: int = 30,
) -> tuple:
    """Process meetings via Batches API for the summary phase.

    Returns ``(new_threads, updated_thread_counter, completed_meeting_ids)``.
    """
    # Phase 1: synchronously group + extract outcome for every new meeting.
    prepared_meetings: list[dict] = []
    all_pending: list[dict] = []

    for meeting in meetings:
        meeting_id = meeting.get("meetingId", "unknown")
        if meeting_id in progress["completed"]:
            log.info("Skipping already completed: %s", meeting_id)
            continue

        log.info("Preparing for batch: %s", meeting_id)
        try:
            prep = prepare_meeting_for_batch(client, meeting, model)
        except Exception as e:
            log.error("Failed to prepare %s: %s", meeting_id, e)
            progress["failed"].append(meeting_id)
            continue

        prepared_meetings.append(prep)
        all_pending.extend(prep["pending"])

    if not all_pending:
        log.info("Batch phase: nothing to summarize")
        return [], thread_counter, []

    # Phase 2: submit + poll + fetch.
    requests = [
        build_summary_request(
            p["meeting"], p["thread_info"], p["thread_speeches"],
            p["custom_id"], model,
        )
        for p in all_pending
    ]
    log.info("Submitting %d summary requests via Batches API", len(requests))
    batch_id = submit_summary_batch(client, requests)
    poll_summary_batch(
        client, batch_id,
        timeout_seconds=batch_timeout_seconds,
        poll_interval_seconds=batch_poll_seconds,
    )
    results = fetch_summary_results(client, batch_id)

    # Phase 3: assemble threads in deterministic order (matches sync mode).
    new_threads: list = []
    completed_meeting_ids: list = []

    for prep in prepared_meetings:
        meeting_id = prep["meeting_id"]
        outcome = prep["outcome"]
        thread_infos = prep["thread_infos"]

        for p in prep["pending"]:
            result = results.get(p["custom_id"])
            if not result:
                log.warning(
                    "No result for %s (thread '%s'); skipping",
                    p["custom_id"], p["thread_info"].get("topic"),
                )
                continue

            thread_counter += 1
            thread_id = make_thread_id(date_str, meeting_id, thread_counter)
            thread = assemble_thread(
                p["meeting"], p["thread_info"], result["speeches"],
                p["raw_lookup"], members, thread_id,
            )
            if not thread:
                continue

            is_last = (p["thread_info"] is thread_infos[-1])
            thread["outcome"] = {
                "result": outcome.get("result") if is_last else None,
                "resolution": outcome.get("resolution") if is_last else None,
                "commitments": result["commitments"] or [],
                "status": outcome.get("status", "ongoing"),
            }
            new_threads.append(thread)

        # Mark the meeting as completed even if some of its threads failed —
        # matches the sync path, which catches per-thread exceptions inside
        # the meeting loop and still records the meeting as done.
        completed_meeting_ids.append(meeting_id)

    return new_threads, thread_counter, completed_meeting_ids


def collect_processed_meeting_ids(threads_path: str) -> set[str]:
    """Recover the set of meeting_ids already represented in a threads file.

    The thread_id encodes the meeting via a 6-char hash of meeting_id, so we
    cannot recover the meeting_id directly. Instead we rely on the convention
    that every thread carries the meeting_id derivable from (committee, house,
    date) — we reconstruct candidate hashes and compare. To keep this robust
    we additionally treat the presence of a per-meeting thread as a marker.

    Returns a set of meeting_id hash prefixes (6 chars) that are already
    represented in the file.
    """
    if not os.path.exists(threads_path):
        return set()
    try:
        with open(threads_path, "r", encoding="utf-8") as f:
            threads = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not read %s for resume: %s", threads_path, e)
        return set()
    # Thread IDs look like ``t_YYYYMMDD_<6hex>_<index>``. Extract the hash.
    hashes: set[str] = set()
    for t in threads:
        tid = t.get("id", "")
        parts = tid.split("_")
        if len(parts) >= 3:
            hashes.add(parts[2])
    return hashes


def run_pipeline(
    date_str: str,
    meeting_filter: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    raw_dir: str = "data/raw",
    output_dir: str = "data/threads",
    members_path: str = "data/members.json",
    resume: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
    batch: bool = False,
    batch_timeout_seconds: int = 5400,
    batch_poll_seconds: int = 30,
) -> None:
    """Run the full summarization pipeline for a given date."""
    # Load raw data — collect meetings from all source files for this date
    import glob as _glob
    candidates = [
        os.path.join(raw_dir, f"ndl-{date_str}.json"),
        os.path.join(raw_dir, f"kantei-{date_str}.json"),
        os.path.join(raw_dir, f"council-{date_str}.json"),  # legacy
        *sorted(_glob.glob(os.path.join(raw_dir, f"council-*-{date_str}.json"))),
        os.path.join(raw_dir, f"{date_str}.json"),  # legacy
    ]
    meetings: list = []
    found_any = False
    for candidate in candidates:
        if os.path.exists(candidate):
            found_any = True
            with open(candidate, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            meetings.extend(raw_data.get("meetings", []))
            log.info("Loaded %d meetings from %s", len(raw_data.get("meetings", [])), candidate)

    if not found_any:
        log.error("Raw data not found for %s in %s", date_str, raw_dir)
        log.error("Run fetch_ndl.py or fetch_kantei.py first")
        sys.exit(1)
    if meeting_filter:
        meetings = [m for m in meetings if meeting_filter in m.get("meeting", "")]

    log.info("Processing %d meetings for %s", len(meetings), date_str)

    if dry_run:
        for m in meetings:
            speech_count = len(m.get("speeches", []))
            log.info("  %s — %d speeches", m.get("meetingId", "?"), speech_count)
        log.info("Dry run complete. No API calls made.")
        return

    # Progress tracking
    output_path = os.path.join(output_dir, f"{date_str}.json")
    progress_path = os.path.join(output_dir, f"{date_str}.progress.json")

    # Auto-resume: if the threads file already exists, treat already-summarized
    # meetings as completed so we only spend API calls on new ones. This keeps
    # the daily window-fetch idempotent without requiring callers to pass --resume.
    auto_resume = not resume and os.path.exists(output_path)

    if resume:
        progress = load_progress(progress_path)
        progress["failed"] = []
    elif auto_resume:
        progress = load_progress(progress_path)
        progress["failed"] = []
        # Seed completed list from existing threads when the progress file is
        # missing (it's deleted on clean completion).
        if not progress["completed"]:
            existing_hashes = collect_processed_meeting_ids(output_path)
            for m in meetings:
                mid = m.get("meetingId", "")
                if not mid:
                    continue
                h = hashlib.sha256(mid.encode("utf-8")).hexdigest()[:6]
                if h in existing_hashes:
                    progress["completed"].append(mid)
            if progress["completed"]:
                log.info(
                    "Auto-resume: %d meeting(s) already represented in %s",
                    len(progress["completed"]), output_path,
                )
    else:
        progress = {"completed": [], "failed": []}

    # Load existing members (accumulative)
    members = load_members(members_path)

    # Initialize API client
    client = anthropic.Anthropic()

    all_threads = []
    thread_counter = 0

    # On resume (explicit or auto), load previously generated threads so new
    # meetings get appended instead of overwriting the file.
    if (resume or auto_resume) and os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            all_threads = json.load(f)
        thread_counter = len(all_threads)
        log.info(
            "%s with %d existing threads",
            "Resumed" if resume else "Auto-resumed",
            len(all_threads),
        )

    if batch:
        try:
            new_threads, thread_counter, completed_ids = run_batch_phase(
                client, meetings, progress, members, model, date_str,
                thread_counter,
                batch_timeout_seconds=batch_timeout_seconds,
                batch_poll_seconds=batch_poll_seconds,
            )
            all_threads.extend(new_threads)
            for mid in completed_ids:
                if mid not in progress["completed"]:
                    progress["completed"].append(mid)
            save_progress(progress, progress_path)
            log.info("Batch phase: +%d threads from %d meeting(s)",
                     len(new_threads), len(completed_ids))
        except Exception as e:
            log.error("Batch phase failed: %s", e)
            # Don't mark any meetings completed on a batch-level failure —
            # they'll be retried on the next run.
            raise
    else:
        for meeting in meetings:
            meeting_id = meeting.get("meetingId", "unknown")

            if meeting_id in progress["completed"]:
                log.info("Skipping already completed: %s", meeting_id)
                continue

            log.info("Processing: %s", meeting_id)

            try:
                threads, thread_counter = process_meeting(
                    client, meeting, members, model, date_str, thread_counter,
                )
                all_threads.extend(threads)
                progress["completed"].append(meeting_id)
                save_progress(progress, progress_path)
                log.info(
                    "Completed %s — %d threads", meeting_id, len(threads),
                )
            except Exception as e:
                log.error("Failed to process %s: %s", meeting_id, e)
                progress["failed"].append(meeting_id)
                save_progress(progress, progress_path)
                continue

    # Cross-thread linking
    if all_threads:
        # Also load existing threads from other dates for cross-date linking
        existing_threads = []
        if os.path.exists(output_dir):
            for fname in os.listdir(output_dir):
                if fname.endswith(".json") and not fname.endswith(".progress.json") and fname != f"{date_str}.json":
                    with open(os.path.join(output_dir, fname), "r", encoding="utf-8") as f:
                        existing_threads.extend(json.load(f))

        link_threads(all_threads + existing_threads)

        # Only keep links for new threads (existing threads' links are not persisted back)
        # Filter out any links pointing to non-existent threads
        all_ids = {t["id"] for t in all_threads + existing_threads}
        for t in all_threads:
            if "relatedThreads" in t:
                t["relatedThreads"] = [
                    l for l in t["relatedThreads"] if l["threadId"] in all_ids
                ]
                if not t["relatedThreads"]:
                    del t["relatedThreads"]

    # Write output
    if all_threads:
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{date_str}.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_threads, f, ensure_ascii=False, indent=2)
        log.info("Wrote %d threads → %s", len(all_threads), output_path)

    # Save members
    save_members(members, members_path)
    log.info("Saved %d members → %s", len(members), members_path)

    # Clean up progress file on full completion
    if not progress["failed"]:
        if os.path.exists(progress_path):
            os.remove(progress_path)
        log.info("Pipeline complete!")
    else:
        log.warning(
            "Pipeline finished with %d failed meetings. "
            "Re-run with --resume to retry.",
            len(progress["failed"]),
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    parser = argparse.ArgumentParser(
        description="Summarize NDL speech records using Claude API"
    )
    parser.add_argument(
        "--date", default=yesterday,
        help="Date to process YYYY-MM-DD (default: yesterday)",
    )
    parser.add_argument("--meeting", default=None, help="Filter by committee name")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help="Claude model to use",
    )
    parser.add_argument("--raw-dir", default="data/raw", help="Raw data directory")
    parser.add_argument("--output-dir", default="data/threads", help="Output directory")
    parser.add_argument("--members-path", default="data/members.json", help="Members file")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--batch", action="store_true",
        help="Use Message Batches API for the summary phase (50%% cost discount, "
             "stackable with prompt caching). Polls until results are ready.",
    )
    parser.add_argument(
        "--batch-timeout", type=int, default=5400,
        help="Max seconds to wait for batch completion (default: 5400 = 90min)",
    )
    parser.add_argument(
        "--batch-poll", type=int, default=30,
        help="Seconds between batch status polls (default: 30)",
    )

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    run_pipeline(
        date_str=args.date,
        meeting_filter=args.meeting,
        model=args.model,
        raw_dir=args.raw_dir,
        output_dir=args.output_dir,
        members_path=args.members_path,
        resume=args.resume,
        dry_run=args.dry_run,
        verbose=args.verbose,
        batch=args.batch,
        batch_timeout_seconds=args.batch_timeout,
        batch_poll_seconds=args.batch_poll,
    )


if __name__ == "__main__":
    main()
