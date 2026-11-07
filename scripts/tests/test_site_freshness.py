"""Guards for the Cloudflare site's deployment manifest and freshness check (#102).

The site moves from Vercel to Cloudflare Workers Builds. A build that fails —
or one Workers Builds never starts — leaves the previous deployment serving 200,
so liveness monitoring cannot see it. `write_deploy_manifest.py` records, inside
the deployed output, how many threads each date held; `check_site_freshness.py`
compares that with the committed `data/threads/`. Both count with
`check_mcp_freshness.committed_index`, so the two sides cannot drift apart.
"""

import json
import os
import re
import subprocess
import sys

import pytest

import check_site_freshness as csf

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(SCRIPTS)
WRITER = os.path.join(SCRIPTS, "write_deploy_manifest.py")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _threads(root, files):
    d = root / "data" / "threads"
    d.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (d / name).write_text(body if isinstance(body, str) else json.dumps(body))
    return d


def _repo(tmp_path, files):
    root = tmp_path / "repo"
    root.mkdir()
    _threads(root, files)
    (root / "out").mkdir()
    env = dict(os.environ, **GIT_ENV)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=root, check=True, env=env)
    return root


def _write_manifest(root):
    return subprocess.run([sys.executable, WRITER], cwd=root,
                          capture_output=True, text=True)


GOOD = {
    "2026-11-05.json": [{"date": "2026.11.05"}, {"date": "2026.11.05"}],
    "2026-11-06.json": [{"date": "2026.11.06"}],
    "2026-11-06.progress.json": [{"date": "2026.11.06"}] * 9,
}


# ---------- the manifest writer ----------

def test_manifest_counts_threads_by_their_own_date(tmp_path):
    root = _repo(tmp_path, GOOD)
    p = _write_manifest(root)
    assert p.returncode == 0, p.stderr
    m = json.loads((root / "out" / "__deployment.json").read_text())
    assert m["threads"] == {"2026-11-05": 2, "2026-11-06": 1}  # progress not counted
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                          capture_output=True, text=True).stdout.strip()
    assert m["gitSha"] == head


def test_manifest_follows_thread_date_not_filename(tmp_path):
    root = _repo(tmp_path, {"2026-11-05.json": [{"date": "2026.11.04"}],
                            "empty.json": []})
    _write_manifest(root)
    m = json.loads((root / "out" / "__deployment.json").read_text())
    assert m["threads"] == {"2026-11-04": 1}


@pytest.mark.parametrize("bad", ["{not json", '{"a": 1}'])
def test_manifest_writer_never_fails_the_build(tmp_path, bad):
    """A manifest problem must not stop publishing: no file, exit 0, say so."""
    root = _repo(tmp_path, {**GOOD, "2026-11-07.json": bad})
    p = _write_manifest(root)
    assert p.returncode == 0
    assert not (root / "out" / "__deployment.json").exists()
    assert "not written" in p.stdout


def test_manifest_writer_without_out_dir_exits_zero(tmp_path):
    root = _repo(tmp_path, GOOD)
    (root / "out").rmdir()
    p = _write_manifest(root)
    assert p.returncode == 0
    assert "not written" in p.stdout


def test_manifest_writer_survives_an_unexpected_error(tmp_path):
    """Even a crash inside the writer (here: its helper module is missing) exits 0."""
    root = _repo(tmp_path, GOOD)
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    import shutil
    shutil.copy(WRITER, shadow / "write_deploy_manifest.py")  # no check_mcp_freshness beside it
    p = subprocess.run([sys.executable, "-I", str(shadow / "write_deploy_manifest.py")],
                       cwd=root, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "not written: unexpected" in p.stdout
    assert not (root / "out" / "__deployment.json").exists()


# ---------- the freshness check ----------

class _Served:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc, self.calls = payload, exc, 0

    def __call__(self, url, timeout=30.0):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.payload


def _check(tmp_path, monkeypatch, served, files=GOOD, attempts=1):
    d = _threads(tmp_path, files)
    monkeypatch.setattr(csf, "fetch_manifest", served)
    monkeypatch.setattr(csf.time, "sleep", lambda s: None)
    return csf.main(["--threads-dir", str(d), "--url", "https://x/__deployment.json",
                     "--attempts", str(attempts)])


def test_matching_counts_pass(tmp_path, monkeypatch, capsys):
    served = _Served({"gitSha": "a" * 40, "threads": {"2026-11-05": 2, "2026-11-06": 1}})
    assert _check(tmp_path, monkeypatch, served) == 0


def test_only_the_sha_differing_still_passes(tmp_path, monkeypatch):
    """A sidecar-only or docs-only push moves HEAD without changing the site."""
    served = _Served({"gitSha": "0" * 40, "threads": {"2026-11-05": 2, "2026-11-06": 1}})
    assert _check(tmp_path, monkeypatch, served) == 0


def test_count_mismatch_is_stale(tmp_path, monkeypatch, capsys):
    served = _Served({"gitSha": "b" * 40, "threads": {"2026-11-05": 1}})
    assert _check(tmp_path, monkeypatch, served) == 1
    out = capsys.readouterr().out
    assert "STALE" in out and "2026-11-06: committed 1 / served absent" in out


def test_unreachable_is_not_called_stale(tmp_path, monkeypatch, capsys):
    served = _Served(exc=csf.Unanswered("HTTP 404"))
    assert _check(tmp_path, monkeypatch, served, attempts=3) == 1
    out = capsys.readouterr().out
    assert "UNKNOWN" in out and "STALE" not in out
    assert served.calls == 3


@pytest.mark.parametrize("payload", [
    "not a dict", {"threads": "x"}, {"threads": {"2026-11-05": "2"}}, {},
])
def test_malformed_manifest_is_unknown_not_stale(tmp_path, monkeypatch, capsys, payload):
    assert _check(tmp_path, monkeypatch, _Served(payload)) == 1
    out = capsys.readouterr().out
    assert "UNKNOWN" in out and "STALE" not in out


def test_unreadable_committed_data_is_reported_as_such(tmp_path, monkeypatch, capsys):
    served = _Served({"threads": {"2026-11-05": 2}})
    assert _check(tmp_path, monkeypatch, served, files={"x.json": "{bad"}) == 1
    assert "cannot read" in capsys.readouterr().out


def test_a_later_attempt_can_catch_up(tmp_path, monkeypatch):
    seq = [{"threads": {"2026-11-05": 2}},
           {"threads": {"2026-11-05": 2, "2026-11-06": 1}}]

    def served(url, timeout=30.0):
        return seq.pop(0)
    assert _check(tmp_path, monkeypatch, served, attempts=2) == 0


class _Resp:
    def __init__(self, body=b"", exc=None):
        self.body, self.exc = body, exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        if self.exc:
            raise self.exc
        return self.body


@pytest.mark.parametrize("resp", [
    _Resp(b"<html>not json</html>"),
    _Resp(exc=__import__("http.client").client.IncompleteRead(b"{\"thr", 100)),
])
def test_fetch_failures_on_the_real_path_are_unknown(tmp_path, monkeypatch, capsys, resp):
    """Through fetch_manifest itself, not a stub: bad body or a cut-off body is UNKNOWN."""
    d = _threads(tmp_path, GOOD)
    monkeypatch.setattr(csf.urllib.request, "urlopen", lambda req, timeout=30.0: resp)
    monkeypatch.setattr(csf.time, "sleep", lambda s: None)
    assert csf.main(["--threads-dir", str(d), "--attempts", "2"]) == 1
    out = capsys.readouterr().out
    assert "UNKNOWN" in out and "STALE" not in out


def test_fetch_sends_its_own_user_agent(tmp_path, monkeypatch):
    """Cloudflare 403s the default urllib agent; the check must name itself."""
    seen = {}

    def fake(req, timeout=30.0):
        seen["ua"] = req.get_header("User-agent")
        return _Resp(b'{"threads": {"2026-11-05": 2, "2026-11-06": 1}}')
    monkeypatch.setattr(csf.urllib.request, "urlopen", fake)
    d = _threads(tmp_path, GOOD)
    assert csf.main(["--threads-dir", str(d), "--attempts", "1"]) == 0
    assert seen["ua"] and "Python-urllib" not in seen["ua"]


def test_fetch_success_on_the_real_path(tmp_path, monkeypatch):
    d = _threads(tmp_path, GOOD)
    body = json.dumps({"gitSha": "x", "threads": {"2026-11-05": 2, "2026-11-06": 1}}).encode()
    monkeypatch.setattr(csf.urllib.request, "urlopen", lambda req, timeout=30.0: _Resp(body))
    assert csf.main(["--threads-dir", str(d), "--attempts", "1"]) == 0


# ---------- writer and checker agree, end to end ----------

def test_writer_output_satisfies_the_checker(tmp_path, monkeypatch):
    root = _repo(tmp_path, GOOD)
    _write_manifest(root)
    body = (root / "out" / "__deployment.json").read_bytes()
    monkeypatch.setattr(csf, "fetch_manifest",
                        lambda url, timeout=30.0: json.loads(body))
    assert csf.main(["--threads-dir", str(root / "data" / "threads"),
                     "--attempts", "1"]) == 0


# ---------- the counting rule is the site's ----------

def test_site_reads_the_files_committed_index_reads():
    """committed_index copies loadThreads' file predicate; pin the site's side.

    If src/lib/data.ts starts reading different files, the manifest would count
    something other than what the site renders.
    """
    with open(os.path.join(REPO_ROOT, "src", "lib", "data.ts"), encoding="utf-8") as f:
        src = f.read()
    assert re.search(
        r'\.filter\(\(f\) => f\.endsWith\("\.json"\) && !f\.endsWith\("\.progress\.json"\)\)',
        src)
    assert 'path.join(process.cwd(), "data", "threads")' in src


# ---------- the wiring: daily-batch.yml runs the check, uptime fails on it ----------

def _workflow(name):
    yaml = pytest.importorskip("yaml")
    with open(os.path.join(REPO_ROOT, ".github", "workflows", name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _verify_job():
    spec = _workflow("daily-batch.yml")
    assert "verify-site" in spec["jobs"], (
        "the site freshness job is gone — a Cloudflare build that fails or never "
        "starts leaves the old site answering 200, and nothing else sees it (#102)")
    return spec, spec["jobs"]["verify-site"]


def test_verify_site_runs_the_check_on_the_branch_tip():
    """On a scheduled run `github.sha` predates the data commit; checking it out
    would compare the site with yesterday's data and pass on a stale morning."""
    _, job = _verify_job()
    checkout = next(s for s in job["steps"]
                    if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout.get("with", {}).get("ref") == "${{ github.ref_name }}"
    runs = [str(s.get("run", "")) for s in job["steps"]]
    assert any(r.strip().startswith("python3 scripts/check_site_freshness.py")
               for r in runs), runs


def test_verify_site_runs_after_a_late_failure_but_only_on_main():
    """Like deploy-mcp: the publish can red on its last step after pushing, and a
    dispatch from a branch would compare that branch's data with production."""
    _, job = _verify_job()
    assert job["needs"] == "fetch-and-summarize"
    assert "!cancelled()" in job["if"] and "success()" not in job["if"]
    assert "github.ref" in job["if"] and "default_branch" in job["if"]
    assert job["permissions"] == {"contents": "read"}


def test_verify_site_waits_long_enough_for_a_build():
    """Spec: measured build+upload (~4.5 min on 2026-11-07) + 10 min, floor 30 min,
    60 s apart. The job timeout must outlast the check's worst case — every
    attempt spending its full fetch timeout too — or the job dies without saying
    STALE or UNKNOWN."""
    import inspect
    import re
    _, job = _verify_job()
    run = next(str(s["run"]) for s in job["steps"]
               if "check_site_freshness.py" in str(s.get("run", "")))
    attempts = int(re.search(r"--attempts (\d+)", run).group(1))
    sleep = float(re.search(r"--sleep (\d+)", run).group(1))
    assert sleep == 60
    # No sleep follows the last attempt, so the span covered is (attempts-1) sleeps.
    assert (attempts - 1) * sleep >= 30 * 60, run
    fetch_timeout = inspect.signature(csf.fetch_manifest).parameters["timeout"].default
    worst = attempts * fetch_timeout + (attempts - 1) * sleep + 300  # + setup
    assert job["timeout-minutes"] * 60 >= worst, (job["timeout-minutes"], worst)
    setup = [s for s in job["steps"]
             if str(s.get("uses", "")).startswith("actions/setup-python")]
    assert setup and str(setup[0]["with"]["python-version"]) == "3.12"


def test_a_stale_site_raises_the_failure_issue():
    """A red check nobody opens is not a notification (#85, now for the site)."""
    spec, _ = _verify_job()
    notify = spec["jobs"]["notify-on-failure"]
    assert "verify-site" in notify["needs"]
    assert notify["if"] == "failure()"
    body = next(s["run"] for s in notify["steps"] if "run" in s)
    assert "verify-site" in body


def test_uptime_fails_when_the_sitemap_is_not_200():
    """Was a warning on Vercel. A missing sitemap after the move is a real outage
    for crawlers, so it must red the check like the home page does."""
    job = _workflow("uptime.yml")["jobs"]["check"]
    step = next(s for s in job["steps"] if "sitemap.xml" in str(s.get("run", "")))
    assert "::error::" in step["run"] and "exit 1" in step["run"]
    assert "::warning::" not in step["run"]


# ---------- the premise of the Workers Builds watch-path exclude ----------
# Moved from the deleted test_vercel_ignore_build.py (#101): the Cloudflare
# build skips pushes that touch only `data/pending-batches/**` (dashboard
# setting, recorded in docs/cloudflare-cutover.md). That skip is safe only while
# (a) the sidecars live there and (b) nothing the build reads lives there.
# Break (a) and every sidecar push rebuilds production, re-opening the ordering
# hazard; break (b) and a sidecar-only push leaves the site stale in a way the
# thread-count manifest cannot see.

BUILD_INPUTS = [
    "src", "scripts/lib", "next.config.ts", "package.json",
    "scripts/gen_status.py", "scripts/pipeline/jsonio.py",
    "scripts/pipeline/__init__.py",
    # The Workers Builds build command also runs the manifest writer.
    "scripts/write_deploy_manifest.py", "scripts/check_mcp_freshness.py",
]
# Every top-level .mjs/.js under scripts/ is treated as a build input, so a new
# one is checked without anyone remembering to list it here.
BUILD_INPUT_GLOBS = ["scripts/*.mjs", "scripts/*.js"]

# The build pipeline this premise was checked against. A new step may read
# something new; when this changes, re-check what it reads before updating it.
EXPECTED_BUILD_SCRIPT = (
    "node scripts/validate-data.mjs --fix && node scripts/enrich-members.mjs"
    " && node scripts/generate-feeds.js && node scripts/generate-sitemap.mjs"
    " && next build"
)


def test_build_script_is_the_one_the_premise_was_checked_against():
    with open(os.path.join(REPO_ROOT, "package.json")) as f:
        assert json.load(f)["scripts"]["build"] == EXPECTED_BUILD_SCRIPT


def test_build_inputs_do_not_read_pending_batches():
    """A new build input that reads data/pending-batches/ must drop the
    watch-path exclude (Cloudflare dashboard) in the same change."""
    import glob
    rels = list(BUILD_INPUTS)
    for g in BUILD_INPUT_GLOBS:
        found = sorted(os.path.relpath(p, REPO_ROOT)
                       for p in glob.glob(os.path.join(REPO_ROOT, g)))
        assert found, f"{g} matched nothing"
        rels += found
    offenders = []
    for rel in rels:
        root = os.path.join(REPO_ROOT, rel)
        # A renamed input must fail here, not silently drop out of the check.
        assert os.path.exists(root), f"build input {rel} does not exist"
        paths = [root] if os.path.isfile(root) else [
            os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs]
        for p in paths:
            with open(p, encoding="utf-8", errors="replace") as f:
                if "pending-batches" in f.read():
                    offenders.append(os.path.relpath(p, REPO_ROOT))
    assert offenders == []


def test_sidecar_dir_is_where_the_watch_path_exclude_looks():
    import pipeline.batch_state as bs
    assert bs.PENDING_DIR == os.path.join("data", "pending-batches")
