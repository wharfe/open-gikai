"""#85: the MCP server must not be able to serve stale data quietly.

`apps/mcp` is a second Vercel project, deployed by hand, and it stopped: its
last deploy was 2026-05-22 and it answered with data ending 2026-05-19 while
the site was at 2026-08-20. Three months. Nothing caught it because the
endpoint returns 200 and `tools/list` works — it was never broken, only old.
Nothing was watching it either: `uptime.yml` curls `open-gikai.net/` and its
sitemap and never touches the MCP server at all. Liveness would not have seen
this anyway; the only comparison that can is "what it answers" against "what is
committed".

Two halves are tested here, because either alone is a fence around nothing: the
comparison itself, and the workflow wiring that runs it every morning.
"""

import json
import urllib.error

import pytest

import check_mcp_freshness as freshness

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _served(index, wrap=None):
    """A fake urlopen answering the MCP envelope shape with this index.

    `index` is `{date: thread count}`; `wrap` turns it into whatever payload
    shape the test is about.
    """
    if wrap is None:
        def wrap(i):
            return {"count": len(i),
                    "dates": [{"date": d, "threads": n} for d, n in i.items()]}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            payload = json.dumps(wrap(index))
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1,
                 "result": {"content": [{"type": "text", "text": payload}]}}
            ).encode("utf-8")

    return lambda request, timeout=None: _Response()


def _threads(tmp_path, *dates, **counts):
    """A committed threads directory. `_threads(p, "2026-08-20")` gives that
    date one thread; `_threads(p, **{"2026-08-20": 3})` gives it three.

    Each thread carries its own `date`, because that — not the filename — is
    what the server counts by, and what this check therefore counts by too.
    """
    d = tmp_path / "threads"
    d.mkdir()
    for date in dates:
        counts.setdefault(date, 1)
    for date, n in counts.items():
        (d / f"{date}.json").write_text(
            json.dumps([{"id": f"t{i}", "date": date} for i in range(n)]),
            encoding="utf-8")
    return str(d)


# --------------------------------------------------------------------------
# The comparison
# --------------------------------------------------------------------------

def test_the_served_index_is_read_from_the_tool_envelope():
    got = freshness.served_index(
        "http://x", opener=_served({"2026-05-19": 2, "2026-08-20": 1}))
    assert got == {"2026-05-19": 2, "2026-08-20": 1}


def test_a_bare_list_of_objects_is_read_too():
    """`list_dates` answers `{count, dates:[...]}` today. The wrapper is not
    this repo's to pin, so the unwrapped list is read as well — but the per-date
    counts are, because the comparison is built on them."""
    assert freshness.served_index(
        "http://x",
        opener=_served({"2026-08-20": 3}, wrap=lambda i: [
            {"date": d, "threads": n} for d, n in i.items()])
    ) == {"2026-08-20": 3}


def test_a_dotted_date_is_not_read_as_staleness():
    """`get_thread` answers `2026.07.14`. If `list_dates` ever does the same, a
    formatting difference must not be reported as three months of stale data."""
    assert freshness.served_index(
        "http://x", opener=_served({"2026.08.20": 1})) == {"2026-08-20": 1}


@pytest.mark.parametrize("shape", [
    ["2026-08-20"],                              # dates with no counts at all
    [{"date": "2026-08-20"}],                    # objects missing `threads`
    [{"date": "2026-08-20", "threads": "1"}],    # a count that is not a number
    [{"date": "2026-08-20", "threads": None}],
])
def test_an_answer_without_readable_counts_is_unanswered(shape):
    """Fail closed. The comparison is per-date counts, so an answer that omits
    them is not comparable — and treating a missing count as 0 would turn a
    changed reply shape into a staleness report, sending an operator to the
    Vercel dashboard for someone else's refactor."""
    with pytest.raises(freshness.Unanswered):
        freshness.served_index("http://x", opener=_served(shape, wrap=lambda i: i))


@pytest.mark.parametrize("opener,why", [
    (lambda r, timeout=None: (_ for _ in ()).throw(
        urllib.error.URLError("boom")), "network"),
    (lambda r, timeout=None: (_ for _ in ()).throw(OSError("reset")), "socket"),
])
def test_a_failure_to_reach_the_server_is_not_an_index(opener, why):
    """Every failure raises rather than returning a sentinel. An "index" that is
    really an error compares unequal and is reported as staleness, sending the
    operator to Vercel for a network blip."""
    with pytest.raises(freshness.Unanswered):
        freshness.served_index("http://x", opener=opener)


def test_an_unreadable_answer_names_what_came_back():
    class _Junk:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return b"<html>504 Gateway Timeout</html>"

    with pytest.raises(freshness.Unanswered) as exc:
        freshness.served_index("http://x",
                               opener=lambda r, timeout=None: _Junk())
    assert "504" in str(exc.value), "the operator is not shown what answered"


def test_an_empty_date_list_is_unanswered_not_fresh():
    """An empty list must not compare equal to anything, and must not be read as
    "no dates committed either"."""
    with pytest.raises(freshness.Unanswered):
        freshness.served_index("http://x", opener=_served({}))


def test_no_committed_threads_refuses_rather_than_passing(tmp_path):
    """The direction that matters: with nothing to compare against, calling the
    server fresh would be a green check that measured nothing."""
    empty = tmp_path / "threads"
    empty.mkdir()
    with pytest.raises(freshness.Unanswered):
        freshness.committed_index(str(empty))


def test_a_missing_threads_directory_is_reported_and_not_a_traceback(tmp_path,
                                                                     capsys):
    """`os.listdir` raises `FileNotFoundError`, which is not `Unanswered` and
    walks straight past `main` — replacing the diagnosis ("there is nothing to
    compare against") with a stack trace."""
    with pytest.raises(freshness.Unanswered):
        freshness.committed_index(str(tmp_path / "not-here"))

    rc = freshness.main(["--threads-dir", str(tmp_path / "not-here"),
                         "--attempts", "1", "--sleep", "0"])
    assert rc == 1
    assert "::error::" in capsys.readouterr().out


@pytest.mark.parametrize("payload", ['{"2026-08-20": []}', "{}", '"[]"', "not json"])
def test_a_threads_file_of_the_wrong_shape_is_unreadable_not_a_count(tmp_path,
                                                                     payload):
    """`len()` of a dict answers with its KEY count, so a hand-edited file would
    contribute a fabricated number and the difference would be reported as
    staleness. Same rule as `_as_list_of_dicts` in summarize.py."""
    d = tmp_path / "threads"
    d.mkdir()
    (d / "2026-08-20.json").write_text(payload, encoding="utf-8")
    with pytest.raises(freshness.Unanswered):
        freshness.committed_index(str(d))


def test_the_committed_index_counts_threads_and_not_files(tmp_path):
    d = _threads(tmp_path, **{"2026-08-20": 3, "2026-08-19": 1})
    assert freshness.committed_index(d) == {"2026-08-20": 3, "2026-08-19": 1}


def test_an_empty_date_file_is_not_a_date_the_server_will_ever_list(tmp_path):
    """A false RED, which is the expensive direction: the server tallies each
    thread's own `date`, so a file holding `[]` contributes nothing there. Count
    it by filename and a byte-perfect deploy disagrees every morning until
    someone edits the data — and `validate-data.mjs` does not forbid `[]`."""
    d = _threads(tmp_path, "2026-08-20")
    (__import__("pathlib").Path(d) / "2026-08-19.json").write_text(
        "[]", encoding="utf-8")
    assert freshness.committed_index(d) == {"2026-08-20": 1}


def test_a_thread_is_counted_under_its_own_date_not_its_filename(tmp_path):
    """Same rule, the other way round. Nothing guarantees the two agree, so the
    checker uses the projection the server uses rather than the one the
    directory layout suggests."""
    d = tmp_path / "threads"
    d.mkdir()
    (d / "2026-08-20.json").write_text(json.dumps([
        {"id": "a", "date": "2026-08-20"},
        {"id": "b", "date": "2026-08-19"},
        {"id": "c", "date": "2026.08.19"},   # dotted, as `get_thread` answers
    ]), encoding="utf-8")
    assert freshness.committed_index(str(d)) == {"2026-08-20": 1,
                                                 "2026-08-19": 2}


def test_date_files_holding_no_threads_at_all_is_refused(tmp_path):
    """Distinct from "no date files": the directory is populated, so this is not
    an empty checkout — but there is still nothing the server could confirm."""
    d = tmp_path / "threads"
    d.mkdir()
    (d / "2026-08-20.json").write_text("[]", encoding="utf-8")
    with pytest.raises(freshness.Unanswered):
        freshness.committed_index(str(d))


def test_a_file_that_is_not_valid_utf8_is_unreadable_not_a_traceback(tmp_path):
    """`UnicodeDecodeError` is a `ValueError`, not an `OSError`. Naming the
    subclasses individually let it walk out past `main`, which is the one
    outcome the guard exists to prevent."""
    d = tmp_path / "threads"
    d.mkdir()
    (d / "2026-08-20.json").write_bytes(b'[{"id": "a", "date": "\xff\xfe"}]')
    with pytest.raises(freshness.Unanswered):
        freshness.committed_index(str(d))


@pytest.mark.parametrize("payload", [
    '["not an object"]',
    '[{"id": "a"}]',                    # no date at all
    '[{"id": "a", "date": null}]',
    '[{"id": "a", "date": ""}]',
])
def test_a_thread_this_check_cannot_place_is_unreadable(tmp_path, payload):
    """Skipping it would undercount silently and report the gap as staleness."""
    d = tmp_path / "threads"
    d.mkdir()
    (d / "2026-08-20.json").write_text(payload, encoding="utf-8")
    with pytest.raises(freshness.Unanswered):
        freshness.committed_index(str(d))


def test_a_progress_sidecar_is_not_mistaken_for_a_date(tmp_path):
    """`*.progress.json` is gitignored so it should never be here, but the
    pattern excludes it rather than a filter doing so — a filter would quietly
    accept it if that ever changed, and `2026-08-21.progress` sorts above
    `2026-08-20`."""
    d = _threads(tmp_path, "2026-08-20")
    (__import__("pathlib").Path(d) / "2026-08-21.progress.json").write_text(
        "{}", encoding="utf-8")
    assert freshness.committed_index(d) == {"2026-08-20": 1}


def test_a_stale_server_exits_non_zero_and_says_what_differs(tmp_path, capsys,
                                                             monkeypatch):
    """The #85 case itself, end to end."""
    monkeypatch.setattr(freshness, "served_index",
                        lambda *a, **k: {"2026-05-19": 1})
    rc = freshness.main(["--threads-dir", _threads(tmp_path, "2026-08-20"),
                         "--attempts", "1", "--sleep", "0"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "::error::" in out
    assert "2026-05-19" in out and "2026-08-20" in out


def test_a_backfill_of_an_older_date_is_caught(tmp_path, capsys, monkeypatch):
    """The hole newest-date-only comparison left open, and the likeliest shape
    of a silently-failed deploy. The pipeline re-visits a 30-day window every
    morning, so most days add threads to dates that ALREADY exist and a quiet
    Diet day adds no date at all: the newest date matches on both sides while
    the server serves a bundle from before the run."""
    monkeypatch.setattr(freshness, "served_index",
                        lambda *a, **k: {"2026-08-20": 1, "2026-08-10": 1})
    rc = freshness.main(
        ["--threads-dir", _threads(tmp_path, **{"2026-08-20": 1,
                                                "2026-08-10": 4}),
         "--attempts", "1", "--sleep", "0"])
    out = capsys.readouterr().out
    assert rc == 1, "a backfill the server never received passed as fresh"
    assert "2026-08-10" in out


def test_a_date_the_server_has_but_we_do_not_also_differs(tmp_path, capsys,
                                                          monkeypatch):
    """The other direction: a bundle from a different tree entirely."""
    monkeypatch.setattr(freshness, "served_index",
                        lambda *a, **k: {"2026-08-20": 1, "2026-08-21": 1})
    rc = freshness.main(["--threads-dir", _threads(tmp_path, "2026-08-20"),
                         "--attempts", "1", "--sleep", "0"])
    assert rc == 1
    assert "2026-08-21" in capsys.readouterr().out


def test_a_fresh_server_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(freshness, "served_index",
                        lambda *a, **k: {"2026-08-20": 1})
    assert freshness.main(["--threads-dir", _threads(tmp_path, "2026-08-20"),
                           "--attempts", "1", "--sleep", "0"]) == 0


def test_a_slow_alias_swap_is_retried_rather_than_red(tmp_path, monkeypatch):
    """An alias swap is not instantaneous. One miss must not red a morning that
    is actually fine — but the retry must stop, or nobody gets an answer."""
    answers = iter([{"2026-08-19": 1}, {"2026-08-19": 1}, {"2026-08-20": 1}])
    monkeypatch.setattr(freshness, "served_index",
                        lambda *a, **k: next(answers))
    assert freshness.main(["--threads-dir", _threads(tmp_path, "2026-08-20"),
                           "--attempts", "5", "--sleep", "0"]) == 0


def test_never_reaching_the_server_is_not_reported_as_staleness(tmp_path,
                                                                capsys,
                                                                monkeypatch):
    """The failure the old message could not avoid. With every attempt erroring
    there is no answer to compare, so the annotation must say the state is
    UNKNOWN. Claiming "the endpoint answers correctly and serves stale data"
    names a cause that was never established — the one thing this repo's
    annotations must not do — and sends an operator to Vercel for a blip."""
    def _boom(*a, **k):
        raise freshness.Unanswered("could not reach http://x: timed out")

    monkeypatch.setattr(freshness, "served_index", _boom)
    rc = freshness.main(["--threads-dir", _threads(tmp_path, "2026-08-20"),
                         "--attempts", "2", "--sleep", "0"])
    out = capsys.readouterr().out
    assert rc == 1
    error = next(l for l in out.splitlines() if l.startswith("::error::"))
    assert "UNKNOWN" in error
    assert "serves stale" not in error and "serves data that is not ours" not in error
    assert "timed out" in error, "the operator is not told what actually failed"


def test_an_answer_is_not_lost_when_a_later_attempt_cannot_reach_the_server(
        tmp_path, capsys, monkeypatch):
    """A stale answer followed by a network failure is still evidence of
    staleness. Overwriting it with the failure throws away the one comparison
    the run managed to make and downgrades a real #85 to "unknown"."""
    answers = iter([{"2026-05-19": 1}])

    def _flaky(*a, **k):
        try:
            return next(answers)
        except StopIteration:
            raise freshness.Unanswered("could not reach http://x: reset")

    monkeypatch.setattr(freshness, "served_index", _flaky)
    rc = freshness.main(["--threads-dir", _threads(tmp_path, "2026-08-20"),
                         "--attempts", "3", "--sleep", "0"])
    out = capsys.readouterr().out
    assert rc == 1
    error = next(l for l in out.splitlines() if l.startswith("::error::"))
    assert "2026-05-19" in error, "the stale answer we did get was thrown away"
    assert "UNKNOWN" not in error


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

    def _step_running(command):
        """The step that RUNS this, not one that merely mentions it.

        The deploy step's failure annotation names the freshness script — it
        tells an operator how to check the current state by hand — and a
        substring match happily read that as the verify step, so this asserted
        `4 < 4` and failed on a workflow that was correctly ordered.
        """
        matches = [i for i, r in enumerate(runs)
                   if any(line.strip().startswith(command)
                          for line in r.splitlines())]
        assert len(matches) == 1, (
            f"expected exactly one step running `{command}`, got {matches} in "
            f"{names}")
        return matches[0]

    bundle = _step_running("node ./scripts/copy-data.mjs")
    deploy = next(i for i, r in enumerate(runs) if "vercel@" in r)
    verify = _step_running("python3 scripts/check_mcp_freshness.py")
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


def test_the_python_the_check_runs_on_is_pinned():
    """The check is stdlib-only, so the runner's default python3 works today —
    which is the argument that stopped being good enough on 2026-08-20 (#80).
    Every other version this workflow runs on is pinned; the interpreter is not
    the runner image's to choose either."""
    _, job = _deploy_job()
    setup = [s for s in job["steps"]
             if str(s.get("uses", "")).startswith("actions/setup-python")]
    assert setup, "deploy-mcp runs python with no actions/setup-python (#80)"
    assert str(setup[0].get("with", {}).get("python-version")) == "3.12", (
        f"pin the same 3.12 CI uses, got {setup[0].get('with')}")


def test_the_vercel_cli_is_pinned():
    """For the reason requirements.txt exists (#80): an unpinned tool that
    re-resolves every morning changes production without a commit."""
    import re
    _, job = _deploy_job()
    run = next(str(s.get("run", "")) for s in job["steps"]
               if "vercel@" in str(s.get("run", "")))
    assert re.search(r"vercel@\d+\.\d+\.\d+\b", run), (
        "the Vercel CLI must be pinned to an exact version")


def test_the_deploy_job_allows_for_the_build_queue():
    """Measured, not chosen. This job's first real run took 18m34s — a 2-second
    upload and then a queued build, because Vercel Hobby builds one at a time
    and the frontend project, triggered by the very same data commit, takes
    10-11 minutes by itself. `timeout-minutes: 20` was therefore "red as soon as
    the site build gets a minute slower", on the NORMAL path. A timeout that
    fires when nothing is wrong is the kind of alarm that gets switched off, and
    runner minutes are free on a public repo.
    """
    _, job = _deploy_job()
    assert job["timeout-minutes"] >= 45, (
        f"timeout-minutes is {job['timeout-minutes']}, but the MCP build queues "
        f"behind an 10-11 minute frontend build every morning (observed: 18m34s "
        f"end to end)")
