#!/usr/bin/env python3
"""Generate data/status.json with pipeline processing statistics."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.jsonio import write_json_atomic  # noqa: E402

THREADS_DIR = "data/threads"
MEMBERS_PATH = "data/members.json"
STATUS_PATH = "data/status.json"


def main() -> None:
    status = {}
    total_threads = 0
    total_speeches = 0
    total_committees = set()

    for fname in sorted(os.listdir(THREADS_DIR)):
        if not fname.endswith(".json") or fname.endswith(".progress.json"):
            continue

        date_str = fname.replace(".json", "")
        with open(os.path.join(THREADS_DIR, fname), "r", encoding="utf-8") as f:
            threads = json.load(f)

        committees = {}
        speeches = 0
        for t in threads:
            key = f"{t['house']}{t['committee']}"
            if key not in committees:
                committees[key] = {"name": t["committee"], "house": t["house"], "status": "completed", "threads": 0}
            committees[key]["threads"] += 1
            speeches += len(t["speeches"])
            total_committees.add(key)

        total_threads += len(threads)
        total_speeches += speeches

        status[date_str] = {
            "updatedAt": datetime.utcnow().isoformat() + "Z",
            "phase": "completed",
            "committees": list(committees.values()),
            "stats": {
                "threads": len(threads),
                "speeches": speeches,
                "committees": len(committees),
            },
        }

    # Add summary
    members_count = 0
    if os.path.exists(MEMBERS_PATH):
        with open(MEMBERS_PATH, "r", encoding="utf-8") as f:
            members_count = len(json.load(f))

    status["_summary"] = {
        "totalDates": len(status) - (1 if "_summary" in status else 0),
        "totalThreads": total_threads,
        "totalSpeeches": total_speeches,
        "totalCommittees": len(total_committees),
        "totalMembers": members_count,
        "generatedAt": datetime.utcnow().isoformat() + "Z",
    }

    write_json_atomic(STATUS_PATH, status)

    print(f"Generated {STATUS_PATH}")
    print(f"  {len(status) - 1} dates, {total_threads} threads, {total_speeches} speeches, {members_count} members")


if __name__ == "__main__":
    main()
