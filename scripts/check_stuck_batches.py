#!/usr/bin/env python3
"""Print a line per sidecar whose in-flight batch is older than the stuck
threshold. Empty output means nothing is stuck. Used by daily-batch.yml.

Dates already reported by this run's failure path are excluded via
``--exclude-dates`` (#71). That is the whole dedup mechanism: a sidecar held for
a human decision reds the run, so notify-on-failure already comments about it,
and this notifier restating it every morning for the weeks a decision can take
buried other failures' comments on the same deduped issue. Excluding by DATE
rather than by "only speak on a green morning" is deliberate — the signal only
this notifier has ("a batch has been in flight for over two days") does not red
the run, so a morning-level mute would report it zero times for as long as
anything else was broken, which is when a second failure is most likely.
"""

import argparse
import glob
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import batch_state as bs  # noqa: E402


def _line_for(path: str, now: str, excluded: set):
    """The report line for one sidecar, or None if it has nothing to say.

    Raises freely — ``main`` turns any failure into a visible degraded line for
    that one sidecar. See the comment there for why that split exists.
    """
    sc = bs.load_sidecar(path)
    if not sc:
        return None
    # Literally the same function Collect reports held/abandoned dates with.
    # Not a copy of the rule — a copy drifts, and the two only dedup while they
    # agree. It also rejects a non-string "date", which a truthiness fallback
    # would pass through into the set membership below (TypeError on a list,
    # a silently non-matching identifier on an int).
    date_str = bs.reporting_date(sc, path)
    if date_str in excluded:
        return None
    if sc.get("attempts") and bs.is_stuck(sc, now):
        blocked = sc.get("blocked")
        if isinstance(blocked, dict) and blocked:
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
            state = f"in flight, retries {sc.get('retry_count', '?')}"
        return (f"- {date_str}: {sc['attempts'][-1].get('batch_id', 'unknown')} "
                f"(age {bs.age_days(sc, now):.1f}d, {state})")
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--exclude-dates", default="",
        help="whitespace-separated dates already reported by this run's "
             "failure path (held/abandoned); they are not restated here")
    args = ap.parse_args()
    excluded = set(args.exclude_dates.split())

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for path in sorted(glob.glob(os.path.join(bs.PENDING_DIR, "*.json"))):
        try:
            line = _line_for(path, now, excluded)
        except Exception as exc:            # noqa: BLE001 — deliberate, see below
            # One hand-edited sidecar must not take the report down with it.
            # The caller runs this as `python check_stuck_batches.py || true`,
            # so an exception here does not fail the step — it produces EMPTY
            # output, which reads as "nothing is stuck". That is the silent-
            # failure shape this whole notifier exists to prevent, and it would
            # hide every OTHER stuck batch too. Degrade the broken one into a
            # line instead: it stays visible, and its neighbours still report.
            line = (f"- {os.path.basename(path)}: UNREADABLE sidecar "
                    f"({exc.__class__.__name__}: {exc}) — cannot tell whether "
                    f"its batch is stuck; needs a human")
        if line:
            print(line)


if __name__ == "__main__":
    main()
