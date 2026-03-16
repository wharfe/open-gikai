"""Cross-thread linking: detect related threads by shared legislation, keywords, or committee."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

log = logging.getLogger("pipeline.linker")

# Minimum keyword overlap ratio to consider threads related
KEYWORD_OVERLAP_THRESHOLD = 0.25
# Minimum shared keywords count
MIN_SHARED_KEYWORDS = 2


def _extract_keywords(thread: dict) -> Set[str]:
    """Collect all keywords from a thread's speeches."""
    kws = set()
    for s in thread.get("speeches", []):
        kws.update(s.get("keywords", []))
    return kws


def _extract_legislation_names(thread: dict) -> Set[str]:
    """Extract bill/law names from topic and keywords using patterns."""
    names = set()
    topic = thread.get("topic", "")
    keywords = _extract_keywords(thread)

    # Patterns for legislation names
    patterns = [
        r"[\w・]+法案",
        r"[\w・]+法(?:改正|等)?",
        r"[\w・]+条約",
        r"[\w・]+協定",
        r"[\w・]+基本計画",
    ]

    for text in [topic] + list(keywords):
        for pat in patterns:
            for match in re.finditer(pat, text):
                name = match.group()
                # Filter out too-short or generic matches
                if len(name) >= 4:
                    names.add(name)

    return names


def _keyword_overlap(kw_a: Set[str], kw_b: Set[str]) -> Tuple[int, float]:
    """Compute keyword overlap count and ratio."""
    if not kw_a or not kw_b:
        return 0, 0.0
    shared = kw_a & kw_b
    ratio = len(shared) / min(len(kw_a), len(kw_b))
    return len(shared), ratio


def _determine_relationship(
    thread_a: dict,
    thread_b: dict,
    shared_laws: Set[str],
    shared_keywords: int,
) -> Optional[str]:
    """Determine the relationship type between two threads."""
    same_committee = thread_a.get("committee") == thread_b.get("committee")
    same_date = thread_a.get("date") == thread_b.get("date")

    if shared_laws:
        if same_committee and not same_date:
            return "続き"
        return "同一法案"

    if shared_keywords >= MIN_SHARED_KEYWORDS:
        return "関連議論"

    return None


def link_threads(threads: List[dict]) -> List[dict]:
    """Add relatedThreads field to each thread based on content similarity.

    Modifies threads in-place and returns them.
    """
    if len(threads) < 2:
        return threads

    # Pre-compute per-thread data
    thread_data = []
    for t in threads:
        thread_data.append({
            "id": t.get("id", ""),
            "keywords": _extract_keywords(t),
            "laws": _extract_legislation_names(t),
            "committee": t.get("committee", ""),
            "date": t.get("date", ""),
            "topic": t.get("topic", ""),
        })

    # Compare all pairs
    links = defaultdict(list)  # thread_id -> [link_dicts]

    for i in range(len(threads)):
        for j in range(i + 1, len(threads)):
            a = thread_data[i]
            b = thread_data[j]

            # Skip self
            if a["id"] == b["id"]:
                continue

            # Check legislation overlap
            shared_laws = a["laws"] & b["laws"]

            # Check keyword overlap
            shared_count, overlap_ratio = _keyword_overlap(a["keywords"], b["keywords"])

            # Determine relationship
            rel = _determine_relationship(
                threads[i], threads[j], shared_laws, shared_count,
            )

            if not rel:
                # Check ratio-based threshold as fallback
                if overlap_ratio >= KEYWORD_OVERLAP_THRESHOLD and shared_count >= MIN_SHARED_KEYWORDS:
                    rel = "関連議論"

            if rel:
                links[a["id"]].append({
                    "threadId": b["id"],
                    "relationship": rel,
                    "topic": b["topic"],
                    "committee": b["committee"],
                    "date": b["date"],
                })
                links[b["id"]].append({
                    "threadId": a["id"],
                    "relationship": rel,
                    "topic": a["topic"],
                    "committee": a["committee"],
                    "date": a["date"],
                })

    # Apply links to threads
    linked_count = 0
    for t in threads:
        tid = t.get("id", "")
        if tid in links:
            t["relatedThreads"] = links[tid]
            linked_count += 1

    log.info("Linked %d/%d threads with related content", linked_count, len(threads))
    return threads
