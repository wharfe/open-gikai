#!/usr/bin/env python3
"""Is the deployed MCP server serving the data this repo has committed?

#85. `apps/mcp` is a second Vercel project, and it was deployed by hand, so it
stopped: its last deploy was 2026-05-22 and it answered with data ending
2026-05-19 while the site was at 2026-08-20. Three months, unnoticed, because
the endpoint returns 200 and `tools/list` works — it was never broken, only
old. Liveness monitoring structurally cannot see that; the only comparison that
can is "what it answers" against "what is committed".

Compares the WHOLE index — every date the server lists, and how many threads it
says each one holds — not just the newest date. Newest-only was the first
version and it had a hole big enough to drive the outage back through: the
pipeline re-visits a 30-day window every morning, so most days add threads to
dates that already exist, and a quiet Diet day adds no date at all. On any such
morning a deploy that silently did not take would answer with exactly the newest
date this repo has and pass. "A stale deploy always shows up as a newest date
that is behind" is only true until the server has caught up once.

Comparing the whole index is not the tightening it looks like, because both
sides come from the SAME checkout: this job bundles `data/threads/` and deploys
it, then asks the server what it now holds. Equality is a contract, not a hope —
a date removed upstream is removed from both sides by the same commit. It is
cheap, too: `list_dates` already answers with the per-date counts, so nothing
about the server had to change.

Be honest about the residual: this sees a date appearing, a date vanishing, and
a date's thread count moving. It does NOT see an edit that leaves the count
identical. That is a much narrower blind spot than the one it replaces, and
closing it would need the server to answer with a digest of the bundle.
"""

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://open-gikai-mcp.vercel.app/api/mcp"


def _is_a_file_the_server_reads(name):
    """The server's own file predicate, copied rather than approximated.

    `loadThreads` (apps/mcp/src/lib/data.ts) takes every `*.json` directly in
    the directory except `*.progress.json`, and the bundle verifier in
    apps/mcp/scripts/copy-data.mjs deliberately uses the same one. Matching
    `YYYY-MM-DD.json` instead reads narrower than the thing being checked, and
    the gap is a false RED on a correct deploy: one `backup.json` holding real
    threads is counted by the server and not here, so the comparison disagrees
    every morning until a human edits the data. `validate-data.mjs` does not
    forbid such a file, so nothing upstream makes the narrow pattern safe.

    `*.progress.json` is excluded because the SERVER excludes it, not because
    it is gitignored — if it ever stops being gitignored, both sides still
    agree.
    """
    return name.endswith(".json") and not name.endswith(".progress.json")


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
    """There is no comparison to make.

    Raised from BOTH sides — the server did not give us an index we can read,
    or the committed data did not. Which side failed decides what the operator
    is told, so `main` keeps them apart; the shared type only means "this run
    learned nothing about staleness", never "the server is stale".
    """


def committed_index(threads_dir):
    """`{date: thread count}` for the committed threads directory.

    Counted the way the SERVER counts, which is not the way the directory is
    named: `listDates` walks every thread and tallies `normalizeDate(t.date)`
    (`apps/mcp/src/lib/mcp/tools.ts`), so the filename never reaches it. Tally
    by filename instead and two shapes turn a byte-perfect deploy into a red
    morning that repeats until someone edits the data — an empty `[]` file,
    which is a date to the filename and no date at all to the server; and a
    thread whose own `date` disagrees with the file it sits in. Neither is
    forbidden anywhere: `validate-data.mjs` allows both. An alarm that cries on
    a correct deploy is one that gets switched off, so the projection is copied
    rather than approximated.

    Which FILES are read is copied for the same reason and from the same place
    — see `_is_a_file_the_server_reads`.

    Every way of failing to read raises `Unanswered`, including the directory
    not being there at all. `os.listdir` would otherwise raise `FileNotFoundError`
    straight past `main`, replacing "there is nothing to compare against" — which
    is the diagnosis — with a traceback.

    A file that does not parse into a LIST of objects is unreadable, not empty:
    counting it anyway means comparing a fabricated number and reporting the
    difference as staleness.

    That last one is the one place this is deliberately LOUDER than the server,
    which skips a non-array file (`if (Array.isArray(data))`) and serves the
    rest. Not an oversight and not a false red: `copy-data.mjs` verifies every
    bundled file parses AND has the right top-level shape, so such a file fails
    the deploy step before this check ever runs. Refusing here means the one way
    it can still be reached — a file that appeared after the bundle was built —
    reports "cannot read", not a thread count nobody vouched for.
    """
    try:
        names = sorted(os.listdir(threads_dir))
    except OSError as exc:
        raise Unanswered(
            f"cannot read {threads_dir} ({exc}) — refusing to call the MCP "
            f"server fresh against nothing") from exc

    index = {}
    files = 0
    for name in names:
        if not _is_a_file_the_server_reads(name):
            continue
        files += 1
        path = os.path.join(threads_dir, name)
        try:
            with open(path, encoding="utf-8") as handle:
                threads = json.load(handle)
            if not isinstance(threads, list):
                raise TypeError(f"parsed into {type(threads).__name__}, not a list")
            for thread in threads:
                if not isinstance(thread, dict):
                    raise TypeError(f"holds a {type(thread).__name__}, not an object")
                date = thread.get("date")
                if not isinstance(date, str) or not date:
                    raise TypeError(f"has a thread with no usable date ({date!r})")
                date = date.replace(".", "-")
                index[date] = index.get(date, 0) + 1
        # `ValueError` covers both `json.JSONDecodeError` and the
        # `UnicodeDecodeError` a file of invalid UTF-8 raises on read — the
        # latter is not an `OSError`, so naming the subclasses let it walk out
        # past `main` as a traceback, which is the one outcome this is for.
        except (OSError, ValueError, TypeError) as exc:
            raise Unanswered(
                f"cannot read {path} ({exc}) — this check cannot say what the "
                f"server ought to be serving, so it is not saying anything about "
                f"staleness")

    if not files:
        raise Unanswered(
            f"no thread files in {threads_dir} — refusing to call the "
            f"MCP server fresh against nothing")
    if not index:
        raise Unanswered(
            f"{files} thread file(s) in {threads_dir} but not one thread in any "
            f"of them — refusing to call the MCP server fresh against nothing")
    return index


def served_index(url, timeout=30.0, opener=None):
    """`{date: thread count}` the deployed MCP server admits to having.

    Every failure raises `Unanswered` rather than returning a sentinel: an
    "index" that is really an error would compare unequal and be reported as
    staleness, sending the operator to Vercel for a network blip.
    """
    body = json.dumps(REQUEST).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    # `URLError`/`HTTPError` are `OSError` subclasses, but the two ways a
    # half-delivered answer arrives are NOT: `http.client.IncompleteRead` is an
    # `HTTPException`, and `UnicodeDecodeError` is a `ValueError`. Both used to
    # walk out past `main` as a traceback — killing the remaining attempts and
    # the carefully-worded "this is not evidence of staleness" note with them,
    # which is the one outcome this function exists to prevent. It is the same
    # trap `committed_index` spells out below; only one side had closed it.
    except (OSError, http.client.HTTPException) as exc:
        raise Unanswered(f"could not reach {url}: {exc}") from exc
    except ValueError as exc:
        raise Unanswered(
            f"{url} answered bytes this check cannot decode as UTF-8 "
            f"({exc})") from exc

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
    index = {}
    # An entry this check cannot read is refused, never skipped — the same rule
    # the count below already followed, applied to the whole entry. Dropping one
    # silently keeps the comparison running against a SHORTER index, so a
    # changed answer shape is reported as "committed N / served absent", i.e.
    # staleness: a cause this check has not established, sending an operator to
    # the Vercel dashboard for someone else's refactor.
    for item in listed if isinstance(listed, list) else []:
        if isinstance(item, str):
            date, threads = item, None
        elif isinstance(item, dict):
            date, threads = item.get("date"), item.get("threads")
        else:
            raise Unanswered(
                f"{url} listed a {type(item).__name__} where a date entry was "
                f"expected ({item!r}) — the tool's answer shape changed, so "
                f"nothing here is comparable")
        if not isinstance(date, str) or not date:
            raise Unanswered(
                f"{url} listed an entry with no usable date ({date!r}) — the "
                f"tool's answer shape changed, so nothing here is comparable")
        # The tool answers `2026.07.14` on threads but plain dates here;
        # normalise so a formatting difference is never read as staleness.
        date = date.replace(".", "-")
        # A date whose count this check cannot read is a date it cannot compare.
        # Recording it as 0 would make an unreadable answer look like a
        # difference; refusing outright is the direction that costs a human a
        # look rather than a wrong diagnosis.
        if not isinstance(threads, int) or isinstance(threads, bool):
            raise Unanswered(
                f"{url} listed {date} without a thread count this check can "
                f"read ({threads!r}) — the tool's answer shape changed, so "
                f"nothing here is comparable")
        index[date] = threads
    if not index:
        raise Unanswered(f"{url} listed no dates at all")
    return index


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
        expected = committed_index(args.threads_dir)
    except Unanswered as exc:
        print(f"::error::{exc}")
        return 1
    print(f"Committed: {len(expected)} dates, {sum(expected.values())} threads "
          f"(newest {max(expected)})")

    # Kept apart on purpose. Folding them into one variable is what let the old
    # version report a network outage as staleness: a failure overwrote the
    # answer, and the final message then explained `<no answer>` as "the
    # endpoint answers correctly and serves stale data" — a cause it had not
    # established, and the one thing this repo's annotations must never do.
    last_answer = None
    last_error = None
    for attempt in range(1, args.attempts + 1):
        try:
            served = served_index(args.url)
        except Unanswered as exc:
            last_error = exc
            print(f"attempt {attempt}: {exc}")
        else:
            last_answer = served
            differences = _differences(expected, served)
            print(f"attempt {attempt}: MCP has {len(served)} dates, "
                  f"{sum(served.values())} threads, "
                  f"{len(differences) or 'no'} difference(s)")
            if not differences:
                print("The MCP server is serving the committed data.")
                return 0
        if attempt < args.attempts:
            time.sleep(args.sleep)

    if last_answer is None:
        # Says nothing about staleness, deliberately: we never got an answer to
        # compare, so we do not know whether the server is current. An operator
        # sent to the Vercel dashboard over a network blip has had their morning
        # taken for nothing.
        print(f"::error::the MCP server at {args.url} did not give this check "
              f"anything it could compare, in {args.attempts} attempt(s), so "
              f"whether it is serving the committed data is UNKNOWN — this is "
              f"not evidence of staleness. Last failure: {last_error}")
        return 1

    shown = _differences(expected, last_answer)
    print(f"::error::the MCP server is not serving the committed data: "
          f"{len(shown)} date(s) differ, e.g. {'; '.join(shown[:5])}. If the "
          f"deploy step reported success, this is the failure #85 was about: "
          f"the endpoint answers correctly and serves data that is not ours. "
          f"Check the Vercel deployment for open-gikai-mcp.")
    return 1


def _differences(expected, served):
    """Human-readable `date: committed N / served M` lines, newest first."""
    out = []
    for date in sorted(set(expected) | set(served), reverse=True):
        ours, theirs = expected.get(date), served.get(date)
        if ours != theirs:
            out.append(f"{date}: committed "
                       f"{'absent' if ours is None else ours} / served "
                       f"{'absent' if theirs is None else theirs}")
    return out


if __name__ == "__main__":
    sys.exit(main())
