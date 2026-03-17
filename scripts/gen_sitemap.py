#!/usr/bin/env python3
"""Generate sitemap.xml from pipeline output data."""

from __future__ import annotations

import json
import os
from datetime import datetime

BASE_URL = "https://open-gikai.net"
THREADS_DIR = "data/threads"
MEMBERS_PATH = "data/members.json"
OUTPUT_PATH = "public/sitemap.xml"


def main() -> None:
    urls = []

    # Static pages
    urls.append({"loc": BASE_URL, "changefreq": "daily", "priority": "1.0"})
    urls.append({"loc": f"{BASE_URL}/about", "changefreq": "monthly", "priority": "0.5"})
    urls.append({"loc": f"{BASE_URL}/search", "changefreq": "daily", "priority": "0.7"})

    # Thread pages
    if os.path.exists(THREADS_DIR):
        for fname in sorted(os.listdir(THREADS_DIR)):
            if not fname.endswith(".json") or fname.endswith(".progress.json"):
                continue
            with open(os.path.join(THREADS_DIR, fname), "r", encoding="utf-8") as f:
                threads = json.load(f)
            for t in threads:
                urls.append({
                    "loc": f"{BASE_URL}/t/{t['id']}",
                    "changefreq": "weekly",
                    "priority": "0.8",
                })

    # Member pages
    if os.path.exists(MEMBERS_PATH):
        with open(MEMBERS_PATH, "r", encoding="utf-8") as f:
            members = json.load(f)
        for mid in members:
            urls.append({
                "loc": f"{BASE_URL}/m/{mid}",
                "changefreq": "weekly",
                "priority": "0.6",
            })

    # Generate XML
    xml_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for url in urls:
        xml_lines.append("  <url>")
        xml_lines.append(f"    <loc>{url['loc']}</loc>")
        xml_lines.append(f"    <changefreq>{url['changefreq']}</changefreq>")
        xml_lines.append(f"    <priority>{url['priority']}</priority>")
        xml_lines.append("  </url>")
    xml_lines.append("</urlset>")

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(xml_lines) + "\n")

    print(f"Generated sitemap.xml with {len(urls)} URLs → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
