#!/usr/bin/env python3
"""Is the deployed MCP server serving the data this repo has committed?

#85. `apps/mcp` is a second Vercel project, and it was deployed by hand, so it
stopped: its last deploy was 2026-05-22 and it answered with data ending
2026-05-19 while the site was at 2026-08-20. Three months, unnoticed, because
the endpoint returns 200 and `tools/list` works — it was never broken, only
old. Liveness monitoring structurally cannot see that; the only comparison that
can is "what it answers" against "what is committed".

Deliberately compares the NEWEST date rather than the whole set. The bundle is
built from `data/threads/`, so a stale deploy always shows up as a newest date
that is behind; comparing every date would also fail for reasons that are not
staleness at all (a date legitimately removed upstream) and turn this into an
alarm people switch off.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://open-gikai-mcp.vercel.app/api/mcp"
DATE_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")

# `list_dates` takes no arguments and is the cheapest question that exposes
# staleness. Kept as a literal so a tool rename fails loudly here rather than
# being papered over by a fallback.
REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "list_dates", "arguments": {}},
}


class Unanswered(Exception):
    """The server did not give us a newest date we can compare."""


def newest_committed_date(threads_dir):
    """The newest `YYYY-MM-DD.json` in the committed threads directory.

    `*.progress.json` is excluded by the pattern, not by a second filter: it is
    gitignored so it should never be here, and a filter would quietly accept it
    if that ever changed.
    """
    import os

    dates = [m.group(1) for name in os.listdir(threads_dir)
             if (m := DATE_FILE.match(name))]
    if not dates:
        raise Unanswered(
            f"no YYYY-MM-DD.json files in {threads_dir} — refusing to call the "
            f"MCP server fresh against nothing")
    return max(dates)


def newest_served_date(url, timeout=30.0, opener=None):
    """The newest date the deployed MCP server admits to having.

    Every failure raises `Unanswered` rather than returning a sentinel: a
    "newest date" that is really an error would compare unequal and be reported
    as staleness, sending the operator to Vercel for a network blip.
    """
    body = json.dumps(REQUEST).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise Unanswered(f"could not reach {url}: {exc}") from exc

    try:
        envelope = json.loads(raw)
        payload = envelope["result"]["content"][0]["text"]
        listed = json.loads(payload)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise Unanswered(
            f"{url} answered something this check cannot read ({exc}); "
            f"first 200 bytes: {raw[:200]!r}") from exc

    if isinstance(listed, dict):
        listed = listed.get("dates", [])
    values = []
    for item in listed if isinstance(listed, list) else []:
        value = item if isinstance(item, str) else (
            item.get("date") if isinstance(item, dict) else None)
        if isinstance(value, str) and value:
            # The tool answers `2026.07.14` on threads but plain dates here;
            # normalise so a formatting difference is never read as staleness.
            values.append(value.replace(".", "-"))
    if not values:
        raise Unanswered(f"{url} listed no dates at all")
    return max(values)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads-dir", default="data/threads")
    parser.add_argument("--url", default=DEFAULT_URL)
    # Retried because an alias swap is not instantaneous and one miss would red
    # a morning that is actually fine. Bounded because a check that waits
    # forever is a check nobody gets an answer from.
    parser.add_argument("--attempts", type=int, default=6)
    parser.add_argument("--sleep", type=float, default=15.0)
    args = parser.parse_args(argv)

    try:
        expected = newest_committed_date(args.threads_dir)
    except Unanswered as exc:
        print(f"::error::{exc}")
        return 1
    print(f"Newest committed date: {expected}")

    last = None
    for attempt in range(1, args.attempts + 1):
        try:
            last = newest_served_date(args.url)
        except Unanswered as exc:
            last = None
            print(f"attempt {attempt}: {exc}")
        else:
            print(f"attempt {attempt}: MCP newest date = {last}")
            if last == expected:
                print("The MCP server is serving the committed data.")
                return 0
        if attempt < args.attempts:
            time.sleep(args.sleep)

    print(f"::error::the MCP server's newest date is {last or '<no answer>'} "
          f"but the newest committed date is {expected}. If the deploy step "
          f"reported success, this is the failure #85 was about: the endpoint "
          f"answers correctly and serves stale data. Check the Vercel "
          f"deployment for open-gikai-mcp.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
