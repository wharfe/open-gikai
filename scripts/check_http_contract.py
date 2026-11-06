#!/usr/bin/env python3
"""Print the HTTP contract of a deployed site, optionally side by side with another (#102).

Used before the DNS cutover: run it against the Vercel production site and the
Cloudflare workers.dev deployment, and read the differences against the table
in docs/cloudflare-cutover.md. Redirects are NOT followed — the status code and
Location of the first response are the contract (a 307 that lands on a 200 and
a 200 serving the same page twice are different outcomes, and following the
redirect would hide which one happened).

    python3 scripts/check_http_contract.py https://open-gikai.net \\
        --compare https://open-gikai.<subdomain>.workers.dev

Standard library only. Read-only GET/HEAD requests; nothing is changed.
"""

import argparse
import hashlib
import http.client
import re
import xml.etree.ElementTree as ET
import sys
import urllib.parse

FIXED_PATHS = [
    "/", "/about", "/about/", "/index.html", "/about.html", "/about/index.html",
    "/this-page-does-not-exist", "/feed.xml", "/sitemap.xml", "/sitemap_index.xml",
    "/263c8aace67646c3b69bfe4fc5cf1c08.txt", "/_headers", "/__deployment.json",
    "/robots.txt",
]
HEADERS = ("location", "content-type", "cache-control", "x-robots-tag")


def get(base, path, method="GET"):
    """(status, {header: value}, body bytes) for one request, no redirects."""
    u = urllib.parse.urlsplit(base)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.netloc, timeout=30)
    try:
        conn.request(method, path, headers={"User-Agent": "open-gikai-contract-check"})
        resp = conn.getresponse()
        body = resp.read() if method == "GET" else b""
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, hdrs, body
    finally:
        conn.close()


def discover(base):
    """A thread page, its OGP image, its RSC payload and one JS chunk, from live HTML."""
    # The home page renders its thread list on the client, so its HTML has no
    # /t/ anchors; the thread sitemap is the reliable index.
    paths = []
    _, _, home = get(base, "/")
    html = home.decode("utf-8", "replace")
    _, _, sm = get(base, "/sitemap-threads.xml")
    m = re.search(r"<loc>https?://[^/<]+(/t/[^<?#]+)</loc>", sm.decode("utf-8", "replace"))
    if m:
        thread = m.group(1)
        # `<id>.txt` and the per-segment `<id>/__next._tree.txt` are what
        # client navigation actually requests (seen in the e2e run).
        paths += [thread, thread + ".txt", thread + "/__next._tree.txt"]
        _, _, page = get(base, thread)
        og = re.search(r'(/t/[^"]+/opengraph-image[^"]*)"', page.decode("utf-8", "replace"))
        if og:
            paths.append(og.group(1).replace("&amp;", "&"))
    js = re.search(r'(/_next/static/[^"]+\.js)"', html)
    if js:
        paths.append(js.group(1))
    return paths


def feed_summary(body):
    """Well-formed? and a digest of the item links (lastBuildDate differs per build)."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        return f"feed=MALFORMED({exc})"
    links = sorted(e.text or "" for e in root.iter("link")
                   if e.text and "/t/" in e.text)
    return f"feed_items={len(links)} links_sha={hashlib.sha256(chr(10).join(links).encode()).hexdigest()[:16]}"


def sitemap_sample(base, n=30):
    """Up to n page paths spread across the per-type sitemaps."""
    out = []
    for name in ("sitemap-threads.xml", "sitemap-members.xml", "sitemap-pages.xml",
                 "sitemap-digests.xml", "sitemap-gov.xml", "sitemap-councils.xml"):
        _, _, sm = get(base, "/" + name)
        locs = re.findall(r"<loc>https?://[^/<]+(/[^<]*)</loc>", sm.decode("utf-8", "replace"))
        step = max(1, len(locs) // 5)
        out += locs[::step][:5]
    return out[:n]


def describe(status, hdrs, body, path):
    parts = [str(status)]
    for h in HEADERS:
        if h in hdrs:
            parts.append(f"{h}={hdrs[h]}")
    if "opengraph-image" in path:
        parts.append("png=" + ("yes" if body[:8] == b"\x89PNG\r\n\x1a\n" else "NO"))
    if path.endswith(".txt") or path == "/_headers":
        parts.append(f"bytes={len(body)}")
    if path.startswith("/263c8aace"):  # IndexNow key: the body itself is the contract
        parts.append("sha256=" + hashlib.sha256(body).hexdigest()[:16])
    if path == "/feed.xml" and status == 200:
        parts.append(feed_summary(body))
    if status == 404:
        parts.append("site404=" + ("yes" if b"<html" in body[:2000].lower() else "NO"))
    return parts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base")
    ap.add_argument("--compare")
    args = ap.parse_args(argv)
    bases = [args.base.rstrip("/")] + ([args.compare.rstrip("/")] if args.compare else [])
    try:
        paths = FIXED_PATHS + discover(bases[0])
    except (OSError, http.client.HTTPException) as exc:
        print(f"could not discover thread/JS paths on {bases[0]} ({exc}); fixed paths only")
        paths = list(FIXED_PATHS)

    differing = 0
    for path in paths:
        rows = []
        for b in bases:
            try:
                rows.append(describe(*get(b, path), path))
            except (OSError, http.client.HTTPException) as exc:
                rows.append([f"ERROR {exc}"])
        same = len(rows) == 1 or rows[0] == rows[1]
        differing += not same
        print(("  " if same else "≠ ") + path)
        for b, r in zip(bases, rows):
            print(f"    {urllib.parse.urlsplit(b).netloc}: {' | '.join(r)}")
    sample = sitemap_sample(bases[0])
    print(f"\nsitemap sample ({len(sample)} pages, status only):")
    for b in bases:
        codes = {}
        for path in sample:
            try:
                st = get(b, path, method="HEAD")[0]
            except (OSError, http.client.HTTPException):
                st = "ERROR"
            codes.setdefault(st, []).append(path)
        summary = ", ".join(f"{k}×{len(v)}" for k, v in codes.items())
        bad = [p for k, v in codes.items() if k != 200 for p in v][:5]
        print(f"    {urllib.parse.urlsplit(b).netloc}: {summary}" + (f"  e.g. {bad}" if bad else ""))
    if args.compare:
        print(f"\n{differing} of {len(paths)} paths differ — judge each against "
              f"docs/cloudflare-cutover.md, this script does not decide.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
