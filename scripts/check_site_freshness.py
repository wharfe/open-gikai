#!/usr/bin/env python3
"""Is the deployed site (Cloudflare) serving the threads this repo has committed?

#102. On Cloudflare Workers Builds a failed site build — or one that never
started, because the push after it touched only Batches sidecars and the build
watch paths skip those — leaves the previous deployment answering 200. Nothing
in liveness monitoring can see that. This compares the deployed
`/__deployment.json` (written at build time by `write_deploy_manifest.py`) with
the committed `data/threads/`, both counted by
`check_mcp_freshness.committed_index`.

What it sees and does not see is exactly #85's MCP check: a date appearing or
vanishing, and a date's thread count moving. Not an edit that keeps the count.

The sha in the manifest is printed but never compared: a sidecar-only or
docs-only push moves HEAD without changing what the site should show.

Exit 0 = the served counts equal the committed ones. Exit 1 otherwise, with the
message saying which of two different things happened — the site answered and
disagreed (STALE), or there was nothing to compare (UNKNOWN).
"""

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from check_mcp_freshness import Unanswered, _differences, committed_index  # noqa: E402

DEFAULT_URL = "https://open-gikai.net/__deployment.json"
# Cloudflare answers 403 to the default "Python-urllib/x.y" agent (measured on
# workers.dev, 2026-11-06), which would turn every run into UNKNOWN.
USER_AGENT = "open-gikai-freshness-check (+https://github.com/wharfe/open-gikai)"


def _threads_of(doc):
    threads = doc.get("threads") if isinstance(doc, dict) else None
    if not isinstance(threads, dict) or not threads or not all(
            isinstance(k, str) and type(v) is int for k, v in threads.items()):
        raise Unanswered("the manifest has no usable `threads` map")
    return threads


def fetch_manifest(url, timeout=30.0):
    """The parsed manifest document, or Unanswered."""
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache",
                                               "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise Unanswered(f"HTTP {exc.code} from {url}") from exc
    # HTTPException covers a body cut short (IncompleteRead), which is not an
    # OSError and would otherwise escape as a traceback instead of UNKNOWN.
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
        raise Unanswered(f"could not fetch {url} ({exc})") from exc
    try:
        return json.loads(body)
    except ValueError as exc:
        raise Unanswered(f"the manifest at {url} is not JSON ({exc})") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads-dir", default="data/threads")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--sleep", type=float, default=60.0)
    args = parser.parse_args(argv)

    try:
        expected = committed_index(args.threads_dir)
    except Unanswered as exc:
        print(f"::error::{exc}")
        return 1
    print(f"Committed: {len(expected)} dates, {sum(expected.values())} threads")

    last_answer = None
    last_error = None
    for attempt in range(1, args.attempts + 1):
        try:
            doc = fetch_manifest(args.url)
            served = _threads_of(doc)
        except Unanswered as exc:
            last_error = exc
            print(f"attempt {attempt}: {exc}")
        else:
            last_answer, last_error = served, None
            differences = _differences(expected, served)
            sha = doc.get("gitSha", "?")
            print(f"attempt {attempt}: site built from {str(sha)[:12]}, "
                  f"{len(differences) or 'no'} difference(s)")
            if not differences:
                print("The site is serving the committed threads.")
                return 0
        if attempt < args.attempts:
            time.sleep(args.sleep)

    if last_answer is None:
        print(f"::error::UNKNOWN — {args.url} gave nothing comparable in "
              f"{args.attempts} attempt(s); this is not evidence of staleness. "
              f"Last failure: {last_error}")
        return 1
    shown = _differences(expected, last_answer)
    tail = "" if last_error is None else (
        f" Later attempts did not get through ({last_error}), so the CURRENT "
        f"state is unconfirmed.")
    print(f"::error::STALE — the site answered and disagreed with the committed "
          f"data: {len(shown)} date(s) differ, e.g. {'; '.join(shown[:5])}.{tail} "
          f"Check the Workers Builds history for open-gikai; re-running the "
          f"latest main build needs a human's go-ahead.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
