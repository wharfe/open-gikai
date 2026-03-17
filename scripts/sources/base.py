"""Abstract base class for source adapters.

All source adapters produce a common intermediate format that the downstream
AI pipeline (summarize.py / batch.py) can consume without modification.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawSpeech:
    """A single speech/statement in a meeting or event."""

    speaker: str
    speaker_group: Optional[str] = None  # party / affiliation
    speaker_position: Optional[str] = None  # title / role
    speaker_yomi: Optional[str] = None  # reading (hiragana)
    text: str = ""
    order: int = 0
    source_url: Optional[str] = None


@dataclass
class RawMeeting:
    """A meeting or event containing speeches.

    This is the common unit that the AI pipeline processes.
    Regardless of the original source, every adapter must produce
    a list of RawMeeting instances.
    """

    meeting_id: str
    source: str  # "ndl", "kantei", "council", etc.
    house: str  # "衆議院", "参議院", "内閣", "審議会", etc.
    meeting_name: str  # committee name, "首相記者会見", etc.
    date: str  # "YYYY-MM-DD"
    session: Optional[int] = None
    meeting_url: Optional[str] = None
    pdf_url: Optional[str] = None
    speeches: list[RawSpeech] = field(default_factory=list)


@dataclass
class FetchResult:
    """Result of a source fetch operation."""

    source: str
    fetched_at: str
    date_from: str
    date_until: str
    total_speeches: int
    meetings: list[RawMeeting]


class SourceAdapter(ABC):
    """Base class for all source adapters."""

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Short identifier for this source (e.g. 'ndl', 'kantei')."""
        ...

    @property
    @abstractmethod
    def source_label(self) -> str:
        """Human-readable label (e.g. '国会会議録', '首相記者会見')."""
        ...

    @abstractmethod
    def fetch(
        self,
        date_from: str,
        date_until: str,
        **kwargs,
    ) -> FetchResult:
        """Fetch records for the given date range.

        Returns a FetchResult containing RawMeeting instances in the
        common intermediate format.
        """
        ...

    def write_output(self, result: FetchResult, output_dir: str = "data/raw") -> str:
        """Write fetch result to a JSON file. Returns the file path."""
        os.makedirs(output_dir, exist_ok=True)

        prefix = self.source_id
        if result.date_from == result.date_until:
            filename = f"{prefix}-{result.date_from}.json"
        else:
            filename = f"{prefix}-{result.date_from}_{result.date_until}.json"

        payload = {
            "metadata": {
                "source": self.source_id,
                "sourceLabel": self.source_label,
                "fetchedAt": result.fetched_at,
                "dateFrom": result.date_from,
                "dateUntil": result.date_until,
                "totalSpeeches": result.total_speeches,
                "totalMeetings": len(result.meetings),
            },
            "meetings": [_meeting_to_dict(m) for m in result.meetings],
        }

        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return filepath


def _meeting_to_dict(m: RawMeeting) -> dict:
    """Convert RawMeeting to the dict format expected by the pipeline.

    Maps from the common dataclass format to the existing pipeline's
    expected field names for backward compatibility.
    """
    return {
        "meetingId": m.meeting_id,
        "source": m.source,
        "issueID": m.meeting_id,  # pipeline uses issueID as key
        "house": m.house,
        "meeting": m.meeting_name,
        "issue": "",
        "date": m.date,
        "session": m.session or 0,
        "meetingURL": m.meeting_url or "",
        "pdfURL": m.pdf_url or "",
        "speeches": [
            {
                "speechID": f"{m.meeting_id}_{s.order}",
                "speechOrder": s.order,
                "speaker": s.speaker,
                "speakerYomi": s.speaker_yomi or "",
                "speakerGroup": s.speaker_group or "",
                "speakerPosition": s.speaker_position or "",
                "speakerRole": "",
                "speech": s.text,
                "speechURL": s.source_url or "",
            }
            for s in m.speeches
        ],
    }
