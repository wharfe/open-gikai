#!/usr/bin/env python3
"""Write out/__deployment.json: what this build published (#102).

Run by Cloudflare Workers Builds right after `npm run build`, from the repo
root. Records the checkout's `git rev-parse HEAD` and `{date: thread count}`,
counted by `check_mcp_freshness.committed_index` — the same projection the
freshness check applies to the committed `data/threads/`, so the two sides are
one function, not two copies that can drift.

This must NEVER stop publishing. Any problem — no `out/`, an unreadable data
file, git missing — prints why and exits 0 WITHOUT writing the file. The site
still deploys; `check_site_freshness.py` then reports the manifest as missing
("UNKNOWN"), which is the right signal: nobody vouched for what was published.

The sha comes from the checkout, not from WORKERS_CI_COMMIT_SHA: a build
started by a Deploy Hook reports the branch name there instead of a sha.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


OUT = "out"
NAME = "__deployment.json"


def main():
    # Imported here, not at the top, so that even an import failure lands in
    # the catch-all below instead of failing the build.
    from check_mcp_freshness import Unanswered, committed_index
    from pipeline.jsonio import write_json_atomic

    if not os.path.isdir(OUT):
        print(f"deploy manifest not written: no {OUT}/ directory in {os.getcwd()}")
        return 0
    try:
        threads = committed_index(os.path.join("data", "threads"))
    except Unanswered as exc:
        print(f"deploy manifest not written: {exc}")
        return 0
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"deploy manifest not written: git rev-parse HEAD failed ({exc})")
        return 0
    path = os.path.join(OUT, NAME)
    try:
        write_json_atomic(path, {"gitSha": sha, "threads": threads})
    except OSError as exc:
        print(f"deploy manifest not written: {exc}")
        return 0
    print(f"deploy manifest: {path} — {sha[:12]}, {len(threads)} dates, "
          f"{sum(threads.values())} threads")
    return 0


def run():
    """main(), but nothing it can raise may fail the site build."""
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 — publishing must not stop here
        print(f"deploy manifest not written: unexpected {type(exc).__name__}: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(run())
