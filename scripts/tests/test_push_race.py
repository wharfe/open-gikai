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

Half of these exist because the FIRST version of the fix lost the day too, in
two subtler ways, so read them as a list of the shapes that look right and are
not: a retry loop that spends a rebase without pushing it; a failed autostash
re-apply left in the index, which then makes every later `git pull` refuse and
burns the rest of the budget; a `continue` that skips a push; git's own English
diagnostics used as a counter (they are translated under a non-C locale); and a
"does not say X" assertion, which approves every other wording of X. Each of
those has a test here that fails when it is reintroduced — verified by
reintroducing them one at a time, not by inspection.
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


def _run_push(repo, path_prefix=None):
    # The timeout is a fence, not a tuning knob: the loop's whole job is to be
    # BOUNDED, so a regression to `while true` against a remote that keeps
    # refusing must fail this suite rather than hang it.
    env = dict(os.environ, GITHUB_REF_NAME="main", **GIT_ENV)
    if path_prefix:
        env["PATH"] = "%s:%s" % (path_prefix, env["PATH"])
    return subprocess.run(
        ["bash", "-e", "-c", _push_tail()], cwd=str(repo),
        capture_output=True, text=True, timeout=120, env=env)


def _git_call_log(tmp_path):
    """Log every `git` the step under test runs, and return (log, PATH prefix).

    The bound is "how many times did it try to push", and nothing else measures
    that. A pre-receive hook counts only pushes that REACHED the remote, so it
    silently misses every client-side non-fast-forward rejection — which is the
    first attempt always, and all four when the fetch is broken. Counting git's
    own "failed to push some refs" instead just swaps that hole for a locale
    dependency. So intercept the invocation.
    """
    real = shutil.which("git")
    if real is None:
        pytest.skip("git is not installed")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "git-calls.log"
    shim = bindir / "git"
    shim.write_text(
        '#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\nexec "%s" "$@"\n' % (log, real),
        encoding="utf-8")
    shim.chmod(0o755)
    return log, str(bindir)


def _pushes(log):
    if not log.exists():
        return 0
    return sum(1 for line in log.read_text(encoding="utf-8").splitlines()
               if line.startswith("push "))


def _reject_pushes(remote, times):
    """Make the remote refuse the first `times` pushes, and count every attempt.

    A synthetic refusal on purpose: the tests above already prove the loop
    survives a REAL diverged remote, and what these need to control is how many
    times in a row the push is refused — which is the loop's bound, not git's
    behaviour.
    """
    counter = remote / "push-attempts"
    hooks = remote / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        'N=$(cat "%s" 2>/dev/null || echo 0)\n'
        "N=$((N+1))\n"
        'echo "$N" > "%s"\n'
        'if [ "$N" -le %d ]; then\n'
        '  echo "simulated: someone else pushed first" >&2\n'
        "  exit 1\n"
        "fi\n"
        "exit 0\n" % (counter, counter, times),
        encoding="utf-8")
    hook.chmod(0o755)
    return counter


def _attempts(counter):
    return int(counter.read_text(encoding="utf-8").strip()) if counter.exists() else 0


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


def test_the_first_push_is_plain_and_says_nothing(tmp_path):
    """Attempt 0 must be a bare push, not a rebase-then-push.

    Every morning the remote has NOT moved is this path, so folding the first
    attempt into the retry shape would spend a fetch and announce "push
    rejected" on runs where nothing was ever rejected — an alarm that fires
    daily is an alarm that gets ignored, and this block's whole value is that
    its warnings mean something.
    """
    remote, ours = _diverged_clone(tmp_path)
    # Undo the divergence: the remote is exactly what our checkout fetched.
    _git(ours, "fetch", "-q", "origin")
    _git(ours, "reset", "-q", "--hard", "origin/main")
    (ours / "data" / "2026-05-14.json").write_text('[{"id": "t_1"}]',
                                                   encoding="utf-8")
    _git(ours, "add", "-A")
    _git(ours, "commit", "-q", "-m", "chore(pipeline): data")
    log, bindir = _git_call_log(tmp_path)

    proc = _run_push(ours, path_prefix=bindir)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "chore(pipeline): data" in _remote_subjects(remote)
    assert _pushes(log) == 1, "the uncontested push was not a single push"
    assert "::warning::" not in proc.stdout


def test_a_pre_existing_stash_is_not_reported_as_a_collision(tmp_path):
    """The collision test must key off THIS re-apply, not off any stash.

    `git stash list` is non-empty for reasons that have nothing to do with the
    rebase, and announcing that the autostash failed to re-apply then names a
    cause that did not happen — the same misreport as calling a fetch failure a
    conflict, one line further down.
    """
    remote, ours = _diverged_clone(tmp_path)
    (ours / "data" / "base.json").write_text("stashed away", encoding="utf-8")
    _git(ours, "stash", "-q")
    # A dirty tree the autostash CAN re-apply: the other push touched a
    # different file (`_diverged_clone`'s default).
    (ours / "data" / "base.json").write_text('[{"id": "hand-edited"}]',
                                             encoding="utf-8")

    proc = _run_push(ours)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "chore(pipeline): data" in _remote_subjects(remote)
    assert "left an unmerged index" not in proc.stdout, (
        "a pre-existing stash was reported as this rebase colliding")


def test_the_retry_is_a_rebase_and_not_a_merge(tmp_path):
    """A merge would publish the data too, so the assertion above passes either
    way. It has to be a rebase: the stalled-pipeline check in the same workflow
    draws its 10 runs out of `git log -n 40`, filtered to `^data: daily-batch
    run `. A merge commit is not miscounted by that grep — it eats one of the 40
    slots, which data commits already share with `chore(pipeline)` ones, so
    enough of them leave that check with fewer than 10 runs to judge a quiet
    streak on.
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


def test_the_last_rebase_the_loop_spends_is_actually_pushed(tmp_path):
    """The off-by-one that cost this fix its own point.

    The first version pushed first and rebased after, so the third rejection
    bought a rebase nothing ever pushed: the branch was left correctly replayed
    onto origin — one `git push` from published — and the step then exited 1 and
    threw it away. Three refusals is exactly the boundary, so this is the case
    that separates "3 pushes" from "3 rebase-and-retries".
    """
    # times=2, not 3: the very first push is refused client-side as a
    # non-fast-forward and never reaches the remote, so the hook only ever sees
    # the three post-rebase pushes. Refusing two of those makes the THIRD
    # rebase-and-retry the one that publishes — precisely the attempt the old
    # shape rebased for and then threw away.
    remote, ours = _diverged_clone(tmp_path)
    counter = _reject_pushes(remote, times=2)
    log, bindir = _git_call_log(tmp_path)
    proc = _run_push(ours, path_prefix=bindir)
    assert proc.returncode == 0, (
        "the loop gave up holding a commit it had already rebased into a "
        "pushable state: " + proc.stdout + proc.stderr)
    assert "chore(pipeline): data" in _remote_subjects(remote)
    assert "(3/3)" in proc.stdout, "the third rebase-and-retry never ran"
    assert _attempts(counter) == 3
    assert _pushes(log) == 4


def test_a_permanently_rejecting_remote_stops_red_instead_of_looping(tmp_path):
    """The other side of the bound. A remote that never accepts must end the
    step, not spin until the job's timeout — and the error must not name a
    cause, because continuous pushes, branch protection and dead credentials
    all arrive here identically.
    """
    remote, ours = _diverged_clone(tmp_path)
    _reject_pushes(remote, times=99)
    log, bindir = _git_call_log(tmp_path)
    proc = _run_push(ours, path_prefix=bindir)
    assert proc.returncode != 0
    assert _pushes(log) == 4, "the retry bound moved: " + proc.stdout
    assert ["(%d/3)" % n in proc.stdout for n in (1, 2, 3)] == [True] * 3
    assert "(4/3)" not in proc.stdout
    assert "::error::" in proc.stdout
    assert "LOCAL" in proc.stdout
    # The error must point at evidence rather than name a cause: continuous
    # pushes, branch protection and dead credentials all arrive here alike.
    assert "see the git output above" in proc.stdout


def test_a_fetch_failure_is_not_reported_as_a_conflict(tmp_path):
    """`git pull --rebase` fails for reasons that are not conflicts — a fetch
    that times out, expired credentials, a ref that will not resolve. None of
    those start a rebase, so `git rebase --abort` fails and `|| true` hides it,
    and announcing a conflict sends the operator hunting a hand edit nobody
    made. That is the morning-wasting misreport the exit-3 contract exists to
    avoid, one layer out.
    """
    remote, ours = _diverged_clone(tmp_path)
    _git(ours, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    log, bindir = _git_call_log(tmp_path)
    proc = _run_push(ours, path_prefix=bindir)
    assert proc.returncode != 0
    assert "stopped part-way" not in proc.stdout, (
        "a fetch failure was reported as the rebase having stopped on a conflict")
    # It is retryable, like a lost push: the bound decides when to stop, not
    # the first failure.
    assert proc.stdout.count("NO rebase was started") == 3
    # And every attempt still PUSHED. Counting the warnings alone would pass
    # against a `continue` re-introduced in this branch only — which is the
    # exact regression that turns three retries into none.
    assert _pushes(log) == 4, (
        "a failed fetch skipped the push it was supposed to keep attempting")


def test_an_unapplyable_autostash_still_publishes_the_day(tmp_path):
    """#75's deliberately-unstaged file colliding with the other push.

    `git pull --rebase --autostash` exits 0 here: git finishes the rebase, says
    "Applying autostash resulted in conflicts", keeps the stash and leaves an
    unmerged index. The stash is the unstaged junk, NOT this run's commit —
    which replayed fine and is pushable. Dropping the day over it would be the
    #82 loss with extra steps, so it publishes and says what it left behind.
    """
    remote, ours = _diverged_clone(tmp_path, their_file="data/base.json",
                                   their_body='[{"id": "theirs"}]')
    (ours / "data" / "base.json").write_text('[{"id": "hand-edited"}]',
                                             encoding="utf-8")
    proc = _run_push(ours)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "chore(pipeline): data" in _remote_subjects(remote)
    assert "left an unmerged index" in proc.stdout
    assert "::error::" not in proc.stdout
    # The step discards the failed RE-APPLY, not the content: the comment in
    # the workflow leans on the stash still holding it, so pin that rather than
    # trusting the sentence.
    assert "autostash" in _git(ours, "stash", "list").stdout
    assert "hand-edited" in _git(ours, "stash", "show", "-p").stdout
    assert _git(ours, "ls-files", "-u").stdout == "", "the unmerged index survived"


def test_an_unapplyable_autostash_does_not_poison_the_remaining_attempts(tmp_path):
    """The compound case, and the one that decides whether the day survives.

    The unmerged index git leaves behind after a failed autostash re-apply is
    not inert: every LATER `git pull` refuses outright ("Pulling is not
    possible because you have unmerged files") and starts no rebase, so it
    arrives at the loop as a not-a-conflict failure. Carry it, and one more
    lost race after the collision spends every remaining attempt on a state
    that cannot resolve itself — the #82 loss again, reached by a longer road.
    So the collision has to be CLEARED, not merely reported: the test that only
    covers "the push right after the collision succeeds" cannot see this.
    """
    remote, ours = _diverged_clone(tmp_path, their_file="data/base.json",
                                   their_body='[{"id": "theirs"}]')
    (ours / "data" / "base.json").write_text('[{"id": "hand-edited"}]',
                                             encoding="utf-8")
    # Refuse the FIRST post-rebase push — the one that lands right after the
    # autostash collision — and accept the next.
    _reject_pushes(remote, times=1)
    proc = _run_push(ours)
    assert proc.returncode == 0, (
        "a lost race after an autostash collision killed the day: "
        + proc.stdout + proc.stderr)
    assert "chore(pipeline): data" in _remote_subjects(remote)
    assert "left an unmerged index" in proc.stdout
    assert "NO rebase was started" not in proc.stdout, (
        "the unmerged index was carried into the next attempt")
