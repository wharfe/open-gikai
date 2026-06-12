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
