"""Shared pytest fixtures: a fake Anthropic Batches client.

The fake mimics only the surface summarize.py uses:
  client.messages.batches.create(requests=...)  -> object with .id / .processing_status
  client.messages.batches.retrieve(batch_id)    -> object with .processing_status / .request_counts
  client.messages.batches.results(batch_id)     -> iterable of result entries
It is driven entirely by attributes set in each test (no network).
"""

import sys
import os
import types

import httpx
import pytest

# Make `pipeline` importable exactly as summarize.py does.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# Sampling params. claude-sonnet-5 answers any of these with a 400
# (`temperature` is deprecated for this model) — #51, which took a whole daily
# run to zero threads. Defined here rather than in test_determinism.py so the
# fake below and the guards there cannot drift apart.
SAMPLING_PARAMS = frozenset({"temperature", "top_p", "top_k"})


def _bad_request(param: str):
    """The 400 the real API answers a sampling param with."""
    import anthropic
    return anthropic.BadRequestError(
        message=f"`{param}` is deprecated for this model.",
        response=httpx.Response(
            400,
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        ),
        body=None,
    )


class _Batch:
    def __init__(self, batch_id, status, counts=None):
        self.id = batch_id
        self.processing_status = status
        self.request_counts = counts or {}


class _ResultEntry:
    def __init__(self, custom_id, result_type, text=None, stop_reason="end_turn"):
        self.custom_id = custom_id
        message = types.SimpleNamespace(
            content=[types.SimpleNamespace(text=text)] if text is not None else [],
            usage=None,
            stop_reason=stop_reason,
        )
        self.result = types.SimpleNamespace(type=result_type, message=message)


class FakeBatches:
    def __init__(self):
        # Tests set these to script behavior.
        self.created_requests = []
        self.next_id = "msgbatch_fake_0001"
        self.statuses = {}          # batch_id -> processing_status to return
        self.results_by_id = {}     # batch_id -> list[_ResultEntry]
        self.expired_results = set()  # batch_ids whose results have expired
        self.cancelled = []

    def create(self, requests):
        # Same 400 the synchronous fake raises, applied per request. The batch
        # path is the DAILY production path, so leaving it unchecked would mean
        # the fake only fails closed on the half of the layer an operator uses
        # by hand.
        for req in requests:
            sampling = SAMPLING_PARAMS & set(req.get("params", {}))
            if sampling:
                raise _bad_request(sorted(sampling)[0])
        self.created_requests.append(requests)
        bid = self.next_id
        self.statuses.setdefault(bid, "in_progress")
        return _Batch(bid, self.statuses[bid])

    def retrieve(self, batch_id):
        return _Batch(batch_id, self.statuses.get(batch_id, "in_progress"),
                      counts={"succeeded": len(self.results_by_id.get(batch_id, []))})

    def results(self, batch_id):
        # Mirror the SDK: an "ended" batch whose results_url has expired (results
        # are only retained ~29 days) raises rather than returning an empty list.
        if batch_id in self.expired_results:
            import anthropic
            raise anthropic.AnthropicError(
                "No `results_url` for the given batch; Has it finished processing? ended"
            )
        return list(self.results_by_id.get(batch_id, []))

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)


class FakeMessages:
    """``client.messages``: the batches surface plus the synchronous ``create``
    the repair path uses to re-issue a single unusable request."""

    def __init__(self):
        self.batches = FakeBatches()
        # Tests set these to script synchronous responses.
        self.create_calls = []          # list of params dicts, in call order
        self.create_text = None         # body returned by create()
        self.create_stop_reason = "end_turn"

    def create(self, **params):
        # Reproduce the 400 that caused #51. Every synchronous call in the
        # summary layer lands here, including the repair path's
        # `messages.create(**params)` — the one site where a sampling param can
        # arrive through a splat, which no AST sweep can see. Raising here is
        # what makes that whole class fail closed instead of requiring each test
        # to remember an assertion.
        sampling = SAMPLING_PARAMS & set(params)
        if sampling:
            raise _bad_request(sorted(sampling)[0])
        # Reproduce the SDK's client-side guard, both branches: a non-streaming
        # create() that supplies no timeout raises a bare ValueError before
        # sending anything when max_tokens implies a worst case over the default
        # read timeout, OR when it exceeds the per-model non-streaming cap. A stub
        # that accepted any params let a ceiling that can never reach the network
        # ship as "30 tests passed".
        from anthropic._constants import MODEL_NONSTREAMING_TOKENS
        from pipeline.summarizer import (
            SDK_DEFAULT_READ_TIMEOUT, SDK_NONSTREAMING_TOKENS_PER_HOUR,
        )
        max_tokens = params.get("max_tokens", 0)
        cap = MODEL_NONSTREAMING_TOKENS.get(params.get("model"))
        if "timeout" not in params and not params.get("stream") and (
            3600.0 * max_tokens / SDK_NONSTREAMING_TOKENS_PER_HOUR
            > SDK_DEFAULT_READ_TIMEOUT
            or (cap is not None and max_tokens > cap)
        ):
            raise ValueError(
                "Streaming is required for operations that may take longer than "
                "10 minutes."
            )
        self.create_calls.append(params)
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text=self.create_text)]
            if self.create_text is not None else [],
            usage=types.SimpleNamespace(output_tokens=0,
                                        cache_read_input_tokens=0,
                                        cache_creation_input_tokens=0),
            stop_reason=self.create_stop_reason,
        )


class FakeClient:
    def __init__(self):
        self.messages = FakeMessages()

    def with_options(self, **_kwargs):
        """The SDK returns a reconfigured copy sharing the transport; the repair
        path uses it to disable in-SDK retries. Tests only care that the same
        recording surface is reached, so hand back self."""
        return self


@pytest.fixture
def fake_client():
    return FakeClient()
