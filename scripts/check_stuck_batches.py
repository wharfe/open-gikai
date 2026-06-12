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
