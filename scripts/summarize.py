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
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

# Add scripts/ to path for pipeline imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.grouper import (
    build_grouping_messages, build_outcome_messages, group_meeting,
    extract_meeting_outcome,
)
from pipeline.summarizer import (
    summarize_thread,
    build_summary_request,
    submit_summary_batch,
    poll_summary_batch,
    fetch_summary_results,
    parse_summary_text,
    sync_call_kwargs,
    SUMMARY_RETRY_MAX_TOKENS,
)
from pipeline.members import (MembersUnreadable, extract_member, load_members,
                              save_members)
from pipeline.linker import link_threads
from pipeline import batch_state as bs
from pipeline.jsonio import write_json_atomic

log = logging.getLogger("summarize")

# claude-sonnet-4-20250514 (Sonnet 4) retired 2026-06-15 → API 404. Migrated to
# Sonnet 5, the official drop-in replacement. Summary/grouping calls set
# thinking=disabled explicitly: Sonnet 5 turns adaptive thinking ON when the
# field is omitted (unlike Sonnet 4), which would share the max_tokens budget
# with the JSON output and risk truncation, and add nondeterminism the summary
# invariants forbid. Keep this in sync with the per-function model defaults in
# pipeline/grouper.py and pipeline/summarizer.py.
DEFAULT_MODEL = "claude-sonnet-5"

# Exit code for "nothing this date asked about produced a usable summary" (see
# systemic_failure). Kept distinct from 1 so the daily workflow can tell an
# outage from a crash and still publish the dates that did work.
EXIT_SYSTEMIC_FAILURE = 3
# Exit code for the same thing seen through too little evidence to act on
# alone: exactly one meeting was attempted, it failed, and the date already has
# threads (see suspect_failure). One of these is ordinary breakage. Several in
# one run is an outage wearing a disguise — a 30-day lookback re-visits already
# published dates every morning, so a total outage can present as nothing but
# 1-of-1 failures and never trip the systemic rule. The workflow, which is the
# only thing that sees every date, applies that threshold.
EXIT_SUSPECT_FAILURE = 4


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _annotate(level: str, message: str) -> None:
    """Emit a GitHub Actions annotation alongside the log line.

    A plain log call is invisible in a green run's summary, which is how a dead
    safety net went unnoticed for months. ``level`` is "error" or "warning" and
    should match the log level, so the two never disagree about severity.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::{level}::{message}", flush=True)


def _write_github_output(**values: list) -> None:
    """Publish date lists as step outputs, deduplicated and sorted.

    Only the resume path writes these: it handles many dates in one process, so
    its verdicts cannot ride on an exit code the way a single-date run's do.
    Deduplicated because the workflow thresholds on how many DATES reported a
    suspect verdict, and the same date listed twice would cross that on its own.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for key, dates in values.items():
            f.write(f"{key}={' '.join(sorted(set(dates)))}\n")


def _git_commit_sidecar(path: str, date_str: str) -> None:
    """Commit + push just the sidecar (CI only) so the in-flight batch survives
    a later kill or set -e failure before the run's final commit.

    The three git steps fail for genuinely different reasons, so they are
    reported separately rather than as one "commit failed":

    * ``add``/``commit`` failing means the net is not armed at all. The batch is
      already submitted and paid for, so if the job is then killed its results
      are unrecoverable — ``::error::``, matching the severity the stale-schema
      guard in collect_pending_batches already uses for an operator-must-act
      condition.
    * ``push`` failing (typically losing a race to a concurrent push) leaves the
      sidecar committed locally, and the workflow's final step pushes on "branch
      is ahead" rather than on "we just committed", so that commit still leaves
      the runner. Warning, not error.

    Deliberately non-fatal in every branch. The batch is already submitted, and
    the overwhelmingly likely outcome is that this same run polls it to
    completion and writes the threads; aborting here to force a red run would
    turn "the net is not armed" into a guaranteed loss of a day that would
    otherwise have succeeded. The ``::error::`` annotation is what makes it
    impossible to miss in an otherwise-green run.
    """
    try:
        subprocess.run(["git", "add", path], check=True)
    except subprocess.CalledProcessError as e:
        _report_dead_net("stage", date_str, e)
        return

    try:
        subprocess.run(
            ["git", "commit", "-m", f"chore(pipeline): persist pending batch {date_str}"],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        _report_dead_net("commit", date_str, e)
        return

    try:
        subprocess.run(["git", "push"], check=True)
    except subprocess.CalledProcessError as e:
        log.warning("Early sidecar push failed for %s (%s) — committed locally, "
                    "relying on the final push", date_str, e)
        _annotate("warning", f"Early sidecar push failed for {date_str} (committed locally)")
        return

    log.info("Early-committed sidecar %s", path)


def _report_dead_net(step: str, date_str: str, e: Exception) -> None:
    log.error("Early sidecar %s FAILED for %s (%s) — the in-flight batch is NOT "
              "protected against this job being killed", step, date_str, e)
    _annotate(
        "error",
        f"Early sidecar {step} failed for {date_str}: the submitted summary "
        f"batch will be orphaned if this job dies before the final commit",
    )


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------

def load_progress(progress_path: str) -> dict:
    """Load progress file for resumability.

    An unreadable one is treated as a missing one (#74). That is the cheapest
    answer available anywhere in this file, and it is cheap for a reason the
    other readers do not have: ``*.progress.json`` is gitignored, so it is
    local bookkeeping and never the only copy of anything — ``auto_resume``
    re-derives ``completed`` from the date's actual threads via
    ``collect_processed_meeting_ids`` whenever it comes back empty. The cost of
    guessing wrong is at most re-summarising meetings that are already
    published, which the reseed then prevents anyway.

    What it must not do is what it used to: raise, which is exit 1, which under
    the workflow's ``set -e`` date loop skips commit and push — the whole
    morning's publish lost to a scratch file.
    """
    if os.path.exists(progress_path):
        try:
            with open(progress_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # Both keys, and both lists. Checking only `isinstance(dict)` was
            # the same mistake one level in: every caller does
            # `progress["completed"].append(...)` unguarded, so `{}` raises
            # KeyError and `{"completed": null}` raises AttributeError — both
            # exit 1, which is what this guard exists to remove.
            if not isinstance(loaded, dict):
                raise TypeError(f"expected an object, found "
                                f"{type(loaded).__name__}")
            for key in ("completed", "failed"):
                if not isinstance(loaded.get(key), list):
                    raise TypeError(
                        f"expected {key!r} to be a list, found "
                        f"{type(loaded.get(key)).__name__}")
            return loaded
        except _RAW_READ_ERRORS as exc:
            log.warning("Progress file %s is unreadable (%s) — starting this "
                        "date from what its threads file already holds",
                        progress_path, exc)
    return {"completed": [], "failed": []}


def new_api_stats() -> dict:
    """Per-run counters for meetings that actually reached the API.

    A meeting counts as ``failed`` when it reached the API and came back with
    nothing usable — whether that surfaced as a raised exception (grouping or
    outcome) or as error entries in a batch's results (summary). Counting only
    exceptions was the first version of this signal and it was blind to the
    summary phase, which is both the bulk of the spend and where the #47/#51
    regression lived.
    """
    return {"attempted": 0, "failed": 0}


def systemic_failure(api_stats: dict, published_threads: int) -> bool:
    """True when every meeting this date asked about came back with nothing usable.

    "Nothing usable" is wider than "the API said no": a summary that parses but
    cannot be assembled into a thread is counted too, because the outcome that
    matters is a speech that never reaches the site. Say that, not "rejected",
    wherever this verdict is reported — sending an operator to hunt an API
    contract change that did not happen costs a morning.

    This is the difference between a quiet Diet day and an outage, and nothing
    else in the pipeline can tell them apart. On 2026-08-05 every grouping,
    outcome and summary request was rejected with a 400 (#51) and the Summarize
    step still exited 0 — the job only went red because a *second*, unrelated
    bug crashed the validate step (#52). Fixing that bug removed the only thing
    making the outage visible, so the signal has to come from here.

    ``published_threads`` is how many threads the date ends up with, existing
    ones included, and it buys exactly one thing: **a single failed meeting is
    not enough evidence to call a date that already published a failure.**
    ``attempted`` only counts meetings this run tried, and auto-resume seeds
    ``progress["completed"]`` from the committed threads file, so a date whose
    other four meetings succeeded *yesterday* arrives here as ``attempted=1``.
    Without the ``attempted == 1`` carve-out, one stubborn late-added meeting on
    an already-published date reads as "every meeting failed" and reds the job
    every morning — ordinary per-meeting breakage, which is what this must not
    fire on. 34% of dates in ``data/raw/`` hold a single meeting, so this is not
    a corner.

    The carve-out is deliberately limited to ``attempted == 1``. Suppressing on
    published threads generally would be a fail-open hole in its own right: a
    real outage that happens to land on dates with prior output would go
    unreported forever, which is the very failure this exists to end. Two or
    more meetings failing together is evidence about the layer, not about a
    meeting, whatever is already on disk.

    It does NOT fire when:
      * nothing was attempted (every meeting already summarized, or procedural)
      * some meetings failed and others succeeded this run
      * exactly one meeting was attempted, it failed, and the date already has
        threads — ``suspect_failure`` picks that case up instead, so the
        evidence is kept rather than discarded.

    It DOES fire when a date's only askable meeting fails and the date is left
    empty. That is the accepted false-positive shape: a red run costs a GitHub
    issue and blocks nothing (the workflow tolerates this exit code per date and
    publishes first), while the opposite error costs weeks of silence, which is
    this pipeline's documented history.
    """
    if not _everything_asked_for_failed(api_stats):
        return False
    return api_stats["attempted"] >= 2 or published_threads == 0


def suspect_failure(api_stats: dict, published_threads: int) -> bool:
    """The one shape ``systemic_failure`` deliberately lets past, kept not dropped.

    Exactly one meeting attempted, it failed, and the date already has threads.
    On its own that is ordinary per-meeting breakage and must not red the run —
    55% of the dates currently published hold a single meeting (78 of 142,
    counting distinct house+committee pairs in data/threads/) and the 30-day
    lookback re-visits published dates every morning, so failing on it means
    failing daily.

    But a *total* outage can present as nothing else. NDL adds one late meeting
    each to three already-published dates; the API is down; every date reports
    1-of-1 failed and the systemic rule stays silent on all three. Answering
    "no" and forgetting is what let that run go green. So this is reported as
    its own exit code and the **workflow** — the only layer that sees every date
    in the run — decides: one is noise, several at once is the outage.
    """
    if not _everything_asked_for_failed(api_stats):
        return False
    return api_stats["attempted"] == 1 and published_threads > 0


def _everything_asked_for_failed(api_stats: dict) -> bool:
    return (api_stats["attempted"] > 0
            and api_stats["failed"] == api_stats["attempted"])


def rejection_verdict(api_stats: dict, published_threads: int) -> int:
    """Trigger 1 as an exit code: the API answered nothing usable.

    A thin wrapper over the two existing predicates so callers can combine this
    with trigger 2 below without re-deriving the ranking. Deliberately does not
    re-implement the boundaries: they carry hard-won carve-outs (see
    ``systemic_failure``) and a second copy would drift from them.
    """
    if systemic_failure(api_stats, published_threads):
        return EXIT_SYSTEMIC_FAILURE
    if suspect_failure(api_stats, published_threads):
        return EXIT_SUSPECT_FAILURE
    return 0


def publication_blocked_verdict(summary_attempted: int, published_threads: int) -> int:
    """Trigger 2: summary requests went out, and the date published nothing.

    Assembly is all-or-nothing — one bad speechOrder discards the whole date —
    so this is a fact about the DATE, not about the meetings it swept up. That
    is why it takes a count and not an ``api_stats``: charging the meetings
    would report meetings that were never even examined as having failed, and
    the cause diagnosis would be fiction. The cause travels separately, as the
    diagnostic ``assemble_from_manifest`` returns.

    ``summary_attempted`` is NOT ``api_stats["attempted"]``. That one counts
    every meeting that reached the API at all, including one whose grouping
    legitimately produced zero threads and therefore sent no summary request.
    Using it here lets a real outage hide behind a quiet meeting: with A quiet
    and B blocked, "everything asked for failed" is false and nothing fires.

    Same evidence rule as trigger 1: one meeting blocked on an already-published
    date is weak evidence and is kept as ``suspect`` for the workflow to
    threshold; anything else is systemic.
    """
    if summary_attempted <= 0:
        return 0
    if summary_attempted == 1 and published_threads > 0:
        return EXIT_SUSPECT_FAILURE
    return EXIT_SYSTEMIC_FAILURE


def worst_verdict(*verdicts: int) -> int:
    """The loudest verdict among several. systemic > suspect > clean.

    The two triggers are NOT exclusive: a fully rejected batch fires trigger 1
    (nothing usable came back) AND trigger 2 (assembly then failed on the very
    same missing results). Both are true and both get reported; this only picks
    the exit code.
    """
    if EXIT_SYSTEMIC_FAILURE in verdicts:
        return EXIT_SYSTEMIC_FAILURE
    if EXIT_SUSPECT_FAILURE in verdicts:
        return EXIT_SUSPECT_FAILURE
    return 0


def save_progress(progress: dict, progress_path: str) -> None:
    """Save progress file."""
    write_json_atomic(progress_path, progress)


# ---------------------------------------------------------------------------
# lex-diff cross-linking
# ---------------------------------------------------------------------------

_LEXDIFF_MAP: Optional[Dict[str, dict]] = None

# Module-level so a test can point it somewhere else. The alternative was
# monkeypatching os.path.join for the duration of a test, which replaces a
# stdlib function globally with a non-equivalent one — a passing test today and
# an order-dependent failure later.
_LEXDIFF_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "lexdiff-mapping.json")

def _get_lexdiff_map() -> Dict[str, dict]:
    """Return the lex-diff mapping, loading lazily."""
    global _LEXDIFF_MAP
    if _LEXDIFF_MAP is None:
        mapping_path = _LEXDIFF_PATH
        _LEXDIFF_MAP = {}
        if os.path.exists(mapping_path):
            try:
                with open(mapping_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise TypeError(f"expected an object, found "
                                    f"{type(loaded).__name__}")
                loaded.pop("_comment", None)
                _LEXDIFF_MAP = loaded
            except _RAW_READ_ERRORS as exc:
                # Outbound law cross-links are auxiliary (CLAUDE.md), so their
                # absence costs some links on a page that otherwise publishes
                # normally. Raising costs the entire morning's publish: this is
                # read while summarising, so exit 1 under `set -e` takes the
                # date loop with it (#74). The mapping is committed, so a
                # broken one is loud enough on its own.
                log.warning("lex-diff mapping %s is unreadable (%s) — "
                            "publishing without outbound law cross-links",
                            mapping_path, exc)
                # Annotated, not just logged, and the reason is the same one
                # stated at the partial-raw branch: this run is GREEN, and a
                # log line is invisible in a green run's summary. The loss is
                # also permanent for these threads — links are baked in at
                # creation and never back-filled — so "someone will notice"
                # is not a plan. Once per process: the map is cached.
                _annotate(
                    "warning",
                    f"lex-diff mapping {mapping_path} is unreadable "
                    f"({exc.__class__.__name__}: {exc}) — every thread written "
                    f"this run publishes WITHOUT outbound law cross-links, and "
                    f"they are not back-filled later. The file is committed, so "
                    f"`git checkout` recovers it; re-run the affected dates to "
                    f"restore their links")
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
    summary_stats: Optional[dict] = None,
    outcome_stats: Optional[dict] = None,
) -> tuple:
    """Process a single meeting through grouping + summarization + outcome.

    Returns (threads_list, updated_thread_counter).

    ``summary_stats`` is an optional out-parameter (``{"attempted", "failed"}``)
    counting this meeting's *summary* requests. Without it the caller cannot
    tell "grouping found nothing to summarize" from "every summary request was
    rejected": both return an empty thread list and neither raises, because the
    per-thread ``except`` below has always swallowed summary failures. That gap
    let a total summary-layer outage on the synchronous path — the path an
    operator's manual recovery run uses — finish green with zero threads, the
    exact 2026-08-05 shape. See ``systemic_failure``.
    """
    if summary_stats is None:
        summary_stats = new_api_stats()
    meeting_id = meeting.get("meetingId", "unknown")
    speeches = meeting.get("speeches", [])
    raw_lookup = build_speech_lookup(speeches)

    # Phase B: Topic grouping
    thread_infos = group_meeting(client, meeting, model=model)
    time.sleep(1)

    # Phase D: Meeting-level outcome (votes, resolutions)
    meeting_outcome = extract_meeting_outcome(client, meeting, model=model,
                                              outcome_stats=outcome_stats)
    time.sleep(1)

    threads = []
    for thread_info in thread_infos:
        thread_counter += 1
        thread_id = make_thread_id(date_str, meeting_id, thread_counter)

        # Gather raw speeches for this thread
        orders = thread_info.get("speechOrders", [])
        thread_speeches = [raw_lookup[o] for o in orders if o in raw_lookup]

        if not thread_speeches:
            # Grouping named speechOrders that are not in the raw record. No
            # summary request goes out, so counting this as "nothing attempted"
            # would file a meeting that produced zero threads as completed and
            # drop it forever, while still paying for grouping and outcome every
            # run. It is a failed answer, not an absent question.
            log.error("Thread '%s' in %s names no speech that exists in raw",
                      thread_info.get("topic"), meeting_id)
            summary_stats["attempted"] += 1
            summary_stats["failed"] += 1
            continue

        # Phase C: Summarize speeches in this thread
        summary_stats["attempted"] += 1
        try:
            summary_result = summarize_thread(
                client, meeting, thread_info, thread_speeches, model=model,
            )
            ai_speeches = summary_result["speeches"]
            commitments = summary_result["commitments"]
            time.sleep(1)
        except Exception as e:
            log.error("Failed to summarize thread '%s': %s", thread_info.get("topic"), e)
            summary_stats["failed"] += 1
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
        else:
            # A summary that came back fine but could not be assembled is still
            # a thread that will never exist. Counting only the raised exception
            # left this branch silent: the meeting was filed as completed with
            # zero threads and auto-resume never looked at it again — a
            # permanent hole in the published record, which is the same class of
            # loss the exit code exists to surface.
            log.error("Could not assemble thread '%s' in %s",
                      thread_info.get("topic"), meeting_id)
            summary_stats["failed"] += 1

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


def _diagnostic(reason: str, meeting_id: Optional[str] = None,
                custom_id: Optional[str] = None) -> dict:
    """One structured observation of why assembly stopped.

    Observation only, never a diagnosis. ``missing_result`` in particular is
    NOT evidence the API rejected anything: a result also goes missing on a
    fetch/parse/custom_id-mapping defect of ours. Naming a cause here would send
    the reader hunting a 400 that may never have happened.

    ``scope`` says which of the three levels the observation is about, so an
    annotation never points at a thread that was not examined. date-scope
    observations are raised by the caller, not by assembly.
    """
    scope = "thread" if custom_id else ("meeting" if meeting_id else "date")
    return {"scope": scope, "meeting_id": meeting_id,
            "custom_id": custom_id, "reason": reason}


def verify_manifest_against_raw(sidecar: dict,
                                meetings_by_id: Dict[str, dict]) -> Optional[dict]:
    """The free half of assembly: does the manifest still describe requests we
    can rebuild from today's raw? Returns the first problem as a ``_diagnostic``,
    or None if every thread verifies.

    Hoisted out of assemble_from_manifest so the caller can run it BEFORE
    fetching the batch's results. That ordering is not a micro-optimisation, it
    is what makes "never resubmit a doomed batch" hold over time: results expire
    ~29 days after submission, and once ``fetch_summary_results`` raises first,
    the very same broken sidecar reports ``results_expired`` — a retryable
    reason — instead of ``hash_mismatch``. See #65.

    Costs nothing but CPU: no network, no tokens. Uses build_summary_request,
    the one summary-request builder, so the hash it computes is the hash a real
    resume would compute (CLAUDE.md "Summary Layer Invariants" #2).
    """
    model = sidecar["model"]
    for m in sidecar["meetings"]:
        meeting_id = m["meeting_id"]
        meeting = meetings_by_id.get(meeting_id)
        if meeting is None:
            log.error("Resume: raw missing for %s — cannot assemble", meeting_id)
            return _diagnostic("raw_missing", meeting_id)
        raw_lookup = build_speech_lookup(meeting.get("speeches", []))
        for mt in m["threads"]:
            custom_id = mt["custom_id"]
            orders = mt["speechOrders"]
            thread_speeches = [raw_lookup[o] for o in orders if o in raw_lookup]
            if len(thread_speeches) != len(orders):
                log.error("Resume: speechOrder gap in %s/%s", meeting_id, custom_id)
                return _diagnostic("speech_gap", meeting_id, custom_id)
            request = build_summary_request(
                meeting, mt["thread_info"], thread_speeches, custom_id, model,
            )
            if bs.compute_input_hash(request["params"]) != mt["input_hash"]:
                log.error("Resume: input_hash mismatch for %s — raw/prompt changed",
                          custom_id)
                return _diagnostic("hash_mismatch", meeting_id, custom_id)
    return None


def assemble_from_manifest(
    sidecar: dict,
    meetings_by_id: Dict[str, dict],
    results: Dict[str, Optional[dict]],
    members: Dict[str, dict],
    thread_counter: int,
) -> tuple:
    """Assemble threads from a sidecar manifest + a completed batch's results.

    Does NOT re-group. Verifies each thread's input_hash against re-fetched raw
    and requires every custom_id to have a parsed result. Returns
    ``(threads, ok, diagnostic)`` where ok is False if ANY thread fails
    verification or is missing — in that case the caller keeps the sidecar for
    retry, and diagnostic is a structured observation of why (None on success).
    """
    date_str = sidecar["date"]
    threads: list = []

    # Verification first, for the whole manifest. The caller has normally run
    # this already (before fetching results — see verify_manifest_against_raw);
    # repeating it costs a few milliseconds of hashing and keeps this function
    # correct when called directly, e.g. from tests.
    #
    # NOTE this changes which problem is reported when a manifest has more than
    # one: a verification failure on thread 2 now wins over a missing result on
    # thread 1. That is deliberate — the deterministic problem is the one an
    # operator must act on, and reporting the retryable one first is what sent
    # 2026-06-16's investigation to the wrong place.
    verify_diag = verify_manifest_against_raw(sidecar, meetings_by_id)
    if verify_diag is not None:
        return [], False, verify_diag

    for m in sidecar["meetings"]:
        meeting_id = m["meeting_id"]
        meeting = meetings_by_id[meeting_id]      # verified present above
        raw_lookup = build_speech_lookup(meeting.get("speeches", []))
        outcome = m["outcome"]
        manifest_threads = m["threads"]

        for mt in manifest_threads:
            custom_id = mt["custom_id"]
            thread_info = mt["thread_info"]

            result = results.get(custom_id)
            if not result:
                log.error("Resume: missing result for %s", custom_id)
                return [], False, _diagnostic("missing_result", meeting_id, custom_id)

            thread_counter += 1
            thread_id = make_thread_id(date_str, meeting_id, thread_counter)
            thread = assemble_thread(
                meeting, thread_info, result["speeches"], raw_lookup, members,
                thread_id,
            )
            if not thread:
                log.error("Resume: assemble_thread returned None for %s", custom_id)
                return [], False, _diagnostic("thread_build_failed", meeting_id, custom_id)

            is_last = (mt is manifest_threads[-1])
            thread["outcome"] = {
                "result": outcome.get("result") if is_last else None,
                "resolution": outcome.get("resolution") if is_last else None,
                "commitments": result["commitments"] or [],
                "status": outcome.get("status", "ongoing"),
            }
            threads.append(thread)

    return threads, True, None


def _raw_candidates_for_date(date_str: str, raw_dir: str) -> list:
    """Every raw file that may hold this date's meetings, in read order.

    One list, three readers: the loader below, the "which file is broken"
    reporter (_raw_read_failure_detail), and run_pipeline's first-run scan. Two
    copies would drift, and the consequence is specifically bad — the reporter
    would name a file that is fine, or none at all, for a failure the loader did
    hit, and resume would assemble from raw the first run never saw.
    """
    import glob as _glob
    return [
        os.path.join(raw_dir, f"ndl-{date_str}.json"),
        os.path.join(raw_dir, f"kantei-{date_str}.json"),
        os.path.join(raw_dir, f"council-{date_str}.json"),
        *sorted(_glob.glob(os.path.join(raw_dir, f"council-*-{date_str}.json"))),
        os.path.join(raw_dir, f"{date_str}.json"),
    ]


class RawUnreadable(ValueError):
    """A specific raw file could not be read, and this says WHICH one.

    Subclasses ValueError so it stays inside ``_RAW_READ_ERRORS`` — every
    existing guard (the abandon gate included) keeps catching it unchanged, and
    in particular the abandon gate keeps failing closed on it.

    It exists because the alternative was reconstructing the answer afterwards:
    re-read each candidate and see which one breaks. That is wrong in two ways
    that both mislead the operator — a transient I/O error that does not
    reproduce makes the second pass report "parses fine, wrong shape", and a
    file that is valid JSON of the wrong shape is not caught by a re-parse at
    all, so no filename is named. Carrying the path out of the failure itself
    cannot drift from it.
    """

    def __init__(self, path: str, cause: BaseException):
        super().__init__(f"{path} ({cause.__class__.__name__}: {cause})")
        self.path = path
        self.cause = cause


def _load_meetings_for_date(date_str: str, raw_dir: str) -> Dict[str, dict]:
    """Re-load all meetings for a date from raw files, keyed by meetingId.

    Mirrors run_pipeline's candidate-file scan so resume sees the same raw.

    Raises ``RawUnreadable`` naming the offending file. Callers decide the
    policy (#72 holds the date); this only makes the failure legible.
    """
    by_id: Dict[str, dict] = {}
    for c in _raw_candidates_for_date(date_str, raw_dir):
        if os.path.exists(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for m in data.get("meetings", []):
                    by_id[m.get("meetingId", "unknown")] = m
            except RawUnreadable:
                raise
            except _RAW_READ_ERRORS as exc:
                # Covers the shape failures too, not just the parse: `data` may
                # be a valid JSON scalar with no .get, and `meetings` may be a
                # string. Those are the cases a re-parse cannot find.
                raise RawUnreadable(c, exc) from exc
    return by_id


def _raw_files_for_date(date_str: str, raw_dir: str) -> list:
    """The candidate raw files that actually exist for this date."""
    return [c for c in _raw_candidates_for_date(date_str, raw_dir) if os.path.exists(c)]


# How many filenames an aggregated annotation names before saying "and N more".
# GitHub truncates a long annotation from the END, so an uncapped list buries
# the actionable part; ten is enough to see a pattern and short enough to read.
_MAX_NAMED_FILES = 10


def _as_list_of_dicts(value, what: str) -> list:
    """``value`` if it is a list of dicts, else raise ``TypeError``.

    A truncated file fails to parse; a hand-edited or half-migrated one parses
    into the wrong shape, and THAT is the case the guards around this kept
    missing. ``json.load`` succeeding says nothing about shape, and the two
    wrong shapes fail late and quietly rather than here: ``[].extend("none")``
    appends four characters, and ``list(some_dict)`` appends its KEYS — either
    way the AttributeError lands in ``link_threads`` or the grouper, far from
    the file that caused it and outside every guard.

    Raising ``TypeError`` is what makes this composable: it is already in
    ``_RAW_READ_ERRORS``, so every reader's existing ``except`` treats a shape
    failure exactly like a parse failure — which is the answer they want, since
    neither one tells you what the file was supposed to hold. The resume path's
    ``_load_meetings_for_date`` gets the same coverage incidentally (its
    ``m.get`` raises AttributeError on a non-dict); this makes it deliberate on
    the paths that had no such accident.
    """
    if not isinstance(value, list):
        raise TypeError(f"expected {what} to be a list, found "
                        f"{type(value).__name__}")
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise TypeError(f"expected {what}[{i}] to be an object, found "
                            f"{type(item).__name__}")
    return value


def _as_thread_list(value, what: str) -> list:
    """``_as_list_of_dicts`` plus the one field every thread consumer assumes.

    Stopping at "list of dicts" left the guard one level too shallow, and the
    gap was reachable: ``[{"topic": "T"}]`` — a half-finished field rename, the
    same threat model as the rest of this — sails through, and then
    ``{t["id"] for t in ...}`` raises ``KeyError``, which is NOT in
    ``_RAW_READ_ERRORS`` and so is exit 1 and no publish. A non-string ``id``
    reaches ``tid.split("_")`` the same way.

    ``id`` earns the check where other fields do not: it is what identifies a
    thread to every consumer (linking, dedup, the site), so a thread without a
    usable one is not a thread, and there is nothing to fall back to. Deeper
    field validation is deliberately NOT here — that is `validate-data.mjs`'s
    job and a full schema check on every read would be a different feature. A
    file that is a list of id-bearing objects with wrongly-shaped *inner*
    fields can still fail downstream (#76).
    """
    for i, item in enumerate(_as_list_of_dicts(value, what)):
        if not isinstance(item.get("id"), str) or not item["id"]:
            raise TypeError(f"expected {what}[{i}] to carry a non-empty string "
                            f"'id', found {item.get('id')!r}")
    return value


def load_raw_meetings_for_date(date_str: str, raw_dir: str) -> tuple:
    """``(meetings, unreadable_files)`` for the first-run path (#74).

    Skips a file it cannot read instead of raising, and reports which. Raising
    here aborts summarize.py with a traceback, which is exit 1 — and under the
    workflow's ``set -e`` date loop that skips commit and push, so ONE broken
    file on ONE date silences the whole morning's publish (#52).

    Skipping is safe here in a way it is not for the resume path: ``data/raw`` is
    gitignored and re-fetched on every run, and the 30-day lookback re-visits the
    date, so a file that is broken today is normally back tomorrow and the
    meetings it held are summarized then. What must not happen is that the loss
    passes unmentioned — hence the second return value, which the caller turns
    into an annotation.

    Deliberately NOT shared with the resume path's ``_load_meetings_for_date``,
    which raises ``RawUnreadable`` instead. There, unreadable-vs-absent decides
    whether a batch is written off as unrecoverable (#69/#72), so a skip that
    silently produced "no meetings" would authorize a delete it never proved.
    Same files, opposite correct answers.
    """
    meetings: list = []
    unreadable: list = []
    for candidate in _raw_files_for_date(date_str, raw_dir):
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            found = _as_list_of_dicts(raw_data.get("meetings", []),
                                      f"{os.path.basename(candidate)}:meetings")
            meetings.extend(found)
        except _RAW_READ_ERRORS as exc:
            # warning, not error: this function skips, it does not decide. How
            # bad a skipped file is depends on what the date has LEFT, which
            # only the caller knows — and _annotate's rule is that the log level
            # and the annotation level must not disagree.
            log.warning("Raw file %s is unreadable (%s) — skipping it",
                        candidate, exc.__class__.__name__)
            unreadable.append(f"{candidate} ({exc.__class__.__name__}: {exc})")
            continue
        log.info("Loaded %d meetings from %s", len(found), candidate)
    return meetings, unreadable


def load_threads_from_other_dates(output_dir: str, date_str: str) -> list:
    """Every other date's threads, for cross-date linking (#74).

    The widest blast radius of the readers this run touches: it walks EVERY
    date's file, so one unparseable file used to fail the date being summarized —
    and under `set -e`, every date after it. It is also the most disposable
    thing being read: cross-date links are auxiliary (CLAUDE.md), and the link
    comes back as soon as the file is fixed. Warn and carry on.

    What the warning must NOT say is that the file is somebody else's problem.
    It is only re-summarised if its date is still inside the lookback window
    (the workflow builds its date list from ``data/raw/``), and ``src/lib/data.ts``
    is deliberately fatal on the same file — so an old broken file is reported
    HERE and nowhere else, while already breaking the site build. Losing the
    links is the small half of what that file costs.
    """
    threads: list = []
    skipped: list = []
    if not os.path.exists(output_dir):
        return threads
    for fname in sorted(os.listdir(output_dir)):
        if not _is_thread_file(fname) or fname == f"{date_str}.json":
            continue
        path = os.path.join(output_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                threads.extend(_as_thread_list(json.load(f), path))
        except _RAW_READ_ERRORS as exc:
            log.warning("Skipping %s for cross-date linking (%s)", path, exc)
            skipped.append(f"{path} ({exc.__class__.__name__}: {exc})")
    if skipped:
        # One annotation per date, not one per file, and capped. Summarize runs
        # one PROCESS per date, so this cannot dedupe across dates — a single
        # old broken file still costs one annotation per date, against GitHub's
        # 10-per-level-per-step limit. Aggregating and capping is what keeps a
        # directory of broken files from pushing every other warning off the
        # page, including the partial-raw-loss one that means actual data is
        # missing. Filenames come FIRST for the same reason: a long message is
        # truncated from the end, and the filenames are the actionable half.
        shown = "; ".join(skipped[:_MAX_NAMED_FILES])
        if len(skipped) > _MAX_NAMED_FILES:
            shown += f"; and {len(skipped) - _MAX_NAMED_FILES} more"
        _annotate(
            "warning",
            f"{date_str}: {len(skipped)} unreadable threads file(s) [{shown}] — "
            f"this date's threads have no cross-date links to them. The links "
            f"are the SMALL loss: the site build (src/lib/data.ts) treats the "
            f"same file as fatal, and the date owning it is only re-summarised "
            f"while it is inside the lookback window, so fix the file rather "
            f"than waiting for its own run to report it")
    return threads


def _is_thread_file(fname: str) -> bool:
    """A published per-date threads file, not a sidecar of one.

    ``.progress.json`` is resume bookkeeping that happens to live in the same
    directory; treating it as threads yields a dict where a list is expected.
    """
    return fname.endswith(".json") and not fname.endswith(".progress.json")


class ThreadsFileUnreadable(Exception):
    """The date's existing threads file is on disk but could not be read.

    Carries the path because that is the only actionable part — see
    RawUnreadable, same reasoning.
    """

    def __init__(self, path: str, cause: BaseException):
        super().__init__(f"{path} ({cause.__class__.__name__}: {cause})")
        self.path = path
        self.cause = cause


def _append_threads_to_date_file(threads: list, threads_dir: str, date_str: str) -> None:
    """Append to the date's threads file, or raise ThreadsFileUnreadable.

    Reading first is not an optimization: this file is the only copy of the
    date's published work, so appending to `[]` when the read fails republishes
    the date without whatever it still held. The caller turns the raise into a
    HOLD, which keeps the sidecar and lets a later run re-collect the very same
    results (#74).
    """
    os.makedirs(threads_dir, exist_ok=True)
    path = os.path.join(threads_dir, f"{date_str}.json")
    existing = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                # Shape inside the try, so a dict or a scalar gets the SAME
                # answer as a parse failure: either way we cannot tell what is
                # already published, and appending regardless would either raise
                # or (for a dict) silently publish its KEYS as threads.
                existing = _as_thread_list(json.load(f), path)
        except _RAW_READ_ERRORS as exc:
            raise ThreadsFileUnreadable(path, exc) from exc
    existing.extend(threads)
    write_json_atomic(path, existing)


def _rebuild_requests_from_manifest(sidecar: dict, meetings_by_id: Dict[str, dict],
                                    model: str) -> Optional[list]:
    """Rebuild batch requests from a sidecar manifest for resubmission.

    Reuses the persisted custom_ids and thread_info (NO re-grouping). Returns
    None if raw is missing or a speechOrder gap prevents a faithful rebuild.

    This function does NOT check that the rebuilt request's input_hash still
    matches the manifest's stored hash — it only detects a missing meeting or a
    speechOrder gap. Callers that need "the resubmitted batch's results still
    match the manifest's input_hash" must run ``verify_manifest_against_raw``
    themselves before calling this (as ``_apply_failure_policy`` does): that is
    what actually confirms the raw/prompt has not changed since submission.
    """
    requests: list = []
    for m in sidecar["meetings"]:
        meeting = meetings_by_id.get(m["meeting_id"])
        if meeting is None:
            return None
        raw_lookup = build_speech_lookup(meeting.get("speeches", []))
        for mt in m["threads"]:
            orders = mt["speechOrders"]
            thread_speeches = [raw_lookup[o] for o in orders if o in raw_lookup]
            if len(thread_speeches) != len(orders):
                return None
            requests.append(build_summary_request(
                meeting, mt["thread_info"], thread_speeches, mt["custom_id"], model,
            ))
    return requests


def usable_result(val: Optional[dict]) -> bool:
    """Whether a parsed batch result can actually become a thread.

    A result that parsed but carries no speeches is just as unusable as a
    missing one: assembly calls it truthy, ``assemble_thread`` then returns
    None, and the date takes the full-resubmit path that fails identically
    every run. Module level rather than nested in ``_repair_unusable_results``
    because the systemic-failure counter has to ask the same question, and two
    spellings of "unusable" would eventually disagree — the same reason
    ``askable_request_kinds`` delegates to the request builders themselves.
    """
    return bool(val) and bool(val.get("speeches"))


# Above this many unusable results, the failure is systemic (bad prompt, bad
# model, quota) rather than a handful of oversized threads — fall through to the
# existing resubmit path instead of hammering the sync API.
REPAIR_LIMIT = 10
# Pacing between synchronous re-issues, matching the other synchronous loops in
# this file (prepare_meeting_for_batch, process_meeting).
REPAIR_PACING_SECONDS = 1
# Wall-clock allowance for one date's re-issues. Deliberately its OWN budget and
# not the caller's poll deadline: polling is time spent *waiting*, and by the time
# repair runs the batch has already ended and its results are in hand, so a spent
# poll budget must not disable recovery. Bounded anyway, because the CI job has a
# hard timeout and being killed mid-repair wastes everything spent so far.
REPAIR_BUDGET_SECONDS = 900
# Transient failures whose right answer is "try again next run for free". Letting
# them read as a repair failure is actively expensive: assembly then fails, which
# resubmits the whole batch and spends one of the three retry slots that stand
# between a stuck date and permanent loss. Deliberately narrower than the
# APIError test used for the results fetch below — a 4xx is deterministic, and
# aborting the run on one would skip every later sidecar.
TRANSIENT_API_ERRORS = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,     # includes APITimeoutError
    anthropic.InternalServerError,    # 500 / 529 overloaded
)


def _repair_unusable_results(
    client,
    sidecar: dict,
    meetings_by_id: Dict[str, dict],
    results: Dict[str, Optional[dict]],
    model: str,
    budget_seconds: int = REPAIR_BUDGET_SECONDS,
) -> int:
    """Re-issue the requests whose batch output was unusable, synchronously.

    Assembly is all-or-nothing, so without this one bad result out of N throws
    away every good one and resubmits the whole batch — which costs a second
    batch and, for a response that was simply too long, fails the same way. The
    re-issue uses a larger ceiling; ``max_tokens`` is outside the input hash, so
    the repaired result still verifies against the stored manifest.

    ``budget_seconds`` gates whether a *new* re-issue is started, so the real
    worst case is the budget plus one in-flight call. That only bounds anything
    because the client is taken with ``max_retries=0``: at the SDK default of 2,
    one stalled call could burn three read timeouts and take the CI job down with
    it. Deferring a transient failure to the next run is this path's policy anyway
    (see TRANSIENT_API_ERRORS), so in-SDK retries buy nothing here.
    Stopping mid-way wastes what was already spent (assembly is all-or-nothing),
    so it is logged as an error, not shrugged off.

    Mutates ``results`` in place. Returns the number of entries repaired.
    """
    # Index the manifest BEFORE consulting results: which custom_ids exist is a
    # property of the manifest, not of whichever raw happens to be loaded. Doing
    # it the other way round made a missing meeting log "not in the manifest",
    # blaming the wrong thing — the same misdiagnosis that cost 2026-06-16.
    manifest_order: list = []
    by_custom_id = {}
    for m in sidecar["meetings"]:
        meeting = meetings_by_id.get(m["meeting_id"])
        raw_lookup = (build_speech_lookup(meeting.get("speeches", []))
                      if meeting is not None else None)
        for mt in m["threads"]:
            manifest_order.append(mt["custom_id"])
            by_custom_id[mt["custom_id"]] = (m["meeting_id"], meeting, mt, raw_lookup)

    unusable = [cid for cid in manifest_order if not usable_result(results.get(cid))]
    if not unusable:
        return 0
    if len(unusable) > REPAIR_LIMIT:
        log.error(
            "Repair: %d/%d results unusable for %s — above repair limit (%d), "
            "treating as a systemic failure",
            len(unusable), len(manifest_order), sidecar["date"], REPAIR_LIMIT,
        )
        return 0

    repaired = 0
    deadline = time.time() + budget_seconds
    repair_client = client.with_options(max_retries=0)
    for idx, custom_id in enumerate(unusable):
        if time.time() >= deadline:
            log.error(
                "Repair: out of budget (%ds) after %d/%d re-issue(s) for %s — "
                "the spend so far is wasted, assembly needs all of them",
                budget_seconds, idx, len(unusable), sidecar["date"],
            )
            break
        meeting_id, meeting, mt, raw_lookup = by_custom_id[custom_id]
        if meeting is None:
            log.error("Repair: raw missing for %s (%s) — cannot re-issue",
                      meeting_id, custom_id)
            continue
        orders = mt["speechOrders"]
        speeches = [raw_lookup[o] for o in orders if o in raw_lookup]
        if len(speeches) != len(orders):
            log.error("Repair: speechOrder gap for %s — cannot re-issue", custom_id)
            continue

        request = build_summary_request(
            meeting, mt["thread_info"], speeches, custom_id, model,
        )
        if bs.compute_input_hash(request["params"]) != mt["input_hash"]:
            # Raw or prompt changed since submission. Re-issuing would produce a
            # summary of different text than the manifest describes, so leave it
            # for the hash check in assemble_from_manifest to reject.
            log.error("Repair: input_hash mismatch for %s — not re-issuing", custom_id)
            continue

        params = dict(request["params"])
        params["max_tokens"] = SUMMARY_RETRY_MAX_TOKENS
        params.update(sync_call_kwargs(SUMMARY_RETRY_MAX_TOKENS))
        log.warning(
            "Repair: re-issuing %s ('%s') at max_tokens=%d",
            custom_id, mt["thread_info"].get("topic", "?"), SUMMARY_RETRY_MAX_TOKENS,
        )
        if idx:
            time.sleep(REPAIR_PACING_SECONDS)
        try:
            response = repair_client.messages.create(**params)
        except TRANSIENT_API_ERRORS:
            # Propagate: the next run collects the same ended batch for free,
            # whereas continuing here fails assembly and burns a resubmit.
            log.error("Repair: transient API failure on %s — deferring to next run",
                      custom_id)
            raise
        except Exception as e:
            log.error("Repair: re-issue failed for %s: %s", custom_id, e)
            continue
        if response.stop_reason == "max_tokens":
            log.error("Repair: %s truncated again at %d tokens", custom_id,
                      SUMMARY_RETRY_MAX_TOKENS)
            continue
        try:
            parsed = parse_summary_text(
                response.content[0].text if response.content else ""
            )
        except Exception as e:
            log.error("Repair: could not parse re-issued %s: %s", custom_id, e)
            continue
        # A re-issue is by construction a response that was already at a ceiling,
        # i.e. the population where a model under output pressure drops items to
        # fit — and assemble_thread iterates the AI's list, so a short result would
        # be published without a word. Report it rather than reject it: the prompt
        # never promises one entry per input speech, batch results are held to no
        # such bar, and rejecting a legitimately-partial response would resubmit
        # all N and deterministically fail — #46's deadlock, re-entered. Losing a
        # whole date is worse than a thread that is short and says so.
        covered = {s.get("speechOrder") for s in parsed["speeches"]}
        if not covered & set(orders):
            # Zero overlap is not a judgement call — assemble_thread would return
            # None and fail the date anyway, so treat it as a failed re-issue.
            log.error("Repair: re-issued %s covers none of its %d speeches",
                      custom_id, len(orders))
            continue
        if covered != set(orders):
            log.warning(
                "Repair: re-issued %s covers %d/%d manifest speeches",
                custom_id, len(covered & set(orders)), len(orders),
            )
        results[custom_id] = parsed
        repaired += 1

    if repaired:
        log.warning("Repair: recovered %d/%d unusable result(s) for %s",
                    repaired, len(unusable), sidecar["date"])
    return repaired


def _resume_summary_attempted(sidecar: dict) -> int:
    """Meetings this resume run actually has summary requests for.

    The manifest does not persist ``askable``, and it does not need to: by the
    time a sidecar exists, grouping and outcome are already done and the only
    question left for the API is the summary. So "asked about this run" is
    exactly "has a non-empty threads list" — no schema change, and therefore
    none of the SCHEMA_VERSION landing constraints (see CLAUDE.md).
    """
    return sum(1 for m in sidecar.get("meetings", []) if m.get("threads"))


def _resume_meetings_with_no_usable_result(
    sidecar: dict, results: Dict[str, Optional[dict]],
) -> int:
    """Trigger 1's failed count for the resume path.

    Mirrors ``count_meetings_with_no_usable_result``'s question — a manifest
    meeting counts as failed only if not one of its ``custom_id``s has a
    usable result — but works off the manifest's own shape (``sidecar
    ["meetings"]``) instead of ``prepared_meetings``, which does not exist on
    this path (see ``_resume_summary_attempted``). Reuses ``usable_result``
    rather than re-deriving "unusable"; see that function's docstring for why
    a second spelling would drift.

    Must be called AFTER ``_repair_unusable_results`` mutates ``results`` in
    place — a result repair recovers reads as usable here too, exactly as it
    would on the new-batch path.
    """
    failed = 0
    for m in sidecar.get("meetings", []):
        custom_ids = [t["custom_id"] for t in m.get("threads", [])]
        if not custom_ids:
            continue
        if any(usable_result(results.get(cid)) for cid in custom_ids):
            continue
        failed += 1
    return failed


def _existing_thread_count(threads_dir: str, date_str: str) -> int:
    """Threads already on disk for this date — the evidence the softener needs."""
    path = os.path.join(threads_dir, f"{date_str}.json")
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except _RAW_READ_ERRORS:
        # Widened from (OSError, JSONDecodeError) (#74): this is called from
        # collect_pending_batches OUTSIDE any try, and a file with invalid
        # UTF-8 raises UnicodeDecodeError at the io layer — a ValueError, but
        # not a JSONDecodeError — which escaped, aborted Collect, and took the
        # morning with it. Every other reader in this file uses _RAW_READ_ERRORS
        # and would have caught it.
        return 0
    return len(existing) if isinstance(existing, list) else 0


def _record_resume_verdict(date_str: str, summary_attempted: int,
                           published_threads: int, diagnostic: Optional[dict],
                           systemic_dates: list, suspect_dates: list,
                           diagnostics: list,
                           api_stats: Optional[dict] = None) -> None:
    """Record one date's verdict and annotate it immediately.

    Annotated here, as the verdict is reached, rather than accumulated for a
    single write at the end: if processing a LATER sidecar raises, the step dies
    and every step below it is skipped, so nothing that reached GITHUB_OUTPUT is
    ever read. An annotation is the one channel that survives a failed step —
    the same reason the Summarize loop annotates before it dies. (Since #65 a
    sidecar's *state* never fails the step — every regime exits 0 — so the case
    this guards against is a genuine crash, not a held or abandoned date.)

    ``api_stats`` carries trigger 1's evidence (see ``rejection_verdict``) for
    call sites that have it — i.e. after this run fetched and repaired batch
    results. The raw-missing call site never fetches results this run (raw
    isn't even loaded), so it has no rejection evidence to offer and passes
    None; only trigger 2 applies there.

    The two triggers are NOT exclusive (see ``run_pipeline`` / design §3.8):
    a fully rejected batch fires both, and both get reported as separate
    lines under ONE ``worst_verdict``-picked severity, so the date is
    recorded exactly once — appending it to both lists would inflate the
    workflow's ``SUSPECT_N -ge 2`` count on a single failing date.
    """
    rejection = rejection_verdict(api_stats, published_threads) if api_stats else 0
    blocked = publication_blocked_verdict(summary_attempted, published_threads)
    verdict = worst_verdict(rejection, blocked)
    if not verdict:
        return
    if diagnostic:
        diagnostics.append({**diagnostic, "date": date_str})
    lines = [f"{date_str}: resumed batch published nothing "
             f"({published_threads} thread(s) on the date)"]
    if rejection:
        lines.append(f"all {api_stats['attempted']} meeting(s) asked about "
                     f"this resume produced no usable summary")
    if blocked:
        d = diagnostic or {}
        lines.append(f"assembly failed: {d.get('reason', 'unknown')} "
                     f"(scope={d.get('scope')}, meeting={d.get('meeting_id')}, "
                     f"custom_id={d.get('custom_id')})")
    if verdict == EXIT_SYSTEMIC_FAILURE:
        systemic_dates.append(date_str)
        _annotate("error", " — ".join(lines))
    else:
        suspect_dates.append(date_str)
        lines.append("on its own this is one bad meeting, but several in "
                     "one run is an outage")
        _annotate("warning", " — ".join(lines))


def _record_held_sidecar(date_str: str, sidecar: dict, diagnostic: dict,
                         held_dates: list, diagnostics: list,
                         detail: Optional[str] = None) -> None:
    """Report a sidecar that is waiting on a human or on restored local state.

    Deliberately NOT ``_record_resume_verdict``. That one grades how far a
    failure spread — one meeting on an already-published date is weak evidence
    and comes out as a *warning* — which is the right question for "did today's
    work reach the site" and the wrong one here. A held sidecar is not weak
    evidence of anything: it is a request for a decision, and it is red at one
    date. Mixing the two also double-counted a date into ``suspect_dates``,
    where two of them would trip the workflow's SUSPECT_N >= 2 threshold under a
    message that does not describe what happened.

    The text must survive being read half-awake, so it says what was NOT done
    (no resubmit, no retry spent) as loudly as what was observed, and it offers
    causes in likelihood order without asserting one. It must never print a bare
    `git rm`: raw lives only on the runner, and past the lookback window a
    removed sidecar is not re-summarised — it is a permanent loss dressed up as
    a fix.

    ``detail`` carries what the reason alone cannot: for ``raw_unreadable``,
    WHICH file failed and how. The reason is the class of problem; the operator
    needs the instance. Two call paths reach this with that reason (the ended
    batch reads raw here, a terminal-status one reads it inside
    ``_apply_failure_policy``), so the detail also travels ON the diagnostic —
    a caller that only forwards ``eff_diag`` must not silently produce the
    filename-less half of the annotation.
    """
    held_dates.append(date_str)
    diagnostics.append({**diagnostic, "date": date_str})

    reason = diagnostic.get("reason", "unknown")
    detail = detail or diagnostic.get("detail")
    attempt = (sidecar.get("attempts") or [{}])[-1]
    submitted = attempt.get("submitted_at", "unknown")
    blocked = sidecar.get("blocked") or {}
    parts = [
        f"{date_str}: resume held — {reason}",
        f"meeting={diagnostic.get('meeting_id')} custom_id={diagnostic.get('custom_id')}",
        f"batch={attempt.get('batch_id')} submitted={submitted}",
        f"sidecar=data/pending-batches/{date_str}.json",
    ]
    if reason == "retry_exhausted":
        parts.append(
            f"three resubmits have failed ({sidecar.get('retry_count')} retries "
            f"spent); no further batch will be sent")
    elif bs.failure_policy(reason) == bs.RESUBMIT:
        # A retryable reason that still ended up here can only mean the rebuild
        # found no usable raw. Saying "rebuilding reproduces this exactly" would
        # be false — the batch is retryable and the next fetch may unblock it.
        parts.append(
            "NOT resubmitted and no retry spent: this reason IS retryable, but "
            "the requests could not be rebuilt from the raw on disk this run")
    elif reason == "threads_file_unreadable":
        # The generic line below would be false here in a way that matters: raw
        # is fine and so is the batch, and sending the reader to look at raw
        # revisions costs them the morning. This failure is downstream of
        # assembly, in the file being written to.
        parts.append(
            "NOT resubmitted and no retry spent: nothing is wrong with the "
            "batch or the raw, and no new batch would fix a local file")
    else:
        parts.append(
            "NOT resubmitted and no retry spent: rebuilding from today's raw "
            "reproduces this failure exactly")
    # Only claim a history that belongs to THIS finding. A sidecar blocked last
    # week on hash_mismatch, whose raw then vanished, is reported today as
    # raw_date_missing — printing the old `since` next to the new reason invents
    # a story and sends the reader to look at raw revisions that are not the
    # problem.
    if blocked.get("since") and blocked.get("reason") == reason:
        parts.append(f"held since {blocked['since']}")
    # Every BLOCKED sidecar needs the clock, not just the hash ones: the results
    # expire on the same schedule regardless of why it is stuck, and deferring
    # the decision is how a held date becomes a lost one.
    age = bs.age_days_or_none(sidecar, _utcnow_iso())
    if age is not None:
        parts.append(
            f"submitted about {age:.0f} day(s) ago; batch results are retained "
            f"roughly 29 days, so this decision has an expiry")
    if reason == "hash_mismatch":
        parts.append(
            "the request rebuilt from today's raw is not the one this batch was "
            "submitted with. Likely causes, in order: (1) compute_input_hash's "
            "param set changed without a SCHEMA_VERSION bump; (2) the raw was "
            "re-fetched and differs. This is not an API rejection")
        parts.append(
            "to act: confirm the date's raw is still re-fetchable inside the "
            "lookback window and secure it BEFORE removing "
            f"data/pending-batches/{date_str}.json — outside the window a removed "
            "sidecar is not re-summarised. Reverting the change that moved the "
            "hash lets the next run collect normally")
    elif reason == "threads_file_unreadable":
        parts.append(
            "the summaries were assembled successfully; what failed is reading "
            "this date's EXISTING threads file, and continuing would have "
            "republished the date without the threads it still holds. Nothing "
            "was written and the sidecar was kept on purpose, so restoring the "
            "file (it is committed — `git checkout` recovers it) lets the next "
            "run collect these same results. They expire ~29 days after "
            "submission, so this one has a deadline")
        if detail:
            parts.append(f"unreadable: {detail}")
    elif reason == "raw_unreadable":
        parts.append(
            "raw for this date IS on disk but could not be read, so nothing was "
            "assembled and nothing was resubmitted (the batch is fine; the local "
            "file is not). data/raw is gitignored and re-fetched every run, so "
            "the next run most likely replaces it — if this repeats, the file is "
            "being written broken rather than arriving broken")
        if detail:
            parts.append(f"unreadable: {detail}")
    elif reason in ("raw_missing", "raw_date_missing", "speech_gap"):
        # Only true while the date is still inside the fetch lookback window
        # (30 days). ABANDON_AGE_DAYS is 31, so a date aged 30-31 days is
        # already outside that window but not yet abandoned: no later run
        # will re-fetch it, and reading this as "tomorrow it self-heals" leads
        # an operator to wait it out instead of acting — the date lands in
        # abandoned_dates on the next run regardless.
        parts.append(
            "the batch is fine; this date's raw is not on disk this run. If "
            "the date is still within the fetch lookback window (30 days), a "
            "later run that re-fetches it collects normally — otherwise it "
            "will not be re-fetched and this sidecar is heading for "
            "abandonment (see abandoned_dates) once it ages past 31 days")
    log.error("Resume held: %s (%s)", date_str, reason)
    _annotate("error", " — ".join(parts))


def _why_a_sidecar_stopped_moving(sidecar: dict) -> str:
    """Best available account of why nothing was resubmitted for this sidecar.

    Only for the abandon record: a loss is worth nothing to a postmortem if it
    does not say what the sidecar was waiting on. Order matters — the ``blocked``
    marker is what the failure policy actually wrote, so it wins over anything
    re-derived here.

    Deliberately not control flow. Nothing decides whether to abandon from this
    string; that follows from the age and the raw check (see the gate in
    ``collect_pending_batches``), which is what lets it run on sidecars whose
    shape is not guaranteed.

    It is best-effort by construction and must read that way. The HOLD regimes
    (``raw_missing`` / ``raw_date_missing``) deliberately do NOT rewrite the
    sidecar — not touching it is the point — so the largest held population
    leaves no ``blocked`` marker at all and lands in the last branch. Do not let
    that branch grow into an assertion about what happened.
    """
    blocked = sidecar.get("blocked")
    if not isinstance(blocked, dict):   # a hand-edited sidecar may carry anything
        blocked = {}
    if blocked.get("reason"):
        return str(blocked["reason"])
    if not bs.is_current_schema(sidecar):
        return "stale_schema"
    try:
        if bs.should_hard_fail(sidecar):
            return "retry_exhausted"
    except (KeyError, TypeError):
        pass
    attempts = sidecar.get("attempts") or []
    last = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    if last.get("terminal_status"):
        return (f"the batch ended {last['terminal_status']} and was never "
                f"rebuilt")
    # No regime left a marker and no terminal status was ever recorded. This is
    # where a HOLD-held sidecar lands, so it must not claim a cause: the batch
    # may have stayed in flight, or Collect may not have run for weeks.
    return "no reason was recorded (a HOLD leaves no marker)"


def _manifest_meeting_ids(sidecar: dict) -> Optional[set]:
    """The meeting ids this sidecar's manifest needs to be rebuildable, or None
    if the manifest cannot be read at all.

    None is not "no meetings" — the two must not collapse, because the caller
    deletes on one of them. Read defensively: this runs before the schema check.
    """
    meetings = sidecar.get("meetings")
    if not isinstance(meetings, list):
        return None
    ids = set()
    for m in meetings:
        if not isinstance(m, dict):
            return None
        mid = m.get("meeting_id")
        # isinstance, not truthiness: a hand-edited list/dict id would raise
        # TypeError on set.add and crash Collect before the schema check.
        if not isinstance(mid, str) or not mid:
            return None
        ids.add(mid)
    return ids


# Reading raw to decide a DELETION must not be able to raise. json.load gives
# ValueError on bad syntax, but structurally odd-but-valid JSON reaches
# _load_meetings_for_date's .get()/iteration and gives AttributeError/TypeError
# (top level a list, "meetings" not a list, an element not an object). All of
# them mean the same thing here — "absence is not established" — and any of them
# escaping aborts Collect under set -e and takes the morning's publish down.
# RecursionError is here for the same reason as the other three: `json.load` on
# a deeply nested corrupt file raises it, and it inherits from RuntimeError, so
# without it that one corrupt file is exit 1 and no publish. It is the only
# member that is not obviously a read failure, which is why it is called out.
_RAW_READ_ERRORS = (OSError, ValueError, TypeError, AttributeError,
                    RecursionError)


def _raw_read_failure_detail(date_str: str, raw_dir: str, exc: BaseException) -> str:
    """Name the raw file that could not be read, for the operator.

    "raw is unreadable" over a directory holding 30 days of files from four
    adapters is not an instruction; a filename is. ``RawUnreadable`` carries the
    filename out of the failure itself, so this is only formatting — it does not
    re-read anything, and therefore cannot name a different file than the one
    that actually broke.

    The fallback exists because ``_RAW_READ_ERRORS`` is deliberately wider than
    the raise site: a future reader that fails some other way still produces a
    held date with an honest annotation rather than a confident wrong filename.
    """
    if isinstance(exc, RawUnreadable):
        return str(exc)
    return (f"raw for {date_str} in {raw_dir} could not be read and the failure "
            f"did not identify a file ({exc.__class__.__name__}: {exc})")


def _all_meeting_ids_in_raw(raw_dir: str) -> set:
    """Every meetingId present anywhere in raw_dir, regardless of date.

    Only for the abandon gate's dateless case, where the date a sidecar's raw
    would live under is exactly what is unknown.
    """
    import glob as _glob
    ids = set()
    for c in sorted(_glob.glob(os.path.join(raw_dir, "*.json"))):
        with open(c, "r", encoding="utf-8") as f:
            data = json.load(f)
        for m in data.get("meetings", []):
            mid = m.get("meetingId")
            if isinstance(mid, str):
                ids.add(mid)
    return ids


def _reason_not_to_abandon(sidecar: dict, path: str,
                           raw_dir: str) -> Optional[str]:
    """None if this over-age sidecar is provably unrecoverable and may be
    deleted; otherwise the reason to keep it, for the log.

    The caller has already established the results-expired half (the age).
    This establishes the other half — that there is nothing left to rebuild
    from — and it is the only place in this pipeline where a "yes" destroys
    data, so **everything it cannot establish is a no**:

    * the date is taken from the FILENAME and must be CONFIRMED by the sidecar's
      own ``date``. The filename is the convention (sidecars are written to
      "{date}.json") but a rename, a merge resolution or a stray copy makes it a
      guess, and a guess must not authorize a delete: looking under the wrong
      date finds no raw and deletes a sidecar whose raw is sitting on disk. A
      disagreement is a keep; a MISSING ``date`` falls back to asking whether
      the manifest's meetings exist anywhere in raw at all, which needs no date.
    * raw that cannot be read is not raw that is absent — and "cannot be read"
      includes structurally odd JSON, not just bad syntax. Letting the read
      raise here would abort Collect under ``set -e`` and take the morning's
      publish down (#65) — over a file we only wanted to count.
    * raw for the date is not enough on its own: the manifest's meetings are
      what a rebuild needs, and a date can have raw from another source adapter
      (kantei/council) while the batch's NDL meetings are gone for good.
    """
    stem = _date_from_sidecar_path(path)
    own = sidecar.get("date")
    if own is not None and not isinstance(own, str):
        return (f"its date field is not a string "
                f"({type(own).__name__}), so it cannot be trusted or compared")
    wanted = _manifest_meeting_ids(sidecar)

    if not own:
        # No self-identifying date. Rather than promote the filename from
        # convention to evidence, ask the question that does not need a date:
        # is any of this batch's raw anywhere on disk? That keeps the #69 bound
        # (a genuinely lost sidecar still has none) without betting a delete on
        # a filename nobody verified.
        if wanted is None:
            return "it carries neither a date nor a readable manifest"
        try:
            present = _all_meeting_ids_in_raw(raw_dir)
        except _RAW_READ_ERRORS as exc:
            return (f"it carries no date and raw could not be scanned "
                    f"({exc.__class__.__name__}), so absence is not established")
        if wanted & present:
            return ("it carries no date, and raw for some of its own meetings "
                    "is on disk under some date")
        return None

    try:
        datetime.strptime(stem, "%Y-%m-%d")
    except ValueError:
        return f"its filename ({stem!r}) is not a date, so its raw cannot be located"
    if own != stem:
        return (f"its own date field ({own!r}) disagrees with its filename "
                f"({stem!r}), so which date's raw to check is unclear")
    try:
        available = _load_meetings_for_date(stem, raw_dir)
    except _RAW_READ_ERRORS as exc:
        return (f"its raw for {stem} is on disk but unreadable "
                f"({exc.__class__.__name__}), so absence is not established")
    if not available:
        return None
    if wanted is None:
        return (f"raw for {stem} is back on disk and its manifest cannot be "
                f"read, so what a rebuild would need is unknown")
    if wanted & set(available):
        return "raw for some of its own meetings is back on disk"
    # Raw exists for the date but none of it is this batch's meetings — e.g. a
    # kantei file for a date whose batch covered NDL. Nothing to rebuild.
    return None


def _abandon_sidecar(path: str, date_str: str, sidecar: dict, age: float,
                     abandoned_dates: list) -> None:
    """Record a provably uncollectable batch as permanently lost, then delete it.

    The ONE abandon site (#69). It used to live inside the raw-missing branch,
    which the three held regimes never reach — they return before the poll — so
    the population most likely to sit for months was the one #66's
    "record the loss and clear it" principle never covered. A held sidecar
    stayed on disk, out of ``abandoned_dates``, reding every morning forever
    while the thing it was protecting had already expired.

    The caller owns the decision — including the raw check, which must stay
    there and not move in here: this function deletes, so everything that could
    make deletion wrong belongs where it can still be reconsidered.

    Not a hard fail: nothing about a loss that already happened is a reason to
    withhold today's other dates.
    """
    reason = _why_a_sidecar_stopped_moving(sidecar)
    # If the sidecar never told us its date, say so. The gate deliberately
    # refused to treat the filename as evidence when deciding to delete (a
    # rename or stray copy makes it a guess), so presenting that same string
    # afterwards as the lost date would send an operator to re-summarize a date
    # this batch may not have covered. It is still the best identifier there is
    # — it just has to be labelled as one.
    unverified = "" if isinstance(sidecar.get("date"), str) and sidecar["date"] else (
        " NOTE: this sidecar carried no date of its own, so the date above is "
        "taken from its filename and is UNVERIFIED — confirm against the batch "
        "before acting on it.")
    # Not bs.current_batch_id: that indexes ["batch_id"] and this runs on shapes
    # this module does not control (the same reason age_days_or_none exists). A
    # KeyError here would abort Collect under set -e and take the morning's
    # publish down — over a string that only appears in an annotation.
    attempts = sidecar.get("attempts") or []
    last = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    batch_id = last.get("batch_id") or "unknown"
    log.error("Resume: %s unrecoverable (age %.1fd, was %s) — abandoning sidecar",
              date_str, age, reason)
    # Say only what is provable, and no more. Two claims an earlier wording got
    # wrong, both of which send an operator the wrong way:
    #
    # * NOT "the date is empty" — this sidecar's uncollected threads are gone,
    #   but the date may well have published threads from earlier runs, and
    #   saying otherwise sends them hunting for threads never lost.
    # * NOT "no action recovers this" — what is unrecoverable is THIS BATCH:
    #   already paid for, its results expired, its manifest's raw off disk. The
    #   date's content is a different question; a manual run with a wide enough
    #   `lookback_days` can re-fetch the raw and summarize it again from
    #   scratch, at the cost of a second batch. Declaring that impossible is how
    #   a recoverable date stays unrecovered.
    abandoned_dates.append(date_str)
    _annotate(
        "error",
        f"{date_str}: this batch is permanently lost — the uncollected threads "
        f"from batch {batch_id} (age {age:.1f}d) can never be assembled: "
        f"its manifest's raw is not on disk (checked this run) and at this age "
        f"the batch results have expired (~29-day retention), so there is "
        f"nothing to assemble from and nothing to resubmit. Last recorded "
        f"state: {reason}. The sidecar is deleted and the loss is on the "
        f"record; no action recovers THESE results. Re-summarizing the date "
        f"from scratch is still possible (a manual run with lookback_days wide "
        f"enough to re-fetch it, paying for a new batch) — decide that "
        f"separately. Threads published for this date by earlier runs are "
        f"unaffected, and this is not an API rejection." + unverified)
    bs.delete_sidecar(path)


def _date_from_sidecar_path(path: str) -> str:
    """The date a sidecar's filename claims — sidecars are always written to
    "{date}.json" (see bs.sidecar_path), so this is the best IDENTIFIER
    available when the sidecar's own "date" is missing or its shape is not
    guaranteed (e.g. a stale-schema sidecar).

    It is a naming convention, not evidence, and the difference matters exactly
    once: a rename, a merge resolution or a stray copy makes it wrong, so
    ``_reason_not_to_abandon`` will not delete anything on its word alone. Use
    it to report and to label; never to decide that data may be destroyed."""
    return os.path.splitext(os.path.basename(path))[0]


def _reporting_date(sidecar: dict, path: str) -> str:
    """See bs.reporting_date — the rule lives there because check_stuck_batches
    has to apply the SAME one to dedup this run's reported dates (#71), and it
    cannot import this module (that would pull in the Anthropic SDK)."""
    return bs.reporting_date(sidecar, path)


def _apply_failure_policy(client, sidecar: dict, path: str, reason: str,
                          diagnostic: Optional[dict], raw_dir: str, model: str,
                          ci_commit: bool) -> tuple:
    """Apply the regime this failure falls into. Returns
    ``(outcome, effective_diagnostic)`` where outcome is
    "resubmitted" | "held" | "blocked".

    Replaces _retry_or_hardfail, which applied ONE regime to every reason: it
    resubmitted a hash_mismatch three times — each a full batch's charge for a
    rebuild that cannot verify — and it called record_terminal BEFORE finding out
    whether it could rebuild at all, so a missing raw file burned a retry slot
    without sending anything (#65).

    The order below is the fix, not a style choice: the policy is consulted
    first, and only the RESUBMIT branch is allowed to touch retry_count.

    ``effective_diagnostic`` exists because the retry-exhaustion escalation
    below changes the reason (e.g. a run of ``missing_result`` resubmits
    becomes ``retry_exhausted``), and the caller needs that changed reason to
    report the failure honestly — reporting the original diagnostic would have
    ``_record_held_sidecar`` claim raw could not be rebuilt when the batch was
    never even retried this run; the truth is the retry budget ran out.
    """
    policy = bs.failure_policy(reason)
    diagnostic = diagnostic or _diagnostic(reason)

    if policy == bs.BLOCKED:
        changed = bs.mark_blocked(sidecar, reason, _utcnow_iso(),
                                  diagnostic.get("meeting_id"),
                                  diagnostic.get("custom_id"))
        if changed:
            bs.save_sidecar(path, sidecar)
            if ci_commit:
                # A stale-schema sidecar's shape is not guaranteed (that is
                # exactly why it is being held), so use the shared reporting
                # rule rather than index a "date" key that may not exist — or
                # may hold something that is not a string.
                _git_commit_sidecar(path, _reporting_date(sidecar, path))
        return "blocked", diagnostic

    if policy == bs.HOLD:
        # No write, no retry spent, nothing submitted. The state this needed may
        # simply not have been fetched yet, and the batch is untouched.
        return "held", diagnostic

    # Rebuild BEFORE counting. The old order counted first and discovered it
    # could not rebuild afterwards, so a morning with no raw on disk spent a
    # retry slot having submitted nothing — three of those and a healthy batch
    # is a permanent human-decision case. This branch is reachable with raw
    # absent because the terminal-status check runs before raw is even loaded.
    # record_terminal is idempotent per attempt, so deferring it is safe.
    try:
        meetings_by_id = _load_meetings_for_date(sidecar["date"], raw_dir)
    except _RAW_READ_ERRORS as exc:
        # #72. Same guard as the abandon gate, for the same reason: letting this
        # raise aborts Collect under `set -e` and takes the morning's publish
        # down. Held rather than resubmitted — the batch is fine and the local
        # file is what is broken, so paying for a new one would not fix it.
        log.error("Resume: raw for %s is unreadable (%s) — holding",
                  sidecar["date"], exc.__class__.__name__)
        # Carried ON the diagnostic, not passed as an argument: this returns to
        # four call sites that all forward `eff_diag` and nothing else, so a
        # detail that only travelled by parameter would reach none of them and
        # the terminal-status path would print "raw is unreadable" over a
        # directory of 30 days' files without naming one.
        return "held", {**_diagnostic("raw_unreadable"),
                        "detail": _raw_read_failure_detail(
                            sidecar["date"], raw_dir, exc)}

    # Verify BEFORE rebuilding. The ended-batch path (assemble_from_manifest)
    # already runs this check, but a terminal-status batch (canceled/expired)
    # never reaches that path — it comes straight here. Without this call,
    # "canceled + the raw/prompt changed since submission" skipped verification
    # entirely: _rebuild_requests_from_manifest only checks for missing raw and
    # speechOrder gaps, so it would happily rebuild and resubmit a batch that is
    # guaranteed to fail the same hash check the next morning, spending a retry
    # and a batch's charge on a doomed request. Routing the verify diagnostic
    # back through _apply_failure_policy makes this door collapse onto the same
    # BLOCKED/HOLD/RESUBMIT decision the ended-batch path already makes.
    verify_diag = verify_manifest_against_raw(sidecar, meetings_by_id)
    if verify_diag is not None:
        return _apply_failure_policy(client, sidecar, path, verify_diag["reason"],
                                     verify_diag, raw_dir, model, ci_commit)

    requests = _rebuild_requests_from_manifest(sidecar, meetings_by_id, model)
    if requests is None:
        log.error("Resume: cannot rebuild %s for resubmit (raw missing/gap) — holding",
                  sidecar["date"])
        return "held", diagnostic

    bs.record_terminal(sidecar, reason, _utcnow_iso())
    if bs.should_hard_fail(sidecar):
        # Three genuine resubmits have failed. Stop paying — but do NOT take the
        # publish down: since the pending gate is per-date, other dates can
        # still reach the site (#44/#52).
        #
        # The reason changes to retry_exhausted here, and so must the
        # diagnostic: the original reason (e.g. missing_result) is now stale —
        # what happened THIS run is retry exhaustion, not another instance of
        # the original failure, and _record_held_sidecar's text branches on
        # `reason == "retry_exhausted"` to say so.
        exhausted_diag = _diagnostic("retry_exhausted", diagnostic.get("meeting_id"),
                                     diagnostic.get("custom_id"))
        return _apply_failure_policy(client, sidecar, path, "retry_exhausted",
                                     exhausted_diag, raw_dir, model, ci_commit)
    bs.clear_blocked(sidecar)
    new_batch_id = submit_summary_batch(client, requests)
    bs.add_attempt(sidecar, new_batch_id, _utcnow_iso())
    bs.save_sidecar(path, sidecar)
    if ci_commit:
        _git_commit_sidecar(path, sidecar["date"])
    log.warning("Resubmitted %s as %s after %s (retry %d)",
                sidecar["date"], new_batch_id, reason, sidecar["retry_count"])
    return "resubmitted", diagnostic


def collect_pending_batches(
    client,
    members: Dict[str, dict],
    model: str,
    pending_dir: str = bs.PENDING_DIR,
    threads_dir: str = "data/threads",
    raw_dir: str = "data/raw",
    budget_seconds: int = 1800,
    poll_seconds: int = 30,
    ci_commit: bool = False,
) -> dict:
    """Resume all in-flight batches.

    Returns a dict (informally ``CollectResult``) with:

    * ``hard_fail`` — no branch in this function sets it True today: since #65,
      every sidecar state this function can see (retry-threshold, older-schema,
      raw-missing, ...) is reported as held or abandoned (see ``held_dates`` /
      ``abandoned_dates``), and this function returns 0. A genuine crash does
      not go through this flag at all — it raises and the process exits
      non-zero on the exception. The field and its test are kept for a future
      hard-fail path, not because one exists now.
    * ``systemic_dates`` / ``suspect_dates`` — dates whose resumed batch
      published nothing, at the two evidence strengths ``publication_blocked_
      verdict`` distinguishes (see that function). A pending sidecar makes the
      daily workflow skip Summarize for that specific date, so for that date
      this function IS the run and has to carry the same failure signal
      ``run_pipeline``'s exit code carries for a normal run — a bare bool
      couldn't say which date failed, and callers that resume many dates in
      one process need to.
    * ``diagnostics`` — structured observations (see ``_diagnostic``), one per
      date that reported a non-clean verdict.
    * ``held_dates`` — dates whose sidecar is waiting on a human (BLOCKED) or on
      restored local state (HOLD); see ``_apply_failure_policy`` /
      ``_record_held_sidecar``. Disjoint from ``systemic_dates`` /
      ``suspect_dates``: a held date is neither a publication verdict nor weak
      evidence, it is a decision request, and must not be diluted into either.
    * ``abandoned_dates`` — dates whose sidecar passed the abandon age AND whose
      raw is not on disk this run, in whatever regime it was held (#69): that
      sidecar's uncollected threads can never be assembled and the sidecar is
      deleted. The only permanently irreversible
      outcome in this function, and disjoint from ``systemic_dates`` /
      ``suspect_dates`` / ``held_dates`` for the same reason those are disjoint
      from each other — it is a distinct claim ("this batch's threads are gone
      for good"), not "nothing published this run" or "waiting on a human".
    """
    import glob as _glob
    hard_fail = False
    systemic_dates: list = []
    suspect_dates: list = []
    held_dates: list = []
    abandoned_dates: list = []
    diagnostics: list = []
    paths = sorted(_glob.glob(os.path.join(pending_dir, "*.json")))
    deadline = time.time() + budget_seconds

    for path in paths:
        sidecar = bs.load_sidecar(path)
        if sidecar is None:
            continue

        # BEFORE every regime check and before the poll (#69). Whether this batch
        # is still collectable does not depend on why it stopped moving, and the
        # three regimes below return early — leaving a provably-lost sidecar to
        # red the run every morning forever if the check sits after them.
        #
        # Safe to run this early because neither half of the proof needs the
        # poll or any field a stale-schema sidecar might not carry:
        #
        # * results expired — inferred from the current attempt's age, and a
        #   resubmit pushes a new attempt, so a batch still being retried can
        #   never be deleted here (bs.is_abandonable); and
        # * nothing is left to rebuild from — OBSERVED, not inferred, and every
        #   way of failing to observe it means "keep". The age implies the date
        #   is outside the DEFAULT lookback, but `lookback_days` is a
        #   workflow_dispatch input accepting up to 365, and widening it is
        #   precisely how a human rescues a held sidecar: that run fetches the
        #   raw back, so inferring this half would delete the sidecar the rescue
        #   was for, in the same job that restored it. See
        #   _reason_not_to_abandon. With the default 30-day lookback raw for an
        #   over-age date is never present (data/raw/ is gitignored, so CI
        #   re-fetches every run), so the bound #69 added still holds for the
        #   daily run.
        now_iso = _utcnow_iso()
        age = bs.age_days_or_none(sidecar, now_iso)
        if age is not None and bs.is_abandonable(sidecar, now_iso):
            keep = _reason_not_to_abandon(sidecar, path, raw_dir)
            if keep is None:
                _abandon_sidecar(path, _date_from_sidecar_path(path),
                                 sidecar, age, abandoned_dates)
                continue
            # Not collectable as it stands (the results are gone), but not
            # provably lost either — fall through and let the regimes below
            # report it as the human decision it is.
            log.error("Resume: %s is past the abandon age (%.1fd) but %s "
                      "— not abandoning", _date_from_sidecar_path(path), age, keep)

        if not bs.is_current_schema(sidecar):
            log.error(
                "Sidecar %s has schema_version %r (expected %d) — holding; "
                "its input_hashes were computed by an older revision",
                path, sidecar.get("schema_version"), bs.SCHEMA_VERSION,
            )
            # Held, not hard-failed. The old comment justified exit 1 with "any
            # sidecar skips Summarize entirely, so exiting 0 would be a green run
            # that processes nothing" — the per-date gate (#44) removed that
            # premise. Resubmitting is still unsafe: the stored hashes come from a
            # different param set, so every thread would fail verification.
            _apply_failure_policy(client, sidecar, path, "stale_schema", None,
                                  raw_dir, model, ci_commit)
            # is_current_schema() being False means this sidecar's shape is
            # not guaranteed, so do not assume "date" is present OR that it is
            # a string — _reporting_date settles both. This branch runs BEFORE
            # the no-date guard below, so it is the one place a dateless (or
            # wrongly-typed-date) sidecar still reaches, and a raw ["date"]
            # here would crash Collect under set -e and take the whole
            # morning's publish down — the failure mode #65 removed.
            _record_held_sidecar(_reporting_date(sidecar, path),
                                 sidecar, _diagnostic("stale_schema"), held_dates,
                                 diagnostics)
            continue

        # AFTER the schema check (a stale-schema sidecar is missing its date
        # *because* it is old, and that is the more useful diagnosis) but before
        # everything else: the retry-threshold branch, the poll, the
        # terminal-status rebuild and the assembly all index sidecar["date"], so
        # a sidecar without one is a shape this function can report but not
        # process. Letting it through would move the KeyError a few lines down
        # rather than prevent it, and a crash here aborts Collect under set -e
        # and takes the morning's publish with it (#65). Held, not abandoned:
        # whether it is recoverable was already settled by the gate above, which
        # deliberately does not need the date.
        if not isinstance(sidecar.get("date"), str) or not sidecar["date"]:
            log.error("Sidecar %s has no usable \"date\" field — holding; "
                      "every path below this point needs one", path)
            # Through the policy, like every other BLOCKED regime, so the marker
            # is actually written: declaring a state without persisting it makes
            # check_stuck_batches.py report this as "in flight, retries 0" (an
            # untried jam) and leaves a later abandon record saying no reason was
            # ever recorded — for a sidecar whose reason is precisely known.
            _apply_failure_policy(client, sidecar, path, "sidecar_has_no_date",
                                  None, raw_dir, model, ci_commit)
            _record_held_sidecar(_reporting_date(sidecar, path), sidecar,
                                 _diagnostic("sidecar_has_no_date"),
                                 held_dates, diagnostics)
            continue

        if bs.should_hard_fail(sidecar):
            # The SECOND hard-fail site. _apply_failure_policy converts the
            # threshold when it is crossed; this one catches a sidecar that
            # crossed it on an earlier run. Fixing only one puts the date back on
            # exit 1 the following morning.
            log.error("Sidecar %s exceeded retry threshold (%d) — holding",
                      path, sidecar["retry_count"])
            _apply_failure_policy(client, sidecar, path, "retry_exhausted", None,
                                  raw_dir, model, ci_commit)
            _record_held_sidecar(sidecar["date"], sidecar,
                                 _diagnostic("retry_exhausted"), held_dates, diagnostics)
            continue

        # Safe to index: the guard above returned for every sidecar without a
        # non-empty string "date". Everything from here down (rebuild, assembly,
        # the resubmit paths) relies on that same guard — do not add a fallback
        # here instead, or a dateless sidecar reaches code that cannot use it.
        date_str = sidecar["date"]
        batch_id = bs.current_batch_id(sidecar)
        remaining = max(0, int(deadline - time.time()))
        batch = poll_summary_batch(client, batch_id,
                                   timeout_seconds=remaining, poll_interval_seconds=poll_seconds)

        if batch.processing_status != "ended":
            if batch.processing_status in bs.TERMINAL_FAILURES:
                outcome, eff_diag = _apply_failure_policy(
                    client, sidecar, path, batch.processing_status, None,
                    raw_dir, model, ci_commit)
                if outcome in ("held", "blocked"):
                    _record_held_sidecar(
                        date_str, sidecar, eff_diag, held_dates, diagnostics)
            else:
                log.info("Batch %s still %s — leaving for next run", batch_id,
                         batch.processing_status)
            continue

        # Load raw BEFORE fetching results: raw is needed to both assemble and
        # resubmit, and checking it first means we never call .results() on a
        # batch we cannot use anyway (which would crash if results have expired).
        try:
            meetings_by_id = _load_meetings_for_date(date_str, raw_dir)
        except _RAW_READ_ERRORS as exc:
            # #72. Unreadable raw is NOT absent raw, and the difference decides
            # whether data survives: absence is what the abandon gate treats as
            # "nothing left to rebuild from", so folding a truncated file into
            # the branch below would let it authorize a delete it never proved.
            log.error("Resume: raw for %s is unreadable (%s) — holding",
                      date_str, exc.__class__.__name__)
            _record_held_sidecar(date_str, sidecar,
                                 _diagnostic("raw_unreadable"),
                                 held_dates, diagnostics,
                                 detail=_raw_read_failure_detail(date_str, raw_dir, exc))
            continue
        if not meetings_by_id:
            # Raw is gone, and this sidecar is younger than the abandon age (the
            # top-of-loop gate already removed the ones that are not), so the miss
            # may be transient — raw simply not fetched this run. Keep it.
            log.error("Resume: no raw for %s (outside window?) — keeping sidecar",
                      date_str)
            _record_held_sidecar(date_str, sidecar,
                                 _diagnostic("raw_date_missing"),
                                 held_dates, diagnostics)
            continue

        # Verify BEFORE touching results. Both are needed to assemble, but only
        # one of them is free and only one of them changes meaning with age: the
        # batch's results expire ~29 days after submission, and if the fetch
        # raises first, a sidecar whose raw has changed reports the retryable
        # ``results_expired`` instead of the deterministic ``hash_mismatch`` —
        # and gets resubmitted for a rebuild that cannot verify. #65.
        verify_diag = verify_manifest_against_raw(sidecar, meetings_by_id)
        if verify_diag is not None:
            outcome, eff_diag = _apply_failure_policy(
                client, sidecar, path, verify_diag["reason"], verify_diag,
                raw_dir, model, ci_commit)
            if outcome in ("held", "blocked"):
                _record_held_sidecar(date_str, sidecar, eff_diag,
                                     held_dates, diagnostics)
            continue

        # Results are retained ~29 days after an "ended" batch; past that the SDK
        # raises because results_url is null. Treat that like a terminal failure
        # and resubmit from the manifest (raw is available at this point).
        try:
            results = fetch_summary_results(client, batch_id)
        except anthropic.AnthropicError as e:
            # Only the expiry case (null results_url on an ended batch) is a bare
            # AnthropicError. Network/HTTP failures are APIError subclasses and
            # are transient — let those propagate so the run retries next time
            # rather than burning a resubmit.
            if isinstance(e, anthropic.APIError):
                raise
            log.error("Resume: results unavailable for %s (%s) — resubmitting",
                      date_str, e)
            outcome, eff_diag = _apply_failure_policy(
                client, sidecar, path, "results_expired", None,
                raw_dir, model, ci_commit)
            if outcome in ("held", "blocked"):
                _record_held_sidecar(date_str, sidecar, eff_diag,
                                     held_dates, diagnostics)
            continue

        _repair_unusable_results(client, sidecar, meetings_by_id, results, model)

        # Trigger 1's evidence for this resume, taken here (after repair, before
        # assembly) so a recovered result reads as usable and a genuinely
        # rejected one is counted whether or not assembly then succeeds.
        api_stats = {
            "attempted": _resume_summary_attempted(sidecar),
            "failed": _resume_meetings_with_no_usable_result(sidecar, results),
        }

        threads, ok, diagnostic = assemble_from_manifest(
            sidecar, meetings_by_id, results, members, thread_counter=0,
        )
        if not ok:
            reason = (diagnostic or {}).get("reason", "unknown")
            log.error("Resume: assembly incomplete for %s (%s)", date_str, reason)
            outcome, eff_diag = _apply_failure_policy(
                client, sidecar, path, reason, diagnostic,
                raw_dir, model, ci_commit)
            if outcome in ("held", "blocked"):
                _record_held_sidecar(date_str, sidecar, eff_diag,
                                     held_dates, diagnostics)
            else:
                # Still a publication outcome: summary requests went out for this
                # date and nothing reached the site.
                _record_resume_verdict(
                    date_str, api_stats["attempted"],
                    _existing_thread_count(threads_dir, date_str),
                    diagnostic, systemic_dates, suspect_dates, diagnostics,
                    api_stats=api_stats,
                )
            continue

        try:
            _append_threads_to_date_file(threads, threads_dir, date_str)
        except ThreadsFileUnreadable as exc:
            # Hold, and specifically hold BEFORE delete_sidecar below: the
            # assembled threads only survive because the batch is still
            # collectable, and its results live ~29 days. Nothing here is
            # written, so a restored file makes the next run collect normally.
            _record_held_sidecar(date_str, sidecar,
                                 _diagnostic("threads_file_unreadable"),
                                 held_dates, diagnostics, detail=str(exc))
            continue
        bs.delete_sidecar(path)
        log.info("Resume: collected %d threads for %s", len(threads), date_str)

    return {
        "hard_fail": hard_fail,
        "systemic_dates": systemic_dates,
        "suspect_dates": suspect_dates,
        "held_dates": held_dates,
        "abandoned_dates": abandoned_dates,
        "diagnostics": diagnostics,
    }


def askable_request_kinds(meeting: dict) -> set:
    """Which kinds of request this meeting sends: {"grouping"}, {"outcome"},
    both, or neither.

    Callers need the *kinds*, not just a bool, because a failure means different
    things per kind (#60). A meeting that sends a grouping request has its
    attempted/failed decided by that request; the outcome request is an
    enrichment and must not be able to fail a meeting whose speeches published.
    But a meeting that sends ONLY an outcome request has nothing else to be
    judged by, and before #60 that meeting was invisible: it was not counted as
    attempted, and extract_meeting_outcome swallowed its failure silently, so a
    date holding just such a meeting could have every request rejected all
    morning and still exit 0.

    Delegates to the very builders the requests are made from, so this cannot
    disagree with what actually goes on the wire.

    Never raises: malformed raw (NDL can emit ``"speech": null``) used to fail
    inside the per-meeting ``try``, and must keep doing so. Claiming both kinds
    lets the very next call re-raise it there, where it lands in ``failed`` and
    the run keeps going.
    """
    try:
        kinds = set()
        if build_grouping_messages(meeting) is not None:
            kinds.add("grouping")
        if build_outcome_messages(meeting) is not None:
            kinds.add("outcome")
        return kinds
    except Exception as e:  # noqa: BLE001 — see docstring
        log.warning(
            "Could not pre-check %s (%s); assuming it reaches the API",
            meeting.get("meetingId", "?"), e,
        )
        return {"grouping", "outcome"}


def has_question_for_the_api(meeting: dict) -> bool:
    """Whether this meeting reaches the API at all.

    Since #60 this means what its name says. It used to mean "sends a *grouping*
    request", with the narrower reading documented as a workaround: a
    procedural-only meeting carrying a 附帯決議 sends an outcome request, but
    ``extract_meeting_outcome`` swallowed that request's exceptions, so counting
    the meeting as ``attempted`` would have made it one that can never become
    ``failed`` — which MASKS an outage instead of catching it. Now that the
    swallow is counted (``outcome_stats``), the pre-check can be honest.

    A meeting whose speeches are all procedural and which passes no resolution
    still answers False: nothing is sent, so its "0 threads" is not evidence the
    API works, and counting it as a success is what would let a total outage look
    like a quiet day (2026-07-24 had both kinds on the same date).
    """
    return bool(askable_request_kinds(meeting))


def _count_outcome_only_failure(meeting_id: str, kinds: set,
                                outcome_stats: dict, api_stats: dict,
                                failed_ids: Optional[list] = None) -> bool:
    """Fold a failed outcome request into ``api_stats`` — and ONLY when it is the
    whole of what this meeting asked (#60). Answers whether it did, because a
    meeting counted as failed must not then be filed as completed: it asked its
    one question and got nothing, so a ``--resume`` has to retry it, the same as
    every other meeting this function's callers charge to ``failed``.

    ``api_stats`` counts MEETINGS, not requests: "this meeting reached the API
    and came back with nothing usable". So the fold is conditional in both
    directions, and both directions matter:

    * ``kinds == {"outcome"}`` — the meeting asked one question and got nothing.
      It is already counted as attempted, and without this it could never become
      failed, which is worse than not counting it: an outage on a date of such
      meetings would read as a quiet day.
    * anything else — the grouping request already decided this meeting's verdict.
      Adding a failure here would either double-count it or, worse, mark a
      meeting whose speeches published as failed over a 附帯決議 blurb no reader
      can see. On a date with two such meetings that alone reads as an outage.

    ``failed_ids`` collects the meetings folded in this way, and it exists for
    the operator rather than the verdict: the annotation's standing phrase is
    "produced no usable summary", which for these meetings names a request that
    was never sent. Sending someone to hunt a summary failure that did not
    happen is the same cost as sending them to hunt a 400 that did not happen.
    """
    if kinds == {"outcome"} and outcome_stats["failed"]:
        api_stats["failed"] += 1
        if failed_ids is not None:
            failed_ids.append(meeting_id)
        log.error("%s asked only the outcome question and it failed", meeting_id)
        return True
    return False


def count_meetings_with_no_usable_result(
    prepared_meetings: list,
    results: Dict[str, Optional[dict]],
    api_stats: dict,
) -> int:
    """Charge ``api_stats["failed"]`` for meetings whose whole batch came back empty.

    The summary phase does not fail by raising. ``fetch_summary_results`` turns
    every errored entry into ``None``, repair swallows a deterministic 4xx, and
    ``assemble_from_manifest`` answers ``ok=False`` — which the caller reports
    as ``pending=True`` and the run exits 0 with the sidecar kept. So a date on
    which *every* summary request was rejected looked exactly like a date whose
    batch merely needs another day, and the counters actively asserted the API
    was healthy (``failed`` stayed 0). That is the same fail-open shape as
    2026-08-05, relocated to the phase that carries the bulk of the spend and
    the #47/#51 regression surface — see ``systemic_failure``.

    Per meeting, not per request: one oversized thread out of twenty is
    ordinary breakage. A meeting counts as failed only when it asked at least
    one question and not one answer was usable. A meeting whose grouping
    legitimately produced no threads asked nothing here and is left alone.

    Called before assembly so the count is taken whether or not assembly then
    succeeds. Returns the number newly charged (for logging/tests).
    """
    newly_failed = 0
    for prep in prepared_meetings:
        if not prep.get("askable"):
            continue
        custom_ids = [p["custom_id"] for p in prep["pending"]]
        if not custom_ids:
            continue
        if any(usable_result(results.get(cid)) for cid in custom_ids):
            continue
        api_stats["failed"] += 1
        newly_failed += 1
        log.error(
            "All %d summary request(s) for %s came back unusable",
            len(custom_ids), prep["meeting_id"],
        )
    return newly_failed


def prepare_meeting_for_batch(
    client,
    meeting: dict,
    model: str,
    outcome_stats: Optional[dict] = None,
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
    meeting_outcome = extract_meeting_outcome(client, meeting, model=model,
                                              outcome_stats=outcome_stats)
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


def _batch_phase_result(threads: list, thread_counter: int,
                        completed_meeting_ids: list, pending: bool,
                        summary_attempted: int = 0,
                        publication_blocked: bool = False,
                        diagnostic: Optional[dict] = None) -> dict:
    """The shape run_batch_phase answers with.

    A dict rather than a longer tuple: this function already carries an
    out-parameter (api_stats), and a 7-tuple unpacked positionally at the call
    site is the kind of thing that breaks silently when a field is inserted.
    """
    return {
        "threads": threads,
        "thread_counter": thread_counter,
        "completed_meeting_ids": completed_meeting_ids,
        "pending": pending,
        "summary_attempted": summary_attempted,
        "publication_blocked": publication_blocked,
        "diagnostic": diagnostic,
    }


def run_batch_phase(
    client,
    meetings: List[dict],
    progress: dict,
    members: Dict[str, dict],
    model: str,
    date_str: str,
    thread_counter: int,
    batch_timeout_seconds: int = 1800,  # 30 min default budget
    batch_poll_seconds: int = 30,
    pending_dir: str = bs.PENDING_DIR,
    ci_commit: bool = False,
    api_stats: Optional[dict] = None,
    outcome_only_failed: Optional[list] = None,
) -> dict:
    """Process meetings via Batches API. Persists a sidecar so a batch that
    does not finish within the budget resumes on a later run.

    Returns a dict — see ``_batch_phase_result`` for the keys.

    ``api_stats`` is an out-parameter (``{"attempted", "failed"}``) counting only
    meetings that would actually reach the API, so run_pipeline can tell "there
    was nothing to summarize" from "every request failed" — see
    ``systemic_failure``. It is deliberately NOT part of ``progress``: that dict
    is persisted and re-read on resume, and a stale count would answer the
    question for a run that never happened.

    ``outcome_only_failed`` is a second out-parameter of the same kind, carrying
    the ids charged to ``failed`` by ``_count_outcome_only_failure`` so the
    annotation can avoid naming a summary request that was never sent. A list
    rather than another key in ``api_stats``: that dict's two keys are compared
    for equality all over the tests, and it answers the verdict's question, not
    the operator's.
    """
    if api_stats is None:
        api_stats = new_api_stats()
    prepared_meetings: list[dict] = []
    all_pending: list[dict] = []

    for meeting in meetings:
        meeting_id = meeting.get("meetingId", "unknown")
        if meeting_id in progress["completed"]:
            log.info("Skipping already completed: %s", meeting_id)
            continue
        kinds = askable_request_kinds(meeting)
        askable = bool(kinds)
        if askable:
            api_stats["attempted"] += 1
        outcome_stats = new_api_stats()
        log.info("Preparing for batch: %s", meeting_id)
        try:
            prep = prepare_meeting_for_batch(client, meeting, model,
                                             outcome_stats=outcome_stats)
            outcome_only_failure = _count_outcome_only_failure(
                meeting_id, kinds, outcome_stats, api_stats,
                failed_ids=outcome_only_failed)
        except Exception as e:
            log.error("Failed to prepare %s: %s", meeting_id, e)
            progress["failed"].append(meeting_id)
            if askable:
                api_stats["failed"] += 1
            continue
        if outcome_only_failure:
            # Charged to api_stats["failed"] above, so it must not also be filed
            # as completed. Nothing is lost by dropping the prep: such a meeting
            # groups to no threads and puts no request in the batch, so its
            # `prep` would only carry an outcome that no thread can display.
            progress["failed"].append(meeting_id)
            continue
        if askable and prep["thread_infos"] and not prep["pending"]:
            # Grouping answered with threads, and not one of them names a speech
            # that exists in the raw record. Nothing goes into the batch, so
            # without this the meeting lands in neither list: never completed,
            # never failed, re-charged for grouping and outcome every morning
            # while the date publishes nothing and the run exits 0.
            log.error("Grouping for %s named no speech that exists in raw", meeting_id)
            progress["failed"].append(meeting_id)
            api_stats["failed"] += 1
            continue
        prep["askable"] = askable
        prepared_meetings.append(prep)
        all_pending.extend(prep["pending"])

    # The denominator for trigger 2 — meetings that actually put a summary
    # request in this batch. Deliberately NOT api_stats["attempted"], which also
    # counts a meeting whose grouping legitimately produced zero threads; see
    # publication_blocked_verdict. Spelled identically to the submission-failure
    # counter in the ``except anthropic.APIError`` branch below on purpose: the
    # two answer the same question, and two spellings of it would eventually
    # disagree.
    summary_attempted = sum(
        1 for p in prepared_meetings if p.get("askable") and p.get("pending")
    )

    if not all_pending:
        log.info("Batch phase: nothing to summarize")
        return _batch_phase_result([], thread_counter, [], False)

    requests = [
        build_summary_request(
            p["meeting"], p["thread_info"], p["thread_speeches"],
            p["custom_id"], model,
        )
        for p in all_pending
    ]
    log.info("Submitting %d summary requests via Batches API", len(requests))
    try:
        batch_id = submit_summary_batch(client, requests)
    except anthropic.APIError as e:
        # An outage must not take the run down with it. Submission is the one
        # API call here with nothing persisted behind it — no sidecar exists
        # yet, so there is nothing to resume and the meetings simply stay
        # uncompleted for the next run. Charging them and returning lets
        # systemic_failure make it loud (exit 3) while the workflow still
        # publishes, commits and pushes. Letting it propagate instead means
        # exit 1, which aborts the date loop and skips every step below —
        # the #52 amplification this whole change exists to end, reached
        # through a 429/529 instead of a 400.
        #
        # anthropic.APIError, NOT Exception. The workflow's contract is "3 and 4
        # mean an outage and the loop continues; anything else is a crash and
        # aborts it" — catching Exception here would quietly reclassify a
        # TypeError in our own request-building as an outage and keep going,
        # which is how a code bug would come to look like a bad API day.
        log.error("Batch submission failed for %s: %s", date_str, e)
        # Only meetings that actually had a request in this batch. A meeting
        # whose grouping legitimately returned nothing never reached the
        # submission, so charging it here would push a partial failure over the
        # "everything failed" line and report a false systemic outage.
        api_stats["failed"] += sum(
            1 for p in prepared_meetings if p.get("askable") and p.get("pending")
        )
        for prep in prepared_meetings:
            if prep["meeting_id"] not in progress["failed"]:
                progress["failed"].append(prep["meeting_id"])
        return _batch_phase_result([], thread_counter, [], False, summary_attempted)

    # Persist the sidecar BEFORE the long poll so a kill mid-poll still resumes.
    submitted_at = _utcnow_iso()
    sidecar = bs.new_sidecar(date_str, model)
    sidecar["meetings"] = build_manifest_meetings(prepared_meetings, model)
    bs.add_attempt(sidecar, batch_id, submitted_at)
    path = bs.sidecar_path(date_str, pending_dir)
    bs.save_sidecar(path, sidecar)
    if ci_commit:
        _git_commit_sidecar(path, date_str)

    try:
        batch = poll_summary_batch(
            client, batch_id,
            timeout_seconds=batch_timeout_seconds,
            poll_interval_seconds=batch_poll_seconds,
        )
        if batch.processing_status != "ended":
            log.info("Batch %s not ended within budget — sidecar kept for resume", batch_id)
            return _batch_phase_result([], thread_counter, [], True, summary_attempted)

        results = fetch_summary_results(client, batch_id)
        meetings_by_id = {m.get("meetingId", "unknown"): m for m in meetings}
        _repair_unusable_results(client, sidecar, meetings_by_id, results, model)
    except anthropic.APIError as e:
        # Past this point the sidecar is on disk, so the honest answer to any
        # API-side failure is "pending" — the batch is real, its results are
        # retained for ~29 days, and the next run resumes it for free. Notably
        # _repair_unusable_results deliberately re-raises RateLimitError /
        # APIConnectionError / 529 rather than treating them as repair
        # failures; unwrapped, those escalated an overloaded API into a run
        # that publishes nothing at all.
        log.error("Batch %s could not be collected this run (%s) — sidecar kept",
                  batch_id, e)
        return _batch_phase_result([], thread_counter, [], True, summary_attempted)
    # Before assembly: assembly is all-or-nothing and reports its failure as
    # "pending", which is indistinguishable from a slow batch. The counters have
    # to be taken from the results themselves.
    count_meetings_with_no_usable_result(prepared_meetings, results, api_stats)
    new_threads, ok, diagnostic = assemble_from_manifest(
        sidecar, meetings_by_id, results, members, thread_counter,
    )
    if not ok:
        log.error("Batch %s ended but assembly incomplete — keeping sidecar", batch_id)
        return _batch_phase_result(
            [], thread_counter, [], True, summary_attempted,
            publication_blocked=True, diagnostic=diagnostic,
        )

    thread_counter += len(new_threads)
    completed_meeting_ids = [m["meeting_id"] for m in sidecar["meetings"]]
    bs.delete_sidecar(path)
    return _batch_phase_result(new_threads, thread_counter,
                               completed_meeting_ids, False, summary_attempted)


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
            threads = _as_thread_list(json.load(f), threads_path)
    except _RAW_READ_ERRORS as e:
        # Widened from (OSError, JSONDecodeError), and the shape check moved
        # inside, because this runs BEFORE run_pipeline's refusal guard on the
        # same file — so a shape it did not catch used to escape as an
        # AttributeError from `t.get` below, i.e. exit 1, front-running the very
        # guard that exists to turn this file's unreadability into exit 3.
        # Returning the empty set is still right: it only means "assume nothing
        # is done yet", and the refusal downstream is what actually decides.
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
    pending_dir: str = bs.PENDING_DIR,
    ci_commit: bool = False,
) -> int:
    """Run the full summarization pipeline for a given date.

    Returns the exit code this date warrants: 0, ``EXIT_SYSTEMIC_FAILURE`` or
    ``EXIT_SUSPECT_FAILURE``. A code rather than a bool because there are three
    answers, not two, and the third one only means something once the workflow
    has seen every date — see ``suspect_failure``.
    """
    # Load raw data — collect meetings from all source files for this date.
    # Shared with the resume path on purpose: _raw_candidates_for_date's whole
    # justification is that resume must see the same raw this does, and a second
    # copy of the list here is exactly the drift it was extracted to prevent.
    meetings, unreadable_raw = load_raw_meetings_for_date(date_str, raw_dir)

    if not meetings and not unreadable_raw and not _raw_files_for_date(date_str, raw_dir):
        log.error("Raw data not found for %s in %s", date_str, raw_dir)
        log.error("Run fetch_ndl.py or fetch_kantei.py first")
        sys.exit(1)
    if unreadable_raw and not meetings:
        # Zero meetings from unreadable files looks exactly like zero meetings
        # from a quiet day, and one of those is a broken runner. Say which, and
        # say what it is NOT: an operator sent to hunt a 400 that never happened
        # has had their morning taken (#59).
        log.error("Every raw file for %s is unreadable — refusing the date",
                  date_str)
        _annotate(
            "error",
            f"{date_str}: nothing could be summarized — no readable raw file "
            f"for this date yielded a single meeting, so none was ever asked "
            f"about. (Not necessarily 'every file is broken': a source that "
            f"legitimately had nothing that day parses fine and contributes "
            f"nothing. The broken ones are named below; the rest are intact.) "
            f"This is a local file problem, not an API rejection. "
            f"data/raw is re-fetched every run, so the next run most likely "
            f"clears it; if it repeats, the file is arriving broken. "
            f"Unreadable: {'; '.join(unreadable_raw)}")
        return EXIT_SYSTEMIC_FAILURE
    if unreadable_raw:
        # Partial loss, and the only branch here that ends in a GREEN run: some
        # source's meetings for this date were dropped and the date publishes
        # without them. That is the whole reason skipping was allowed (the file
        # is re-fetched, the lookback re-visits the date) — but "allowed" is not
        # "unremarkable", and a log line is invisible on a green run, which is
        # what _annotate exists for. Warning, not error: the date did publish.
        _annotate(
            "warning",  # log level below must agree — see _annotate
            f"{date_str}: published WITHOUT the meetings from "
            f"{len(unreadable_raw)} unreadable raw file(s) — every other source "
            f"for this date summarized normally. This is a local file problem, "
            f"not an API rejection. data/raw is re-fetched every run and the "
            f"lookback re-visits this date, so the missing meetings normally "
            f"arrive on the next run; if the same file repeats, it is arriving "
            f"broken. Unreadable: {'; '.join(unreadable_raw)}")
        log.warning("%s published without %d unreadable raw file(s)",
                    date_str, len(unreadable_raw))
    if meeting_filter:
        meetings = [m for m in meetings if meeting_filter in m.get("meeting", "")]

    log.info("Processing %d meetings for %s", len(meetings), date_str)

    if dry_run:
        for m in meetings:
            speech_count = len(m.get("speeches", []))
            log.info("  %s — %d speeches", m.get("meetingId", "?"), speech_count)
        log.info("Dry run complete. No API calls made.")
        return 0

    # Progress tracking
    output_path = os.path.join(output_dir, f"{date_str}.json")
    progress_path = os.path.join(output_dir, f"{date_str}.progress.json")

    # Auto-resume: if the threads file already exists, treat already-summarized
    # meetings as completed so we only spend API calls on new ones. This keeps
    # the daily window-fetch idempotent without requiring callers to pass --resume.
    auto_resume = not resume and os.path.exists(output_path)

    if resume or auto_resume:
        progress = load_progress(progress_path)
        progress["failed"] = []
        # Seed completed list from existing threads when the progress file does
        # not say (it is deleted on clean completion).
        #
        # This used to run only under auto_resume, and moving it is part of
        # #74's guard rather than a tidy-up: once load_progress answers an
        # UNREADABLE file the same way it answers a missing one, the explicit
        # `--resume` path — the one a human reaches for to unstick a date —
        # would start from an empty `completed`, re-summarise meetings the
        # threads file already holds, and APPEND them. Duplicate threads on a
        # published date is a worse outcome than the exit 1 the guard removed,
        # and unlike exit 1 it is silent.
        if not progress["completed"] and os.path.exists(output_path):
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
    try:
        members = load_members(members_path)
    except MembersUnreadable as exc:
        # Fatal by design (see MembersUnreadable), so this is exit 1 and the
        # date loop does stop — correctly: members.json is shared by every
        # date, so the next date would hit the identical failure. Being loud
        # about that beats 30 identical tracebacks. What changes is that the
        # operator is told which file and that nothing was published.
        log.error("members.json is unreadable: %s", exc)
        _annotate(
            "error",
            f"members.json is unreadable ({exc}) — the run is stopped on "
            f"purpose rather than publishing every date with its speakers "
            f"unresolved under a green build. This file is shared by all "
            f"dates, so no date could have succeeded. It is committed, so "
            f"`git checkout data/members.json` recovers it")
        sys.exit(1)

    # Initialize API client
    client = anthropic.Anthropic()

    all_threads = []
    thread_counter = 0

    # On resume (explicit or auto), load previously generated threads so new
    # meetings get appended instead of overwriting the file.
    if (resume or auto_resume) and os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                all_threads = _as_thread_list(json.load(f), output_path)
        except _RAW_READ_ERRORS as exc:
            # The ONLY copy of this date's published work, and the one reader
            # here where skipping is worse than stopping (#74): carrying on with
            # `all_threads = []` republishes the date with just this run's
            # threads and drops every thread the file still holds. Refuse the
            # date, leave the file exactly as it is, and let every other date
            # publish — a verdict, not a raise, because a raise is exit 1 and
            # aborts the workflow's date loop.
            log.error("Existing threads file %s is unreadable (%s) — refusing "
                      "to overwrite it", output_path, exc.__class__.__name__)
            _annotate(
                "error",
                f"{date_str}: skipped — its existing threads file "
                f"{output_path} is on disk but unreadable "
                f"({exc.__class__.__name__}: {exc}), and continuing would "
                f"republish the date without the threads it still holds. "
                f"NOTHING was changed: the file is untouched, no summary was "
                f"attempted, and no other date is affected. This is a local "
                f"file problem, not an API rejection — restore the file (it is "
                f"committed, so `git checkout` recovers it) and the next run "
                f"picks the date up normally")
            return EXIT_SYSTEMIC_FAILURE
        thread_counter = len(all_threads)
        log.info(
            "%s with %d existing threads",
            "Resumed" if resume else "Auto-resumed",
            len(all_threads),
        )

    pending = False
    publication_blocked = False
    summary_attempted = 0
    assembly_diagnostic = None
    api_stats = new_api_stats()
    # Meetings charged to api_stats["failed"] over an outcome request that was
    # the whole of what they asked. Kept next to api_stats, and read only by the
    # annotation below — see _count_outcome_only_failure.
    outcome_only_failed: list = []
    if batch:
        phase = run_batch_phase(
            client, meetings, progress, members, model, date_str,
            thread_counter,
            batch_timeout_seconds=batch_timeout_seconds,
            batch_poll_seconds=batch_poll_seconds,
            pending_dir=pending_dir,
            ci_commit=ci_commit,
            api_stats=api_stats,
            outcome_only_failed=outcome_only_failed,
        )
        new_threads = phase["threads"]
        thread_counter = phase["thread_counter"]
        completed_ids = phase["completed_meeting_ids"]
        pending = phase["pending"]
        publication_blocked = phase["publication_blocked"]
        summary_attempted = phase["summary_attempted"]
        assembly_diagnostic = phase["diagnostic"]
        all_threads.extend(new_threads)
        for mid in completed_ids:
            if mid not in progress["completed"]:
                progress["completed"].append(mid)
        save_progress(progress, progress_path)
        log.info("Batch phase: +%d threads from %d meeting(s)%s",
                 len(new_threads), len(completed_ids),
                 " (batch pending — will resume)" if pending else "")
    else:
        for meeting in meetings:
            meeting_id = meeting.get("meetingId", "unknown")

            if meeting_id in progress["completed"]:
                log.info("Skipping already completed: %s", meeting_id)
                continue

            log.info("Processing: %s", meeting_id)
            kinds = askable_request_kinds(meeting)
            askable = bool(kinds)
            if askable:
                api_stats["attempted"] += 1

            summary_stats = new_api_stats()
            outcome_stats = new_api_stats()
            try:
                threads, thread_counter = process_meeting(
                    client, meeting, members, model, date_str, thread_counter,
                    summary_stats=summary_stats, outcome_stats=outcome_stats,
                )
                outcome_only_failure = _count_outcome_only_failure(
                    meeting_id, kinds, outcome_stats, api_stats,
                    failed_ids=outcome_only_failed)
                all_threads.extend(threads)
                # Its own predicate, deliberately NOT systemic_failure(): that
                # one carries a date-scope carve-out ("one failing meeting does
                # not overturn a published date") whose threshold is tuned to
                # meetings, while here the unit is threads. Reusing it works
                # today only by accident, and would silently stop protecting
                # small meetings the moment that threshold is retuned for
                # date-scope reasons.
                if (summary_stats["attempted"] > 0
                        and summary_stats["failed"] == summary_stats["attempted"]):
                    # Grouping worked, so the meeting has substance — yet not one
                    # summary became a thread. process_meeting swallows those per
                    # thread and returns cleanly, so without this the meeting is
                    # filed as completed and never retried, and the run exits 0
                    # having published nothing for it.
                    log.error(
                        "None of the %d summary request(s) for %s produced a thread",
                        summary_stats["attempted"], meeting_id,
                    )
                    progress["failed"].append(meeting_id)
                    if askable:
                        api_stats["failed"] += 1
                elif outcome_only_failure:
                    # Already charged to api_stats["failed"]; filing it as
                    # completed would let a --resume skip the one question it
                    # asked and drop the failure from the counters entirely.
                    progress["failed"].append(meeting_id)
                else:
                    progress["completed"].append(meeting_id)
                    log.info(
                        "Completed %s — %d threads", meeting_id, len(threads),
                    )
                save_progress(progress, progress_path)
            except Exception as e:
                log.error("Failed to process %s: %s", meeting_id, e)
                progress["failed"].append(meeting_id)
                if askable:
                    api_stats["failed"] += 1
                save_progress(progress, progress_path)
                continue

    # Cross-thread linking
    if all_threads:
        # Also load existing threads from other dates for cross-date linking
        existing_threads = load_threads_from_other_dates(output_dir, date_str)

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
        output_path = os.path.join(output_dir, f"{date_str}.json")
        write_json_atomic(output_path, all_threads)
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

    # The two triggers are not exclusive. A fully rejected batch fires BOTH:
    # nothing usable came back (trigger 1), and assembly then failed on those
    # very same missing results (trigger 2). Report both observations — calling
    # it "answered but not assemblable" while the API was in fact rejecting
    # everything sends the reader away from the 400 that is actually there.
    rejection = rejection_verdict(api_stats, len(all_threads))
    blocked = (publication_blocked_verdict(summary_attempted, len(all_threads))
               if publication_blocked else 0)
    verdict = worst_verdict(rejection, blocked)

    if verdict:
        level = "error" if verdict == EXIT_SYSTEMIC_FAILURE else "warning"
        lines = [f"{date_str}: nothing this run produced reached the site "
                 f"({len(all_threads)} thread(s) on the date in total)"]
        if rejection:
            lines.append(
                f"all {api_stats['attempted']} meeting(s) asked about this run "
                f"produced no usable summary")
            if outcome_only_failed:
                # Without this the phrase above sends the reader to the summary
                # phase for a request that was never sent: these meetings are
                # procedural with a 附帯決議, so the outcome request is the only
                # one they make and no thread was ever possible for them.
                lines.append(
                    f"{len(outcome_only_failed)} of those sent no summary "
                    f"request at all — their only question was the 附帯決議 "
                    f"outcome one ({', '.join(sorted(outcome_only_failed))})")
        if blocked:
            d = assembly_diagnostic or {}
            lines.append(
                f"assembly failed: {d.get('reason', 'unknown')} "
                f"(scope={d.get('scope')}, meeting={d.get('meeting_id')}, "
                f"custom_id={d.get('custom_id')})")
        if verdict == EXIT_SUSPECT_FAILURE:
            lines.append("on its own this is one bad meeting, but several in "
                         "one run is an outage")
        _annotate(level, " — ".join(lines))
        return verdict
    return 0


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
        "--batch-timeout", type=int, default=1800,
        help="Max seconds to wait for batch completion (default: 1800 = 30min)",
    )
    parser.add_argument(
        "--batch-poll", type=int, default=30,
        help="Seconds between batch status polls (default: 30)",
    )
    parser.add_argument(
        "--collect-pending", action="store_true",
        help="Resume in-flight batches from data/pending-batches/ and exit. "
             "Non-zero exit if a sidecar exceeds the retry threshold.",
    )
    parser.add_argument(
        "--pending-dir", default="data/pending-batches",
        help="Directory for in-flight batch sidecars",
    )
    parser.add_argument(
        "--batch-budget", type=int, default=1800,
        help="Per-run poll budget in seconds (default 1800 = 30min)",
    )
    parser.add_argument(
        "--ci-commit", action="store_true",
        help="Early-commit+push the sidecar after submit (CI only)",
    )

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if args.collect_pending:
        client = anthropic.Anthropic()
        try:
            members = load_members(args.members_path)
        except MembersUnreadable as exc:
            # Still fatal (see MembersUnreadable) — but named. This runs before
            # collect_pending_batches, so the bare version killed the Collect
            # step, and with it the whole job, without saying which file.
            _annotate(
                "error",
                f"members.json is unreadable ({exc}) — the whole run is stopped "
                f"on purpose: every date would otherwise publish with its "
                f"speakers unresolved, under a green build. The file is "
                f"committed, so `git checkout data/members.json` recovers it. "
                f"Nothing was written and no sidecar was touched")
            log.error("members.json is unreadable: %s", exc)
            sys.exit(1)
        result = collect_pending_batches(
            client, members, args.model,
            pending_dir=args.pending_dir, threads_dir=args.output_dir,
            raw_dir=args.raw_dir, budget_seconds=args.batch_budget,
            poll_seconds=args.batch_poll, ci_commit=args.ci_commit,
        )
        save_members(members, args.members_path)
        # NOT wrapped in try/finally. A SystemExit raised from a finally block
        # REPLACES the exception that sent us there, so an unwritable
        # GITHUB_OUTPUT would exit 0 with no verdict transported and no
        # traceback — a fail-open in the one transport #59 depends on. Let it
        # raise: the annotations are already out, and a crash here is honest.
        _write_github_output(
            systemic_dates=result["systemic_dates"],
            suspect_dates=result["suspect_dates"],
            held_dates=result["held_dates"],
            abandoned_dates=result["abandoned_dates"],
        )
        # 1, never 3 or 4 — and since #65, effectively never at all. This process
        # speaks for many dates, so its exit code cannot say WHICH one failed: the
        # outputs above do that, and the annotations survive even a failed step.
        # A sidecar that needs a human (stale schema, exhausted retries, a hash
        # that no longer verifies) is reported through held_dates and reds the job
        # in the final step, WITHOUT taking this morning's publish down with it.
        # hard_fail remains for a genuine crash path.
        sys.exit(1 if result["hard_fail"] else 0)

    exit_code = run_pipeline(
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
        batch_timeout_seconds=args.batch_budget,
        batch_poll_seconds=args.batch_poll,
        pending_dir=args.pending_dir,
        ci_commit=args.ci_commit,
    )
    if exit_code:
        # DISTINCT codes, never 1. The caller has to keep going — publishing
        # what already exists must not be blocked by this date failing (that
        # amplification is #52) — so the workflow tolerates 3 and 4 per date,
        # finishes the run, commits, and only then decides whether to fail the
        # job. A bare 1 is indistinguishable from a crash and would abort the
        # loop under `set -e`.
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
