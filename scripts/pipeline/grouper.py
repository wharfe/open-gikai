"""Topic grouping: split a meeting's speeches into thematic threads via Claude API."""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from .prompts import GROUPING_SYSTEM, GROUPING_PROMPT, OUTCOME_SYSTEM, OUTCOME_PROMPT

log = logging.getLogger("pipeline.grouper")


def _is_procedural(speech: dict) -> bool:
    """Heuristic: detect procedural/boilerplate speeches to exclude."""
    speaker = speech.get("speaker", "")
    text = speech.get("speech", "").strip()
    role = speech.get("speakerRole", "") or ""

    # Meeting metadata record
    if speaker == "会議録情報":
        return True
    # Chairperson: check role field or text prefix
    is_chair = "委員長" in role or "委員長" in text[:30]
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
    model: str = "claude-sonnet-4-20250514",
) -> List[dict]:
    """Group a meeting's speeches into thematic threads.

    Args:
        client: anthropic.Anthropic client instance
        meeting: a meeting dict from raw NDL data
        model: Claude model to use

    Returns:
        List of thread dicts with keys: topic, topicTag, topicColor, summary, speechOrders
    """
    speeches = meeting.get("speeches", [])

    # Filter out procedural speeches and format for prompt
    substantive = [s for s in speeches if not _is_procedural(s)]
    if not substantive:
        log.info("No substantive speeches in %s", meeting.get("meetingId", "?"))
        return []

    formatted = "\n\n".join(_format_speech_for_grouping(s) for s in substantive)

    prompt = GROUPING_PROMPT.format(
        house=meeting.get("house", ""),
        meeting=meeting.get("meeting", ""),
        date=meeting.get("date", ""),
        speeches=formatted,
    )

    log.info(
        "Grouping %s (%d substantive speeches)",
        meeting.get("meetingId", "?"), len(substantive),
    )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=GROUPING_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    result = _parse_json_response(response.content[0].text)
    threads = result.get("threads", [])

    log.info("Found %d threads in %s", len(threads), meeting.get("meetingId", "?"))
    return threads


# ---------------------------------------------------------------------------
# Meeting-level outcome extraction
# ---------------------------------------------------------------------------

def _extract_outcome_by_pattern(speeches: List[dict]) -> Optional[dict]:
    """Try to extract vote result from procedural text using regex patterns.

    Returns outcome dict if found, None if API call is needed.
    """
    import re

    procedural_text = ""
    for s in speeches:
        role = s.get("speakerRole", "") or ""
        text = s.get("speech", "")
        speaker = s.get("speaker", "")
        # Chairperson: check role, text prefix, or metadata
        is_chair = (
            "委員長" in role
            or "委員長" in text[:30]
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
    model: str = "claude-sonnet-4-20250514",
) -> dict:
    """Extract meeting-level outcome (votes, resolutions) from procedural speeches.

    Uses pattern matching first, falls back to API for resolution details.
    """
    speeches = meeting.get("speeches", [])
    outcome = _extract_outcome_by_pattern(speeches)

    if not outcome:
        return {"result": None, "resolution": None, "status": "ongoing"}

    # If there's a resolution, use API to summarize it
    if outcome.get("resolution") and client:
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
                procedural_speeches="\n\n".join(procedural[-10:]),  # last 10 to avoid token overflow
            )

            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=1024,
                    system=OUTCOME_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
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
