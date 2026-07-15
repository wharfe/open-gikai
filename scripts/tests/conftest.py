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

import pytest

# Make `pipeline` importable exactly as summarize.py does.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _Batch:
    def __init__(self, batch_id, status, counts=None):
        self.id = batch_id
        self.processing_status = status
        self.request_counts = counts or {}


class _ResultEntry:
    def __init__(self, custom_id, result_type, text=None):
        self.custom_id = custom_id
        message = types.SimpleNamespace(
            content=[types.SimpleNamespace(text=text)] if text is not None else [],
            usage=None,
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


class FakeClient:
    def __init__(self):
        self.messages = types.SimpleNamespace(batches=FakeBatches())


@pytest.fixture
def fake_client():
    return FakeClient()
