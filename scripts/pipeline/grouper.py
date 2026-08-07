"""Topic grouping: split a meeting's speeches into thematic threads via Claude API."""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from .prompts import (
    GROUPING_SYSTEM,
    GROUPING_INSTRUCTIONS,
    GROUPING_INPUT_TEMPLATE,
    OUTCOME_SYSTEM,
    OUTCOME_PROMPT,
)
# Not summary-specific despite living there: it is the rule for every
# synchronous messages.create() in this package. See the comment above its
# definition for why omitting a timeout is a ValueError, not a slow request.
# summarizer imports only .prompts, so this direction adds no cycle.
from .summarizer import sync_call_kwargs

log = logging.getLogger("pipeline.grouper")

# Ceiling for a grouping response. The synchronous path can detect truncation
# and retry at GROUPING_RETRY_MAX_TOKENS; the Batches API cannot (a truncated
# result is only visible once the whole batch is back), so batch-mode callers
# must submit at the retry ceiling from the start.
GROUPING_MAX_TOKENS = 8192
GROUPING_RETRY_MAX_TOKENS = 16384
# Outcome extraction emits a handful of short fields, not a per-speech list, so
# it does not share the growth-with-speech-count problem that truncated summaries.
OUTCOME_MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# Shared request construction
#
# The synchronous path (group_meeting / extract_meeting_outcome) and the
# Batches API path (scripts/batch.py, scripts/bulk_batch.py) MUST build byte
# -identical prompts: the same speech has to summarize to the same text whether
# it went through the daily run or an operator's recovery run, or the
# determinism invariant in CLAUDE.md is only true of one of them. They used to
# assemble their own prompts side by side, and drifted — batch.py was still
# importing prompt constants that prompts.py stopped exporting when grouping
# moved to cached instruction blocks, so both recovery scripts had been dead on
# import for months while looking maintained. One builder each, used by both.
# ---------------------------------------------------------------------------

def substantive_speeches(meeting: dict) -> List[dict]:
    """The speeches grouping actually asks about — everything non-procedural.

    Exposed so the grouping log can report the count without a second copy of
    the predicate. "Found 0 threads" means two unrelated things (a meeting with
    one real speech, or grouping returning nothing at all) and this count is the
    only thing that tells them apart in a single log line.
    """
    return [s for s in meeting.get("speeches", []) if not _is_procedural(s)]


def build_grouping_messages(meeting: dict) -> Optional[List[dict]]:
    """Messages for one meeting's grouping call, or None if nothing to group."""
    substantive = substantive_speeches(meeting)
    if not substantive:
        return None

    user_input = GROUPING_INPUT_TEMPLATE.format(
        house=meeting.get("house", ""),
        meeting=meeting.get("meeting", ""),
        date=meeting.get("date", ""),
        speeches="\n\n".join(_format_speech_for_grouping(s) for s in substantive),
    )
    # Static rules + format spec are sent as a separate content block with
    # cache_control so subsequent calls within ~5min only pay ~10% of input
    # tokens for this prefix. See pipeline/prompts.py for details.
    return [{
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": GROUPING_INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": user_input},
        ],
    }]


def build_outcome_messages(meeting: dict) -> Optional[List[dict]]:
    """Messages for one meeting's outcome call, or None if it needs no API call.

    Returns None both when the pattern matcher finds no resolution and when it
    finds one but no procedural speech is long enough to summarize — the two
    conditions callers previously re-implemented around this prompt.
    """
    speeches = meeting.get("speeches", [])
    if not _extract_outcome_by_pattern(speeches).get("resolution"):
        return None

    procedural = []
    for s in speeches:
        combined = (s.get("speakerRole", "") or "") + (s.get("speakerPosition", "") or "")
        text = s.get("speech", "").strip()
        is_chair = (
            any(kw in combined for kw in ("委員長", "会長", "議長", "主査"))
            or any(kw in text[:30] for kw in ("委員長", "会長", "議長", "主査"))
        )
        if (is_chair or "附帯決議" in text) and len(text) > 50:
            procedural.append(f"[{s.get('speaker', '')}] {text}")
    if not procedural:
        return None

    prompt = OUTCOME_PROMPT.format(
        house=meeting.get("house", ""),
        meeting=meeting.get("meeting", ""),
        date=meeting.get("date", ""),
        # Last 10 to avoid token overflow.
        procedural_speeches="\n\n".join(procedural[-10:]),
    )
    return [{"role": "user", "content": prompt}]


def build_grouping_request(meeting: dict, custom_id: str, model: str) -> Optional[dict]:
    """Batches API request for one meeting's grouping, or None if nothing to group.

    Submits at GROUPING_RETRY_MAX_TOKENS rather than GROUPING_MAX_TOKENS: the
    synchronous path re-issues at that ceiling when it sees stop_reason ==
    "max_tokens", and a batch request gets no such second chance — truncation is
    only visible once the whole batch is back. A ceiling is not billed unused.
    """
    messages = build_grouping_messages(meeting)
    if messages is None:
        return None
    return {
        "custom_id": custom_id,
        "params": {
            "model": model,
            "max_tokens": GROUPING_RETRY_MAX_TOKENS,
            # No sampling params — see summarizer.build_summary_request.
            "thinking": {"type": "disabled"},
            "system": GROUPING_SYSTEM,
            "messages": messages,
        },
    }


def build_outcome_request(meeting: dict, custom_id: str, model: str) -> Optional[dict]:
    """Batches API request for one meeting's outcome, or None if not needed."""
    messages = build_outcome_messages(meeting)
    if messages is None:
        return None
    return {
        "custom_id": custom_id,
        "params": {
            "model": model,
            "max_tokens": OUTCOME_MAX_TOKENS,
            # No sampling params — see summarizer.build_summary_request.
            "thinking": {"type": "disabled"},
            "system": OUTCOME_SYSTEM,
            "messages": messages,
        },
    }


def _is_procedural(speech: dict) -> bool:
    """Heuristic: detect procedural/boilerplate speeches to exclude."""
    speaker = speech.get("speaker", "")
    text = speech.get("speech", "").strip()
    role = speech.get("speakerRole", "") or ""
    position = speech.get("speakerPosition", "") or ""
    combined = role + position

    # Meeting metadata record
    if speaker == "会議録情報":
        return True
    # Chairperson: 委員長, 会長, 議長, and their deputies
    chair_keywords = ("委員長", "会長", "議長", "主査")
    is_chair = any(kw in combined for kw in chair_keywords) or any(kw in text[:30] for kw in chair_keywords)
    if is_chair and len(text) < 150:
        return True
    return False


def _truncate_speech(text: str, max_chars: int = 200) -> str:
    """Truncate speech text for grouping (only need context, not full text)."""
    text = text.strip().replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _format_speech_for_grouping(speech: dict) -> str:
    """Format a single speech record for the grouping prompt."""
    order = speech.get("speechOrder", 0)
    speaker = speech.get("speaker", "")
    group = speech.get("speakerGroup", "") or ""
    position = speech.get("speakerPosition", "") or ""
    snippet = _truncate_speech(speech.get("speech", ""))
    return f"[{order}] {speaker}（{group}、{position}）\n{snippet}"


def _parse_json_response(text: str) -> dict:
    """Extract JSON from Claude's response, handling markdown fences."""
    # Try direct parse first
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Try finding first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from response: {text[:200]}...")


def group_meeting(
    client,
    meeting: dict,
    model: str = "claude-sonnet-5",
) -> List[dict]:
    """Group a meeting's speeches into thematic threads.

    Args:
        client: anthropic.Anthropic client instance
        meeting: a meeting dict from raw NDL data
        model: Claude model to use

    Returns:
        List of thread dicts with keys: topic, topicTag, topicColor, summary, speechOrders
    """
    messages = build_grouping_messages(meeting)
    if messages is None:
        log.info("No substantive speeches in %s", meeting.get("meetingId", "?"))
        return []

    log.info("Grouping %s (%d substantive speeches)",
             meeting.get("meetingId", "?"), len(substantive_speeches(meeting)))

    response = client.messages.create(
        model=model,
        max_tokens=GROUPING_MAX_TOKENS,
        # No sampling params — see summarizer.build_summary_request.
        # Disable Sonnet 5 adaptive thinking (ON when omitted) so the JSON output
        # keeps the full token budget and stays deterministic. See summarize.py.
        thinking={"type": "disabled"},
        system=GROUPING_SYSTEM,
        messages=messages,
        **sync_call_kwargs(GROUPING_MAX_TOKENS),
    )

    # Check if response was truncated
    if response.stop_reason == "max_tokens":
        log.warning("Grouping response truncated for %s, retrying with higher limit",
                     meeting.get("meetingId", "?"))
        response = client.messages.create(
            model=model,
            max_tokens=GROUPING_RETRY_MAX_TOKENS,
            thinking={"type": "disabled"},
            system=GROUPING_SYSTEM,
            messages=messages,
            # Without this the SDK refuses this call outright under an opus-4.x
            # model id (per-model non-streaming cap is 8192) — a bare ValueError
            # before any request is sent, on the retry path that exists to
            # rescue a truncated response.
            **sync_call_kwargs(GROUPING_RETRY_MAX_TOKENS),
        )

    _log_cache_usage(response, "grouping")

    result = _parse_json_response(response.content[0].text)
    threads = result.get("threads", [])

    log.info("Found %d threads in %s", len(threads), meeting.get("meetingId", "?"))
    return threads


def _log_cache_usage(response, phase: str) -> None:
    """Log prompt cache hit/write stats so we can verify caching works."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    if read or write:
        log.info("  cache[%s]: read=%d write=%d", phase, read, write)


# ---------------------------------------------------------------------------
# Meeting-level outcome extraction
# ---------------------------------------------------------------------------

def _extract_outcome_by_pattern(speeches: List[dict]) -> dict:
    """Try to extract vote result from procedural text using regex patterns.

    Always returns the outcome dict; ``result``/``resolution`` are None when no
    pattern matched. (It never returns None — callers guarding on falsiness are
    guarding against a case that cannot happen.)
    """
    import re

    procedural_text = ""
    _chair_kw = ("委員長", "会長", "議長", "主査")
    for s in speeches:
        role = s.get("speakerRole", "") or ""
        position = s.get("speakerPosition", "") or ""
        text = s.get("speech", "")
        speaker = s.get("speaker", "")
        combined = role + position
        is_chair = (
            any(kw in combined for kw in _chair_kw)
            or any(kw in text[:30] for kw in _chair_kw)
            or speaker == "会議録情報"
        )
        if is_chair:
            procedural_text += text + "\n"

    if not procedural_text:
        return {"result": None, "resolution": None, "status": "ongoing"}

    result = None
    resolution = None
    status = "ongoing"

    # Detect vote results
    if re.search(r"(原案のとおり|修正議決|全会一致で).*(可決|議決)", procedural_text):
        result = "可決"
        status = "resolved"
    elif re.search(r"否決", procedural_text):
        result = "否決"
        status = "resolved"

    # Detect attached resolutions
    if "附帯決議" in procedural_text:
        resolution = "附帯決議あり"  # Will be enriched by API if needed

    return {
        "result": result,
        "resolution": resolution,
        "status": status,
    }


def extract_meeting_outcome(
    client,
    meeting: dict,
    model: str = "claude-sonnet-5",
) -> dict:
    """Extract meeting-level outcome (votes, resolutions) from procedural speeches.

    Uses pattern matching first, falls back to API for resolution details.
    """
    outcome = _extract_outcome_by_pattern(meeting.get("speeches", []))

    # If there's a resolution, use API to summarize it. build_outcome_messages
    # re-checks that condition, and returns None when there is nothing to ask.
    if client:
        messages = build_outcome_messages(meeting)
        if messages is not None:
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=OUTCOME_MAX_TOKENS,
                    thinking={"type": "disabled"},
                    system=OUTCOME_SYSTEM,
                    messages=messages,
                    **sync_call_kwargs(OUTCOME_MAX_TOKENS),
                )
                api_result = _parse_json_response(response.content[0].text)
                # Merge: keep pattern-match result but use API resolution text
                if api_result.get("resolution"):
                    outcome["resolution"] = api_result["resolution"]
                if api_result.get("result"):
                    outcome["result"] = api_result["result"]
                    outcome["status"] = api_result.get("status", "resolved")
                log.info("Extracted outcome: %s", outcome)
            except Exception as e:
                log.warning("Failed to extract outcome via API: %s", e)

    return outcome
