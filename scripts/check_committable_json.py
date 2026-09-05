#!/usr/bin/env python3
"""Print a line per data file that must NOT be committed, and why (#75).

#57's option (b). The atomic writer (option (c)) stops this pipeline from
*creating* a corrupt data file; it does nothing about one that arrives by another
route — a hand edit, a bad merge, a failing disk. That gap matters because
``src/lib/data.ts`` is deliberately fatal on a corrupt file: commit one and the
next Vercel production build dies, taking the whole site's deploy with it, long
after the run that committed it went green.

So the rule this implements is: **a file we cannot read must not enter the
commit.** Leaving it unstaged keeps the repository's previous version of that
date — the site is a day stale for one date instead of failing to build at all.

**That last sentence is only true when the repository's version is itself
readable, and for the causes named above it usually is not.** A hand edit or a
bad merge reaches CI *already committed*, so the checkout starts from the corrupt
file: ``git add`` stages content identical to ``HEAD``, ``git reset`` unstages
nothing, and the production build stays broken no matter what this run does. That
is not a reason to drop the check — the case it does cover (a file this run
damaged, which the atomic writer makes unlikely rather than impossible) is real —
but it is a reason the report must not claim a rescue it did not perform. So every
line carries a **verdict** about the committed copy alongside the reason, and the
caller words each case differently:

``head_ok``
    ``HEAD`` holds a readable version. Unstaging restores it; the site is stale
    for that path and nothing else.
``head_broken``
    ``HEAD`` holds an unreadable version too. Unstaging changes nothing that
    matters: the repository already contains a file that fails the production
    build, and only a human commit fixes it.
``not_in_head``
    The file is new. Unstaging keeps it out entirely, so that date does not
    appear on the site — absent, not stale.
``head_unknown``
    The committed copy could not be inspected (no git, no ``HEAD``, unreadable
    object). Fails closed to *promise nothing*, because the alternative is
    telling an operator the site is fine when it may not be.

Reports through stdout, exits 0 unless it was misused. Same contract as
``check_stuck_batches.py`` and for the same reason: the caller runs several of
these in one step and needs to keep going, and a non-zero exit under ``set -e``
would abort the commit step — turning "one path is stale" into "nothing
publishes", which is the amplification #52/#65/#72 keep removing.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

# Every path the daily commit stages, and the shape each must have. The frontend
# and the MCP bundle both index these, so the shape is not decoration: a thread
# file that parses to `{}` does not raise in `loadThreads` — it is skipped, and
# the date silently serves zero threads under a green build.
#
# `.progress.json` is deliberately absent: it is resume bookkeeping that lives in
# the same directory, is gitignored, and is not staged.
#
# Only ONE of these four is reachable for a syntax error today, and knowing which
# is the difference between a fence and a decoration:
#
# * `data/threads/*.json` — reachable. Every reader before the commit step is
#   guarded (validate-data.mjs, generate-feeds.js, generate-sitemap.mjs all
#   try/catch per file; gen_status.py dies but `--fix` swallows it as a non-fatal
#   error; the metrics step counts an unreadable file as 0 by design, #74).
# * `data/members.json` — the two failure modes are no longer symmetric, and
#   that changed under Gate3 on 2026-09-05 (see
#   docs/design-debate/member-links-rewiring/verdict.md §3 for the reversal
#   this paragraph now reflects). A *parse* failure still never gets here:
#   the Collect step reads members first and exits 1 by design
#   (`MembersUnreadable`), and if that is skipped, `validate-data.mjs`'s
#   `checkMembers` (a bare `JSON.parse`, scripts/validate-data.mjs:99) throws
#   on it too, with no `continue-on-error`. Either way the job is dead before
#   this step exists — that entry is dormant against a syntax error, same as
#   before.
#   The wrong-*shape* case ([] instead of an object) is now different from
#   what it was: `checkMembers` still does not die on it (`JSON.parse("[]")`
#   succeeds, and the member-lookup loop below it,
#   scripts/validate-data.mjs:100-118, just misreports rather than
#   throwing), but `enrich-members.mjs` no longer dies on it either — it
#   writes an `::error::` annotation, leaves the file untouched, and exits 0
#   (verified: `echo '[]' > x.json && node scripts/enrich-members.mjs
#   --members-path x.json` → annotation printed, exit 0, `x.json` unchanged),
#   because a non-object `data/members.json` means the file was already
#   broken in HEAD — a hand edit or a bad merge — and aborting the step under
#   `bash -e` a few steps before `git add data/members.json` would only throw
#   away that morning's already-assembled threads without fixing anything.
#   `generate-feeds.js` / `generate-sitemap.mjs` tolerate the same shape too
#   (bare `Object.keys`/`Object.entries` on an array just enumerate nothing).
#   So a wrong-shape `data/members.json` now survives every step ahead of
#   this one and DOES reach this checker — the `("data/members.json", dict)`
#   entry is live for that case, not dormant: it is what stops such a file
#   from being (re-)staged, and reports `head_broken` for exactly the case
#   that produces it (the shape already sat in HEAD). It remains dormant only
#   against the syntax-error case above, for the reason given there.
# * `data/status.json` — regenerated by gen_status.py under `--fix` before the
#   commit, so a corrupt one is normally overwritten rather than excluded. The
#   check covers the case where that regeneration itself failed.
# * `data/pending-batches/*.json` — a corrupt sidecar aborts the Collect step
#   first: `batch_state.load_sidecar` catches only `FileNotFoundError`. And a
#   sidecar this run wrote is already committed AND pushed by summarize.py's own
#   `_git_commit_sidecar`, so excluding it here cannot reach it either.
#
# The unreachable ones are kept rather than removed: they cost nothing, and the
# reason they are unreachable is a separate amplification (a corrupt file
# killing the morning outright) that is tracked on its own — not a property of
# this file. `data/status.json` and `data/pending-batches/*.json` still see
# `HEAD`-side corruption even though a run-introduced one is caught upstream.
# `data/members.json` is the odd one out in the other direction as of
# 2026-09-05: its *syntax-error* case is dormant against both (detailed
# above), but its *wrong-shape* case is now live and reachable — see above.
# Do not read any entry's mere presence as a claim of protection; read the
# paragraph for that path.
CHECKS = (
    ("data/threads/*.json", list),
    ("data/members.json", dict),
    ("data/status.json", dict),
    ("data/pending-batches/*.json", dict),
)

HEAD_OK = "head_ok"
HEAD_BROKEN = "head_broken"
NOT_IN_HEAD = "not_in_head"
HEAD_UNKNOWN = "head_unknown"


def _is_progress_file(path: str) -> bool:
    return path.endswith(".progress.json")


def _problem_with(text: str, expected: type) -> str | None:
    """Why this content cannot be committed, or ``None`` if it is fine.

    One function for both sides of the comparison on purpose: a verdict about the
    committed copy is only meaningful if it was judged by the same rule as the
    working-tree copy. Two rules would let a file be "broken" here and "fine"
    there for no reason an operator could follow.
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        return f"{exc.__class__.__name__}: {exc}"
    if not isinstance(data, expected):
        return (f"expected a JSON {expected.__name__}, found "
                f"{type(data).__name__}")
    return None


def _git(args: list, repo_root: str) -> subprocess.CompletedProcess | None:
    """``git -C repo_root <args>``, or ``None`` if git could not be run at all."""
    try:
        return subprocess.run(["git", "-C", repo_root] + args,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def committed_verdict(rel: str, expected: type, repo_root: str = ".") -> str:
    """What the repository's own copy of ``rel`` is — one of the four verdicts.

    Every branch that cannot *establish* an answer returns ``head_unknown``
    rather than guessing. Guessing ``not_in_head`` would report "that date is
    simply absent" for a file whose committed copy is breaking the build, which
    is the one wrong answer that reads as reassuring.
    """
    head = _git(["rev-parse", "--verify", "--quiet", "HEAD"], repo_root)
    if head is None or head.returncode != 0:
        return HEAD_UNKNOWN

    listed = _git(["ls-tree", "HEAD", "--", rel], repo_root)
    if listed is None or listed.returncode != 0:
        return HEAD_UNKNOWN
    if not listed.stdout.strip():
        return NOT_IN_HEAD

    blob = _git(["cat-file", "blob", f"HEAD:{rel}"], repo_root)
    if blob is None or blob.returncode != 0:
        return HEAD_UNKNOWN
    try:
        text = blob.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return HEAD_BROKEN
    return HEAD_OK if _problem_with(text, expected) is None else HEAD_BROKEN


def problems_for(pattern: str, expected: type, repo_root: str = ".") -> list:
    """``(path, verdict, message)`` per file matching ``pattern`` we cannot commit."""
    found = []
    for path in sorted(glob.glob(os.path.join(repo_root, pattern))):
        if _is_progress_file(path):
            continue
        rel = os.path.relpath(path, repo_root)
        try:
            with open(path, "r", encoding="utf-8") as f:
                why = _problem_with(f.read(), expected)
        except OSError as exc:
            why = f"{exc.__class__.__name__}: {exc}"
        except UnicodeDecodeError as exc:
            why = f"{exc.__class__.__name__}: {exc}"
        if why is not None:
            found.append((rel, committed_verdict(rel, expected, repo_root), why))
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", default=".",
                    help="directory the data/ paths are relative to")
    args = ap.parse_args(argv)

    for pattern, expected in CHECKS:
        for rel, verdict, why in problems_for(pattern, expected, args.repo_root):
            # Path first, verdict second, prose last — both machine-read fields
            # ahead of the one that contains arbitrary text. The caller builds
            # `git reset -- <path>` from field 1 and picks its wording from
            # field 2, so a path preceded by prose, or a verdict after a message
            # that may itself contain a tab, unstages the wrong thing or nothing.
            print(f"{rel}\t{verdict}\t{why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
