#!/usr/bin/env python3
"""Fetch press conference transcripts from the Prime Minister's Office.

Usage:
    python scripts/fetch_kantei.py --date-from 2026-03-01
    python scripts/fetch_kantei.py --date-from 2026-03-01 --date-until 2026-03-15 --verbose
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

sys.path.insert(0, __import__("os").path.dirname(__file__))

from sources.kantei import KanteiAdapter, DEFAULT_CABINET

log = logging.getLogger("fetch_kantei")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    parser = argparse.ArgumentParser(
        description="Fetch PM press conference transcripts from kantei.go.jp"
    )
    parser.add_argument(
        "--date-from", default=None,
        help="Start date YYYY-MM-DD (default: yesterday, ignored when --lookback-days is set)",
    )
    parser.add_argument(
        "--date-until", default=None,
        help="End date YYYY-MM-DD (default: same as --date-from)",
    )
    parser.add_argument(
        "--lookback-days", type=int, default=None,
        help="Fetch the last N days up to today. Overrides --date-from/--date-until.",
    )
    parser.add_argument(
        "--cabinet", default=DEFAULT_CABINET,
        help=f"Cabinet number (default: {DEFAULT_CABINET})",
    )
    parser.add_argument(
        "--output-dir", default="data/raw", help="Output directory",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging",
    )

    args = parser.parse_args(argv)

    if args.lookback_days is not None:
        if args.lookback_days < 0:
            parser.error("--lookback-days must be >= 0")
        today = date.today()
        args.date_from = (today - timedelta(days=args.lookback_days)).isoformat()
        args.date_until = today.isoformat()
    else:
        if args.date_from is None:
            args.date_from = yesterday
        if args.date_until is None:
            args.date_until = args.date_from

    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    log.info("Fetching press conferences %s → %s (cabinet %s)",
             args.date_from, args.date_until, args.cabinet)

    adapter = KanteiAdapter(cabinet=args.cabinet)
    result = adapter.fetch(
        date_from=args.date_from,
        date_until=args.date_until,
        verbose=args.verbose,
    )

    if not result.meetings:
        log.info("No press conferences found in date range")
        return

    if args.date_from != args.date_until:
        written = adapter.write_output_by_meeting_date(result, output_dir=args.output_dir)
        for meeting_date, filepath, count in written:
            log.info("  %s: %d speeches → %s", meeting_date, count, filepath)
        log.info("Wrote %d per-date raw files", len(written))
    else:
        filepath = adapter.write_output(result, output_dir=args.output_dir)
        log.info("Wrote %d conference(s) (%d speeches) → %s",
                 len(result.meetings), result.total_speeches, filepath)


if __name__ == "__main__":
    main()
