"""#82: the day's data commit must survive something else pushing during the run.

The commit step measured `AHEAD` against the `origin/<branch>` its checkout
fetched and then pushed once. That count stays true while the remote moves on,
so any other push during the run turned this into a non-fast-forward rejection —
and under the workflow's `bash -e` the step died with the commit still only
local, i.e. the run produced everything and published none of it. On 2026-08-23
a hand push during a recovery run cost 236 assembled threads exactly that way.

These run the step's OWN shell against a scratch repo with a real diverged
remote, sliced out of the YAML rather than copied. `git push` behaviour is the
thing being fixed, so asserting on the workflow text would pass on a step that
still cannot push — which is how the bug got there: the block's comment already
reasoned carefully about the early sidecar push losing this race, and left the
final push treating it as fatal.
"""

import os
import shutil
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Enough identity for the scratch repos; the real job configures this once at
# the top of the workflow.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _push_tail():
    """The step's shell from the `AHEAD` guard to the end.

    Sliced, not copied: a copy drifts, and a drifted copy of a push retry is a
    test that reports a retry nothing runs.
    """
    yaml = pytest.importorskip("yaml")
    path = os.path.join(REPO_ROOT, ".github", "workflows", "daily-batch.yml")
    with open(path, encoding="utf-8") as fh:
        spec = yaml.safe_load(fh.read())
    steps = spec["jobs"]["fetch-and-summarize"]["steps"]
    run = next(s for s in steps if s.get("id") == "commit")["run"]
    marker = "if ! AHEAD="
    assert marker in run, "the commit step no longer has the expected push guard"
    return run[run.index(marker):]


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True,
                          env=dict(os.environ, **GIT_ENV))


def _diverged_clone(tmp_path, their_file="theirs.json", their_body="[]"):
    """A working repo one commit ahead, whose remote is one DIFFERENT commit ahead.

    Mirrors the real shape: the run committed locally (its early sidecar push
    already landed, so `git push` is the only thing left) and someone else
    pushed meanwhile.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                   check=True, env=dict(os.environ, **GIT_ENV))

    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True,
                   env=dict(os.environ, **GIT_ENV))
    (seed / "data").mkdir()
    (seed / "data" / "base.json").write_text("[]", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "push", "-q", "origin", "main")

    ours = tmp_path / "ours"
    subprocess.run(["git", "clone", "-q", str(remote), str(ours)], check=True,
                   env=dict(os.environ, **GIT_ENV))
    # Our unpushed "day's data commit".
    (ours / "data" / "2026-05-14.json").write_text('[{"id": "t_1"}]',
                                                   encoding="utf-8")
    _git(ours, "add", "-A")
    _git(ours, "commit", "-q", "-m", "chore(pipeline): data")

    # Their push, landing after our checkout fetched origin/main.
    (seed / their_file).write_text(their_body, encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "theirs")
    _git(seed, "push", "-q", "origin", "main")

    return remote, ours


def _run_push(repo):
    return subprocess.run(
        ["bash", "-e", "-c", _push_tail()], cwd=str(repo),
        capture_output=True, text=True,
        env=dict(os.environ, GITHUB_REF_NAME="main", **GIT_ENV))


def _remote_subjects(remote):
    out = subprocess.run(["git", "-C", str(remote), "log", "--format=%s", "main"],
                         check=True, capture_output=True, text=True,
                         env=dict(os.environ, **GIT_ENV)).stdout
    return out.split("\n")


def test_a_concurrent_push_no_longer_costs_the_day_its_commit(tmp_path):
    """The whole point. Both commits must end up on the remote."""
    remote, ours = _diverged_clone(tmp_path)
    proc = _run_push(ours)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    subjects = _remote_subjects(remote)
    assert "chore(pipeline): data" in subjects, (
        "the run's data commit never reached the remote: " + proc.stdout + proc.stderr)
    assert "theirs" in subjects, "the other push was discarded — this must rebase, not force"


def test_the_retry_is_a_rebase_and_not_a_merge(tmp_path):
    """A merge would publish the data too, so the assertion above passes either
    way. It has to be a rebase: `main` on this repo is a linear history of
    generated data commits, and a merge commit per race makes `git log -n 10`
    — which the stalled-pipeline check counts data commits in — stop meaning
    what that check reads it as.
    """
    remote, ours = _diverged_clone(tmp_path)
    assert _run_push(ours).returncode == 0
    parents = subprocess.run(
        ["git", "-C", str(remote), "rev-list", "--merges", "--count", "main"],
        check=True, capture_output=True, text=True,
        env=dict(os.environ, **GIT_ENV)).stdout.strip()
    assert parents == "0", "the retry merged instead of rebasing"


def test_an_unstaged_tracked_file_does_not_block_the_rebase(tmp_path):
    """#75 leaves an unreadable data file deliberately unstaged, so the tree is
    dirty at push time by design. Without `--autostash` the rebase refuses to
    start and this fix would fail on exactly the mornings both features matter.
    """
    remote, ours = _diverged_clone(tmp_path)
    (ours / "data" / "base.json").write_text('[{"id": "hand-edited"}]',
                                             encoding="utf-8")
    proc = _run_push(ours)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "chore(pipeline): data" in _remote_subjects(remote)


def test_a_conflicting_push_fails_loudly_and_leaves_no_rebase_in_progress(tmp_path):
    """The other direction. A real conflict is a human decision, so retrying is
    wrong — but going quiet is worse: the commit dies with the runner, so the
    error has to say the data did not reach the site. And the rebase must be
    aborted, or the checkout is left mid-rebase for whatever runs next.
    """
    remote, ours = _diverged_clone(tmp_path, their_file="data/2026-05-14.json",
                                   their_body='[{"id": "conflict"}]')
    proc = _run_push(ours)
    assert proc.returncode != 0, "a conflict was swallowed: " + proc.stdout
    assert "::error::" in proc.stdout
    assert "LOCAL" in proc.stdout, (
        "the error does not say the commit never reached the site")
    assert not (ours / ".git" / "rebase-merge").exists()
    assert not (ours / ".git" / "rebase-apply").exists()


def test_nothing_to_push_stays_silent_and_green(tmp_path):
    """The no-op path. A retry loop that treats "already up to date" as a
    rejection would red every morning the pipeline finds nothing.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)],
                   check=True, env=dict(os.environ, **GIT_ENV))
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True,
                   env=dict(os.environ, **GIT_ENV))
    (seed / "base").write_text("x", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "push", "-q", "origin", "main")
    ours = tmp_path / "ours"
    subprocess.run(["git", "clone", "-q", str(remote), str(ours)], check=True,
                   env=dict(os.environ, **GIT_ENV))

    proc = _run_push(ours)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Nothing to push" in proc.stdout
    assert "::warning::" not in proc.stdout
