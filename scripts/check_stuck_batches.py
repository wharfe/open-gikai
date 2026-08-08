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
            blocked = sc.get("blocked")
            if blocked:
                # Without this, a held sidecar reads as "retries 0" — an untried,
                # transient jam — when it is the opposite: deliberately not
                # retried, waiting on a person. #65/#66.
                state = (f"HELD for a human decision "
                         f"(reason {blocked.get('reason')}, since "
                         f"{blocked.get('since')}; not retried by design)")
            else:
                state = f"in flight, retries {sc['retry_count']}"
            print(f"- {sc['date']}: {bs.current_batch_id(sc)} "
                  f"(age {bs.age_days(sc, now):.1f}d, {state})")


if __name__ == "__main__":
    main()
