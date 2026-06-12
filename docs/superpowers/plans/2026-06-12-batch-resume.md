# Cross-Run Batch Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist each summary batch's id + grouping manifest to a committed sidecar so a timed-out Anthropic batch resumes on the next daily run instead of being cancelled and lost.

**Architecture:** A new pure module `scripts/pipeline/batch_state.py` owns the sidecar format (manifest + `attempts[]` + per-thread `input_hash`). `summarize.py` writes a sidecar at submit time (early-committed in CI), polls within a budget, and on a later run re-fetches raw + assembles from the manifest *without re-grouping* (custom_id is positional, so re-grouping risks silent corruption). The workflow gains a pre-collect step, a `concurrency` lock, break-on-pending, and a stuck-batch alert.

**Tech Stack:** Python 3.12, Anthropic Message Batches API, pytest (new), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-06-12-batch-resume-design.md`

---

## File Structure

- **Create** `scripts/pipeline/batch_state.py` — sidecar load/save/delete, manifest build, `input_hash`, `attempts[]` retry state machine, age/stuck helpers. Pure functions (no Anthropic client, no network), fully unit-testable.
- **Create** `scripts/tests/__init__.py`, `scripts/tests/conftest.py` (fake Anthropic client + fixtures), `scripts/tests/test_batch_state.py`, `scripts/tests/test_resume.py`, `scripts/tests/test_poll.py`.
- **Create** `scripts/tests/pytest.ini` (or rely on repo-root config) — minimal pytest config.
- **Modify** `scripts/pipeline/summarizer.py` — `poll_summary_batch` returns the final batch object on ended OR budget-exhaustion; no cancel, no raise.
- **Modify** `scripts/summarize.py` — manifest-aware submit, `assemble_from_manifest`, collect/resume path, `--collect-pending` CLI mode, `--batch-budget`, sidecar early-commit gate, HAS_PENDING via sidecar presence.
- **Modify** `.github/workflows/daily-batch.yml` — `concurrency`, pre-collect step, break-on-pending submit loop, `data/pending-batches/` in `git add`, stuck-batch alert job with `issues: write`.
- **Modify** `.github/workflows/ci.yml` — add a pytest step.
- **Modify** `README.md` — monitoring section note.

Sidecars live in `data/pending-batches/{date}.json` — **outside** `data/threads/`, because `validate-data.mjs:54-56` and `generate-sitemap.mjs:93-95` spread every `data/threads/*.json` as an array and would `TypeError` on an object.

---

## Task 1: pytest scaffolding + fake Anthropic client

**Files:**
- Create: `scripts/tests/__init__.py`
- Create: `scripts/tests/conftest.py`
- Create: `scripts/pytest.ini`

- [ ] **Step 1: Create the package marker**

Create `scripts/tests/__init__.py` (empty file).

- [ ] **Step 2: Create pytest config**

Create `scripts/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_functions = test_*
addopts = -q
```

- [ ] **Step 3: Create the fake Anthropic client and fixtures**

Create `scripts/tests/conftest.py`:

```python
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
        return list(self.results_by_id.get(batch_id, []))

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)


class FakeClient:
    def __init__(self):
        self.messages = types.SimpleNamespace(batches=FakeBatches())


@pytest.fixture
def fake_client():
    return FakeClient()
```

- [ ] **Step 4: Verify pytest discovers the (empty) suite**

Run: `cd scripts && python -m pytest -q`
Expected: `no tests ran` (exit 5) or `0 passed` — confirms config + import path work with no collection errors.

- [ ] **Step 5: Commit**

```bash
git add scripts/tests/__init__.py scripts/tests/conftest.py scripts/pytest.ini
git commit -m "test: add pytest scaffolding and fake Anthropic batches client"
```

---

## Task 2: `batch_state.py` — canonical hash + sidecar new/load/save/delete

**Files:**
- Create: `scripts/pipeline/batch_state.py`
- Test: `scripts/tests/test_batch_state.py`

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_batch_state.py`:

```python
import json
import os

from pipeline import batch_state as bs


def test_canonical_json_is_order_insensitive():
    a = bs.canonical_json({"b": 1, "a": [3, 2]})
    b = bs.canonical_json({"a": [3, 2], "b": 1})
    assert a == b


def test_compute_input_hash_stable_and_prefixed():
    params = {"model": "m", "max_tokens": 8192, "system": "S",
              "messages": [{"role": "user", "content": "x"}]}
    h1 = bs.compute_input_hash(params)
    h2 = bs.compute_input_hash(dict(reversed(list(params.items()))))
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_compute_input_hash_changes_with_prompt():
    p1 = {"system": "A", "messages": []}
    p2 = {"system": "B", "messages": []}
    assert bs.compute_input_hash(p1) != bs.compute_input_hash(p2)


def test_new_sidecar_shape():
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    assert sc["schema_version"] == 1
    assert sc["date"] == "2026-05-14"
    assert sc["model"] == "claude-x"
    assert sc["retry_count"] == 0
    assert sc["attempts"] == []
    assert sc["meetings"] == []


def test_save_load_delete_roundtrip(tmp_path):
    path = str(tmp_path / "2026-05-14.json")
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    bs.save_sidecar(path, sc)
    assert os.path.exists(path)
    loaded = bs.load_sidecar(path)
    assert loaded == sc
    bs.delete_sidecar(path)
    assert not os.path.exists(path)
    assert bs.load_sidecar(path) is None


def test_sidecar_path_lives_outside_threads():
    p = bs.sidecar_path("2026-05-14")
    assert p == os.path.join("data", "pending-batches", "2026-05-14.json")
    assert "threads" not in p
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scripts && python -m pytest tests/test_batch_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.batch_state'`.

- [ ] **Step 3: Write the minimal implementation**

Create `scripts/pipeline/batch_state.py`:

```python
"""Persistence for in-flight summary batches (cross-run resume).

A sidecar at ``data/pending-batches/{date}.json`` records the batch id(s) and a
grouping *manifest* for one date, so a timed-out batch can be collected on a
later run without re-running the (non-deterministic-enough) grouping step.

This module is pure: no Anthropic client, no network. It is the unit-testable
core of the resume feature.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

PENDING_DIR = os.path.join("data", "pending-batches")
SCHEMA_VERSION = 1

# Terminal Anthropic batch statuses that are NOT a successful collection.
TERMINAL_FAILURES = {"canceled", "expired"}


def sidecar_path(date_str: str, pending_dir: str = PENDING_DIR) -> str:
    return os.path.join(pending_dir, f"{date_str}.json")


def canonical_json(obj) -> str:
    """Deterministic JSON: sorted keys, compact separators, no ASCII escaping."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_input_hash(params: dict) -> str:
    """SHA256 of the canonical JSON of a batch request's ``params`` block.

    Hashing the whole params (model, max_tokens, system, messages) ties a stored
    result to the exact input + prompt that produced it, so a resume cannot
    assemble a stale result against changed raw data or a changed prompt.
    """
    digest = hashlib.sha256(canonical_json(params).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def new_sidecar(date_str: str, model: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "date": date_str,
        "model": model,
        "retry_count": 0,
        "attempts": [],
        "meetings": [],
    }


def load_sidecar(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_sidecar(path: str, sidecar: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, ensure_ascii=False, indent=2)


def delete_sidecar(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scripts && python -m pytest tests/test_batch_state.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/pipeline/batch_state.py scripts/tests/test_batch_state.py
git commit -m "feat(pipeline): add batch_state sidecar core (hash, new/load/save/delete)"
```

---

## Task 3: `batch_state.py` — attempts[] + retry state machine + age

**Files:**
- Modify: `scripts/pipeline/batch_state.py`
- Test: `scripts/tests/test_batch_state.py`

- [ ] **Step 1: Write the failing tests (append to test file)**

Append to `scripts/tests/test_batch_state.py`:

```python
def test_add_attempt_and_current_batch_id():
    sc = bs.new_sidecar("2026-05-14", "m")
    bs.add_attempt(sc, "msgbatch_A", "2026-06-11T21:50:00Z")
    assert bs.current_batch_id(sc) == "msgbatch_A"
    assert sc["attempts"][-1]["terminal_status"] is None


def test_record_terminal_increments_once_per_transition():
    sc = bs.new_sidecar("2026-05-14", "m")
    bs.add_attempt(sc, "msgbatch_A", "2026-06-11T21:50:00Z")
    # First observation of a failure transitions null -> expired: count.
    assert bs.record_terminal(sc, "expired", "2026-06-11T23:20:00Z") is True
    assert sc["retry_count"] == 1
    # Re-observing the same terminal on the same attempt must NOT double count.
    assert bs.record_terminal(sc, "expired", "2026-06-12T00:00:00Z") is False
    assert sc["retry_count"] == 1


def test_record_terminal_three_failures_across_attempts():
    sc = bs.new_sidecar("2026-05-14", "m")
    for i in range(3):
        bs.add_attempt(sc, f"msgbatch_{i}", "2026-06-11T21:50:00Z")
        assert bs.record_terminal(sc, "expired", "2026-06-11T23:20:00Z") is True
    assert sc["retry_count"] == 3
    assert bs.should_hard_fail(sc) is True


def test_should_hard_fail_below_threshold():
    sc = bs.new_sidecar("2026-05-14", "m")
    bs.add_attempt(sc, "msgbatch_A", "2026-06-11T21:50:00Z")
    bs.record_terminal(sc, "canceled", "2026-06-11T23:20:00Z")
    assert bs.should_hard_fail(sc) is False


def test_age_days_from_last_attempt():
    sc = bs.new_sidecar("2026-05-14", "m")
    bs.add_attempt(sc, "msgbatch_A", "2026-06-10T00:00:00Z")
    assert bs.age_days(sc, "2026-06-12T00:00:00Z") == 2.0


def test_is_stuck_uses_threshold():
    sc = bs.new_sidecar("2026-05-14", "m")
    bs.add_attempt(sc, "msgbatch_A", "2026-06-10T00:00:00Z")
    assert bs.is_stuck(sc, "2026-06-12T01:00:00Z") is True   # >2d
    assert bs.is_stuck(sc, "2026-06-11T00:00:00Z") is False  # 1d
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scripts && python -m pytest tests/test_batch_state.py -v -k "attempt or terminal or hard_fail or age or stuck"`
Expected: FAIL with `AttributeError: module 'pipeline.batch_state' has no attribute 'add_attempt'`.

- [ ] **Step 3: Implement (append to `batch_state.py`)**

Append to `scripts/pipeline/batch_state.py`:

```python
from datetime import datetime, timezone

HARD_FAIL_RETRIES = 3
STUCK_AGE_DAYS = 2.0


def add_attempt(sidecar: dict, batch_id: str, submitted_at: str) -> None:
    sidecar["attempts"].append({
        "batch_id": batch_id,
        "submitted_at": submitted_at,
        "terminal_status": None,
        "terminal_at": None,
    })


def current_batch_id(sidecar: dict) -> Optional[str]:
    if not sidecar["attempts"]:
        return None
    return sidecar["attempts"][-1]["batch_id"]


def record_terminal(sidecar: dict, status: str, at: str) -> bool:
    """Record a terminal failure on the current attempt.

    Increments ``retry_count`` only on the null -> failure transition so the
    same terminal state observed across multiple runs is not double counted.
    Returns True if it newly transitioned (and counted), else False.
    """
    if not sidecar["attempts"]:
        return False
    attempt = sidecar["attempts"][-1]
    if attempt["terminal_status"] is not None:
        return False
    attempt["terminal_status"] = status
    attempt["terminal_at"] = at
    sidecar["retry_count"] += 1
    return True


def should_hard_fail(sidecar: dict) -> bool:
    return sidecar["retry_count"] >= HARD_FAIL_RETRIES


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_days(sidecar: dict, now_iso: str) -> float:
    submitted = sidecar["attempts"][-1]["submitted_at"]
    delta = _parse_iso(now_iso) - _parse_iso(submitted)
    return delta.total_seconds() / 86400.0


def is_stuck(sidecar: dict, now_iso: str, threshold_days: float = STUCK_AGE_DAYS) -> bool:
    return age_days(sidecar, now_iso) > threshold_days
```

- [ ] **Step 4: Run to verify pass**

Run: `cd scripts && python -m pytest tests/test_batch_state.py -v`
Expected: PASS (all batch_state tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/pipeline/batch_state.py scripts/tests/test_batch_state.py
git commit -m "feat(pipeline): add attempts[] retry state machine and stuck detection"
```

---

## Task 4: `poll_summary_batch` returns status instead of cancel+raise

**Files:**
- Modify: `scripts/pipeline/summarizer.py:189-237`
- Test: `scripts/tests/test_poll.py`

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_poll.py`:

```python
from pipeline.summarizer import poll_summary_batch


def test_poll_returns_ended_batch(fake_client):
    b = fake_client.messages.batches
    b.statuses["msgbatch_X"] = "ended"
    batch = poll_summary_batch(fake_client, "msgbatch_X",
                               timeout_seconds=5, poll_interval_seconds=0)
    assert batch.processing_status == "ended"
    assert b.cancelled == []  # never cancels


def test_poll_returns_pending_on_budget_exhaustion(fake_client):
    b = fake_client.messages.batches
    b.statuses["msgbatch_Y"] = "in_progress"
    batch = poll_summary_batch(fake_client, "msgbatch_Y",
                               timeout_seconds=0, poll_interval_seconds=0)
    assert batch.processing_status == "in_progress"
    assert b.cancelled == []  # no cancel — the batch is resumed next run
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scripts && python -m pytest tests/test_poll.py -v`
Expected: FAIL — `test_poll_returns_pending_on_budget_exhaustion` raises `TimeoutError` (current behavior cancels + raises).

- [ ] **Step 3: Replace the poll implementation**

In `scripts/pipeline/summarizer.py`, replace the body of `poll_summary_batch` (lines 189-237) with:

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd scripts && python -m pytest tests/test_poll.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/pipeline/summarizer.py scripts/tests/test_poll.py
git commit -m "refactor(pipeline): poll_summary_batch returns status instead of cancel+raise"
```

---

## Task 5: Manifest builder in `summarize.py`

Builds the sidecar `meetings[]` from the `prepared_meetings` that `run_batch_phase` already computes, capturing full `thread_info` + `input_hash` per thread.

**Files:**
- Modify: `scripts/summarize.py` (add `build_manifest_meetings`, import `batch_state` + `build_summary_request` already imported)
- Test: `scripts/tests/test_resume.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_resume.py`:

```python
from pipeline import batch_state as bs
import summarize


def _prep(meeting_id, threads):
    """Mimic prepare_meeting_for_batch's output shape."""
    return {
        "meeting_id": meeting_id,
        "outcome": {"result": None, "resolution": None, "status": "ongoing"},
        "pending": [
            {
                "custom_id": t["custom_id"],
                "meeting": {"house": "参議院", "meeting": "外交防衛委員会"},
                "thread_info": t["thread_info"],
                "thread_speeches": t["speeches"],
            }
            for t in threads
        ],
    }


def test_build_manifest_meetings_captures_full_thread_info_and_hash():
    threads = [{
        "custom_id": "s_abc_00",
        "thread_info": {"topic": "T", "topicTag": "tag", "topicColor": "#111",
                        "summary": "s", "speechOrders": [1, 2]},
        "speeches": [{"speechOrder": 1, "speech": "a"}, {"speechOrder": 2, "speech": "b"}],
    }]
    prepared = [_prep("M1", threads)]
    manifest = summarize.build_manifest_meetings(prepared, model="claude-x")
    assert len(manifest) == 1
    m = manifest[0]
    assert m["meeting_id"] == "M1"
    t = m["threads"][0]
    assert t["custom_id"] == "s_abc_00"
    assert t["thread_idx"] == 0
    assert t["thread_info"]["topicTag"] == "tag"   # FULL thread_info, not a subset
    assert t["speechOrders"] == [1, 2]
    assert t["input_hash"].startswith("sha256:")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scripts && python -m pytest tests/test_resume.py::test_build_manifest_meetings_captures_full_thread_info_and_hash -v`
Expected: FAIL — `AttributeError: module 'summarize' has no attribute 'build_manifest_meetings'`.

- [ ] **Step 3: Implement**

In `scripts/summarize.py`, add the `batch_state` import near the other pipeline imports (after line 41):

```python
from pipeline import batch_state as bs
```

Add this function just below `make_batch_custom_id` (after line 346):

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd scripts && python -m pytest tests/test_resume.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/summarize.py scripts/tests/test_resume.py
git commit -m "feat(summarize): build sidecar manifest with full thread_info and input_hash"
```

---

## Task 6: `assemble_from_manifest` — resume without re-grouping

Given a sidecar, re-fetched meetings keyed by id, and batch results, assemble threads using the manifest only. Verify `input_hash` and require all custom_ids present.

**Files:**
- Modify: `scripts/summarize.py` (add `assemble_from_manifest`)
- Test: `scripts/tests/test_resume.py`

- [ ] **Step 1: Write the failing tests (append)**

Append to `scripts/tests/test_resume.py`:

```python
def _sidecar_with_one_thread(input_hash):
    return {
        "schema_version": 1, "date": "2026-05-14", "model": "claude-x",
        "retry_count": 0,
        "attempts": [{"batch_id": "b1", "submitted_at": "2026-06-11T21:50:00Z",
                      "terminal_status": None, "terminal_at": None}],
        "meetings": [{
            "meeting_id": "M1",
            "outcome": {"result": None, "resolution": None, "status": "ongoing"},
            "threads": [{
                "custom_id": "s_abc_00", "thread_idx": 0,
                "thread_info": {"topic": "T", "topicTag": "tag", "topicColor": "#111",
                                "summary": "s", "speechOrders": [1]},
                "speechOrders": [1], "input_hash": input_hash,
            }],
        }],
    }


def _meeting():
    return {"meetingId": "M1", "house": "参議院", "meeting": "外交防衛委員会",
            "date": "2026-05-14", "source": "ndl",
            "speeches": [{"speechOrder": 1, "speech": "a", "speaker": "X",
                          "speakerGroup": "G", "speakerPosition": "P",
                          "speechURL": "http://x"}]}


def _correct_hash():
    m = _meeting()
    ti = {"topic": "T", "topicTag": "tag", "topicColor": "#111", "summary": "s",
          "speechOrders": [1]}
    req = summarize.build_summary_request(m, ti, [m["speeches"][0]], "s_abc_00", "claude-x")
    return bs.compute_input_hash(req["params"])


def test_assemble_from_manifest_success():
    sidecar = _sidecar_with_one_thread(_correct_hash())
    results = {"s_abc_00": {"speeches": [{"speechOrder": 1, "tension": "確認",
               "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
               "commitments": []}}
    threads, ok = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results, members={}, thread_counter=0,
    )
    assert ok is True
    assert len(threads) == 1
    assert threads[0]["topicTag"] == "tag"


def test_assemble_fails_on_hash_mismatch():
    sidecar = _sidecar_with_one_thread("sha256:deadbeef")  # wrong
    results = {"s_abc_00": {"speeches": [{"speechOrder": 1}], "commitments": []}}
    threads, ok = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results, members={}, thread_counter=0,
    )
    assert ok is False


def test_assemble_fails_on_missing_result():
    sidecar = _sidecar_with_one_thread(_correct_hash())
    threads, ok = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results={}, members={}, thread_counter=0,
    )
    assert ok is False
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scripts && python -m pytest tests/test_resume.py -v -k assemble`
Expected: FAIL — `AttributeError: ... 'assemble_from_manifest'`.

- [ ] **Step 3: Implement**

Add to `scripts/summarize.py` (below `build_manifest_meetings`):

```python
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
    ``(threads, ok)`` where ok is False if ANY thread fails verification or is
    missing — in that case the caller keeps the sidecar for retry.
    """
    model = sidecar["model"]
    date_str = sidecar["date"]
    threads: list = []

    for m in sidecar["meetings"]:
        meeting_id = m["meeting_id"]
        meeting = meetings_by_id.get(meeting_id)
        if meeting is None:
            log.error("Resume: raw missing for %s — cannot assemble", meeting_id)
            return [], False
        raw_lookup = build_speech_lookup(meeting.get("speeches", []))
        outcome = m["outcome"]
        manifest_threads = m["threads"]

        for mt in manifest_threads:
            custom_id = mt["custom_id"]
            thread_info = mt["thread_info"]
            orders = mt["speechOrders"]
            thread_speeches = [raw_lookup[o] for o in orders if o in raw_lookup]
            if len(thread_speeches) != len(orders):
                log.error("Resume: speechOrder gap in %s/%s", meeting_id, custom_id)
                return [], False

            request = build_summary_request(
                meeting, thread_info, thread_speeches, custom_id, model,
            )
            if bs.compute_input_hash(request["params"]) != mt["input_hash"]:
                log.error("Resume: input_hash mismatch for %s — raw/prompt changed",
                          custom_id)
                return [], False

            result = results.get(custom_id)
            if not result:
                log.error("Resume: missing result for %s", custom_id)
                return [], False

            thread_counter += 1
            thread_id = make_thread_id(date_str, meeting_id, thread_counter)
            thread = assemble_thread(
                meeting, thread_info, result["speeches"], raw_lookup, members,
                thread_id,
            )
            if not thread:
                log.error("Resume: assemble_thread returned None for %s", custom_id)
                return [], False

            is_last = (mt is manifest_threads[-1])
            thread["outcome"] = {
                "result": outcome.get("result") if is_last else None,
                "resolution": outcome.get("resolution") if is_last else None,
                "commitments": result["commitments"] or [],
                "status": outcome.get("status", "ongoing"),
            }
            threads.append(thread)

    return threads, True
```

- [ ] **Step 4: Run to verify pass**

Run: `cd scripts && python -m pytest tests/test_resume.py -v`
Expected: PASS (all resume tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/summarize.py scripts/tests/test_resume.py
git commit -m "feat(summarize): assemble_from_manifest resumes batches without re-grouping"
```

---

## Task 7: Submit path writes sidecar + manifest (replace cancel-on-fail behaviour)

Rework `run_batch_phase` so that after submit it persists a sidecar, polls within a budget, and on `ended` assembles + deletes the sidecar; on non-ended it leaves the sidecar (pending) and returns a pending flag. `prepare_meeting_for_batch` already returns everything needed.

**Files:**
- Modify: `scripts/summarize.py:394-492` (`run_batch_phase`) and its caller (lines 630-649)
- Test: `scripts/tests/test_resume.py`

- [ ] **Step 1: Write the failing test (append)**

Append to `scripts/tests/test_resume.py`:

```python
import os


def test_run_batch_phase_persists_sidecar_when_pending(fake_client, tmp_path, monkeypatch):
    # One meeting, grouping stubbed to a single thread; batch stays in_progress.
    monkeypatch.setattr(summarize, "group_meeting",
                        lambda c, m, model: [{"topic": "T", "topicTag": "tag",
                                              "topicColor": "#111", "summary": "s",
                                              "speechOrders": [1]}])
    monkeypatch.setattr(summarize, "extract_meeting_outcome",
                        lambda c, m, model: {"result": None, "resolution": None,
                                             "status": "ongoing"})
    pending_dir = str(tmp_path / "pending")
    meeting = _meeting()
    fake_client.messages.batches.statuses["msgbatch_fake_0001"] = "in_progress"

    new_threads, _, completed, pending = summarize.run_batch_phase(
        fake_client, [meeting], {"completed": [], "failed": []},
        members={}, model="claude-x", date_str="2026-05-14", thread_counter=0,
        batch_timeout_seconds=0, batch_poll_seconds=0,
        pending_dir=pending_dir, ci_commit=False,
    )
    assert pending is True
    assert new_threads == []
    sc = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert sc is not None
    assert bs.current_batch_id(sc) == "msgbatch_fake_0001"
    assert sc["meetings"][0]["threads"][0]["input_hash"].startswith("sha256:")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scripts && python -m pytest tests/test_resume.py::test_run_batch_phase_persists_sidecar_when_pending -v`
Expected: FAIL — `run_batch_phase()` got an unexpected keyword `pending_dir` / wrong return arity.

- [ ] **Step 3: Implement — rewrite `run_batch_phase`**

Replace `run_batch_phase` (lines 394-492) with the version below. It keeps Phase-1 prepare unchanged, then persists a sidecar before polling. New params: `pending_dir`, `ci_commit`. New return: a 4-tuple `(new_threads, thread_counter, completed_meeting_ids, pending)`.

```python
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
) -> tuple:
    """Process meetings via Batches API. Persists a sidecar so a batch that
    does not finish within the budget resumes on a later run.

    Returns ``(new_threads, thread_counter, completed_meeting_ids, pending)``.
    """
    prepared_meetings: list[dict] = []
    all_pending: list[dict] = []

    for meeting in meetings:
        meeting_id = meeting.get("meetingId", "unknown")
        if meeting_id in progress["completed"]:
            log.info("Skipping already completed: %s", meeting_id)
            continue
        log.info("Preparing for batch: %s", meeting_id)
        try:
            prep = prepare_meeting_for_batch(client, meeting, model)
        except Exception as e:
            log.error("Failed to prepare %s: %s", meeting_id, e)
            progress["failed"].append(meeting_id)
            continue
        prepared_meetings.append(prep)
        all_pending.extend(prep["pending"])

    if not all_pending:
        log.info("Batch phase: nothing to summarize")
        return [], thread_counter, [], False

    requests = [
        build_summary_request(
            p["meeting"], p["thread_info"], p["thread_speeches"],
            p["custom_id"], model,
        )
        for p in all_pending
    ]
    log.info("Submitting %d summary requests via Batches API", len(requests))
    batch_id = submit_summary_batch(client, requests)

    # Persist the sidecar BEFORE the long poll so a kill mid-poll still resumes.
    submitted_at = _utcnow_iso()
    sidecar = bs.new_sidecar(date_str, model)
    sidecar["meetings"] = build_manifest_meetings(prepared_meetings, model)
    bs.add_attempt(sidecar, batch_id, submitted_at)
    path = bs.sidecar_path(date_str, pending_dir)
    bs.save_sidecar(path, sidecar)
    if ci_commit:
        _git_commit_sidecar(path, date_str)

    batch = poll_summary_batch(
        client, batch_id,
        timeout_seconds=batch_timeout_seconds,
        poll_interval_seconds=batch_poll_seconds,
    )
    if batch.processing_status != "ended":
        log.info("Batch %s not ended within budget — sidecar kept for resume", batch_id)
        return [], thread_counter, [], True

    results = fetch_summary_results(client, batch_id)
    meetings_by_id = {m.get("meetingId", "unknown"): m for m in meetings}
    new_threads, ok = assemble_from_manifest(
        sidecar, meetings_by_id, results, members, thread_counter,
    )
    if not ok:
        log.error("Batch %s ended but assembly incomplete — keeping sidecar", batch_id)
        return [], thread_counter, [], True

    thread_counter += len(new_threads)
    completed_meeting_ids = [m["meeting_id"] for m in sidecar["meetings"]]
    bs.delete_sidecar(path)
    return new_threads, thread_counter, completed_meeting_ids, False
```

Add two small helpers near the top of `summarize.py` (after the imports, e.g. below line 45):

```python
import subprocess
from datetime import datetime, timezone


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_commit_sidecar(path: str, date_str: str) -> None:
    """Commit + push just the sidecar (CI only) so the in-flight batch survives
    a later kill or set -e failure before the run's final commit."""
    try:
        subprocess.run(["git", "add", path], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore(pipeline): persist pending batch {date_str}"],
            check=True,
        )
        subprocess.run(["git", "push"], check=True)
        log.info("Early-committed sidecar %s", path)
    except subprocess.CalledProcessError as e:
        log.warning("Early sidecar commit failed (%s) — relying on final commit", e)
```

- [ ] **Step 4: Update the caller in `run_pipeline`**

Replace the `if batch:` block (lines 630-649) with:

```python
    pending = False
    if batch:
        new_threads, thread_counter, completed_ids, pending = run_batch_phase(
            client, meetings, progress, members, model, date_str,
            thread_counter,
            batch_timeout_seconds=batch_timeout_seconds,
            batch_poll_seconds=batch_poll_seconds,
            pending_dir=pending_dir,
            ci_commit=ci_commit,
        )
        all_threads.extend(new_threads)
        for mid in completed_ids:
            if mid not in progress["completed"]:
                progress["completed"].append(mid)
        save_progress(progress, progress_path)
        log.info("Batch phase: +%d threads from %d meeting(s)%s",
                 len(new_threads), len(completed_ids),
                 " (batch pending — will resume)" if pending else "")
```

Add `pending_dir` and `ci_commit` to `run_pipeline`'s signature (line 525-538) with defaults `pending_dir: str = bs.PENDING_DIR` and `ci_commit: bool = False`, and thread them from `main()` (see Task 9). The early-resume threads-load block already handles appending.

- [ ] **Step 5: Run to verify pass**

Run: `cd scripts && python -m pytest tests/ -v`
Expected: PASS (all tests, including the new sidecar-persist test).

- [ ] **Step 6: Commit**

```bash
git add scripts/summarize.py scripts/tests/test_resume.py
git commit -m "feat(summarize): persist sidecar at submit, resume-or-keep on poll result"
```

---

## Task 8: Collect/resume path + `--collect-pending` CLI

A new path that scans `pending_dir`, retrieves each sidecar's current batch, and assembles ended ones (fetching that date's raw + writing into `{date}.json`). Hard-fails when any sidecar has `retry_count >= 3`.

**Files:**
- Modify: `scripts/summarize.py` (add `collect_pending_batches`, `_load_meetings_for_date`, CLI flag)
- Test: `scripts/tests/test_resume.py`

- [ ] **Step 1: Write the failing test (append)**

Append to `scripts/tests/test_resume.py`:

```python
def test_collect_assembles_ended_and_deletes_sidecar(fake_client, tmp_path, monkeypatch):
    pending_dir = str(tmp_path / "pending")
    threads_dir = str(tmp_path / "threads")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    # Write raw for the date so collect can re-fetch from disk.
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)
    # Sidecar with a correct hash, batch now ended with a result.
    sidecar = _sidecar_with_one_thread(_correct_hash())
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    from tests.conftest import _ResultEntry  # type: ignore
    import json as J
    b.results_by_id["b1"] = [_ResultEntry("s_abc_00", "succeeded",
        text=J.dumps({"speeches": [{"speechOrder": 1, "tension": "確認",
        "summaries": {"easy": "e", "teen": "t", "adult": "a"}}], "commitments": []}))]

    hard_fail = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=threads_dir, raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert hard_fail is False
    assert not os.path.exists(os.path.join(pending_dir, "2026-05-14.json"))
    assert os.path.exists(os.path.join(threads_dir, "2026-05-14.json"))


def test_collect_hard_fails_at_retry_threshold(fake_client, tmp_path):
    pending_dir = str(tmp_path / "pending")
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["retry_count"] = 3
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    hard_fail = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"),
        raw_dir=str(tmp_path / "r"),
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert hard_fail is True
```

- [ ] **Step 2: Run to verify failure**

Run: `cd scripts && python -m pytest tests/test_resume.py -v -k collect`
Expected: FAIL — `AttributeError: ... 'collect_pending_batches'`.

- [ ] **Step 3: Implement**

Add to `scripts/summarize.py`:

```python
def _load_meetings_for_date(date_str: str, raw_dir: str) -> Dict[str, dict]:
    """Re-load all meetings for a date from raw files, keyed by meetingId.

    Mirrors run_pipeline's candidate-file scan so resume sees the same raw.
    """
    import glob as _glob
    candidates = [
        os.path.join(raw_dir, f"ndl-{date_str}.json"),
        os.path.join(raw_dir, f"kantei-{date_str}.json"),
        os.path.join(raw_dir, f"council-{date_str}.json"),
        *sorted(_glob.glob(os.path.join(raw_dir, f"council-*-{date_str}.json"))),
        os.path.join(raw_dir, f"{date_str}.json"),
    ]
    by_id: Dict[str, dict] = {}
    for c in candidates:
        if os.path.exists(c):
            with open(c, "r", encoding="utf-8") as f:
                data = json.load(f)
            for m in data.get("meetings", []):
                by_id[m.get("meetingId", "unknown")] = m
    return by_id


def _append_threads_to_date_file(threads: list, threads_dir: str, date_str: str) -> None:
    os.makedirs(threads_dir, exist_ok=True)
    path = os.path.join(threads_dir, f"{date_str}.json")
    existing = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    existing.extend(threads)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


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
) -> bool:
    """Resume all in-flight batches. Returns True if any sidecar hit the
    hard-fail retry threshold (caller should exit non-zero)."""
    import glob as _glob
    hard_fail = False
    paths = sorted(_glob.glob(os.path.join(pending_dir, "*.json")))
    deadline = time.time() + budget_seconds

    for path in paths:
        sidecar = bs.load_sidecar(path)
        if sidecar is None:
            continue
        if bs.should_hard_fail(sidecar):
            log.error("Sidecar %s exceeded retry threshold (%d) — hard fail",
                      path, sidecar["retry_count"])
            hard_fail = True
            continue

        date_str = sidecar["date"]
        batch_id = bs.current_batch_id(sidecar)
        remaining = max(0, int(deadline - time.time()))
        batch = poll_summary_batch(client, batch_id,
                                   timeout_seconds=remaining, poll_interval_seconds=poll_seconds)

        if batch.processing_status != "ended":
            if batch.processing_status in bs.TERMINAL_FAILURES:
                if bs.record_terminal(sidecar, batch.processing_status, _utcnow_iso()):
                    bs.save_sidecar(path, sidecar)
                    if ci_commit:
                        _git_commit_sidecar(path, date_str)
                log.warning("Batch %s %s — will re-submit next run", batch_id,
                            batch.processing_status)
            else:
                log.info("Batch %s still %s — leaving for next run", batch_id,
                         batch.processing_status)
            continue

        results = fetch_summary_results(client, batch_id)
        meetings_by_id = _load_meetings_for_date(date_str, raw_dir)
        if not meetings_by_id:
            log.error("Resume: no raw for %s (outside window?) — keeping sidecar",
                      date_str)
            continue
        threads, ok = assemble_from_manifest(
            sidecar, meetings_by_id, results, members, thread_counter=0,
        )
        if not ok:
            if bs.record_terminal(sidecar, "assemble_failed", _utcnow_iso()):
                bs.save_sidecar(path, sidecar)
            log.error("Resume: assembly incomplete for %s — keeping sidecar", date_str)
            continue

        _append_threads_to_date_file(threads, threads_dir, date_str)
        bs.delete_sidecar(path)
        log.info("Resume: collected %d threads for %s", len(threads), date_str)

    return hard_fail
```

Note: `assemble_from_manifest` uses `thread_counter` only for id suffixing; collect passes 0 and `_append_threads_to_date_file` preserves existing threads. Thread-id collisions are avoided because `make_thread_id` hashes the meeting_id and the per-meeting index space is small; if a collision is a concern, seed `thread_counter` from the existing file length — out of scope here (YAGNI; ids include the meeting hash).

- [ ] **Step 4: Run to verify pass**

Run: `cd scripts && python -m pytest tests/ -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/summarize.py scripts/tests/test_resume.py
git commit -m "feat(summarize): collect_pending_batches resumes/retries in-flight batches"
```

---

## Task 9: Wire CLI flags + `run_pipeline` signature

**Files:**
- Modify: `scripts/summarize.py` (`run_pipeline` signature, `parse_args`, `main`)

- [ ] **Step 1: Extend `run_pipeline` signature**

In `run_pipeline` (line 525-538) add params: `pending_dir: str = bs.PENDING_DIR`, `ci_commit: bool = False`. (Defaults keep existing callers/tests working.)

- [ ] **Step 2: Add CLI flags in `parse_args`** (after line 760)

```python
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
```

Also change `--batch-timeout` default from `5400` to `1800` to match the new budget.

- [ ] **Step 3: Branch in `main`** (replace the `run_pipeline(...)` call, line 773-786)

```python
    client_kwargs = {}
    if args.collect_pending:
        import anthropic as _anthropic
        client = _anthropic.Anthropic()
        members = load_members(args.members_path)
        hard_fail = collect_pending_batches(
            client, members, args.model,
            pending_dir=args.pending_dir, threads_dir=args.output_dir,
            raw_dir=args.raw_dir, budget_seconds=args.batch_budget,
            poll_seconds=args.batch_poll, ci_commit=args.ci_commit,
        )
        save_members(members, args.members_path)
        if hard_fail:
            sys.exit(1)
        return

    run_pipeline(
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
```

- [ ] **Step 4: Run the full suite + a dry smoke**

Run: `cd scripts && python -m pytest tests/ -v && python summarize.py --collect-pending --pending-dir /tmp/empty_pending`
Expected: tests PASS; the collect smoke logs nothing to do and exits 0 (no sidecars).

- [ ] **Step 5: Commit**

```bash
git add scripts/summarize.py
git commit -m "feat(summarize): add --collect-pending/--batch-budget/--ci-commit CLI"
```

---

## Task 10: Workflow — concurrency, pre-collect, break-on-pending, git add

**Files:**
- Modify: `.github/workflows/daily-batch.yml`

- [ ] **Step 1: Add a concurrency lock** (after line 17, top-level `permissions:` block)

```yaml
concurrency:
  group: daily-batch
  cancel-in-progress: false
```

- [ ] **Step 2: Add a pre-collect step** (before "Summarize new content", after the "Detect dates" step ~line 84)

```yaml
      # ----- Resume in-flight batches from prior runs FIRST -----
      # Collects any committed sidecars (data/pending-batches/) within a shared
      # budget. Exits non-zero only when a batch has failed too many times.
      - name: Collect pending batches
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: |
          python scripts/summarize.py --collect-pending --batch-budget 1800 --ci-commit
          if ls data/pending-batches/*.json >/dev/null 2>&1; then
            echo "has_pending=true" >> "$GITHUB_OUTPUT"
          else
            echo "has_pending=false" >> "$GITHUB_OUTPUT"
          fi
        id: collect
```

- [ ] **Step 3: Guard the submit loop with break-on-pending** (replace the Summarize step body, lines 90-101)

```yaml
      - name: Summarize new content with Claude API
        if: steps.dates.outputs.list != '' && steps.collect.outputs.has_pending != 'true'
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          DATES_LIST: ${{ steps.dates.outputs.list }}
        run: |
          set -e
          for d in $DATES_LIST; do
            echo "::group::Summarize $d"
            python scripts/summarize.py --date "$d" --batch --batch-budget 1200 --ci-commit
            echo "::endgroup::"
            # Once a date leaves a pending sidecar, stop submitting new batches
            # so we never pile up multiple in-flight batches in one run.
            if [ -f "data/pending-batches/$d.json" ]; then
              echo "Batch for $d is pending — stopping further submits this run"
              break
            fi
          done
```

- [ ] **Step 4: Add `data/pending-batches/` to the commit step** (line 164)

Change the `git add` line to include the pending dir:

```yaml
          git add data/threads/ data/pending-batches/ data/members.json data/status.json \
            public/sitemap.xml public/sitemap-*.xml public/sitemap_index.xml \
            public/feed.xml
```

Add a guard just before it so `git add` does not fail when the dir is absent:

```yaml
          mkdir -p data/pending-batches
```

- [ ] **Step 5: Validate the workflow YAML**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/daily-batch.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/daily-batch.yml
git commit -m "ci(daily-batch): concurrency lock, pre-collect, break-on-pending, commit sidecars"
```

---

## Task 11: Workflow — stuck-batch alert job (`issues: write`)

A separate job (so it has `issues: write` independent of the `contents: write`-only main job) that, after the main job, checks committed sidecars older than 2 days and comments on Issue #41.

**Files:**
- Modify: `.github/workflows/daily-batch.yml`

- [ ] **Step 1: Add the alert job** (after the `notify-on-failure` job)

```yaml
  notify-stuck-batch:
    name: Notify on stuck batch
    needs: fetch-and-summarize
    if: always()
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Comment if any sidecar is older than 2 days
        env:
          GH_TOKEN: ${{ github.token }}
          GH_REPO: ${{ github.repository }}
        run: |
          STUCK=$(python scripts/check_stuck_batches.py || true)
          if [ -n "$STUCK" ]; then
            gh label create pipeline-failure --color B60205 \
              --description "Daily batch pipeline failure" 2>/dev/null || true
            EXISTING=$(gh issue list --state open --label pipeline-failure \
              --json number --jq '.[0].number' 2>/dev/null || true)
            BODY=$(printf 'Stuck summary batch(es) detected (in-flight > 2 days):\n\n%s' "$STUCK")
            if [ -n "$EXISTING" ]; then
              gh issue comment "$EXISTING" --body "$BODY"
            else
              gh issue create --title "Stuck summary batch" \
                --label pipeline-failure --body "$BODY"
            fi
          fi
```

- [ ] **Step 2: Create the checker script**

Create `scripts/check_stuck_batches.py`:

```python
#!/usr/bin/env python3
"""Print a line per sidecar whose in-flight batch is older than the stuck
threshold. Empty output means nothing is stuck. Used by daily-batch.yml."""

import glob
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import batch_state as bs  # noqa: E402


def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for path in sorted(glob.glob(os.path.join(bs.PENDING_DIR, "*.json"))):
        sc = bs.load_sidecar(path)
        if sc and sc.get("attempts") and bs.is_stuck(sc, now):
            print(f"- {sc['date']}: {bs.current_batch_id(sc)} "
                  f"(age {bs.age_days(sc, now):.1f}d, retries {sc['retry_count']})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Smoke-test the checker**

Run: `cd scripts && python check_stuck_batches.py`
Expected: no output (no sidecars locally) and exit 0.

- [ ] **Step 4: Validate YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/daily-batch.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/daily-batch.yml scripts/check_stuck_batches.py
git commit -m "ci(daily-batch): alert on summary batches stuck in-flight over 2 days"
```

---

## Task 12: CI — run pytest

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add a pytest job/step**

Add a job to `.github/workflows/ci.yml` (mirror the existing job style):

```yaml
  python-tests:
    name: Python unit tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install anthropic python-dotenv pytest
      - run: cd scripts && python -m pytest -q
```

- [ ] **Step 2: Validate YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run pipeline pytest suite"
```

---

## Task 13: Docs + memory update

**Files:**
- Modify: `README.md` (monitoring section)
- Modify: `/home/feathach/.claude/projects/-home-feathach-dev-open-gikai/memory/project_daily_batch_failures.md`

- [ ] **Step 1: Update README monitoring note**

Find the monitoring/daily-batch section in `README.md` and add a short paragraph:

> Summary batches that exceed the per-run poll budget are no longer cancelled.
> Their id + grouping manifest is persisted to `data/pending-batches/{date}.json`
> (committed) and resumed on the next run. A batch stuck in-flight for >2 days,
> or one that fails 3 runs in a row, opens/updates the `pipeline-failure` issue.

- [ ] **Step 2: Update the memory file**

In `project_daily_batch_failures.md`, update bullet 4 to note the root fix landed: batch_id is now persisted via `data/pending-batches/` sidecars with `input_hash` verification and cross-run resume; `poll_summary_batch` no longer cancels; retry_count>=3 hard-fails; stuck>2d alerts via a dedicated `issues: write` job.

- [ ] **Step 3: Final full-suite run**

Run: `cd scripts && python -m pytest -q`
Expected: PASS (all tests).

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: note cross-run batch resume in monitoring section"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** §0 manifest (Task 5/6), §1 sidecar+input_hash+attempts (Task 2/3/5), §2 early commit + concurrency + hash verify (Task 7/10/6), §3 pre-collect + break-on-pending + budget (Task 8/9/10), §4 assemble + completeness + raw-missing (Task 6/8), §5 exit 0 + sidecar-only marker (Task 8/10), §6 retry 3→hard fail + stuck alert (Task 3/8/11), §7 pytest (Task 1-12), influence files (all). 
- **Determinism / invariants:** resume never re-groups; it re-fetches raw and verifies `input_hash` before assembling, so a changed prompt or raw cannot corrupt output — it fails closed (keeps sidecar). No new state crosses into the summary content.
- **Type consistency:** `run_batch_phase` now returns a 4-tuple `(threads, counter, completed_ids, pending)` — the only caller (Task 7 Step 4) is updated to match. `assemble_from_manifest` signature is identical in Task 6 and its callers (Task 7/8).
```
