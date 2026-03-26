#!/usr/bin/env python3
"""Enrich thread data with related news articles from Bing News RSS.

For each thread, searches Bing News by topic keywords, extracts article
metadata and OGP images, then stores them in context.news[].

Usage:
    python scripts/enrich-news.py --date 2025-01-25
    python scripts/enrich-news.py --all          # process all thread files
    python scripts/enrich-news.py --all --dry-run # preview without writing
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from datetime import date, timedelta
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen
from urllib.error import URLError

log = logging.getLogger("enrich-news")

THREADS_DIR = "data/threads"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}
MAX_ARTICLES_PER_THREAD = 3
BING_DELAY = 1.0  # seconds between Bing requests
OGP_TIMEOUT = 5
OGP_DELAY = 0.3


def search_bing_news(query: str) -> list[dict]:
    """Search Bing News RSS and return article metadata."""
    url = f"https://www.bing.com/news/search?q={quote(query)}&format=rss"
    req = Request(url, headers=HEADERS)

    try:
        with urlopen(req, timeout=10) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
    except (URLError, TimeoutError) as e:
        log.warning("Bing search failed for '%s': %s", query, e)
        return []

    articles = []
    items = re.findall(r"<item>(.*?)</item>", data, re.DOTALL)

    for item_xml in items:
        title_m = re.search(r"<title>(.*?)</title>", item_xml)
        link_m = re.search(r"url=(https?%3a%2f%2f[^&]+)", item_xml)
        source_m = re.search(r"<News:Source>(.*?)</News:Source>", item_xml)
        pub_m = re.search(r"<pubDate>(.*?)</pubDate>", item_xml)

        if not link_m:
            continue

        articles.append({
            "title": _clean_html(title_m.group(1)) if title_m else "",
            "url": unquote(link_m.group(1)),
            "source": source_m.group(1) if source_m else "",
            "pubDate": pub_m.group(1) if pub_m else "",
        })

    return articles


def fetch_ogp_image(url: str) -> str | None:
    """Fetch a page and extract the og:image meta tag."""
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=OGP_TIMEOUT) as resp:
            html = resp.read(15000).decode("utf-8", errors="ignore")

        # Try both attribute orderings
        for pattern in [
            r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
            r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"',
        ]:
            m = re.search(pattern, html, re.IGNORECASE)
            if m:
                img = m.group(1)
                # Skip tiny placeholder images
                if "1x1" in img or "blank" in img or len(img) < 20:
                    continue
                return img
    except Exception:
        pass
    return None


def build_query(thread: dict) -> str:
    """Build a search query from thread metadata."""
    topic = thread.get("topic", "")
    committee = thread.get("committee", "")
    tag = thread.get("topicTag", "")

    # Use topic as primary query, trim to key terms
    # Remove generic procedural words
    skip = {"について", "に関する", "等", "における", "のための", "及び"}
    words = topic
    for s in skip:
        words = words.replace(s, " ")

    # Keep it focused: topic + tag if distinct
    query = words.strip()
    if tag and tag not in query:
        query = f"{query} {tag}"

    # Truncate to avoid overly specific queries
    if len(query) > 60:
        query = query[:60]

    return query


def enrich_thread(thread: dict, dry_run: bool = False) -> int:
    """Add news articles to a single thread. Returns count of articles added."""
    # Skip if already enriched
    context = thread.get("context", {})
    if context.get("news"):
        return 0

    query = build_query(thread)
    if not query or len(query) < 4:
        return 0

    articles = search_bing_news(query)
    time.sleep(BING_DELAY)

    if not articles:
        return 0

    news_items = []
    for article in articles[:MAX_ARTICLES_PER_THREAD]:
        item = {
            "title": article["title"],
            "url": article["url"],
            "source": article["source"],
            "pubDate": article["pubDate"],
        }

        if not dry_run:
            img = fetch_ogp_image(article["url"])
            if img:
                item["image"] = img
            time.sleep(OGP_DELAY)

        news_items.append(item)

    if news_items:
        if "context" not in thread:
            thread["context"] = {}
        thread["context"]["news"] = news_items

    return len(news_items)


def process_file(filepath: str, dry_run: bool = False) -> tuple[int, int]:
    """Process a single thread file. Returns (threads_enriched, articles_added)."""
    with open(filepath, "r", encoding="utf-8") as f:
        threads = json.load(f)

    total_enriched = 0
    total_articles = 0

    for thread in threads:
        count = enrich_thread(thread, dry_run=dry_run)
        if count > 0:
            total_enriched += 1
            total_articles += count
            log.info(
                "  %s: +%d articles (%s)",
                thread["id"],
                count,
                thread.get("topic", "")[:40],
            )

    if total_enriched > 0 and not dry_run:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(threads, f, ensure_ascii=False, indent=2)
            f.write("\n")

    return total_enriched, total_articles


def _clean_html(text: str) -> str:
    """Remove HTML entities and tags."""
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&#39;", "'").replace("&quot;", '"')
    return text.strip()


def main():
    parser = argparse.ArgumentParser(description="Enrich threads with related news")
    parser.add_argument("--date", help="Date to process (YYYY-MM-DD)")
    parser.add_argument("--all", action="store_true", help="Process all thread files")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if args.all:
        files = sorted(
            os.path.join(THREADS_DIR, f)
            for f in os.listdir(THREADS_DIR)
            if f.endswith(".json")
        )
    elif args.date:
        files = [os.path.join(THREADS_DIR, f"{args.date}.json")]
    else:
        parser.error("Specify --date or --all")

    grand_enriched = 0
    grand_articles = 0

    for filepath in files:
        if not os.path.exists(filepath):
            log.warning("File not found: %s", filepath)
            continue

        log.info("Processing %s", filepath)
        enriched, articles = process_file(filepath, dry_run=args.dry_run)
        grand_enriched += enriched
        grand_articles += articles

    log.info(
        "Done: %d threads enriched, %d articles added%s",
        grand_enriched,
        grand_articles,
        " (dry-run)" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
