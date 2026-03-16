"""Topic grouping: split a meeting's speeches into thematic threads via Claude API."""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from .prompts import GROUPING_SYSTEM, GROUPING_PROMPT

log = logging.getLogger("pipeline.grouper")


def _is_procedural(speech: dict) -> bool:
    """Heuristic: detect procedural/boilerplate speeches to exclude."""
    speaker = speech.get("speaker", "")
    text = speech.get("speech", "")
    role = speech.get("speakerRole", "") or ""

    # Meeting metadata record
    if speaker == "会議録情報":
        return True
    # Very short chairperson utterances (procedural)
    if "委員長" in role and len(text.strip()) < 150:
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
