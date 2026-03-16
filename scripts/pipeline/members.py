"""Member extraction and normalization from NDL speech records."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Dict, List, Optional


# Party name normalization: NDL会派名 -> 正式政党名
PARTY_NORMALIZE = {
    "自由民主党・無所属の会": "自由民主党",
    "立憲民主党・無所属": "立憲民主党",
    "日本維新の会・教育無償化を実現する会": "日本維新の会",
    "日本維新の会": "日本維新の会",
    "公明党": "公明党",
    "日本共産党": "日本共産党",
    "国民民主党・無所属クラブ": "国民民主党",
    "国民民主党": "国民民主党",
    "れいわ新選組": "れいわ新選組",
    "社会民主党・護憲連合": "社会民主党",
    "有志の会": "有志の会",
    "参政党": "参政党",
    "NHKから国民を守る党": "NHK党",
    "各派に属しない議員": None,
}

# Keywords indicating minister/vice-minister rank
MINISTER_KEYWORDS = ["内閣総理大臣", "総理大臣"]
CABINET_KEYWORDS = ["大臣", "長官"]
VICE_MINISTER_KEYWORDS = ["副大臣", "大臣政務官"]


def normalize_party(speaker_group: Optional[str], speaker_position: Optional[str]) -> Optional[str]:
    """Normalize NDL faction name to standard party name."""
    if not speaker_group:
        return None
    # Direct lookup
    if speaker_group in PARTY_NORMALIZE:
        return PARTY_NORMALIZE[speaker_group]
    # Partial match fallback
    for faction, party in PARTY_NORMALIZE.items():
        if faction in speaker_group or speaker_group in faction:
            return party
    return speaker_group


def detect_rank(speaker_position: Optional[str], speaker_role: Optional[str]) -> str:
    """Detect member rank from their position/role fields."""
    pos = speaker_position or ""
    role = speaker_role or ""
    combined = pos + role

    if any(kw in combined for kw in MINISTER_KEYWORDS):
        return "pm"
    if any(kw in combined for kw in VICE_MINISTER_KEYWORDS):
        return "viceminister"
    if any(kw in combined for kw in CABINET_KEYWORDS):
        return "minister"
    return "member"


def generate_member_id(speaker: str, speaker_yomi: Optional[str]) -> str:
    """Generate a stable member ID from speaker name/yomi.

    Uses romanized yomi if available, otherwise a hash of the name.
    """
    if speaker_yomi:
        # Simple kana-to-romaji for ID purposes
        romaji = _kana_to_romaji(speaker_yomi)
        if romaji:
            return romaji

    # Fallback: hash of the name
    h = hashlib.sha256(speaker.encode("utf-8")).hexdigest()[:8]
    return f"m_{h}"


def _kana_to_romaji(yomi: str) -> Optional[str]:
    """Convert hiragana reading to a simple romaji ID.

    Returns None if the input doesn't look like valid hiragana.
    """
    # Basic hiragana -> romaji mapping
    TABLE = {
        "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
        "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
        "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
        "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
        "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
        "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
        "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
        "や": "ya", "ゆ": "yu", "よ": "yo",
        "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
        "わ": "wa", "を": "wo", "ん": "n",
        "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
        "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
        "だ": "da", "ぢ": "di", "づ": "du", "で": "de", "ど": "do",
        "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
        "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
        "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
        "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
        "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
        "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
        "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
        "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
        "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
        "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
        "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
        "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
        "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
        "っ": "",  # handled specially below
        "ー": "",
        "　": "_", " ": "_",
    }

    # Clean up
    yomi = yomi.strip()
    if not yomi:
        return None

    result = []
    i = 0
    while i < len(yomi):
        # Try two-char combos first (きゃ, etc.)
        if i + 1 < len(yomi) and yomi[i:i+2] in TABLE:
            result.append(TABLE[yomi[i:i+2]])
            i += 2
        elif yomi[i] in TABLE:
            if yomi[i] == "っ" and i + 1 < len(yomi):
                # Double consonant: get next char's romaji and double first letter
                next_i = i + 1
                if next_i + 1 < len(yomi) and yomi[next_i:next_i+2] in TABLE:
                    next_romaji = TABLE[yomi[next_i:next_i+2]]
                elif yomi[next_i] in TABLE:
                    next_romaji = TABLE[yomi[next_i]]
                else:
                    next_romaji = ""
                if next_romaji:
                    result.append(next_romaji[0])
                i += 1
            else:
                result.append(TABLE[yomi[i]])
                i += 1
        else:
            # Non-hiragana character — bail out
            return None

    romaji = "".join(result)
    if not romaji or "_" not in romaji:
        # Expect at least family_given structure
        return romaji if romaji else None
    return romaji


def extract_member(speech_rec: dict) -> dict:
    """Extract a Member dict from a raw NDL speech record.

    Fields that require external data (bio, stance, district, since)
    are filled with placeholder values for Phase 1.
    """
    speaker = speech_rec.get("speaker", "")
    speaker_yomi = speech_rec.get("speakerYomi")
    speaker_group = speech_rec.get("speakerGroup")
    speaker_position = speech_rec.get("speakerPosition")
    speaker_role = speech_rec.get("speakerRole")

    member_id = generate_member_id(speaker, speaker_yomi)
    party = normalize_party(speaker_group, speaker_position)
    rank = detect_rank(speaker_position, speaker_role)

    # Determine role display string
    role = speaker_position or speaker_role or "議員"

    return {
        "id": member_id,
        "name": speaker,
        "party": party,
        "role": role,
        "district": None,
        "since": None,
        "bio": "",
        "stance": [],
        "rank": rank,
    }


def load_members(path: str) -> Dict[str, dict]:
    """Load existing members.json if it exists."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Convert list to dict if needed
        if isinstance(data, list):
            return {m["id"]: m for m in data}
        return data
    return {}


def save_members(members: Dict[str, dict], path: str) -> None:
    """Save members dict to JSON file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(members, f, ensure_ascii=False, indent=2)
