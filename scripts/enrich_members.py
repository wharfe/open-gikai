#!/usr/bin/env python3
"""Enrich member profiles with external links.

Generates links to Wikipedia, official parliamentary sites, and
Google search for official websites/X accounts.

Usage:
    python scripts/enrich_members.py
"""

from __future__ import annotations

import json
import os
import urllib.parse

MEMBERS_PATH = "data/members.json"

# Roles that indicate bureaucrats (not elected officials)
BUREAUCRAT_ROLES = [
    "審議官", "局長", "部長", "課長", "参事官", "官房長",
    "事務次官", "施設監", "政策統括官", "次長",
]


def is_elected_member(member: dict) -> bool:
    """Check if a member is an elected official (not a bureaucrat)."""
    role = member.get("role", "")
    if any(kw in role for kw in BUREAUCRAT_ROLES):
        return False
    # Members with party affiliation are likely elected
    if member.get("party"):
        return True
    # PM and ministers are elected
    if member.get("rank") in ("pm", "minister", "viceminister"):
        return True
    return False


def generate_links(member: dict) -> list:
    """Generate external links for a member."""
    name = member.get("name", "")
    if not name:
        return []

    links = []
    encoded_name = urllib.parse.quote(name)

    # Wikipedia (Japanese)
    links.append({
        "label": "Wikipedia",
        "url": f"https://ja.wikipedia.org/wiki/{encoded_name}",
    })

    # Only for elected officials
    if is_elected_member(member):
        # Google search for official site
        links.append({
            "label": "公式サイト検索",
            "url": "https://www.google.com/search?" + urllib.parse.urlencode({
                "q": f"{name} 公式サイト"
            }),
        })

        # Google search for X/Twitter account
        links.append({
            "label": "X (Twitter) 検索",
            "url": "https://www.google.com/search?" + urllib.parse.urlencode({
                "q": f"{name} site:x.com OR site:twitter.com"
            }),
        })

    return links


def main() -> None:
    if not os.path.exists(MEMBERS_PATH):
        print(f"Members file not found: {MEMBERS_PATH}")
        return

    with open(MEMBERS_PATH, "r", encoding="utf-8") as f:
        members = json.load(f)

    enriched = 0
    for mid, member in members.items():
        links = generate_links(member)
        if links:
            member["links"] = links
            enriched += 1

    with open(MEMBERS_PATH, "w", encoding="utf-8") as f:
        json.dump(members, f, ensure_ascii=False, indent=2)

    elected = sum(1 for m in members.values() if is_elected_member(m))
    print(f"Enriched {enriched}/{len(members)} members ({elected} elected officials)")


if __name__ == "__main__":
    main()
