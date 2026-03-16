"""Per-thread speech summarization via Claude API."""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from .prompts import SUMMARY_SYSTEM, SUMMARY_PROMPT

log = logging.getLogger("pipeline.summarizer")


def _parse_json_response(text: str) -> dict:
    """Extract JSON from Claude's response, handling markdown fences."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from response: {text[:200]}...")


def _format_speech_for_summary(speech: dict) -> str:
    """Format a speech record for the summarization prompt."""
    order = speech.get("speechOrder", 0)
    speaker = speech.get("speaker", "")
    group = speech.get("speakerGroup", "") or ""
    position = speech.get("speakerPosition", "") or ""
    text = speech.get("speech", "").strip()
    return (
        f"[speechOrder: {order}]\n"
        f"発言者: {speaker}（{group}、{position}）\n"
        f"発言内容:\n{text}"
    )


def summarize_thread(
    client,
    meeting: dict,
    thread_info: dict,
    speeches: List[dict],
    model: str = "claude-sonnet-4-20250514",
) -> List[dict]:
    """Summarize all speeches in a thread.

    Args:
        client: anthropic.Anthropic client instance
        meeting: meeting dict (for house/committee context)
        thread_info: dict with topic, topicTag, etc. from grouper
        speeches: list of raw speech dicts belonging to this thread

    Returns:
        List of processed speech dicts with tension, keywords, quote, summaries
    """
    if not speeches:
        return []

    formatted = "\n\n---\n\n".join(_format_speech_for_summary(s) for s in speeches)

    prompt = SUMMARY_PROMPT.format(
        house=meeting.get("house", ""),
        meeting=meeting.get("meeting", ""),
        topic=thread_info.get("topic", ""),
        speeches=formatted,
    )

    log.info(
        "Summarizing thread '%s' (%d speeches)",
        thread_info.get("topic", "?"), len(speeches),
    )

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=SUMMARY_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    result = _parse_json_response(response.content[0].text)
    return result.get("speeches", [])
