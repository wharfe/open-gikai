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
from datetime import datetime, timezone
from typing import Any, Optional

PENDING_DIR = os.path.join("data", "pending-batches")
SCHEMA_VERSION = 1

# Terminal Anthropic batch statuses that are NOT a successful collection.
TERMINAL_FAILURES = {"canceled", "expired"}


def sidecar_path(date_str: str, pending_dir: str = PENDING_DIR) -> str:
    return os.path.join(pending_dir, f"{date_str}.json")


def canonical_json(obj: Any) -> str:
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
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_sidecar(path: str, sidecar: dict) -> None:
    dirpart = os.path.dirname(path)
    if dirpart:
        os.makedirs(dirpart, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, ensure_ascii=False, indent=2)


def delete_sidecar(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


HARD_FAIL_RETRIES = 3
STUCK_AGE_DAYS = 2.0
# Past this age a sidecar's raw input has aged out of the daily fetch window
# (default 30-day lookback; data/raw/ is gitignored so CI cannot re-fetch it)
# AND its batch results have expired (~29-day retention). Such a sidecar can
# never be assembled or resubmitted, so it is abandoned rather than kept.
# INVARIANT: this MUST stay strictly greater than the daily-batch LOOKBACK_DAYS
# window — while raw is still within that window a later run can re-fetch it and
# collect the batch, so abandoning earlier would discard recoverable threads.
# Bump this if that default lookback is ever raised above 30.
ABANDON_AGE_DAYS = 31.0


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


def is_abandonable(sidecar: dict, now_iso: str,
                   threshold_days: float = ABANDON_AGE_DAYS) -> bool:
    """A sidecar past the abandon age is structurally unrecoverable (raw gone
    from the fetch window, batch results expired) and should be deleted.

    Same age test as ``is_stuck`` but a much longer threshold: stuck is a
    2-day *alert*, abandon is a 31-day *give-up*."""
    return is_stuck(sidecar, now_iso, threshold_days)
