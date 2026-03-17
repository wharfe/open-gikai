"""Kantei (Prime Minister's Office) press conference source adapter.

Fetches press conference transcripts from kantei.go.jp and produces
the common intermediate format.

NOTE: kantei.go.jp blocks automated requests with 403. This adapter
uses a browser-like User-Agent. If that stops working, consider using
a headless browser or manual download + local parsing.

URL patterns (as of 2026):
  Index:  https://www.kantei.go.jp/jp/{cabinet}/statement/{year}/index.html
  Page:   https://www.kantei.go.jp/jp/{cabinet}/statement/{year}/{MMDD}kaiken.html

Cabinet numbers: 102=石破①, 103=石破②, 104=高市①, 105=高市②
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import requests

from .base import FetchResult, RawMeeting, RawSpeech, SourceAdapter

KANTEI_BASE = "https://www.kantei.go.jp"
# Browser-like headers to avoid 403
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.5",
}
REQUEST_TIMEOUT = 30
# Current cabinet number — update when cabinet changes
DEFAULT_CABINET = "105"

log = logging.getLogger("source.kantei")


class KanteiAdapter(SourceAdapter):
    """Adapter for Prime Minister's press conferences from kantei.go.jp."""

    def __init__(self, cabinet: str = DEFAULT_CABINET):
        self.cabinet = cabinet

    @property
    def source_id(self) -> str:
        return "kantei"

    @property
    def source_label(self) -> str:
        return "首相記者会見"

    def fetch(
        self,
        date_from: str,
        date_until: str,
        verbose: bool = False,
        **kwargs,
    ) -> FetchResult:
        """Fetch press conference transcripts for the given date range.

        First discovers conference URLs from the index page, then
        fetches and parses each one.
        """
        urls = self._discover_conferences(date_from, date_until, verbose=verbose)
        log.info("Found %d press conference(s) in range %s — %s", len(urls), date_from, date_until)

        meetings: list[RawMeeting] = []
        for url, conf_date, title in urls:
            try:
                meeting = self._fetch_conference(url, conf_date, title, verbose=verbose)
                if meeting:
                    meetings.append(meeting)
            except Exception as exc:
                log.warning("Failed to fetch %s: %s", url, exc)

            time.sleep(2)  # polite rate-limiting

        total_speeches = sum(len(m.speeches) for m in meetings)
        return FetchResult(
            source=self.source_id,
            fetched_at=datetime.utcnow().isoformat() + "Z",
            date_from=date_from,
            date_until=date_until,
            total_speeches=total_speeches,
            meetings=meetings,
        )

    # ----- Discovery -----

    def _discover_conferences(
        self,
        date_from: str,
        date_until: str,
        verbose: bool = False,
    ) -> list[tuple[str, str, str]]:
        """Find press conference URLs from the index page(s).

        Returns list of (url, date_str, title) tuples for conferences
        that fall within the date range and have 'kaiken' in the URL.
        """
        # Determine which years to scan
        year_from = int(date_from[:4])
        year_until = int(date_until[:4])
        results: list[tuple[str, str, str]] = []

        for year in range(year_from, year_until + 1):
            index_url = f"{KANTEI_BASE}/jp/{self.cabinet}/statement/{year}/index.html"
            if verbose:
                log.info("Scanning index: %s", index_url)

            try:
                html = self._get(index_url)
            except Exception as exc:
                log.warning("Could not fetch index %s: %s", index_url, exc)
                continue

            # Extract links that contain 'kaiken' (press conferences)
            # Pattern: href="MMDD[suffix]kaiken.html" or href="./MMDD..."
            for match in re.finditer(
                r'href=["\']([^"\']*kaiken[^"\']*\.html)["\']', html
            ):
                href = match.group(1)
                full_url = urljoin(index_url, href)

                # Extract date from filename: {MMDD}kaiken.html or {MMDD}xxx_kaiken.html
                date_match = re.search(r"/(\d{4})(\d{2})(\d{2})", full_url)
                if not date_match:
                    # Try MMDD pattern
                    fname_match = re.search(r"/(\d{4})(?:\w*)kaiken", full_url)
                    if fname_match:
                        mmdd = fname_match.group(1)
                        conf_date = f"{year}-{mmdd[:2]}-{mmdd[2:]}"
                    else:
                        continue
                else:
                    conf_date = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"

                # Check date range
                if conf_date < date_from or conf_date > date_until:
                    continue

                # Try to extract title from surrounding text
                title = self._extract_link_title(html, match.start())
                results.append((full_url, conf_date, title))

        return results

    @staticmethod
    def _extract_link_title(html: str, link_pos: int) -> str:
        """Best-effort title extraction from HTML near a link position."""
        # Look for text within the <a> tag
        chunk = html[link_pos : link_pos + 500]
        a_match = re.search(r">([^<]+)</a>", chunk)
        if a_match:
            return a_match.group(1).strip()
        return "首相記者会見"

    # ----- Fetching & Parsing -----

    def _fetch_conference(
        self,
        url: str,
        conf_date: str,
        title: str,
        verbose: bool = False,
    ) -> Optional[RawMeeting]:
        """Fetch and parse a single press conference page."""
        if verbose:
            log.info("Fetching conference: %s (%s)", title, url)

        html = self._get(url)
        speeches = self._parse_conference(html)

        if not speeches:
            log.warning("No speeches parsed from %s", url)
            return None

        meeting_id = f"kantei_{conf_date}_{title[:20]}"
        return RawMeeting(
            meeting_id=meeting_id,
            source=self.source_id,
            house="内閣",
            meeting_name=title,
            date=conf_date,
            meeting_url=url,
            speeches=speeches,
        )

    def _parse_conference(self, html: str) -> list[RawSpeech]:
        """Parse press conference HTML into speech turns.

        Kantei press conferences typically have this structure:
        - Opening remarks by the PM (冒頭発言)
        - Q&A section with alternating reporter questions and PM answers
        - Reporter name/affiliation usually in parentheses or bold

        NOTE: This parser may need adjustment as kantei.go.jp's HTML
        structure varies. The current implementation handles common
        patterns but should be tested against actual pages.
        """
        # Remove HTML tags but preserve paragraph structure
        # Replace <p>, <br>, </div> with newlines
        text = re.sub(r"<br\s*/?>", "\n", html)
        text = re.sub(r"</p>|</div>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        speeches: list[RawSpeech] = []
        order = 1

        # Split into speaker turns
        # Common patterns:
        #   (内閣総理大臣) or 【総理】 for PM
        #   (記者) or reporter name for press
        sections = re.split(
            r"\n\s*(?=[\(（【](?:内閣総理大臣|総理|記者|幹事社|司会)[\)）】])",
            text,
        )

        for section in sections:
            section = section.strip()
            if not section or len(section) < 20:
                continue

            # Determine speaker
            speaker_match = re.match(
                r"[\(（【]([^）\)】]+)[\)）】]\s*(.*)",
                section,
                re.DOTALL,
            )
            if speaker_match:
                speaker_label = speaker_match.group(1).strip()
                body = speaker_match.group(2).strip()
            else:
                # Treat as PM remarks if no speaker tag
                speaker_label = "内閣総理大臣"
                body = section

            if not body or len(body) < 10:
                continue

            # Map speaker labels
            if "総理" in speaker_label or "大臣" in speaker_label:
                speaker = "内閣総理大臣"
                group = None
                position = "内閣総理大臣"
            else:
                speaker = speaker_label
                group = "記者"
                position = "記者"

            speeches.append(
                RawSpeech(
                    speaker=speaker,
                    speaker_group=group,
                    speaker_position=position,
                    text=body,
                    order=order,
                )
            )
            order += 1

        return speeches

    # ----- HTTP -----

    def _get(self, url: str) -> str:
        """Fetch a URL with browser-like headers."""
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
