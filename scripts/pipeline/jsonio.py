"""Atomic JSON writes for everything this pipeline commits or re-reads.

One function, one rule: a reader must never see a half-written file. Writing
JSON in place does not give that — ``json.dump`` serializes incrementally, so a
job killed mid-write (the daily workflow has a 120-minute timeout and can be
cancelled) or a full disk leaves a truncated document that is now the file.

That single cause detonated in two different places, which is why both issues
point here:

* ``data/threads/{date}.json`` — the publish chain names and skips a corrupt
  file, so the run stays green and commits it; the Vercel build then dies in
  ``JSON.parse`` (#57).
* ``data/raw/*.json`` — ``--collect-pending`` re-reads raw to assemble or
  rebuild, and an unguarded ``json.load`` aborts Collect under ``set -e``,
  taking the morning's publish down (#72).

Guarding the readers is worth doing anyway (a file can arrive corrupt by other
routes), but it only decides how well the pipeline survives corruption. This
decides whether the pipeline creates any.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from typing import Any


def _fsync_directory(directory: str) -> None:
    """Persist the RENAME, not just the bytes.

    Fsyncing the file only guarantees its contents survive a host crash; the
    directory entry that points at them is a separate write. Without this, a
    power loss right after ``os.replace`` can come back up with the *old* file
    — or, for a new file, with no entry at all — while every log says the write
    succeeded. Best-effort by design: some filesystems refuse to open a
    directory for fsync, and failing the whole write over a durability upgrade
    would be worse than the crash window it closes.
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _replacement_mode(path: str) -> int:
    """The permission bits the replacement file should end up with.

    ``tempfile.mkstemp`` creates 0600 by design, and ``os.replace`` keeps the
    *source* file's mode — so a naive temp-and-rename silently tightens every
    file it touches, which the writers this replaces (plain ``open(path, "w")``,
    i.e. 0666 masked by the umask) never did. Git records only the exec bit, so
    the drift is invisible in review and in CI; it surfaces as EACCES wherever
    ``data/`` is read by a different user than the one that wrote it.

    An existing file keeps exactly the mode it already had; a new one gets what
    ``open()`` would have given it.
    """
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        current = os.umask(0)
        os.umask(current)
        return 0o666 & ~current


def write_json_atomic(path: str, obj: Any, *, indent: int = 2,
                      ensure_ascii: bool = False,
                      trailing_newline: bool = False) -> None:
    """Write ``obj`` to ``path`` as JSON, or leave ``path`` exactly as it was.

    Serializes into a temp file **in the same directory** — ``os.replace`` is
    only atomic within one filesystem, and a temp dir on another mount would
    make this quietly non-atomic on precisely the CI runners and containers this
    runs in — then fsyncs the file, renames over the target, and fsyncs the
    directory so the rename itself is durable and not just the bytes it points
    at.

    Nothing is replaced until serialization has fully succeeded, so a value
    ``json`` cannot encode raises with the previous file untouched and no debris
    left behind. Be exact about the reach of that second half: cleanup runs on
    exceptions, including the ``KeyboardInterrupt`` a Ctrl-C or a handled
    SIGTERM raises — but a ``SIGKILL`` (the workflow's 120-minute timeout, the
    OOM killer) runs no Python at all, so it CAN strand a temp file. That is why
    ``.gitignore`` also excludes the ``.<name>.*.tmp`` pattern: the process-level
    guarantee has a hole, and the one thing a stray temp file must never do is
    get committed by ``git add data/threads/``. Readers are safe either way —
    every one of them filters on ``.json``.

    The defaults match the writers this replaces (``indent=2``,
    ``ensure_ascii=False``): changing either would rewrite every committed data
    file on the next run and bury the real diff. ``trailing_newline`` exists for
    the same reason — one caller (enrich-news.py) has always written one, and
    dropping it would touch every enriched thread file.
    """
    # A symlinked target must keep pointing where it pointed. The writers this
    # replaces used ``open(path, "w")``, which follows the link and updates its
    # destination; ``os.replace`` would instead drop a regular file ON TOP of
    # the link, so the real file silently stops receiving updates and every
    # reader of the other end keeps serving stale data. Resolve first, and the
    # temp file then lands beside the true target (which is also what keeps the
    # rename on one filesystem).
    path = os.path.realpath(path) if os.path.islink(path) else path

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    mode = _replacement_mode(path)
    fd, tmp_path = tempfile.mkstemp(
        dir=directory or ".", prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # fchmod on the already-open descriptor, not chmod on the pathname:
            # between mkstemp and fdopen the fd is owned by neither, so a raise
            # in there leaks it. Inside the `with`, close is guaranteed.
            os.fchmod(f.fileno(), mode)
            json.dump(obj, f, ensure_ascii=ensure_ascii, indent=indent)
            if trailing_newline:
                f.write("\n")
            f.flush()
            # The rename is atomic with respect to readers, but on a crash the
            # rename can outlive the data unless the bytes are on disk first.
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(directory or ".")
    except BaseException:
        # BaseException, not Exception: a SIGINT/SIGTERM-driven KeyboardInterrupt
        # is exactly the kill this function exists for, and it must not be the
        # one path that leaves a temp file behind.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
