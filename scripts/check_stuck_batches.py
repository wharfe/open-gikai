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
                #
                # The HOLD regime deliberately does not rewrite the sidecar
                # (that is the point of not touching it), so `blocked.reason`
                # here is whatever last wrote it — possibly a BLOCKED verdict
                # from a previous week, no longer the current cause (e.g.
                # hash_mismatch last week, raw_date_missing today). Label the
                # reason with its own timestamp so a stale one reads as stale
                # instead of as today's diagnosis; mirrors the same guard in
                # _record_held_sidecar (summarize.py), which only prints
                # "held since" when the stored reason still matches.
                since = blocked.get("since")
                age_note = ""
                if since:
                    held_age_days = (
                        bs._parse_iso(now) - bs._parse_iso(since)
                    ).total_seconds() / 86400.0
                    age_note = f", {held_age_days:.1f}d ago"
                state = (f"HELD for a human decision "
                         f"(reason as of last write: {blocked.get('reason')}, "
                         f"since {since}{age_note} — may be stale if the cause "
                         f"has since changed; not retried by design)")
            else:
                state = f"in flight, retries {sc['retry_count']}"
            print(f"- {sc['date']}: {bs.current_batch_id(sc)} "
                  f"(age {bs.age_days(sc, now):.1f}d, {state})")


if __name__ == "__main__":
    main()
