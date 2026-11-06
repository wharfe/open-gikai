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
