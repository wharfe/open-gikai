#!/usr/bin/env python3
"""Fetch parliamentary speech records from the NDL (National Diet Library) API.

Produces intermediate JSON files grouped by meeting, suitable for downstream
AI summarization.  Uses the NDL source adapter from scripts/sources/.

Usage:
    python scripts/fetch_ndl.py --date-from 2025-03-14
    python scripts/fetch_ndl.py --date-from 2025-03-14 --date-until 2025-03-15 --verbose
    python scripts/fetch_ndl.py --lookback-days 30          # last 30 days, split per meeting date

NDL transcripts are typically published days or weeks after the actual
proceeding.  Use ``--lookback-days N`` from the daily batch to re-fetch
the last N days each run so retroactively published meetings are picked up.
Multi-day fetches are automatically split into single-date raw files so the
downstream summarizer can consume them without changes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

# Allow running as `python scripts/fetch_ndl.py` from project root
sys.path.insert(0, __import__("os").path.dirname(__file__))

from sources.ndl import NDLAdapter

log = logging.getLogger("fetch_ndl")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    parser = argparse.ArgumentParser(
        description="Fetch Diet speech records from the NDL API"
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
        help="Fetch the last N days up to today. Overrides --date-from/--date-until. "
             "Recommended for daily batches because NDL publishes transcripts with a "
             "multi-day lag.",
    )
    parser.add_argument("--house", default=None, help="衆議院 or 参議院")
    parser.add_argument("--meeting", default=None, help="Committee name filter")
    parser.add_argument(
        "--max-records", type=int, default=100,
        help="Records per API request (default: 100)",
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

    log.info("Fetching speeches %s → %s", args.date_from, args.date_until)

    adapter = NDLAdapter()
    result = adapter.fetch(
        date_from=args.date_from,
        date_until=args.date_until,
        house=args.house,
        meeting=args.meeting,
        max_records=args.max_records,
        verbose=args.verbose,
    )

    log.info("Received %d speeches in %d meetings", result.total_speeches, len(result.meetings))

    # Split multi-day windows by meeting date so summarize.py can consume the
    # single-date filename convention. Single-day fetches keep the original
    # behavior for backward compatibility.
    if args.date_from != args.date_until:
        written = adapter.write_output_by_meeting_date(result, output_dir=args.output_dir)
        if not written:
            log.info("No meetings found in window %s → %s", args.date_from, args.date_until)
            return
        for meeting_date, filepath, count in written:
            log.info("  %s: %d speeches → %s", meeting_date, count, filepath)
        log.info("Wrote %d per-date raw files", len(written))
    else:
        filepath = adapter.write_output(result, output_dir=args.output_dir)
        log.info("Wrote → %s", filepath)


if __name__ == "__main__":
    main()
