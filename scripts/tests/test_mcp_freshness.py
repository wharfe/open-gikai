"""#85: the MCP server must not be able to serve stale data quietly.

`apps/mcp` is a second Vercel project, deployed by hand, and it stopped: its
last deploy was 2026-05-22 and it answered with data ending 2026-05-19 while
the site was at 2026-08-20. Three months. Nothing caught it because the
endpoint returns 200 and `tools/list` works — it was never broken, only old,
and `uptime.yml` checks liveness, which structurally cannot see that.

Two halves are tested here, because either alone is a fence around nothing: the
comparison itself, and the workflow wiring that runs it every morning.
"""

import json
import urllib.error

import pytest

import check_mcp_freshness as freshness

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _served(dates, wrap=lambda d: d):
    """A fake urlopen answering the MCP envelope shape with these dates."""
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            payload = json.dumps(wrap(dates))
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1,
                 "result": {"content": [{"type": "text", "text": payload}]}}
            ).encode("utf-8")

    return lambda request, timeout=None: _Response()


def _threads(tmp_path, *dates):
    d = tmp_path / "threads"
    d.mkdir()
    for date in dates:
        (d / f"{date}.json").write_text("[]", encoding="utf-8")
    return str(d)


# --------------------------------------------------------------------------
# The comparison
# --------------------------------------------------------------------------

def test_the_newest_served_date_is_read_from_the_tool_envelope():
    got = freshness.newest_served_date(
        "http://x", opener=_served(["2026-05-19", "2026-08-20", "2023-03-31"]))
    assert got == "2026-08-20"


@pytest.mark.parametrize("wrap", [
    lambda d: d,                                  # a bare list
    lambda d: {"dates": d},                       # wrapped
    lambda d: [{"date": x} for x in d],           # list of objects
])
def test_the_shapes_the_tool_actually_answers_with_are_all_read(wrap):
    """The tool's own reply shape is not this repo's to pin, and a shape this
    check cannot read would raise — which the caller reports as staleness, i.e.
    a red morning pointing at Vercel for a parsing problem."""
    assert freshness.newest_served_date(
        "http://x", opener=_served(["2026-08-20", "2026-05-19"], wrap)) == "2026-08-20"


def test_a_dotted_date_is_not_read_as_staleness():
    """`get_thread` answers `2026.07.14`. If `list_dates` ever does the same, a
    formatting difference must not be reported as three months of stale data."""
    assert freshness.newest_served_date(
        "http://x", opener=_served(["2026.08.20"])) == "2026-08-20"


@pytest.mark.parametrize("opener,why", [
    (lambda r, timeout=None: (_ for _ in ()).throw(
        urllib.error.URLError("boom")), "network"),
    (lambda r, timeout=None: (_ for _ in ()).throw(OSError("reset")), "socket"),
])
def test_a_failure_to_reach_the_server_is_not_a_date(opener, why):
    """Every failure raises rather than returning a sentinel. A "newest date"
    that is really an error compares unequal and is reported as staleness,
    sending the operator to Vercel for a network blip."""
    with pytest.raises(freshness.Unanswered):
        freshness.newest_served_date("http://x", opener=opener)


def test_an_unreadable_answer_names_what_came_back():
    class _Junk:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return b"<html>504 Gateway Timeout</html>"

    with pytest.raises(freshness.Unanswered) as exc:
        freshness.newest_served_date("http://x",
                                     opener=lambda r, timeout=None: _Junk())
    assert "504" in str(exc.value), "the operator is not shown what answered"


def test_an_empty_date_list_is_unanswered_not_fresh():
    """An empty list must not compare equal to anything, and must not be read as
    "no dates committed either"."""
    with pytest.raises(freshness.Unanswered):
        freshness.newest_served_date("http://x", opener=_served([]))


def test_no_committed_threads_refuses_rather_than_passing(tmp_path, capsys):
    """The direction that matters: with nothing to compare against, calling the
    server fresh would be a green check that measured nothing."""
    empty = tmp_path / "threads"
    empty.mkdir()
    with pytest.raises(freshness.Unanswered):
        freshness.newest_committed_date(str(empty))


def test_a_progress_sidecar_is_not_mistaken_for_a_date(tmp_path):
    """`*.progress.json` is gitignored so it should never be here, but the
    pattern excludes it rather than a filter doing so — a filter would quietly
    accept it if that ever changed, and `2026-08-21.progress` sorts above
    `2026-08-20`."""
    d = _threads(tmp_path, "2026-08-20")
    (__import__("pathlib").Path(d) / "2026-08-21.progress.json").write_text(
        "{}", encoding="utf-8")
    assert freshness.newest_committed_date(d) == "2026-08-20"


def test_a_stale_server_exits_non_zero_and_says_both_dates(tmp_path, capsys,
                                                           monkeypatch):
    """The #85 case itself, end to end."""
    monkeypatch.setattr(freshness, "newest_served_date",
                        lambda *a, **k: "2026-05-19")
    rc = freshness.main(["--threads-dir", _threads(tmp_path, "2026-08-20"),
                         "--attempts", "1", "--sleep", "0"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "::error::" in out
    assert "2026-05-19" in out and "2026-08-20" in out


def test_a_fresh_server_exits_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(freshness, "newest_served_date",
                        lambda *a, **k: "2026-08-20")
    assert freshness.main(["--threads-dir", _threads(tmp_path, "2026-08-20"),
                           "--attempts", "1", "--sleep", "0"]) == 0


def test_a_slow_alias_swap_is_retried_rather_than_red(tmp_path, capsys,
                                                     monkeypatch):
    """An alias swap is not instantaneous. One miss must not red a morning that
    is actually fine — but the retry must stop, or nobody gets an answer."""
    answers = iter(["2026-08-19", "2026-08-19", "2026-08-20"])
    monkeypatch.setattr(freshness, "newest_served_date",
                        lambda *a, **k: next(answers))
    assert freshness.main(["--threads-dir", _threads(tmp_path, "2026-08-20"),
                           "--attempts", "5", "--sleep", "0"]) == 0


# --------------------------------------------------------------------------
# The wiring. Without this the comparison above is a fence around nothing.
# --------------------------------------------------------------------------

def _deploy_job():
    yaml = pytest.importorskip("yaml")
    spec = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "daily-batch.yml").read_text(
            encoding="utf-8"))
    assert "deploy-mcp" in spec["jobs"], (
        "the MCP deploy job is gone — it is the only thing keeping that server "
        "from drifting three months behind the site again (#85)")
    return spec, spec["jobs"]["deploy-mcp"]


def test_the_deploy_checks_out_the_branch_tip_and_not_the_trigger_sha():
    """The subtlest way this whole job becomes a no-op. On a scheduled run
    `github.sha` is main as it was when the run STARTED — before the data commit
    the job exists to publish. Checking that out deploys yesterday's data every
    morning and reports success, and the freshness check would then be the only
    thing failing, pointing at Vercel."""
    _, job = _deploy_job()
    checkout = next(s for s in job["steps"]
                    if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("ref") == "${{ github.ref_name }}", (
        "the deploy must check out the tip of the branch, not the trigger SHA")


def test_the_data_is_bundled_before_the_deploy_and_verified_after():
    """Order is the whole thing. `vercel deploy` uploads `apps/mcp` alone, so the
    repo-root `data/` is not there to copy from and the remote prebuild only
    verifies the bundle already in the upload (#73). Bundle after deploying — or
    not at all — and the deploy succeeds while shipping whatever was last
    bundled, which is exactly #85."""
    _, job = _deploy_job()
    names = [str(s.get("name", "") or s.get("uses", "")) for s in job["steps"]]
    runs = [str(s.get("run", "")) for s in job["steps"]]
    bundle = next(i for i, r in enumerate(runs) if "copy-data.mjs" in r)
    deploy = next(i for i, r in enumerate(runs) if "vercel@" in r)
    verify = next(i for i, r in enumerate(runs)
                  if "check_mcp_freshness.py" in r)
    assert bundle < deploy < verify, names


def test_a_missing_token_fails_instead_of_skipping():
    """A warning nobody reads is how this went stale for three months. The
    publish happens in the job above, so a red here does not block the site."""
    _, job = _deploy_job()
    step = next(s for s in job["steps"] if "vercel@" in str(s.get("run", "")))
    run = step["run"]
    assert 'if [ -z "$VERCEL_TOKEN" ]' in run
    assert "::error::" in run and "exit 1" in run
    assert "VERCEL_TOKEN" in str(step.get("env", {}))


def test_the_deploy_job_cannot_write_to_the_repo():
    """A deploy credential and `contents: write` do not belong in one job."""
    _, job = _deploy_job()
    assert job["permissions"] == {"contents": "read"}, job.get("permissions")


def test_the_deploy_runs_even_when_a_single_date_failed():
    """The main job fails on its LAST step for a systemic-summary date, long
    after pushing. `success()` here would leave the MCP server a day behind the
    site every time one date misbehaved — and those are not rare."""
    _, job = _deploy_job()
    assert job["if"] == "${{ !cancelled() }}", job.get("if")
    assert job["needs"] == "fetch-and-summarize"


def test_a_failed_deploy_raises_an_issue_and_not_just_a_red_check():
    """#85 was invisible for three months. A red check is not a notification."""
    spec, _ = _deploy_job()
    notify = spec["jobs"]["notify-on-failure"]
    assert "deploy-mcp" in notify["needs"], (
        "a morning that stops deploying the MCP server must raise the same "
        "issue a failed publish does")
    assert notify["if"] == "failure()"


def test_the_vercel_cli_is_pinned():
    """For the reason requirements.txt exists (#80): an unpinned tool that
    re-resolves every morning changes production without a commit."""
    import re
    _, job = _deploy_job()
    run = next(str(s.get("run", "")) for s in job["steps"]
               if "vercel@" in str(s.get("run", "")))
    assert re.search(r"vercel@\d+\.\d+\.\d+\b", run), (
        "the Vercel CLI must be pinned to an exact version")
