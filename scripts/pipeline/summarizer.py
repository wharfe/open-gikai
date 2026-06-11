"""Per-thread speech summarization via Claude API.

Provides both a synchronous path (``summarize_thread``) and an asynchronous
batched path (``build_summary_request`` + ``submit_summary_batch`` + polling
helpers). The batch path uses Anthropic's Message Batches API for a 50%
cost discount on input + output tokens, stackable with prompt caching.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

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


# ---------------------------------------------------------------------------
# Batch API: build, submit, poll, fetch
# ---------------------------------------------------------------------------

def build_summary_request(
    meeting: dict,
    thread_info: dict,
    speeches: List[dict],
    custom_id: str,
    model: str = "claude-sonnet-4-20250514",
) -> dict:
    """Build a single Message Batches API request for one thread's summary.

    Returns the request dict shape expected by client.messages.batches.create().
    """
    formatted = "\n\n---\n\n".join(_format_speech_for_summary(s) for s in speeches)

    user_input = SUMMARY_INPUT_TEMPLATE.format(
        house=meeting.get("house", ""),
        meeting=meeting.get("meeting", ""),
        topic=thread_info.get("topic", ""),
        speeches=formatted,
    )

    return {
        "custom_id": custom_id,
        "params": {
            "model": model,
            "max_tokens": 8192,
            "system": SUMMARY_SYSTEM,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": SUMMARY_INSTRUCTIONS,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": user_input},
                ],
            }],
        },
    }


def submit_summary_batch(client, requests: List[dict]) -> str:
    """Submit a batch of summary requests. Returns the batch ID."""
    log.info("Submitting batch with %d summary requests", len(requests))
    batch = client.messages.batches.create(requests=requests)
    log.info("Batch %s created (status=%s)", batch.id, batch.processing_status)
    return batch.id


def poll_summary_batch(
    client,
    batch_id: str,
    timeout_seconds: int = 5400,
    poll_interval_seconds: int = 30,
):
    """Poll a batch until it ends or the timeout elapses.

    Returns the final batch object. Raises TimeoutError on timeout.
    """
    start = time.time()
    last_logged = 0.0

    while True:
        batch = client.messages.batches.retrieve(batch_id)
        elapsed = int(time.time() - start)

        if batch.processing_status == "ended":
            log.info(
                "Batch %s ended after %ds (counts=%s)",
                batch_id, elapsed, batch.request_counts,
            )
            return batch

        # Log every ~2 minutes to avoid spamming CI logs
        if elapsed - last_logged >= 120:
            log.info(
                "Batch %s status=%s elapsed=%ds",
                batch_id, batch.processing_status, elapsed,
            )
            last_logged = elapsed

        if elapsed >= timeout_seconds:
            # The batch_id is not persisted across runs, so a timed-out batch
            # can never be collected — cancel it to stop paying for results we
            # will throw away (the next run re-submits a fresh batch anyway).
            try:
                client.messages.batches.cancel(batch_id)
                log.warning("Cancelled timed-out batch %s", batch_id)
            except Exception as cancel_err:  # noqa: BLE001 - best-effort cleanup
                log.warning(
                    "Failed to cancel timed-out batch %s: %s",
                    batch_id, cancel_err,
                )
            raise TimeoutError(
                f"Batch {batch_id} did not end within {timeout_seconds}s "
                f"(last status: {batch.processing_status})"
            )
        time.sleep(poll_interval_seconds)


def fetch_summary_results(client, batch_id: str) -> Dict[str, Optional[dict]]:
    """Fetch and parse all results from a completed batch.

    Returns a dict mapping custom_id -> parsed result dict (or None if the
    request failed or its output could not be parsed).
    """
    results: Dict[str, Optional[dict]] = {}
    succeeded = 0
    failed = 0

    for entry in client.messages.batches.results(batch_id):
        custom_id = entry.custom_id
        result = entry.result

        if result.type != "succeeded":
            log.error(
                "Batch request %s failed: type=%s",
                custom_id, result.type,
            )
            results[custom_id] = None
            failed += 1
            continue

        message = result.message
        text = message.content[0].text if message.content else ""
        try:
            parsed = _parse_json_response(text)
            results[custom_id] = {
                "speeches": parsed.get("speeches", []),
                "commitments": parsed.get("commitments", []),
            }
            succeeded += 1
        except Exception as e:
            log.error("Failed to parse batch result %s: %s", custom_id, e)
            results[custom_id] = None
            failed += 1

        # Surface cache hit info from the first few entries to confirm
        # caching is working in batch mode too.
        if succeeded <= 3:
            usage = getattr(message, "usage", None)
            if usage:
                read = getattr(usage, "cache_read_input_tokens", 0) or 0
                write = getattr(usage, "cache_creation_input_tokens", 0) or 0
                if read or write:
                    log.info(
                        "  cache[batch %s]: read=%d write=%d",
                        custom_id, read, write,
                    )

    log.info(
        "Batch %s parsed: %d succeeded, %d failed",
        batch_id, succeeded, failed,
    )
    return results
