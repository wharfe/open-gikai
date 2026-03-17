#!/usr/bin/env python3
"""Fetch parliamentary speech records from the NDL (National Diet Library) API.

Produces intermediate JSON files grouped by meeting, suitable for downstream
AI summarization.  Uses the NDL source adapter from scripts/sources/.

Usage:
    python scripts/fetch_ndl.py --date-from 2025-03-14
    python scripts/fetch_ndl.py --date-from 2025-03-14 --date-until 2025-03-15 --verbose
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
        "--date-from", default=yesterday,
        help="Start date YYYY-MM-DD (default: yesterday)",
    )
    parser.add_argument(
        "--date-until", default=None,
        help="End date YYYY-MM-DD (default: same as --date-from)",
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

    filepath = adapter.write_output(result, output_dir=args.output_dir)
    log.info("Wrote → %s", filepath)


if __name__ == "__main__":
    main()
