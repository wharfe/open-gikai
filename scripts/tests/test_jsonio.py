"""#57 / #72: every JSON file this pipeline commits or re-reads must be written
atomically.

The failure both issues describe starts the same way: the daily job is killed
(or the disk fills) partway through a `json.dump`, leaving a half-written file
on disk. From there they diverge only in where it detonates — a truncated
`data/threads/{date}.json` gets committed and crashes the Vercel build (#57), a
truncated `data/raw/*.json` crashes `--collect-pending` and stops the morning's
publish (#72). A writer that never leaves a partial file removes the cause of
both.
"""

import fnmatch
import json
import os
import stat
import tempfile
from unittest import mock

import pytest

from pipeline.jsonio import write_json_atomic


def test_it_writes_a_file_that_round_trips(tmp_path):
    path = str(tmp_path / "out.json")
    write_json_atomic(path, {"a": [1, 2], "日本語": "そのまま"})
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"a": [1, 2], "日本語": "そのまま"}
    # Non-ASCII is written as-is, matching every writer this replaces — a switch
    # to \u escapes would rewrite every committed data file on the next run.
    assert "日本語" in open(path, encoding="utf-8").read()


def test_it_creates_the_parent_directory(tmp_path):
    path = str(tmp_path / "deep" / "nested" / "out.json")
    write_json_atomic(path, [])
    assert os.path.exists(path)


def test_a_failed_serialization_leaves_the_previous_file_intact(tmp_path):
    """THE test. Everything else here is housekeeping.

    json.dump writes incrementally, so it can raise having already emitted half
    the document. Writing in place means that half IS the file from then on —
    which is the corruption #57 and #72 are both downstream of.
    """
    path = str(tmp_path / "out.json")
    write_json_atomic(path, {"good": True})

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(path, {"big": ["x" * 100] * 50, "bad": Unserializable()})

    with open(path, encoding="utf-8") as f:
        assert json.load(f) == {"good": True}, "the old file was destroyed"


def test_a_failed_write_leaves_no_debris_behind(tmp_path):
    """A temp file left in data/ would be picked up by `git add data/threads/`
    and by the readers that glob the directory."""
    path = str(tmp_path / "out.json")

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(path, {"bad": Unserializable()})
    assert os.listdir(tmp_path) == []


def test_it_replaces_an_existing_file_wholesale(tmp_path):
    """os.replace, not truncate-and-write: a shorter document must not leave the
    tail of the longer one behind it."""
    path = str(tmp_path / "out.json")
    write_json_atomic(path, list(range(500)))
    write_json_atomic(path, [1])
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == [1]
    assert os.listdir(tmp_path) == ["out.json"]


def test_the_temp_file_lives_beside_its_target(tmp_path, monkeypatch):
    """os.replace is only atomic within one filesystem. Writing the temp file to
    the system temp dir would make this silently non-atomic wherever /tmp is a
    different mount — the exact environments (CI runners, containers) this runs
    in."""
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((os.path.dirname(os.path.abspath(src)),
                     os.path.dirname(os.path.abspath(dst))))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    path = str(tmp_path / "sub" / "out.json")
    write_json_atomic(path, {})
    assert seen and seen[0][0] == seen[0][1]


def test_it_does_not_tighten_the_permissions_of_the_file_it_replaces(tmp_path):
    """mkstemp creates 0600 and os.replace keeps the SOURCE file's mode, so the
    naive temp-and-rename silently locks down every file it touches. Git records
    only the exec bit, so the drift survives review and CI and surfaces as
    EACCES wherever data/ is read by another user."""
    path = str(tmp_path / "out.json")
    write_json_atomic(path, {})
    # Derived from the live umask, not hardcoded to 0644: the contract is "what
    # open() would have given it", and a developer running under `umask 077` is
    # entitled to 0600 here. Asserting the bits directly would fail them for
    # doing nothing wrong — and, worse, would pass on a machine where mkstemp's
    # 0600 happened to match the umask.
    current = os.umask(0)
    os.umask(current)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o666 & ~current

    os.chmod(path, 0o640)
    write_json_atomic(path, {"again": True})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640, "an existing mode was not preserved"


def test_it_writes_through_a_symlink_instead_of_replacing_it(tmp_path):
    """`open(path, "w")` — every writer this replaces — follows a symlink and
    updates its destination. `os.replace` would drop a regular file ON TOP of
    the link, so the real file silently stops receiving updates while the write
    reports success and every reader of the other end serves stale data."""
    real = tmp_path / "real.json"
    link = tmp_path / "link.json"
    write_json_atomic(str(real), {"v": 1})
    os.symlink(real, link)

    write_json_atomic(str(link), {"v": 2})

    assert os.path.islink(str(link)), "the symlink was replaced by a regular file"
    with open(str(real), encoding="utf-8") as f:
        assert json.load(f) == {"v": 2}, "the link's destination was not updated"


def test_the_temp_file_is_covered_by_gitignore(tmp_path):
    """A SIGKILL runs no cleanup, so a stray temp file is possible by design.
    The one thing it must never do is get committed by `git add data/threads/`
    — which makes the .gitignore entry part of this function's contract, not
    housekeeping beside it."""
    recorded = []
    real_mkstemp = tempfile.mkstemp

    def spy(*a, **kw):
        fd, p = real_mkstemp(*a, **kw)
        recorded.append(os.path.basename(p))
        return fd, p

    with mock.patch.object(tempfile, "mkstemp", spy):
        write_json_atomic(str(tmp_path / "2026-05-14.json"), [])

    assert recorded, "mkstemp was not used — the atomic write changed shape"
    name = recorded[0]
    assert name.startswith(".") and name.endswith(".tmp"), name

    ignore = os.path.join(os.path.dirname(SCRIPTS_DIR), ".gitignore")
    patterns = [ln.strip() for ln in open(ignore, encoding="utf-8")
                if ln.strip() and not ln.startswith("#")]
    assert any(fnmatch.fnmatch(name, p) for p in patterns), (
        f"{name} matches no .gitignore pattern; `git add data/threads/` would "
        f"commit a stranded temp file permanently")


def test_the_js_half_of_the_rule_holds_too():
    """scripts/validate-data.mjs --fix rewrites the committed data/members.json,
    and daily-batch.yml runs it immediately before `git add data/members.json`.
    The AST sweep below only walks *.py, so this writer can regress without any
    Python test noticing — and its failure mode is the whole of #57."""
    src = open(os.path.join(SCRIPTS_DIR, "validate-data.mjs"), encoding="utf-8").read()
    assert "writeJsonAtomic(MEMBERS_PATH" in src, (
        "members.json must be written through the atomic helper (#57)")
    assert "writeFileSync(MEMBERS_PATH" not in src


# --- The rule has to hold for writers not yet written (#57/#72) ---------------

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# jsonio.py IS the implementation; the sweep would otherwise flag its own dump.
_ALLOWED = {os.path.join(SCRIPTS_DIR, "pipeline", "jsonio.py")}


def _python_files():
    for root, dirs, files in os.walk(SCRIPTS_DIR):
        dirs[:] = [d for d in dirs
                   if d not in {"tests", "__pycache__", ".pytest_cache"}]
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.join(root, name)


def _json_write_offenders(source: str, filename: str = "<test>"):
    """Line numbers of every in-place JSON write in ``source``.

    Matching the literal text ``json.dump`` is not enough, and the gap is not
    theoretical — it is the shape the NEXT writer takes. All of these write a
    file incrementally and none of them read as ``json.dump``::

        import json as j;      j.dump(obj, f)
        from json import dump; dump(obj, f)
        f.write(json.dumps(obj))

    So resolve the import aliases first, then match on what the name actually
    refers to. ``json.dumps`` on its own stays legal — it builds a string and
    touches no file, which is why ``batch_state.canonical_json`` is not caught;
    only handing that string straight to a ``.write()`` is a violation.
    """
    import ast

    tree = ast.parse(source, filename=filename)

    dump_names, dumps_names, json_aliases = set(), set(), {"json"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "json":
                    json_aliases.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "json":
            for a in node.names:
                if a.name == "dump":
                    dump_names.add(a.asname or a.name)
                elif a.name == "dumps":
                    dumps_names.add(a.asname or a.name)

    def _is(node, attr, bare_names):
        """``<json-alias>.<attr>(...)`` or a bare name imported from json."""
        if isinstance(node, ast.Attribute):
            return node.attr == attr and isinstance(node.value, ast.Name) \
                and node.value.id in json_aliases
        return isinstance(node, ast.Name) and node.id in bare_names

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is(node.func, "dump", dump_names):
            offenders.append(node.lineno)
        elif (isinstance(node.func, ast.Attribute) and node.func.attr == "write"
              and any(isinstance(arg, ast.Call) and _is(arg.func, "dumps", dumps_names)
                      for arg in node.args)):
            offenders.append(node.lineno)
    return sorted(offenders)


def test_no_script_writes_json_without_the_atomic_writer():
    """A fence, not a style check.

    Converting today's writers fixes today's corruption; the next one someone
    adds reopens it, and the symptom appears somewhere else entirely (a red
    Vercel build, a Collect crash) weeks later, with nothing pointing back here.
    Cheaper to fail in CI at the moment it is written.
    """
    offenders = []
    for path in _python_files():
        if path in _ALLOWED:
            continue
        rel = os.path.relpath(path, SCRIPTS_DIR)
        offenders += [f"{rel}:{line}" for line in
                      _json_write_offenders(open(path, encoding="utf-8").read(), path)]

    assert offenders == [], (
        "writing JSON straight into an open file serializes incrementally, so a "
        "killed process leaves a truncated file that is then the file (#57 "
        "crashed the Vercel build, #72 crashed Collect). Use "
        "pipeline.jsonio.write_json_atomic: " + ", ".join(offenders)
    )


@pytest.mark.parametrize("source", [
    'import json\nwith open("x", "w") as f:\n    json.dump({}, f)\n',
    'import json as j\nwith open("x", "w") as f:\n    j.dump({}, f)\n',
    'from json import dump\nwith open("x", "w") as f:\n    dump({}, f)\n',
    'import json\nwith open("x", "w") as f:\n    f.write(json.dumps({}))\n',
    'from json import dumps\nwith open("x", "w") as f:\n    f.write(dumps({}))\n',
], ids=["plain", "aliased-module", "from-import", "write-dumps", "aliased-dumps"])
def test_the_sweep_can_actually_see_a_violation(source):
    """The guard above passes trivially if the walk finds nothing or the matcher
    is wrong, and a fence that cannot fail is decoration (CLAUDE.md's
    fail-closed lesson). Point the same matcher at files that DO violate — one
    per evasion the plain-text version of this fence used to wave through."""
    assert _json_write_offenders(source) == [3], source


def test_the_sweep_leaves_string_building_alone():
    """`json.dumps` into a variable touches no file. Flagging it would make the
    fence noisy enough to be disabled, and batch_state.canonical_json — which
    the determinism invariants depend on — is exactly that shape."""
    assert _json_write_offenders(
        'import json\ns = json.dumps({})\nprint(s)\n') == []


def test_the_sweep_reaches_the_scripts_it_claims_to_cover():
    swept = {os.path.relpath(p, SCRIPTS_DIR) for p in _python_files()}
    for expected in ("summarize.py", "batch.py", "bulk_batch.py",
                     "gen_status.py", os.path.join("pipeline", "members.py"),
                     os.path.join("sources", "base.py")):
        assert expected in swept, f"{expected} is not being swept"

    # ...and the walk must actually reach the scripts it claims to cover.
    swept = {os.path.relpath(p, SCRIPTS_DIR) for p in _python_files()}
    for expected in ("summarize.py", "batch.py", "bulk_batch.py",
                     "gen_status.py", os.path.join("pipeline", "members.py"),
                     os.path.join("sources", "base.py")):
        assert expected in swept, f"{expected} is not being swept"
