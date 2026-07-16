#!/usr/bin/env python3
"""Bulk ingest pipeline: fetch NDL records for a date range and process them.

Fetches all speeches for a date range, splits by session date, and runs
batch.py for each date that doesn't already have processed threads.

Usage:
    python scripts/bulk_ingest.py --from 2025-04-01 --until 2026-03-25
    python scripts/bulk_ingest.py --from 2025-04-01 --until 2026-03-25 --fetch-only
    python scripts/bulk_ingest.py --from 2025-04-01 --until 2026-03-25 --process-only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sources.ndl import NDLAdapter
from sources.base import _meeting_to_dict

log = logging.getLogger("bulk_ingest")

RAW_DIR = "data/raw"
THREADS_DIR = "data/threads"


def existing_thread_dates() -> set[str]:
    """Return set of date strings that already have processed threads."""
    dates = set()
    if not os.path.isdir(THREADS_DIR):
        return dates
    for f in os.listdir(THREADS_DIR):
        if f.endswith(".json") and not f.endswith(".progress.json"):
            dates.add(f.replace(".json", ""))
    return dates


def existing_raw_dates() -> set[str]:
    """Return set of date strings that already have raw data."""
    dates = set()
    if not os.path.isdir(RAW_DIR):
        return dates
    for f in os.listdir(RAW_DIR):
        # Match both {date}.json and ndl-{date}.json
        if f.endswith(".json"):
            name = f.replace(".json", "")
            if name.startswith("ndl-"):
                name = name[4:]
            # Only include single-date files (YYYY-MM-DD format)
            if len(name) == 10 and name[4] == "-" and name[7] == "-":
                dates.add(name)
    return dates


def fetch_and_split(date_from: str, date_until: str) -> list[str]:
    """Fetch NDL data for range and split into per-date raw files.

    Returns list of date strings that have new raw data.
    """
    adapter = NDLAdapter()
    log.info("Fetching NDL speeches %s → %s ...", date_from, date_until)
    result = adapter.fetch(
        date_from=date_from,
        date_until=date_until,
        max_records=100,
    )
    log.info("Received %d speeches in %d meetings", result.total_speeches, len(result.meetings))

    if not result.meetings:
        log.info("No meetings found in this range")
        return []

    # Group meetings by date
    by_date: dict[str, list] = defaultdict(list)
    for meeting in result.meetings:
        by_date[meeting.date].append(meeting)

    processed = existing_thread_dates()
    os.makedirs(RAW_DIR, exist_ok=True)
    new_dates = []

    for meeting_date, meetings in sorted(by_date.items()):
        if meeting_date in processed:
            log.info("  %s: already processed (%d meetings), skipping",
                     meeting_date, len(meetings))
            continue

        raw_path = os.path.join(RAW_DIR, f"{meeting_date}.json")
        payload = {
            "metadata": {
                "source": "ndl",
                "sourceLabel": "国会会議録",
                "fetchedAt": datetime.utcnow().isoformat() + "Z",
                "dateFrom": meeting_date,
                "dateUntil": meeting_date,
                "totalSpeeches": sum(len(m.speeches) for m in meetings),
                "totalMeetings": len(meetings),
            },
            "meetings": [_meeting_to_dict(m) for m in meetings],
        }
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        log.info("  %s: %d meetings, %d speeches → %s",
                 meeting_date, len(meetings),
                 payload["metadata"]["totalSpeeches"], raw_path)
        new_dates.append(meeting_date)

    return new_dates


def process_dates(dates: list[str], model: str = "claude-sonnet-5") -> None:
    """Run batch.py for each date."""
    from batch import run as batch_run

    total = len(dates)
    for i, d in enumerate(dates, 1):
        log.info("=" * 60)
        log.info("Processing %s (%d/%d)", d, i, total)
        log.info("=" * 60)
        try:
            batch_run(date_str=d, model=model)
        except Exception as e:
            log.error("Failed to process %s: %s", d, e)
            continue


def main():
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    parser = argparse.ArgumentParser(description="Bulk ingest NDL records")
    parser.add_argument("--from", dest="date_from", required=True,
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--until", dest="date_until", default=yesterday,
                        help="End date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--fetch-only", action="store_true",
                        help="Only fetch, don't process with Claude API")
    parser.add_argument("--process-only", action="store_true",
                        help="Only process existing raw files, don't fetch")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.process_only:
        # Find raw files without corresponding thread files
        raw = existing_raw_dates()
        processed = existing_thread_dates()
        pending = sorted(d for d in raw
                         if d >= args.date_from and d <= args.date_until
                         and d not in processed)
        log.info("Found %d unprocessed dates in range", len(pending))
        if pending:
            process_dates(pending, model=args.model)
        return

    # Fetch in monthly chunks to avoid API timeouts
    from datetime import date as date_type
    start = date_type.fromisoformat(args.date_from)
    end = date_type.fromisoformat(args.date_until)

    all_new_dates = []
    current = start
    while current <= end:
        # Chunk by month
        month_end = (current.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if month_end > end:
            month_end = end

        chunk_dates = fetch_and_split(current.isoformat(), month_end.isoformat())
        all_new_dates.extend(chunk_dates)

        current = month_end + timedelta(days=1)

    log.info("")
    log.info("Fetch complete: %d new dates to process", len(all_new_dates))

    if args.fetch_only:
        log.info("Fetch-only mode. Raw files saved. Run with --process-only to process.")
        for d in all_new_dates:
            print(d)
        return

    if all_new_dates:
        process_dates(all_new_dates, model=args.model)


if __name__ == "__main__":
    main()
