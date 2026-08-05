"""Guards for the early-sidecar-commit safety net (#49).

The net exists so that a job killed mid-poll does not orphan an already-paid-for
batch. In CI it had never once fired: ``git config user.name/email`` only ran in
the later "Commit and push data" step, so every ``git commit`` here exited 128 —
and the failure was logged at warning level, inside a run that GitHub still
painted green.

These tests pin the two things that made that invisible: a failed *commit* (the
net is not armed) must be distinguishable from a failed *push* (the net is
armed, the sidecar just has not left the runner yet), and neither may be
swallowed silently in CI. The commit branch annotates at ``::error::`` — the
same level the stale-schema guard uses — because "already-paid-for batch will
be lost" is not a yellow-triangle fact, and a ``::warning::`` among the
warnings a normal run already emits is how the original bug hid.
"""

import subprocess

import pytest

import summarize


class _FakeGit:
    """Records git argv and fails whichever subcommand a test names."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, argv, *args, **kwargs):
        self.calls.append(argv[1])
        if argv[1] == self.fail_on:
            raise subprocess.CalledProcessError(128, argv)
        return None


@pytest.fixture
def in_ci(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")


def test_successful_commit_pushes_and_stays_quiet(monkeypatch, capsys, caplog):
    git = _FakeGit()
    monkeypatch.setattr(summarize.subprocess, "run", git)

    with caplog.at_level("INFO"):
        summarize._git_commit_sidecar("data/pending-batches/2026-05-14.json", "2026-05-14")

    assert git.calls == ["add", "commit", "push"]
    assert "::" not in capsys.readouterr().out
    assert "Early-committed sidecar" in caplog.text


def test_failed_commit_is_an_error_and_skips_the_push(monkeypatch, capsys, caplog, in_ci):
    """The #49 case. The batch is already submitted, so a dead net means the
    spend is unrecoverable if the job dies — this cannot be a quiet warning."""
    git = _FakeGit(fail_on="commit")
    monkeypatch.setattr(summarize.subprocess, "run", git)

    with caplog.at_level("INFO"):
        summarize._git_commit_sidecar("data/pending-batches/2026-05-14.json", "2026-05-14")

    assert git.calls == ["add", "commit"]          # no pointless push after
    assert any(r.levelname == "ERROR" for r in caplog.records)
    assert "::error::" in capsys.readouterr().out


def test_failed_add_is_reported_as_a_dead_net_not_as_a_commit_failure(
    monkeypatch, capsys, caplog, in_ci
):
    """`git add` failing has the same consequence as `git commit` failing, but
    saying "commit failed" sends the operator to look at the wrong step."""
    git = _FakeGit(fail_on="add")
    monkeypatch.setattr(summarize.subprocess, "run", git)

    with caplog.at_level("INFO"):
        summarize._git_commit_sidecar("data/pending-batches/2026-05-14.json", "2026-05-14")

    assert git.calls == ["add"]
    assert any(r.levelname == "ERROR" for r in caplog.records)
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "stage failed" in out


def test_failed_push_is_reported_but_not_escalated(monkeypatch, capsys, caplog, in_ci):
    """A push race with a concurrent run still leaves the sidecar committed
    locally, so the run's final push carries it. Different severity, and the
    log has to say which one happened."""
    git = _FakeGit(fail_on="push")
    monkeypatch.setattr(summarize.subprocess, "run", git)

    with caplog.at_level("INFO"):
        summarize._git_commit_sidecar("data/pending-batches/2026-05-14.json", "2026-05-14")

    assert git.calls == ["add", "commit", "push"]
    assert not any(r.levelname == "ERROR" for r in caplog.records)
    assert "push failed" in caplog.text
    out = capsys.readouterr().out
    assert "::warning::" in out
    assert "::error::" not in out


def test_no_github_annotation_outside_ci(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(summarize.subprocess, "run", _FakeGit(fail_on="commit"))

    summarize._git_commit_sidecar("data/pending-batches/2026-05-14.json", "2026-05-14")

    assert capsys.readouterr().out == ""
