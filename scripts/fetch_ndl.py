#!/usr/bin/env python3
"""Fetch parliamentary speech records from the NDL (National Diet Library) API.

Produces intermediate JSON files grouped by meeting, suitable for downstream
AI summarization.  No AI processing is performed by this script.

Usage:
    python scripts/fetch_ndl.py --date-from 2025-03-14
    python scripts/fetch_ndl.py --date-from 2025-03-14 --date-until 2025-03-15 --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

import requests

NDL_API_URL = "https://kokkai.ndl.go.jp/api/speech"
USER_AGENT = "OpenGIKAI/1.0"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

log = logging.getLogger("fetch_ndl")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def fetch_page(params: dict, verbose: bool = False) -> dict:
    """Fetch a single page from the NDL API with retry + back-off."""
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if verbose:
                log.info("Request startRecord=%s", params.get("startRecord", 1))
            resp = requests.get(
                NDL_API_URL,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code >= 500:
                raise requests.exceptions.HTTPError(
                    f"Server error {resp.status_code}", response=resp
                )
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.HTTPError) as exc:
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            log.warning("Attempt %d failed (%s), retrying in %ds…", attempt, exc, wait)
            time.sleep(wait)

    # Unreachable, but keeps type-checkers happy
    raise RuntimeError("fetch_page: exhausted retries")


def fetch_all_speeches(
    date_from: str,
    date_until: str,
    house: str | None = None,
    meeting: str | None = None,
    max_records: int = 100,
    verbose: bool = False,
) -> list[dict]:
    """Paginate through the NDL API and return all speechRecord dicts."""
    params: dict = {
        "from": date_from,
        "until": date_until,
        "maximumRecords": max_records,
        "recordPacking": "json",
        "startRecord": 1,
    }
    if house:
        params["nameOfHouse"] = house
    if meeting:
        params["nameOfMeeting"] = meeting

    all_records: list[dict] = []
    start = 1

    while True:
        params["startRecord"] = start
        data = fetch_page(params, verbose=verbose)

        records = data.get("speechRecord", [])
        all_records.extend(records)

        next_pos = data.get("nextRecordPosition")
        total = data.get("numberOfRecords", 0)

        if verbose:
            log.info(
                "Fetched %d records (total so far: %d / %d)",
                len(records), len(all_records), total,
            )

        if not next_pos or next_pos > total:
            break

        start = next_pos
        time.sleep(1)  # polite rate-limiting

    return all_records


# ---------------------------------------------------------------------------
# Grouping & transformation
# ---------------------------------------------------------------------------

def build_meeting_id(rec: dict) -> str:
    """Build a human-readable meeting identifier."""
    parts = [
        rec.get("nameOfHouse", ""),
        rec.get("nameOfMeeting", ""),
        rec.get("date", ""),
        rec.get("issue", ""),
    ]
    return "_".join(p for p in parts if p)


def speech_from_record(rec: dict) -> dict:
    """Extract the speech-level fields we care about."""
    return {
        "speechID": rec.get("speechID", ""),
        "speechOrder": rec.get("speechOrder", 0),
        "speaker": rec.get("speaker", ""),
        "speakerYomi": rec.get("speakerYomi", ""),
        "speakerGroup": rec.get("speakerGroup", ""),
        "speakerPosition": rec.get("speakerPosition", ""),
        "speakerRole": rec.get("speakerRole", ""),
        "speech": rec.get("speech", ""),
        "speechURL": rec.get("speechURL", ""),
    }


def group_into_meetings(records: list[dict]) -> list[dict]:
    """Group flat speech records into meeting-level dicts."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    meeting_meta: dict[str, dict] = {}

    for rec in records:
        issue_id = rec.get("issueID", "")
        buckets[issue_id].append(rec)
        if issue_id not in meeting_meta:
            meeting_meta[issue_id] = {
                "meetingId": build_meeting_id(rec),
                "issueID": issue_id,
                "house": rec.get("nameOfHouse", ""),
                "meeting": rec.get("nameOfMeeting", ""),
                "issue": rec.get("issue", ""),
                "date": rec.get("date", ""),
                "session": rec.get("session", 0),
                "meetingURL": rec.get("meetingURL", ""),
                "pdfURL": rec.get("pdfURL", ""),
            }

    meetings: list[dict] = []
    for issue_id, recs in buckets.items():
        recs.sort(key=lambda r: r.get("speechOrder", 0))
        meta = meeting_meta[issue_id]
        meta["speeches"] = [speech_from_record(r) for r in recs]
        meetings.append(meta)

    # Sort meetings by date then name for deterministic output
    meetings.sort(key=lambda m: (m["date"], m["house"], m["meeting"]))
    return meetings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_output(
    meetings: list[dict],
    total_speeches: int,
    date_from: str,
    date_until: str,
    output_dir: str,
) -> str:
    """Write the grouped data to a dated JSON file. Returns the file path."""
    payload = {
        "metadata": {
            "fetchedAt": datetime.utcnow().isoformat() + "Z",
            "dateFrom": date_from,
            "dateUntil": date_until,
            "totalSpeeches": total_speeches,
            "totalMeetings": len(meetings),
        },
        "meetings": meetings,
    }

    os.makedirs(output_dir, exist_ok=True)

    filename = f"{date_from}.json" if date_from == date_until else f"{date_from}_{date_until}.json"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return filepath


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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

    log.info(
        "Fetching speeches %s → %s", args.date_from, args.date_until,
    )

    records = fetch_all_speeches(
        date_from=args.date_from,
        date_until=args.date_until,
        house=args.house,
        meeting=args.meeting,
        max_records=args.max_records,
        verbose=args.verbose,
    )

    log.info("Received %d speech records", len(records))

    meetings = group_into_meetings(records)
    filepath = write_output(
        meetings=meetings,
        total_speeches=len(records),
        date_from=args.date_from,
        date_until=args.date_until,
        output_dir=args.output_dir,
    )

    log.info(
        "Wrote %d meetings (%d speeches) → %s",
        len(meetings), len(records), filepath,
    )


if __name__ == "__main__":
    main()
