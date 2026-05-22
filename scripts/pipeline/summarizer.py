"""Per-thread speech summarization via Claude API."""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional

from .prompts import SUMMARY_SYSTEM, SUMMARY_INSTRUCTIONS, SUMMARY_INPUT_TEMPLATE

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
) -> dict:
    """Summarize all speeches in a thread.

    Args:
        client: anthropic.Anthropic client instance
        meeting: meeting dict (for house/committee context)
        thread_info: dict with topic, topicTag, etc. from grouper
        speeches: list of raw speech dicts belonging to this thread

    Returns:
        Dict with "speeches" list and "commitments" list
    """
    if not speeches:
        return {"speeches": [], "commitments": []}

    formatted = "\n\n---\n\n".join(_format_speech_for_summary(s) for s in speeches)

    user_input = SUMMARY_INPUT_TEMPLATE.format(
        house=meeting.get("house", ""),
        meeting=meeting.get("meeting", ""),
        topic=thread_info.get("topic", ""),
        speeches=formatted,
    )

    # Static rules / NG-OK examples / output format are sent as a separate
    # content block with cache_control. Per-thread variable content (committee
    # + topic + speeches) lives in the second block.
    messages = [{
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": SUMMARY_INSTRUCTIONS,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": user_input},
        ],
    }]

    log.info(
        "Summarizing thread '%s' (%d speeches)",
        thread_info.get("topic", "?"), len(speeches),
    )

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=SUMMARY_SYSTEM,
        messages=messages,
    )

    _log_cache_usage(response)

    result = _parse_json_response(response.content[0].text)
    return {
        "speeches": result.get("speeches", []),
        "commitments": result.get("commitments", []),
    }


def _log_cache_usage(response) -> None:
    """Log prompt cache hit/write stats so we can verify caching works."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    if read or write:
        log.info("  cache[summary]: read=%d write=%d", read, write)
