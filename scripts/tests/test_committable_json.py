"""#75 / #57's option (b): a corrupt data file must not enter the commit.

Option (c) — the atomic writer — stops this pipeline from creating one. This is
the other half, for a file that arrived broken another way (a hand edit, a bad
merge, a failing disk). It matters because `src/lib/data.ts` is deliberately
fatal on a corrupt file: commit one and the next Vercel production build dies,
with nothing tying the failure back to the run that committed it.

Note what this is NOT allowed to become: a step that fails the commit. Excluding
one file leaves the repository's previous version of that date — the site is a
day stale for one date. Failing the step publishes nothing at all, which is the
amplification the pipeline keeps removing (#52/#65/#72). Until #74 there was an
accidental version of exactly that: the metrics step crashed on a corrupt threads
file under `bash -e`, before `git add`. It was never a design, and removing it is
why this check has to exist rather than being nice to have.
"""

import json
import os
import shutil
import subprocess

import pytest

import check_committable_json as ccj

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _data_tree(tmp_path):
    (tmp_path / "data" / "threads").mkdir(parents=True)
    (tmp_path / "data" / "pending-batches").mkdir(parents=True)
    (tmp_path / "data" / "threads" / "2026-05-14.json").write_text(
        json.dumps([{"id": "t_1"}]), encoding="utf-8")
    (tmp_path / "data" / "members.json").write_text(json.dumps({"m_1": {}}),
                                                    encoding="utf-8")
    (tmp_path / "data" / "status.json").write_text(json.dumps({"2026-05-14": {}}),
                                                   encoding="utf-8")
    return tmp_path


def _report(tmp_path, capsys):
    assert ccj.main(["--repo-root", str(tmp_path)]) == 0
    return capsys.readouterr().out


def test_a_healthy_tree_reports_nothing(tmp_path, capsys):
    """Empty output is the "nothing to exclude" signal, so a false positive here
    would drop a good date out of the commit every morning."""
    _data_tree(tmp_path)
    assert _report(tmp_path, capsys) == ""


def test_a_truncated_thread_file_is_reported(tmp_path, capsys):
    _data_tree(tmp_path)
    (tmp_path / "data" / "threads" / "2026-05-15.json").write_text(
        '[{"id": "t_2', encoding="utf-8")
    out = _report(tmp_path, capsys)
    assert "data/threads/2026-05-15.json" in out
    assert "2026-05-14" not in out, "a healthy sibling was dragged in"


def test_the_path_comes_first_and_is_tab_separated(tmp_path, capsys):
    """The caller splits this to build `git reset -- <paths>`, so the format is
    load-bearing: a path preceded by prose, or separated by a space, unstages
    something else or nothing at all. The verdict is field 2 for the same reason
    — the message is arbitrary text and may itself contain a tab."""
    _data_tree(tmp_path)
    (tmp_path / "data" / "threads" / "bad.json").write_text("{", encoding="utf-8")
    line = _report(tmp_path, capsys).strip()
    assert line.startswith("data/threads/bad.json\t"), repr(line)
    fields = line.split("\t")
    assert " " not in fields[0]
    assert fields[1] in (ccj.HEAD_OK, ccj.HEAD_BROKEN, ccj.NOT_IN_HEAD,
                         ccj.HEAD_UNKNOWN), repr(line)


@pytest.mark.parametrize("rel,content", [
    ("data/threads/2026-05-14.json", "{}"),          # parses, wrong shape
    ("data/members.json", "[]"),
    ("data/status.json", "[]"),
])
def test_a_file_of_the_wrong_shape_is_reported(tmp_path, capsys, rel, content):
    """Syntax is not the only way a data file is unusable, and the shape failures
    are the quieter half: `loadThreads` SKIPS a non-array rather than throwing, so
    a `{}` thread file is a green build serving zero threads for that date.
    """
    _data_tree(tmp_path)
    (tmp_path / rel).write_text(content, encoding="utf-8")
    assert rel in _report(tmp_path, capsys)


def test_a_progress_sidecar_is_not_a_thread_file(tmp_path, capsys):
    """`*.progress.json` lives in data/threads/, is gitignored, and is a dict.
    Reporting it would exclude a path that was never staged and red every run
    that has one — which is most of them."""
    _data_tree(tmp_path)
    (tmp_path / "data" / "threads" / "2026-05-14.progress.json").write_text(
        json.dumps({"completed": []}), encoding="utf-8")
    assert _report(tmp_path, capsys) == ""


def test_a_corrupt_pending_sidecar_is_reported(tmp_path, capsys):
    """The commit stages data/pending-batches/ too, and summarize.py re-reads it
    every morning — a corrupt one committed there is #44's deadlock with a
    different cause."""
    _data_tree(tmp_path)
    (tmp_path / "data" / "pending-batches" / "2026-05-14.json").write_text(
        '{"date"', encoding="utf-8")
    assert "data/pending-batches/2026-05-14.json" in _report(tmp_path, capsys)


def test_it_exits_zero_even_when_it_finds_something(tmp_path, capsys):
    """The contract. The commit step runs under `set -e`, so a non-zero exit here
    would abort it — turning "one date is stale" into "nothing publishes", which
    is the failure this whole check is meant to avoid causing."""
    _data_tree(tmp_path)
    (tmp_path / "data" / "threads" / "bad.json").write_text("nope", encoding="utf-8")
    assert ccj.main(["--repo-root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip()


def _git_repo(tmp_path):
    """A scratch repo with the healthy tree committed, or skip if git is absent."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    _data_tree(tmp_path)
    for cmd in (["init", "-q", "."], ["config", "user.email", "s@example.com"],
                ["config", "user.name", "sim"]):
        subprocess.run(["git", "-C", str(tmp_path)] + cmd, check=True)
    return tmp_path


def _commit_all(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "base"],
                   check=True)


def test_a_verdict_of_head_ok_is_the_only_case_unstaging_rescues(tmp_path, capsys):
    """The whole point of excluding a file is that the repository's version takes
    over. That only holds when the committed copy is readable, so the report has
    to say which case it is rather than assuming this one."""
    _git_repo(tmp_path)
    _commit_all(tmp_path)
    (tmp_path / "data" / "threads" / "2026-05-14.json").write_text(
        '[{"id": "t_2', encoding="utf-8")
    line = _report(tmp_path, capsys).strip()
    assert line.split("\t")[1] == ccj.HEAD_OK, repr(line)


def test_corruption_that_is_already_committed_reports_head_broken(tmp_path, capsys):
    """#75's own causes — a hand edit, a bad merge — arrive in CI ALREADY
    committed, so `git add` stages content identical to HEAD and `git reset`
    undoes nothing. Reporting head_ok here would tell an operator the site is
    stale for one date when the production build is in fact broken, and send them
    to `git checkout HEAD -- <file>`, which restores the corruption."""
    _git_repo(tmp_path)
    (tmp_path / "data" / "threads" / "2026-05-15.json").write_text(
        '[{"id": "t_2', encoding="utf-8")
    _commit_all(tmp_path)
    line = _report(tmp_path, capsys).strip()
    assert line.startswith("data/threads/2026-05-15.json\t"), repr(line)
    assert line.split("\t")[1] == ccj.HEAD_BROKEN, repr(line)


def test_a_committed_file_of_the_wrong_shape_is_head_broken_too(tmp_path, capsys):
    """One rule for both sides. Judging HEAD by parseability alone would call a
    `{}` thread file fine there and broken here, for no reason an operator could
    follow — and `{}` is the quieter failure of the two (zero threads, green
    build)."""
    _git_repo(tmp_path)
    (tmp_path / "data" / "threads" / "2026-05-15.json").write_text(
        "{}", encoding="utf-8")
    _commit_all(tmp_path)
    line = _report(tmp_path, capsys).strip()
    assert line.split("\t")[1] == ccj.HEAD_BROKEN, repr(line)


def test_a_new_corrupt_file_reports_not_in_head(tmp_path, capsys):
    """Absent, not stale. Excluding it works — there is just nothing behind it,
    so the message must not promise the previous version."""
    _git_repo(tmp_path)
    _commit_all(tmp_path)
    (tmp_path / "data" / "threads" / "2026-05-16.json").write_text(
        "{", encoding="utf-8")
    line = _report(tmp_path, capsys).strip()
    assert line.split("\t")[1] == ccj.NOT_IN_HEAD, repr(line)


def test_an_uninspectable_repository_fails_closed_to_unknown(tmp_path, capsys):
    """No git, no HEAD, an unreadable object: every branch that cannot ESTABLISH
    an answer must say so. Guessing not_in_head would read as reassuring ("that
    date is simply absent") about a file that is breaking the build."""
    _data_tree(tmp_path)  # deliberately NOT a git repo
    (tmp_path / "data" / "threads" / "bad.json").write_text("{", encoding="utf-8")
    line = _report(tmp_path, capsys).strip()
    assert line.split("\t")[1] == ccj.HEAD_UNKNOWN, repr(line)


def test_the_commit_step_reads_the_verdict_field():
    """The verdict only does its job if the caller prints it. A future edit that
    drops it back to two fields silently restores the message this fixed."""
    yaml = pytest.importorskip("yaml")
    path = os.path.join(REPO_ROOT, ".github", "workflows", "daily-batch.yml")
    spec = yaml.safe_load(open(path, encoding="utf-8").read())
    steps = spec["jobs"]["fetch-and-summarize"]["steps"]
    commit = next(s for s in steps if s.get("id") == "commit")
    assert "read -r f verdict why" in commit["run"], (
        "the commit step must read the verdict field, not just path and reason")
    for verdict in (ccj.HEAD_OK, ccj.HEAD_BROKEN, ccj.NOT_IN_HEAD,
                    ccj.HEAD_UNKNOWN):
        assert verdict in commit["run"], (
            f"{verdict} is reported but the commit step never explains it")


def _commit_step():
    yaml = pytest.importorskip("yaml")
    path = os.path.join(REPO_ROOT, ".github", "workflows", "daily-batch.yml")
    spec = yaml.safe_load(open(path, encoding="utf-8").read())
    steps = spec["jobs"]["fetch-and-summarize"]["steps"]
    return next(s for s in steps if s.get("id") == "commit")


def _staged_data_paths(run):
    """The data/ pathspecs of the step's actual `git add`, and only those.

    Deliberately not `run.split()`: the step's prose names data/ paths too (the
    operator-facing explanation of what a stale file costs), and a scan of the
    whole block reads those as staged. It would then pass by agreeing with a
    sentence instead of with a command.
    """
    lines, add = run.splitlines(), []
    for i, line in enumerate(lines):
        if not line.strip().startswith("git add "):
            continue
        while True:                       # follow backslash continuations
            add.append(lines[i])
            if not lines[i].rstrip().endswith("\\"):
                break
            i += 1
    tokens = " ".join(add).replace("\\", " ").split()
    return [t for t in tokens if t.startswith("data/")]


def test_the_checks_cover_every_path_the_commit_stages():
    """The list of staged paths lives in daily-batch.yml, and this checker is only
    as good as its agreement with it. Checked BOTH ways: a staged path with no
    check reopens #57 for that file silently, and a check for a path nothing
    stages is a fence around nothing that reads like coverage."""
    staged = _staged_data_paths(_commit_step()["run"])
    assert staged, "could not find the staged data/ paths in the commit step"

    checked = {pattern for pattern, _ in ccj.CHECKS}
    covered = set()
    for path_spec in staged:
        # A directory is staged recursively; the checker covers it with a glob.
        candidates = {path_spec, path_spec.rstrip("/") + "/*.json"}
        hit = candidates & checked
        assert hit, (
            f"the commit stages {path_spec} but check_committable_json.py does "
            f"not check it — a corrupt file there would be committed (#57)")
        covered |= hit
    assert checked == covered, (
        f"check_committable_json.py checks {sorted(checked - covered)}, which the "
        f"commit step does not stage — that check can never change an outcome")


# ---------------------------------------------------------------------------
# What the change actually IS is git behaviour, so these run the step's own
# shell. Asserting on the checker alone is how the first version of this shipped
# believing it rescued a file that was already committed.
# ---------------------------------------------------------------------------

def _commit_step_prefix():
    """The step's shell up to (not including) its commit, so a test can inspect
    the index instead of the push. Sliced from the YAML rather than copied: a
    copy is what drifts, and drift here is silent."""
    run = _commit_step()["run"]
    marker = "if git diff --cached --quiet"
    assert marker in run, "the commit step no longer has the expected commit gate"
    return run[:run.index(marker)]


def _staging_repo(tmp_path):
    """A scratch repo the step's `git add` line can run against unmodified."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = _git_repo(tmp_path)
    (repo / "scripts").mkdir()
    shutil.copy(os.path.join(REPO_ROOT, "scripts", "check_committable_json.py"),
                repo / "scripts")
    public = repo / "public"
    public.mkdir()
    for name in ("sitemap.xml", "sitemap-0.xml", "sitemap_index.xml", "feed.xml"):
        (public / name).write_text("<xml/>", encoding="utf-8")
    _commit_all(repo)
    return repo


def _run_staging(repo):
    env = dict(os.environ, GITHUB_OUTPUT=str(repo / "gh_output"))
    (repo / "gh_output").write_text("", encoding="utf-8")
    proc = subprocess.run(["bash", "-e", "-c", _commit_step_prefix()],
                          cwd=str(repo), capture_output=True, text=True, env=env)
    return proc, (repo / "gh_output").read_text(encoding="utf-8")


def _staged(repo):
    out = subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--name-only"],
                         capture_output=True, text=True, check=True).stdout
    return set(out.split())


def test_a_corrupt_tracked_file_is_kept_out_of_the_index(tmp_path):
    """The claim the whole change rests on: the commit keeps HEAD's version of
    the bad path and this run's version of every good one."""
    repo = _staging_repo(tmp_path)
    (repo / "data" / "threads" / "2026-05-14.json").write_text('[{"id": "t_2',
                                                               encoding="utf-8")
    (repo / "data" / "threads" / "2026-05-13.json").write_text(
        '[{"id":"a"},{"id":"b"}]', encoding="utf-8")
    proc, gh_output = _run_staging(repo)
    assert proc.returncode == 0, proc.stderr
    staged = _staged(repo)
    assert "data/threads/2026-05-14.json" not in staged, proc.stdout
    assert "data/threads/2026-05-13.json" in staged
    assert "broken_json=data/threads/2026-05-14.json" in gh_output
    assert ccj.HEAD_OK in proc.stdout


def test_an_unreadable_file_does_not_take_the_whole_commit_down(tmp_path):
    """`git add` on a directory holding an unreadable file exits 128 having
    staged NOTHING — not even the healthy siblings. Unstaging after the add
    therefore never runs, and under the workflow's `bash -e` the step dies before
    the commit: one bad file, nothing published, which is the amplification
    #52/#65/#72 keep removing. Excluding it from the add is the only order that
    survives this input."""
    if os.geteuid() == 0:
        pytest.skip("root can read a 0o000 file, so there is nothing to survive")
    repo = _staging_repo(tmp_path)
    bad = repo / "data" / "threads" / "2026-05-14.json"
    (repo / "data" / "threads" / "2026-05-13.json").write_text(
        '[{"id":"a"},{"id":"b"}]', encoding="utf-8")
    bad.chmod(0o000)
    try:
        proc, gh_output = _run_staging(repo)
    finally:
        bad.chmod(0o644)
    assert proc.returncode == 0, f"the step died: {proc.stderr}"
    assert "data/threads/2026-05-13.json" in _staged(repo), proc.stdout
    assert "data/threads/2026-05-14.json" in gh_output


def test_a_healthy_run_stages_everything_and_says_nothing(tmp_path):
    """The path taken every ordinary morning. A false positive here drops a good
    date out of the commit, and an accidental `broken_json` reds the run."""
    repo = _staging_repo(tmp_path)
    (repo / "data" / "threads" / "2026-05-13.json").write_text(
        '[{"id":"a"},{"id":"b"}]', encoding="utf-8")
    proc, gh_output = _run_staging(repo)
    assert proc.returncode == 0, proc.stderr
    assert "data/threads/2026-05-13.json" in _staged(repo)
    assert "broken_json" not in gh_output, gh_output
    assert "::error::" not in proc.stdout, proc.stdout


def test_the_checker_breaking_is_reported_and_still_publishes(tmp_path):
    """A non-zero exit means the CHECKER broke, not the data. Staging everything
    unexamined under a green run is the one outcome that must not happen
    silently: publish, but say so and carry it to the step that reds the run."""
    repo = _staging_repo(tmp_path)
    (repo / "scripts" / "check_committable_json.py").write_text(
        "import sys\nsys.exit(9)\n", encoding="utf-8")
    (repo / "data" / "threads" / "2026-05-13.json").write_text(
        '[{"id":"a"},{"id":"b"}]', encoding="utf-8")
    proc, gh_output = _run_staging(repo)
    assert proc.returncode == 0, proc.stderr
    assert "data/threads/2026-05-13.json" in _staged(repo)
    assert "broken_json_check_failed=true" in gh_output
    assert "exit 9" in proc.stdout, proc.stdout
