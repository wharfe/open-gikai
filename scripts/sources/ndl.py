"""NDL (National Diet Library) source adapter.

Fetches parliamentary speech records from the NDL API and produces
the common intermediate format.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

import requests

from .base import FetchResult, RawMeeting, RawSpeech, SourceAdapter

NDL_API_URL = "https://kokkai.ndl.go.jp/api/speech"
USER_AGENT = "OpenGIKAI/1.0"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

log = logging.getLogger("source.ndl")


class NDLAdapter(SourceAdapter):
    """Adapter for the NDL Diet Records API."""

    @property
    def source_id(self) -> str:
        return "ndl"

    @property
    def source_label(self) -> str:
        return "国会会議録"

    def fetch(
        self,
        date_from: str,
        date_until: str,
        house: Optional[str] = None,
        meeting: Optional[str] = None,
        max_records: int = 100,
        verbose: bool = False,
    ) -> FetchResult:
        records = self._fetch_all(
            date_from, date_until, house, meeting, max_records, verbose
        )
        meetings = self._group_into_meetings(records)

        total_speeches = sum(len(m.speeches) for m in meetings)
        return FetchResult(
            source=self.source_id,
            fetched_at=datetime.utcnow().isoformat() + "Z",
            date_from=date_from,
            date_until=date_until,
            total_speeches=total_speeches,
            meetings=meetings,
        )

    # ----- API helpers -----

    def _fetch_page(self, params: dict, verbose: bool = False) -> dict:
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
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.HTTPError,
            ) as exc:
                if attempt == MAX_RETRIES:
                    raise
                wait = 2**attempt
                log.warning(
                    "Attempt %d failed (%s), retrying in %ds…", attempt, exc, wait
                )
                time.sleep(wait)

        raise RuntimeError("_fetch_page: exhausted retries")

    def _fetch_all(
        self,
        date_from: str,
        date_until: str,
        house: Optional[str],
        meeting: Optional[str],
        max_records: int,
        verbose: bool,
    ) -> list[dict]:
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
            data = self._fetch_page(params, verbose=verbose)

            records = data.get("speechRecord", [])
            all_records.extend(records)

            next_pos = data.get("nextRecordPosition")
            total = data.get("numberOfRecords", 0)

            if verbose:
                log.info(
                    "Fetched %d records (total so far: %d / %d)",
                    len(records),
                    len(all_records),
                    total,
                )

            if not next_pos or next_pos > total:
                break

            start = next_pos
            time.sleep(1)

        return all_records

    # ----- Grouping -----

    @staticmethod
    def _build_meeting_id(rec: dict) -> str:
        parts = [
            rec.get("nameOfHouse", ""),
            rec.get("nameOfMeeting", ""),
            rec.get("date", ""),
            rec.get("issue", ""),
        ]
        return "_".join(p for p in parts if p)

    def _group_into_meetings(self, records: list[dict]) -> list[RawMeeting]:
        buckets: dict[str, list[dict]] = defaultdict(list)
        meeting_meta: dict[str, dict] = {}

        for rec in records:
            issue_id = rec.get("issueID", "")
            buckets[issue_id].append(rec)
            if issue_id not in meeting_meta:
                meeting_meta[issue_id] = {
                    "meeting_id": self._build_meeting_id(rec),
                    "house": rec.get("nameOfHouse", ""),
                    "meeting_name": rec.get("nameOfMeeting", ""),
                    "date": rec.get("date", ""),
                    "session": rec.get("session", 0),
                    "meeting_url": rec.get("meetingURL", ""),
                    "pdf_url": rec.get("pdfURL", ""),
                }

        meetings: list[RawMeeting] = []
        for issue_id, recs in buckets.items():
            recs.sort(key=lambda r: r.get("speechOrder", 0))
            meta = meeting_meta[issue_id]
            speeches = [
                RawSpeech(
                    speaker=r.get("speaker", ""),
                    speaker_group=r.get("speakerGroup", ""),
                    speaker_position=r.get("speakerPosition", ""),
                    speaker_yomi=r.get("speakerYomi", ""),
                    text=r.get("speech", ""),
                    order=r.get("speechOrder", 0),
                    source_url=r.get("speechURL", ""),
                )
                for r in recs
            ]
            meetings.append(
                RawMeeting(
                    meeting_id=meta["meeting_id"],
                    source=self.source_id,
                    house=meta["house"],
                    meeting_name=meta["meeting_name"],
                    date=meta["date"],
                    session=meta["session"],
                    meeting_url=meta["meeting_url"],
                    pdf_url=meta["pdf_url"],
                    speeches=speeches,
                )
            )

        meetings.sort(key=lambda m: (m.date, m.house, m.meeting_name))
        return meetings
