"""Guards for the Vercel Ignored Build Step (#101).

`vercel.json` runs `scripts/vercel-ignore-build.sh` before every Git-triggered
build of the open-gikai project. Exit 0 SKIPS the production build, so the one
outcome these tests exist to prevent is a 0 when the site's inputs changed:
that leaves the published site stale with nothing pointing back here.

Every case asserts the `reason=` line as well as the exit code. Exit code alone
is not evidence: a case meant to reach the diff check that is instead stopped
by an earlier guard (no VERCEL_ENV, an empty sha) returns the same 1, and the
diff check could then be broken to "always skip" without anything going red.
"""

import json
import os
import re
import stat
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "vercel-ignore-build.sh")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}

EXPECTED_IGNORE_COMMAND = "bash scripts/vercel-ignore-build.sh || exit 1"


def _git(repo, *args):
    out = subprocess.run(["git", "-C", str(repo), *args], check=True,
                         capture_output=True, text=True,
                         env=dict(os.environ, **GIT_ENV))
    return out.stdout.strip()


def _write(repo, rel, text="x"):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _commit(repo, msg="c"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _write(r, "data/threads/2026-11-01.json", "[]")
    _write(r, "data/pending-batches/2026-11-01.json", "{}")
    _write(r, "data/status.json", "{}")
    _commit(r, "base")
    return r


def _run(cwd, prev=None, env_name="production", path_prefix=None):
    env = dict(os.environ, **GIT_ENV)
    env.pop("VERCEL_ENV", None)
    env.pop("VERCEL_GIT_PREVIOUS_SHA", None)
    if env_name is not None:
        env["VERCEL_ENV"] = env_name
    if prev is not None:
        env["VERCEL_GIT_PREVIOUS_SHA"] = prev
    if path_prefix:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    p = subprocess.run(["bash", SCRIPT], cwd=str(cwd), capture_output=True,
                       text=True, env=env)
    return p.returncode, p.stdout


def _reason(stdout):
    m = re.search(r"^vercel-ignore: (build|skip) reason=(\S+)", stdout, re.M)
    assert m, f"no decision line in: {stdout!r}"
    return m.group(1), m.group(2)


def _assert(result, code, verdict, reason):
    rc, out = result
    assert (rc, _reason(out)) == (code, (verdict, reason)), out


# ---------- skip (exit 0) ----------

def test_sidecar_add_update_delete_only_skips(repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, "data/pending-batches/2026-11-02.json", "{}")       # add
    _write(repo, "data/pending-batches/2026-11-01.json", '{"a":1}')  # update
    _commit(repo)
    _assert(_run(repo, prev), 0, "skip", "pending_batches_only")
    prev = _git(repo, "rev-parse", "HEAD")
    (repo / "data/pending-batches/2026-11-02.json").unlink()        # delete
    _commit(repo)
    _assert(_run(repo, prev), 0, "skip", "pending_batches_only")


def test_several_sidecar_commits_since_previous_skip(repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, "data/pending-batches/a.json")
    _commit(repo)
    _write(repo, "data/pending-batches/b.json")
    _commit(repo)
    _assert(_run(repo, prev), 0, "skip", "pending_batches_only")


def test_rename_inside_sidecar_dir_skips(repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "data/pending-batches/2026-11-01.json",
         "data/pending-batches/2026-11-09.json")
    _commit(repo)
    _assert(_run(repo, prev), 0, "skip", "pending_batches_only")


# ---------- build (exit 1): something the site reads changed ----------

def test_sidecar_mixed_with_threads_builds(repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, "data/pending-batches/a.json")
    _write(repo, "data/threads/2026-11-01.json", '[{"id": 1}]')
    _commit(repo)
    _assert(_run(repo, prev), 1, "build", "outside_pending_batches")


def test_threads_content_change_with_same_line_count_builds(repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, "data/threads/2026-11-01.json", "{}")  # "+0 threads" day
    _commit(repo)
    _assert(_run(repo, prev), 1, "build", "outside_pending_batches")


@pytest.mark.parametrize("rel", [
    "data/status.json", "public/feed.xml", "docs/a.md", "scripts/x.py",
    "data/pending-batches-old/a.json",
])
def test_other_paths_build(repo, rel):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, rel, "changed")
    _commit(repo)
    _assert(_run(repo, prev), 1, "build", "outside_pending_batches")


def test_pending_batches_as_a_file_builds(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _write(r, "README", "x")
    prev = _commit(r)
    _write(r, "data/pending-batches", "not a dir")
    _commit(r)
    _assert(_run(r, prev), 1, "build", "outside_pending_batches")


def test_rename_threads_into_sidecar_dir_builds(repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "data/threads/2026-11-01.json",
         "data/pending-batches/moved.json")
    _commit(repo)
    _assert(_run(repo, prev), 1, "build", "outside_pending_batches")


def test_rename_sidecar_into_threads_builds(repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "data/pending-batches/2026-11-01.json",
         "data/threads/moved.json")
    _commit(repo)
    _assert(_run(repo, prev), 1, "build", "outside_pending_batches")


def test_unpublished_threads_change_below_a_sidecar_tip_builds(repo):
    """HEAD^..HEAD would see only the sidecar and skip; the last READY is older."""
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, "data/threads/2026-11-01.json", '[{"id": 2}]')
    _commit(repo, "threads (its build failed)")
    _write(repo, "data/pending-batches/z.json")
    _commit(repo, "chore(pipeline): persist pending batch")
    _assert(_run(repo, prev), 1, "build", "outside_pending_batches")


@pytest.mark.parametrize("name", ["data/threads/a b.json", "data/threads/a\nb.json"])
def test_odd_names_outside_prefix_build(repo, name):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, name)
    _commit(repo)
    _assert(_run(repo, prev), 1, "build", "outside_pending_batches")


# ---------- build (exit 1): cannot decide ----------

def test_previous_unset_builds(repo):
    _assert(_run(repo, None), 1, "build", "previous_missing")


def test_previous_empty_builds(repo):
    _assert(_run(repo, ""), 1, "build", "previous_missing")


@pytest.mark.parametrize("bad", ["--output=/tmp/x", "HEAD~1", "zzzzzzz", "abc"])
def test_previous_malformed_builds(repo, bad):
    _assert(_run(repo, bad), 1, "build", "bad_sha")


def test_previous_nonexistent_builds(repo):
    _assert(_run(repo, "0" * 40), 1, "build", "previous_unavailable")


def test_previous_missing_from_shallow_clone_builds(tmp_path, repo):
    prev = _git(repo, "rev-parse", "HEAD")
    for i in range(3):
        _write(repo, f"data/pending-batches/{i}.json")
        _commit(repo)
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth=1", f"file://{repo}", str(shallow)],
                   check=True, env=dict(os.environ, **GIT_ENV))
    _assert(_run(shallow, prev), 1, "build", "previous_unavailable")


def test_same_sha_builds(repo):
    head = _git(repo, "rev-parse", "HEAD")
    _assert(_run(repo, head), 1, "build", "no_changes")


def test_different_commit_same_tree_builds(repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "empty")
    _assert(_run(repo, prev), 1, "build", "no_changes")


@pytest.mark.parametrize("env_name", ["preview", "", None])
def test_not_production_builds(repo, env_name):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, "data/pending-batches/a.json")
    _commit(repo)
    _assert(_run(repo, prev, env_name=env_name), 1, "build", "not_production")


def test_outside_a_repo_builds(tmp_path, monkeypatch):
    d = tmp_path / "plain"
    d.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    _assert(_run(d, "a" * 40), 1, "build", "not_a_repo")


def _fake_git(tmp_path, body):
    """A `git` on PATH that runs `body` for `diff` and defers to the real one otherwise."""
    real = subprocess.run(["bash", "-c", "command -v git"], capture_output=True,
                          text=True, check=True).stdout.strip()
    d = tmp_path / "fakebin"
    d.mkdir()
    g = d / "git"
    g.write_text(f'#!/bin/bash\nif [ "$1" = diff ]; then\n{body}\nfi\nexec {real} "$@"\n')
    g.chmod(g.stat().st_mode | stat.S_IEXEC)
    return str(d)


def test_git_diff_failure_builds(tmp_path, repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, "data/pending-batches/a.json")
    _commit(repo)
    fake = _fake_git(tmp_path, "exit 128")
    _assert(_run(repo, prev, path_prefix=fake), 1, "build", "git_diff_failed")


def test_git_diff_failure_after_partial_output_builds(tmp_path, repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, "data/pending-batches/a.json")
    _commit(repo)
    fake = _fake_git(tmp_path, "printf 'data/pending-batches/a.json\\0'; exit 1")
    _assert(_run(repo, prev, path_prefix=fake), 1, "build", "git_diff_failed")


def test_last_record_without_trailing_nul_is_still_judged(tmp_path, repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, "data/pending-batches/a.json")
    _commit(repo)
    fake = _fake_git(tmp_path,
                     "printf 'data/pending-batches/a.json\\0data/threads/x.json'; exit 0")
    _assert(_run(repo, prev, path_prefix=fake), 1, "build", "outside_pending_batches")


def test_empty_record_builds(tmp_path, repo):
    prev = _git(repo, "rev-parse", "HEAD")
    _write(repo, "data/pending-batches/a.json")
    _commit(repo)
    fake = _fake_git(tmp_path, "printf 'data/pending-batches/a.json\\0\\0'; exit 0")
    _assert(_run(repo, prev, path_prefix=fake), 1, "build", "outside_pending_batches")


# ---------- wiring and the premise the skip rests on ----------

def test_vercel_json_wires_the_script():
    with open(os.path.join(REPO_ROOT, "vercel.json")) as f:
        cfg = json.load(f)
    assert cfg.get("ignoreCommand") == EXPECTED_IGNORE_COMMAND


def test_no_vercel_json_under_apps_mcp():
    # open-gikai-mcp is deployed by the CLI from apps/mcp; this check must not reach it.
    assert not os.path.exists(os.path.join(REPO_ROOT, "apps", "mcp", "vercel.json"))


BUILD_INPUTS = [
    "src", "scripts/lib", "next.config.ts", "package.json",
    "scripts/validate-data.mjs", "scripts/enrich-members.mjs",
    "scripts/generate-feeds.js", "scripts/generate-sitemap.mjs",
    "scripts/gen_status.py", "scripts/pipeline/jsonio.py",
    "scripts/pipeline/__init__.py",
]


def test_build_inputs_do_not_read_pending_batches():
    """The skip is safe only while nothing the build reads lives in data/pending-batches/.

    A new build input that reads it must remove the skip in the same change.
    """
    offenders = []
    for rel in BUILD_INPUTS:
        root = os.path.join(REPO_ROOT, rel)
        paths = [root] if os.path.isfile(root) else [
            os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs]
        for p in paths:
            with open(p, encoding="utf-8", errors="replace") as f:
                if "pending-batches" in f.read():
                    offenders.append(os.path.relpath(p, REPO_ROOT))
    assert offenders == []


def test_sidecar_dir_is_where_the_skip_looks():
    import pipeline.batch_state as bs
    assert bs.PENDING_DIR == os.path.join("data", "pending-batches")
