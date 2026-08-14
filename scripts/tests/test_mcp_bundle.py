"""#73: the MCP bundle must never be half-copied, and a half-copied one must
never be accepted as complete.

`apps/mcp/scripts/copy-data.mjs` runs as the MCP project's `prebuild`. It used to
delete `apps/mcp/data/` and then fill it, so an interrupted run (SIGKILL, a build
timeout, a full disk) left the destination with `threads/` half-copied or
`members.json` truncated. The second half is what made that durable: when the
repo-root `data/` is absent — the CLI-deploy case — the script's only test for
"already bundled" was that each entry EXISTS, which a wrecked directory passes.
The MCP server then serves whatever survived, and `apps/mcp/src/lib/data.ts` is
deliberately fatal on a corrupt file (#57), so requests fail.

Same shape as #57/#72 one mechanism over: the pipeline must not create a partial
artifact, and must not accept one it cannot verify. Different mechanism though —
a directory copy, not a JSON write — so `write_json_atomic` does not apply and
the Python AST fence cannot see this file at all.
"""

import json
import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO_ROOT, "apps", "mcp", "scripts", "copy-data.mjs")
MANIFEST_NAME = ".bundle-manifest.json"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is required to run copy-data.mjs")


def _fake_repo(tmp_path, threads=("2026-05-14.json",), with_source=True):
    """A repo-shaped tree: <root>/data (source) and <root>/apps/mcp (project).

    The script derives both paths from its own location, so the copy is exercised
    by placing a copy of the script in a fake tree rather than by passing flags —
    which also means a refactor that hard-codes a path is caught here.
    """
    project = tmp_path / "apps" / "mcp"
    (project / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, project / "scripts" / "copy-data.mjs")
    if with_source:
        source = tmp_path / "data"
        (source / "threads").mkdir(parents=True)
        for name in threads:
            (source / "threads" / name).write_text(
                json.dumps([{"id": f"t_{name}"}]), encoding="utf-8")
        (source / "members.json").write_text(json.dumps({"m_1": {}}),
                                             encoding="utf-8")
    return project


def _run(project):
    return subprocess.run(
        ["node", str(project / "scripts" / "copy-data.mjs")],
        capture_output=True, text=True)


def test_a_normal_run_copies_the_bundle_and_records_a_manifest(tmp_path):
    project = _fake_repo(tmp_path, threads=("a.json", "b.json"))
    r = _run(project)
    assert r.returncode == 0, r.stderr

    dest = project / "data"
    assert (dest / "threads" / "a.json").exists()
    assert (dest / "members.json").exists()

    manifest = json.loads((dest / MANIFEST_NAME).read_text(encoding="utf-8"))
    # Per-file sizes, not just a count: truncation is the failure mode, and a
    # count cannot see it.
    assert manifest["files"][os.path.join("threads", "a.json")] > 0
    assert "members.json" in manifest["files"]


def test_a_truncated_bundle_is_rejected_instead_of_reused(tmp_path):
    """The durable half of #73. With the source absent, this check is the only
    thing standing between a wrecked bundle and production."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    shutil.rmtree(tmp_path / "data")                    # the CLI-deploy case

    victim = project / "data" / "threads" / "2026-05-14.json"
    victim.write_text('[{"id": "t_2026', encoding="utf-8")   # interrupted copy

    r = _run(project)
    assert r.returncode != 0, "a truncated bundle was accepted as complete"
    assert "2026-05-14.json" in (r.stderr + r.stdout), (
        "the rejected file must be named — otherwise the operator has a whole "
        "directory to search")


def test_a_bundle_missing_a_file_is_rejected(tmp_path):
    project = _fake_repo(tmp_path, threads=("a.json", "b.json"))
    assert _run(project).returncode == 0
    shutil.rmtree(tmp_path / "data")
    os.remove(project / "data" / "threads" / "b.json")

    r = _run(project)
    assert r.returncode != 0
    assert "b.json" in (r.stderr + r.stdout)


def test_a_bundle_missing_a_whole_include_entry_is_rejected(tmp_path):
    """The manifest describes what was copied, so it agrees with itself about a
    bundle that never held `threads/` at all. The old `INCLUDE.every(existsSync)`
    check caught exactly this, and a manifest built from the copy cannot — the
    required set has to be asserted against INCLUDE, not against the copy."""
    project = _fake_repo(tmp_path, with_source=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "members.json").write_text("{}", encoding="utf-8")

    r = _run(project)                      # source present, but threads/ is not
    assert r.returncode != 0, (
        "a bundle with no threads/ at all was assembled and accepted; the MCP "
        "server would serve zero threads under a green build")
    assert "threads" in (r.stderr + r.stdout)


def test_an_unverifiable_bundle_is_rejected(tmp_path):
    """No manifest means the bundle predates this check or was assembled by
    something else. Either way it cannot be verified, and "cannot verify" must
    not read as "fine" — that reading is the whole bug."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    shutil.rmtree(tmp_path / "data")
    os.remove(project / "data" / MANIFEST_NAME)

    r = _run(project)
    assert r.returncode != 0
    assert MANIFEST_NAME in (r.stderr + r.stdout)


def test_a_malformed_source_file_is_not_swapped_in(tmp_path):
    """Sizes are recorded FROM the copy, so a file that was already broken at the
    source is recorded as broken and matches itself forever. Parsing is the only
    check that survives that, and #73 asked for it by name."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    before = (project / "data" / "threads" / "2026-05-14.json").read_text(
        encoding="utf-8")

    (tmp_path / "data" / "threads" / "2026-05-14.json").write_text(
        '[{"id": "t_2026', encoding="utf-8")

    r = _run(project)
    assert r.returncode != 0, "a malformed source file was bundled and shipped"
    assert "2026-05-14.json" in (r.stderr + r.stdout)
    assert (project / "data" / "threads" / "2026-05-14.json").read_text(
        encoding="utf-8") == before, "the good bundle was replaced by the bad copy"


def test_manifest_keys_are_posix_separated(tmp_path):
    """A bundle built on native Windows records `threads\\a.json` if the keys go
    through path.join; every file then reads as missing on Vercel's Linux
    builder, and the CLI-deploy reuse path dies on a bundle that is fine.

    The runtime assertion below cannot fail on Linux — path.join already yields
    "/" here — so it would approve the regression it exists to catch. The source
    check is the actual guard; it is the only one that can run on the platform
    this test's own CI uses.
    """
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    manifest = json.loads(
        (project / "data" / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "threads/2026-05-14.json" in manifest["files"], manifest["files"]

    source = open(SCRIPT, encoding="utf-8").read()
    rel_line = next(l for l in source.splitlines() if "const rel = prefix" in l)
    assert "path.join" not in rel_line, (
        f"manifest keys are built with path.join again: {rel_line.strip()}")
    assert 'rel.split("/")' in source, (
        "manifest keys must be resolved back through split(\"/\"), not treated "
        "as native paths")


def test_a_leftover_staging_directory_is_swept(tmp_path):
    """A run killed between the two renames runs none of its own cleanup, and
    nothing else ever collects what it left — 60MB uploaded with the project."""
    project = _fake_repo(tmp_path)
    orphan = project / ".data-staging-999999"
    (orphan / "threads").mkdir(parents=True)
    (orphan / "threads" / "junk.json").write_text("[]", encoding="utf-8")
    stale_retired = project / ".data-retired-999998"
    stale_retired.mkdir()

    assert _run(project).returncode == 0
    assert not orphan.exists(), "a stale staging directory outlived a later run"
    assert not stale_retired.exists()


def test_a_live_processs_staging_directory_is_left_alone(tmp_path):
    """Sweeping by pattern alone would delete a concurrent run's working set —
    and one of the moments to do it is right after that run moved the good
    bundle aside, which destroys the only complete copy."""
    project = _fake_repo(tmp_path)
    mine = project / f".data-retired-{os.getpid()}"   # this pytest process: alive
    mine.mkdir()

    r = _run(project)
    assert r.returncode == 0, r.stderr
    assert mine.exists(), "a live process's directory was swept"
    assert "still running" in (r.stderr + r.stdout)


def test_an_unrecorded_file_inside_threads_is_rejected(tmp_path):
    """`apps/mcp/src/lib/data.ts` reads EVERY *.json under threads/, so a file
    the manifest never described is either unvouched-for content or a fatal
    parse at request time. Outside an INCLUDE entry it is only weight."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    shutil.rmtree(tmp_path / "data")

    (project / "data" / "threads" / "stale.json").write_text(
        '[{"id": "t_stale', encoding="utf-8")

    r = _run(project)
    assert r.returncode != 0, "an unverified extra file under threads/ was accepted"
    assert "stale.json" in (r.stderr + r.stdout)


@pytest.mark.parametrize("name", [
    "NOTES.txt",                            # outside every INCLUDE entry
    "threads/.DS_Store",                    # inside, but the runtime never opens it
    "threads/2026-05-14.progress.json",     # inside and .json, but filtered out
])
def test_an_unrecorded_file_the_runtime_never_opens_is_only_a_warning(tmp_path, name):
    """The other side of the same call. The predicate has to be the runtime's
    own — reject what it will read, tolerate what it will not — or a stray
    .DS_Store breaks every CLI deploy."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    shutil.rmtree(tmp_path / "data")
    (project / "data" / name).write_text("scratch", encoding="utf-8")

    r = _run(project)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("rel,content", [
    ("threads/2026-05-14.json", "{}"),      # loadThreads skips a non-array
    ("members.json", "[]"),                 # loadMembers takes whatever it got
])
def test_a_file_of_the_wrong_shape_is_rejected(tmp_path, rel, content):
    """Parsing is not enough: neither of these throws at request time, they just
    serve nothing. A green deploy with zero threads is the failure this whole
    file is named after — arriving quietly instead of loudly."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0

    (tmp_path / "data" / rel).write_text(content, encoding="utf-8")

    r = _run(project)
    assert r.returncode != 0, f"{rel} = {content} was bundled and shipped"
    assert "shape" in (r.stderr + r.stdout)


def test_a_symlinked_file_deep_in_the_bundle_is_rejected(tmp_path):
    """Not just the INCLUDE entry itself: a single thread file can be a link,
    and it resolves — with the right size and valid JSON — on the machine that
    built it. Vercel uploads the link."""
    project = _fake_repo(tmp_path, threads=("a.json", "b.json"))
    assert _run(project).returncode == 0

    outside = project / "b-real.json"
    os.rename(project / "data" / "threads" / "b.json", outside)
    os.symlink(outside, project / "data" / "threads" / "b.json")
    shutil.rmtree(tmp_path / "data")

    r = _run(project)
    assert r.returncode != 0, "a symlinked thread file was accepted"
    assert "symlink" in (r.stderr + r.stdout)


def test_two_verifiable_retired_bundles_stop_rather_than_guess(tmp_path):
    """A manifest records what a bundle holds, not when. Picking by readdir
    order would ship the older one under a green build."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    shutil.copytree(project / "data", project / ".data-retired-999998")
    os.rename(project / "data", project / ".data-retired-999999")
    shutil.rmtree(tmp_path / "data")

    r = _run(project)
    assert r.returncode != 0, "one of two candidates was restored by luck of ordering"
    assert ".data-retired-999998" in (r.stderr + r.stdout)
    assert ".data-retired-999999" in (r.stderr + r.stdout)
    assert (project / ".data-retired-999998").exists(), "a candidate was deleted"
    assert (project / ".data-retired-999999").exists()


def test_a_bundle_is_restored_when_a_run_died_mid_swap(tmp_path):
    """The one-rename-wide window: old moved aside, new not yet moved in. The
    previous bundle is whole but not where anything looks for it."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    # Reproduce the crashed state exactly: DEST gone, a retired copy beside it.
    os.rename(project / "data", project / ".data-retired-999999")
    shutil.rmtree(tmp_path / "data")                    # and no source to redo it

    r = _run(project)
    assert r.returncode == 0, (
        f"a complete bundle sat next to DEST and the run failed anyway: {r.stderr}")
    assert (project / "data" / "members.json").exists()
    assert not (project / ".data-retired-999999").exists()


def test_a_failed_rollback_keeps_the_previous_bundle_and_says_where(tmp_path):
    """Both renames fail: the new bundle cannot go in, and the old one cannot go
    back. The old one is still whole — an unconditional cleanup deletes it, which
    is the exact destruction this whole change exists to make impossible."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    before = (project / "data" / "members.json").read_text(encoding="utf-8")

    # Let `DEST -> RETIRED` through, then fail the install and the rollback.
    (project / "scripts" / "breakrename.cjs").write_text(
        "const fs = require('fs');\n"
        "const real = fs.renameSync;\n"
        "let n = 0;\n"
        "fs.renameSync = (a, b) => {\n"
        "  if (++n === 1) return real(a, b);\n"
        "  throw Object.assign(new Error('EBUSY: injected'), {code: 'EBUSY'});\n"
        "};\n",
        encoding="utf-8")
    r = subprocess.run(
        ["node", "-r", str(project / "scripts" / "breakrename.cjs"),
         str(project / "scripts" / "copy-data.mjs")],
        capture_output=True, text=True)

    assert r.returncode != 0
    retired = [n for n in os.listdir(project) if n.startswith(".data-retired-")]
    assert len(retired) == 1, (
        f"the only complete bundle was deleted by cleanup: {os.listdir(project)}")
    assert (project / retired[0] / "members.json").read_text(
        encoding="utf-8") == before
    assert retired[0] in r.stderr, "the operator is not told where their data went"

    # And the next run puts it back on its own, as the error promises.
    r2 = _run(project)
    assert r2.returncode == 0, r2.stderr
    assert (project / "data" / "members.json").read_text(encoding="utf-8") == before


def test_a_manifest_key_escaping_the_bundle_is_rejected(tmp_path):
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    shutil.rmtree(tmp_path / "data")

    manifest_path = project / "data" / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["../../data/members.json"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    r = _run(project)
    assert r.returncode != 0
    assert "bundle-relative" in (r.stderr + r.stdout)


def test_a_required_entry_of_the_wrong_kind_is_rejected(tmp_path):
    """`threads` as a regular file satisfies the entry name and then reads as
    zero threads at request time. Coverage is what catches it — nothing is
    recorded under `threads/` — so this pins the outcome, not one check."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    shutil.rmtree(tmp_path / "data")

    dest = project / "data"
    manifest = json.loads((dest / MANIFEST_NAME).read_text(encoding="utf-8"))
    shutil.rmtree(dest / "threads")
    (dest / "threads").write_text("not a directory", encoding="utf-8")
    manifest["files"] = {k: v for k, v in manifest["files"].items()
                         if not k.startswith("threads/")}
    manifest["files"]["threads"] = len("not a directory")
    (dest / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    r = _run(project)
    assert r.returncode != 0, "threads/ as a plain file was accepted"
    assert "threads" in (r.stderr + r.stdout)


def test_a_required_entry_that_is_a_symlink_is_rejected(tmp_path):
    """The case only the kind check sees: every file resolves, every size and
    parse is right, and none of it is IN the bundle. It works on the machine
    that built it and ships an empty directory to Vercel."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0

    real = project / "threads-real"
    os.rename(project / "data" / "threads", real)
    os.symlink(real, project / "data" / "threads")
    shutil.rmtree(tmp_path / "data")                    # the CLI-deploy case

    r = _run(project)
    assert r.returncode != 0, "a bundle whose threads/ lives outside it was accepted"
    assert "symlink" in (r.stderr + r.stdout)


def test_a_verified_bundle_is_reused_without_the_source(tmp_path):
    """The case that must keep working: this early return is why Vercel CLI
    deploys succeed at all. A verification that rejected everything would be
    just as broken as one that accepted everything."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    shutil.rmtree(tmp_path / "data")

    r = _run(project)
    assert r.returncode == 0, r.stderr
    assert (project / "data" / "members.json").exists()


def test_a_failed_copy_leaves_the_previous_bundle_intact(tmp_path):
    """The other half: the destination is not destroyed before the replacement
    is complete. Staged into a sibling directory, then swapped."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    before = (project / "data" / "members.json").read_text(encoding="utf-8")

    # Make the copy fail partway: an entry that exists but cannot be read.
    unreadable = tmp_path / "data" / "threads"
    os.chmod(unreadable, 0o000)
    try:
        r = _run(project)
        if r.returncode == 0:
            pytest.skip("running as root: an unreadable directory still copies")
    finally:
        os.chmod(unreadable, 0o755)

    assert (project / "data" / "members.json").read_text(encoding="utf-8") == before, (
        "the previous bundle was destroyed by a copy that then failed")
    assert (project / "data" / MANIFEST_NAME).exists()


def test_no_staging_or_swap_directory_survives_a_successful_run(tmp_path):
    """A leftover staging directory would be uploaded with the project and
    counted in the function bundle's size budget."""
    project = _fake_repo(tmp_path)
    assert _run(project).returncode == 0
    leftovers = [n for n in os.listdir(project)
                 if n != "data" and n != "scripts" and n.startswith(".data")]
    assert leftovers == [], leftovers
