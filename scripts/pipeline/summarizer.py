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

# A thread's summary JSON grows with its speech count. 8192 truncated real
# threads (a 31-speech thread needed ~9.6k output tokens), and a truncated
# response is unparseable, which used to discard the entire batch it belonged
# to. max_tokens is a ceiling, not a sampling parameter: raising it cannot
# change a response that already fit, and unused budget is not billed.
SUMMARY_MAX_TOKENS = 16384
# Ceiling for re-issuing a request that hit SUMMARY_MAX_TOKENS anyway.
SUMMARY_RETRY_MAX_TOKENS = 32768

# The SDK refuses a NON-streaming messages.create() it thinks could outlast its
# 10-minute default read timeout, raising a bare ValueError *before sending
# anything*. In anthropic 0.72 the test has two branches (_base_client.py,
# _calculate_nonstreaming_timeout):
#   1. 3600 * max_tokens / 128_000 > 600     -> i.e. max_tokens > 21333
#   2. a per-model cap, MODEL_NONSTREAMING_TOKENS -> 8192 for the opus-4.x ids
# Branch 2 means even SUMMARY_MAX_TOKENS (16384) is refused under `--model
# claude-opus-4-1-*`, which a manual rescue run could plausibly pass. Both
# branches are skipped entirely when the call supplies its own timeout, so the
# robust rule is to ALWAYS supply one rather than to predict the thresholds.
# ValueError is not an AnthropicError; in the repair path it lands in a generic
# `except Exception`, which quietly downgrades every re-issue to "failed" and
# resubmits the whole batch — the #46 deadlock, re-entered.
# The Batches API has no such guard (it is async), so batch requests are exempt.
SDK_NONSTREAMING_TOKENS_PER_HOUR = 128_000
SDK_DEFAULT_READ_TIMEOUT = 600.0


def sync_call_kwargs(max_tokens: int) -> dict:
    """Extra kwargs every *synchronous* summary messages.create() must splat in.

    Sizes the read timeout by the SDK's own worst-case model rather than a magic
    number, and floors it at the SDK default so the ordinary ceiling keeps its
    current behavior. ``connect`` is pinned to the SDK's 5s: passing a bare float
    would replace all four httpx timeouts, turning a DNS/TCP hang into a
    multi-minute stall instead of a fast failure.
    """
    import httpx
    read = max(SDK_DEFAULT_READ_TIMEOUT,
               3600.0 * max_tokens / SDK_NONSTREAMING_TOKENS_PER_HOUR)
    return {"timeout": httpx.Timeout(read, connect=5.0)}


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


def parse_summary_text(text: str) -> dict:
    """Parse one summary response body into the shape callers store.

    Shared by the batch collector and the repair path so a re-issued request is
    normalized exactly like a batched one.
    """
    parsed = _parse_json_response(text)
    return {
        "speeches": parsed.get("speeches", []),
        "commitments": parsed.get("commitments", []),
    }


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
    model: str = "claude-sonnet-5",
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

    def _call(max_tokens: int):
        return client.messages.create(
            model=model,
            max_tokens=max_tokens,
            # Sonnet 5 enables adaptive thinking when omitted; disable it to keep
            # the full max_tokens budget for JSON output and preserve
            # deterministic, thinking-free behavior (matches the retired
            # Sonnet 4).
            thinking={"type": "disabled"},
            system=SUMMARY_SYSTEM,
            messages=messages,
            **sync_call_kwargs(max_tokens),
        )

    response = _call(SUMMARY_MAX_TOKENS)
    if response.stop_reason == "max_tokens":
        # Same escape hatch grouper.group_meeting uses: a truncated response is
        # unparseable, so retry once with a larger ceiling before failing.
        log.warning(
            "Summary for '%s' truncated at %d tokens — retrying at %d",
            thread_info.get("topic", "?"), SUMMARY_MAX_TOKENS,
            SUMMARY_RETRY_MAX_TOKENS,
        )
        response = _call(SUMMARY_RETRY_MAX_TOKENS)
        if response.stop_reason == "max_tokens":
            # Name the cause here rather than letting _parse_json_response fail
            # with a generic "could not parse JSON" — that message is what sent
            # the 2026-06-16 investigation looking at the prompt instead.
            log.error(
                "Summary for '%s' truncated again at %d tokens — dropping thread",
                thread_info.get("topic", "?"), SUMMARY_RETRY_MAX_TOKENS,
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
    model: str = "claude-sonnet-5",
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
            "max_tokens": SUMMARY_MAX_TOKENS,
            # See summarize_thread: disable Sonnet 5 adaptive thinking so the
            # batch output isn't truncated and stays deterministic.
            "thinking": {"type": "disabled"},
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
    timeout_seconds: int = 1800,
    poll_interval_seconds: int = 30,
):
    """Poll a batch until it ends or the budget elapses.

    Returns the final batch object in BOTH cases. The caller inspects
    ``batch.processing_status``: ``"ended"`` -> collect results; anything else
    -> the batch is still in flight and will be resumed on a later run (its id
    is persisted in a sidecar). We never cancel: cancelling would throw away a
    batch that simply needs more time than this run's budget.
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

        if elapsed - last_logged >= 120:
            log.info(
                "Batch %s status=%s elapsed=%ds",
                batch_id, batch.processing_status, elapsed,
            )
            last_logged = elapsed

        if elapsed >= timeout_seconds:
            log.info(
                "Batch %s still %s after %ds budget — leaving for resume",
                batch_id, batch.processing_status, elapsed,
            )
            return batch
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
            results[custom_id] = parse_summary_text(text)
            succeeded += 1
        except Exception as e:
            # Name truncation explicitly: it is the one unparseable cause that a
            # re-issue at a higher ceiling actually fixes, and "could not parse
            # JSON" alone sent past investigations looking at the prompt.
            if getattr(message, "stop_reason", None) == "max_tokens":
                log.error(
                    "Batch result %s truncated at max_tokens (%d output tokens) "
                    "— needs re-issue at a higher ceiling",
                    custom_id, getattr(message.usage, "output_tokens", -1),
                )
            else:
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
