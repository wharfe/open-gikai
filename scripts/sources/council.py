"""Council (審議会) meeting minutes source adapter.

Fetches meeting minutes from Japanese government council websites and
produces the common intermediate format.

Initial target: 規制改革推進会議 (Regulatory Reform Promotion Council)
URL: https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html

Meeting minutes are published as PDF files. The adapter:
  1. Scrapes the index page to discover meeting links
  2. Downloads PDF minutes
  3. Extracts text and splits by speaker turns (○発言者名 pattern)

Designed for extensibility — additional councils can be added by defining
new CouncilConfig entries.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import fitz  # PyMuPDF
import requests
from bs4 import BeautifulSoup

from .base import FetchResult, RawMeeting, RawSpeech, SourceAdapter

log = logging.getLogger("source.council")

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

# Japanese era conversion: 令和 starts in 2019
_REIWA_OFFSET = 2018  # Reiwa 1 = 2019


@dataclass
class CouncilConfig:
    """Configuration for a specific council."""

    council_id: str  # e.g. "kisei"
    council_name: str  # e.g. "規制改革推進会議"
    index_url: str
    source_label: str


# Supported councils — add new entries here to extend
COUNCILS: dict[str, CouncilConfig] = {
    "kisei": CouncilConfig(
        council_id="kisei",
        council_name="規制改革推進会議",
        index_url="https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html",
        source_label="規制改革推進会議",
    ),
}

# Speaker turn pattern: ○ at line start followed by speaker identifier.
# Handles two layout variants:
#   1. "○林議長代理\n" — name on its own line, body starts next line
#   2. "○幕内参事官 定刻となりました..." — name + body on same line
# And speaker formats:
#   - "○落合座長" (name + role)
#   - "○事務局" (group name)
#   - "○一般社団法人日本産業用無人航空機工業会（曽谷理事）" (org + person)
#   - "○総務省（翁長部長）" (ministry + person)
_SPEAKER_RE = re.compile(
    r"^○\s*"
    r"("
    # Pattern 1: Org/ministry（person role）
    r"[^（）\n]+?[（(][^）)\n]+[）)]"
    r"|"
    # Pattern 2: name + known role suffix
    r"[^\s○\n]+?(?:議長代理|議長|座長代理|座長|専門委員|委員|大臣|副大臣|政務官|室長|参事官|審議官)"
    r"|"
    # Pattern 3: bare name/label (事務局 etc.) — up to first space or EOL
    r"[^\s○\n]+"
    r")",
    re.MULTILINE,
)

# Attendance line pattern for extracting members and roles
_ATTENDEE_RE = re.compile(
    r"([^\s、，,]+?)"  # name
    r"(議長代理|議長|座長代理|座長|委員|大臣|副大臣|政務官|室長|参事官|審議官)"
)


class CouncilAdapter(SourceAdapter):
    """Adapter for Japanese government council meeting minutes."""

    def __init__(self, council: str = "kisei"):
        if council not in COUNCILS:
            raise ValueError(
                f"Unknown council '{council}'. "
                f"Available: {', '.join(COUNCILS.keys())}"
            )
        self.config = COUNCILS[council]

    @property
    def source_id(self) -> str:
        return "council"

    @property
    def source_label(self) -> str:
        return self.config.source_label

    def fetch(
        self,
        date_from: str,
        date_until: str,
        verbose: bool = False,
        **kwargs,
    ) -> FetchResult:
        """Fetch council meeting minutes for the given date range."""
        entries = self._discover_meetings(date_from, date_until, verbose=verbose)
        log.info(
            "Found %d meeting(s) with minutes in range %s — %s",
            len(entries), date_from, date_until,
        )

        meetings: list[RawMeeting] = []
        for entry in entries:
            try:
                meeting = self._fetch_meeting(entry, verbose=verbose)
                if meeting:
                    meetings.append(meeting)
            except Exception as exc:
                log.warning("Failed to fetch %s: %s", entry["pdf_url"], exc)

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

    def _discover_meetings(
        self,
        date_from: str,
        date_until: str,
        verbose: bool = False,
    ) -> list[dict]:
        """Scrape the index page to find meetings with PDF minutes.

        Returns list of dicts with keys: title, date, pdf_url, page_url.
        """
        index_url = self.config.index_url
        if verbose:
            log.info("Scanning index: %s", index_url)

        html = self._get_html(index_url)
        soup = BeautifulSoup(html, "html.parser")

        results: list[dict] = []

        # The page uses tables with columns:
        #   [回数, 開催日, 会議資料・議題, 議事録, (会議後記者会見)]
        # Some rows have rowspan causing extra cells; we scan each cell
        # to find the date and minutes link rather than relying on indices.
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue

                # Find the date cell — try each cell for a parseable date
                iso_date = None
                date_cell_idx = -1
                for idx, cell in enumerate(cells):
                    parsed = self._parse_wareki_date(cell.get_text(strip=True))
                    if parsed:
                        iso_date = parsed
                        date_cell_idx = idx
                        break

                if not iso_date:
                    continue

                if iso_date < date_from or iso_date > date_until:
                    continue

                # Agenda / title is in the cell after the date
                title_text = ""
                page_url = None
                if date_cell_idx + 1 < len(cells):
                    agenda_cell = cells[date_cell_idx + 1]
                    title_text = agenda_cell.get_text(strip=True)
                    page_link = agenda_cell.find("a", href=True)
                    page_url = (
                        urljoin(index_url, page_link["href"]) if page_link else None
                    )

                # Minutes PDF link — scan remaining cells for 'minutes' in href
                # or 議事録/議事概要 in link text
                pdf_url = None
                for cell in cells[date_cell_idx + 2:]:
                    for link in cell.find_all("a", href=True):
                        href = link["href"]
                        link_text = link.get_text(strip=True)
                        if "minutes" in href.lower() or "議事録" in link_text or "議事概要" in link_text:
                            pdf_url = urljoin(index_url, href)
                            break
                    if pdf_url:
                        break

                if not pdf_url:
                    if verbose:
                        log.debug("No minutes PDF for %s (%s)", title_text[:40], iso_date)
                    continue

                # Build a meaningful title
                title = title_text or self.config.council_name
                # Strip leading "資料" prefix that comes from the materials link
                title = re.sub(r"^資料", "", title)
                # Truncate overly long agenda text
                if len(title) > 100:
                    title = title[:97] + "…"

                results.append({
                    "title": title,
                    "date": iso_date,
                    "pdf_url": pdf_url,
                    "page_url": page_url,
                })

                if verbose:
                    log.info("  Found: %s (%s) → %s", title, iso_date, pdf_url)

        return results

    # ----- Fetching & Parsing -----

    def _fetch_meeting(
        self,
        entry: dict,
        verbose: bool = False,
    ) -> Optional[RawMeeting]:
        """Download a PDF and parse it into a RawMeeting."""
        pdf_url = entry["pdf_url"]
        if verbose:
            log.info("Fetching PDF: %s", pdf_url)

        pdf_bytes = self._get_bytes(pdf_url)
        text = self._extract_pdf_text(pdf_bytes)

        if not text.strip():
            log.warning("Empty text extracted from %s", pdf_url)
            return None

        # Parse attendees for role mapping
        role_map = self._parse_attendees(text)

        # Parse speaker turns
        speeches = self._parse_speeches(text, role_map, source_url=pdf_url)

        if not speeches:
            log.warning("No speeches parsed from %s", pdf_url)
            return None

        meeting_id = f"council_{self.config.council_id}_{entry['date']}"
        return RawMeeting(
            meeting_id=meeting_id,
            source=self.source_id,
            house="審議会",
            meeting_name=self.config.council_name,
            date=entry["date"],
            meeting_url=entry.get("page_url") or pdf_url,
            pdf_url=pdf_url,
            speeches=speeches,
        )

    @staticmethod
    def _extract_pdf_text(pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes using PyMuPDF."""
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages = []
        for page in doc:
            pages.append(page.get_text())
        doc.close()
        return "\n".join(pages)

    @staticmethod
    def _parse_attendees(text: str) -> dict[str, str]:
        """Extract a name→role mapping from the attendance section.

        Looks for the attendance block (３．出席者 or similar) and parses
        names with their roles.
        """
        role_map: dict[str, str] = {}

        # Find attendance section
        att_match = re.search(
            r"出席者[：:\s]*\n(.*?)(?:\n\d[．.）]|\n○)",
            text,
            re.DOTALL,
        )
        if not att_match:
            return role_map

        att_block = att_match.group(1)

        # Determine category from (委　員) (政　府) (事務局) markers
        current_category = ""
        for line in att_block.split("\n"):
            line = line.strip()
            if re.search(r"[（(].*委\s*員.*[）)]", line):
                current_category = "委員"
            elif re.search(r"[（(].*政\s*府.*[）)]", line):
                current_category = "政府"
            elif re.search(r"[（(].*事務局.*[）)]", line):
                current_category = "事務局"
            elif re.search(r"[（(].*関係者.*[）)]", line):
                current_category = "関係者"

            for m in _ATTENDEE_RE.finditer(line):
                name = m.group(1)
                role = m.group(2)
                # Clean up whitespace in names
                name = re.sub(r"\s+", "", name)
                role_map[name] = role

        return role_map

    @staticmethod
    def _parse_speeches(
        text: str,
        role_map: dict[str, str],
        source_url: str = "",
    ) -> list[RawSpeech]:
        """Parse speaker turns from PDF text.

        Speaker turns are marked by ○名前 at the start of a line.
        """
        speeches: list[RawSpeech] = []
        order = 1

        # Find all speaker markers
        markers = list(_SPEAKER_RE.finditer(text))
        if not markers:
            return []

        for i, marker in enumerate(markers):
            raw_speaker = marker.group(1).strip()

            # Text runs from after this marker to the start of the next
            start = marker.end()
            end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
            body = text[start:end].strip()

            # Skip very short fragments
            if len(body) < 20:
                continue

            # Clean up body text
            body = re.sub(r"\n{3,}", "\n\n", body)

            # Determine speaker name and role
            speaker, position, group = _classify_speaker(raw_speaker, role_map)

            speeches.append(
                RawSpeech(
                    speaker=speaker,
                    speaker_group=group,
                    speaker_position=position,
                    text=body,
                    order=order,
                    source_url=source_url,
                )
            )
            order += 1

        return speeches

    # ----- Date parsing -----

    @staticmethod
    def _parse_wareki_date(text: str) -> Optional[str]:
        """Parse a Japanese era date string to ISO format.

        Handles formats like:
          令和８年２月26日
          令和8年2月26日
          R8.2.26
        """
        # Normalize full-width digits to ASCII
        normalized = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

        # Pattern: 令和N年M月D日
        m = re.search(r"令和\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", normalized)
        if m:
            year = _REIWA_OFFSET + int(m.group(1))
            month = int(m.group(2))
            day = int(m.group(3))
            return f"{year}-{month:02d}-{day:02d}"

        return None

    # ----- HTTP -----

    def _get_html(self, url: str) -> str:
        """Fetch a URL and return HTML text."""
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    def _get_bytes(self, url: str) -> bytes:
        """Fetch a URL and return raw bytes (for PDF downloads)."""
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.content


def _classify_speaker(
    raw_speaker: str,
    role_map: dict[str, str],
) -> tuple[str, Optional[str], Optional[str]]:
    """Classify a speaker string into (name, position, group).

    Handles patterns like:
      林議長代理  →  (林, 議長代理, None)
      城内大臣    →  (城内, 大臣, 政府)
      事務局      →  (事務局, 事務局, 事務局)
      オリックス自動車株式会社（桧部長）  →  (桧, 部長, オリックス自動車株式会社)
    """
    # Handle external org pattern: 組織名（氏名役職）
    ext_match = re.match(r"(.+?)[（(](.+?)[）)]", raw_speaker)
    if ext_match:
        org = ext_match.group(1).strip()
        person = ext_match.group(2).strip()
        return (person, "関係者", org)

    # Handle 事務局 as a group
    if raw_speaker == "事務局":
        return ("事務局", "事務局", "事務局")

    # Try to split name + role suffix
    role_match = re.match(
        r"(.+?)(議長代理|議長|座長代理|座長|委員|大臣|副大臣|政務官|室長|参事官|審議官)$",
        raw_speaker,
    )
    if role_match:
        name = role_match.group(1).strip()
        role = role_match.group(2)
        group = "政府" if role in ("大臣", "副大臣", "政務官") else None
        return (name, role, group)

    # Look up in role map
    # Strip whitespace for matching
    clean = re.sub(r"\s+", "", raw_speaker)
    if clean in role_map:
        role = role_map[clean]
        group = "政府" if role in ("大臣", "副大臣", "政務官") else None
        return (clean, role, group)

    # Fallback: use raw name as-is
    return (raw_speaker, None, None)
