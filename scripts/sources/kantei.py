"""Kantei (Prime Minister's Office) press conference source adapter.

Fetches press conference transcripts from kantei.go.jp and produces
the common intermediate format.

URL patterns (as of 2026):
  Index:  https://www.kantei.go.jp/jp/{cabinet}/statement/{year}/index.html
  Page:   https://www.kantei.go.jp/jp/{cabinet}/statement/{year}/{MMDD}kaiken.html

Cabinet numbers: 102=石破①, 103=石破②, 104=高市①, 105=高市②

HTML structure (verified 2026-03-17):
  Index page: <a href="...kaiken.html"> with <img alt="タイトル"> inside
  Conference page: Speaker turns marked by （高市総理）（記者）（内閣広報官）etc.
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
# Browser-like headers — kantei.go.jp blocks bare bot User-Agents
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

# Speaker tag pattern: （高市総理）【高市総理冒頭発言】（記者）（内閣広報官）etc.
# Matches full-width parentheses （）or brackets 【】 with known speaker labels.
_SPEAKER_TAG_RE = re.compile(
    r"[（【]("
    r"[^）】]*総理[^）】]*|"
    r"記者|"
    r"内閣広報官|"
    r"内閣官房長官|"
    r"幹事社[^）】]*|"
    r"司会[^）】]*"
    r")[）】]"
)


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
        """Fetch press conference transcripts for the given date range."""
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

        Returns list of (url, date_str, title) tuples.
        """
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

            # Extract kaiken links with their titles from <img alt="...">
            for match in re.finditer(
                r'<a[^>]+href=["\']([^"\']*kaiken[^"\']*\.html)["\'][^>]*>(.*?)</a>',
                html,
                re.DOTALL,
            ):
                href = match.group(1)
                link_body = match.group(2)
                full_url = urljoin(index_url, href)

                # Extract date from filename: /MMDDkaiken.html
                fname_match = re.search(r"/(\d{4})(?:\w*)kaiken", full_url)
                if not fname_match:
                    continue
                mmdd = fname_match.group(1)
                conf_date = f"{year}-{mmdd[:2]}-{mmdd[2:]}"

                if conf_date < date_from or conf_date > date_until:
                    continue

                # Title from <img alt="..."> inside the link
                title = self._extract_link_title(link_body)
                results.append((full_url, conf_date, title))

        return results

    @staticmethod
    def _extract_link_title(link_html: str) -> str:
        """Extract title from the HTML inside an <a> tag.

        kantei.go.jp uses <img alt="タイトル"> inside conference links.
        """
        alt_match = re.search(r'alt="([^"]+)"', link_html)
        if alt_match:
            return alt_match.group(1).strip()
        # Fallback: strip tags and use text content
        text = re.sub(r"<[^>]+>", "", link_html).strip()
        return text or "首相記者会見"

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
        speeches = self._parse_conference(html, url)

        if not speeches:
            log.warning("No speeches parsed from %s", url)
            return None

        meeting_id = f"kantei_{conf_date}"
        return RawMeeting(
            meeting_id=meeting_id,
            source=self.source_id,
            house="内閣",
            meeting_name=title,
            date=conf_date,
            meeting_url=url,
            speeches=speeches,
        )

    def _parse_conference(self, html: str, url: str = "") -> list[RawSpeech]:
        """Parse press conference HTML into speech turns.

        Verified against actual kantei.go.jp pages (2026-03-17).
        Speaker turns are marked by full-width parenthesized labels:
          （高市総理冒頭発言）（記者）（高市総理）（内閣広報官）
        """
        # Step 1: Remove <script> and <style> blocks to avoid false matches
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Step 2: Replace block-level tags with newlines, strip all HTML
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"</p>|</div>|</li>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"&[a-z]+;", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Step 3: Split by speaker tags
        speeches: list[RawSpeech] = []
        order = 1

        # Find all speaker tag positions
        tags = list(_SPEAKER_TAG_RE.finditer(text))
        if not tags:
            return []

        for i, tag_match in enumerate(tags):
            speaker_label = tag_match.group(1).strip()

            # Text runs from after this tag to the start of the next tag
            start = tag_match.end()
            end = tags[i + 1].start() if i + 1 < len(tags) else len(text)
            body = text[start:end].strip()

            # Skip very short fragments (moderator transitions etc.)
            if len(body) < 20:
                continue

            # Classify speaker
            if "総理" in speaker_label:
                speaker = "内閣総理大臣"
                group = None
                position = "内閣総理大臣"
            elif "広報官" in speaker_label or "官房長官" in speaker_label:
                speaker = speaker_label
                group = None
                position = speaker_label
            else:
                # Reporter or moderator
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
                    source_url=url,
                )
            )
            order += 1

        return speeches

    # ----- HTTP -----

    def _get(self, url: str) -> str:
        """Fetch a URL with browser-like headers."""
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return resp.text
