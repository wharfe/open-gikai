"""Contract tests for scripts/enrich-members.mjs.

This file drives the generator against fixtures. The tests that check the
committed data/members.json are added in the task that regenerates it — adding
them here would land a red main, because the committed file has 1241 members
with no links until then, and ci.yml runs pytest on every push to main.

Fixtures are the load-bearing half either way: if the generator dies and the
enrich step fails, members.json keeps its previous good content and every
assertion about the committed file passes against data nothing regenerated.
"""

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "enrich-members.mjs"

WIKIPEDIA_HOST_SUFFIX = "wikipedia.org"


def _run(members, threads=None, tmp_path=None, expect_ok=True):
    """Write a fixture tree, run the real CLI over it, return the result."""
    data_dir = tmp_path / "data"
    (data_dir / "threads").mkdir(parents=True)
    members_path = data_dir / "members.json"
    members_path.write_text(json.dumps(members, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    for name, payload in (threads or {}).items():
        (data_dir / "threads" / name).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    proc = subprocess.run(
        ["node", str(SCRIPT), "--members-path", str(members_path)],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    if expect_ok:
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    return proc, members_path, json.loads(members_path.read_text(encoding="utf-8"))


# Fixtures carry an `id` equal to their key because that is the real shape:
# 1267 of 1298 committed members have `id == key`, and getMemberMinistry
# (ministry.mjs:86) returns null without one. The enricher resolves the ministry
# from the STORED object, exactly as src/lib/data.ts does, so a fixture with no
# id would silently test the "no ministry" path while claiming to test the
# ministry path. The one fixture that deliberately omits `id` is the validator
# stub below — that IS the 31-member shape, and it must keep omitting it.


def _is_wikipedia(url):
    """Host suffix, not a substring: ja.m.wikipedia.org and en.wikipedia.org are
    the same mistake, and `notwikipedia.org` is not one. Both groups in this
    file use this one predicate — two spellings of the same rule drift."""
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    return host == WIKIPEDIA_HOST_SUFFIX or host.endswith("." + WIKIPEDIA_HOST_SUFFIX)


def _thread_with_speakers(*member_ids):
    return [{"id": "t_x", "date": "2026-05-14", "committee": "C", "house": "参議院",
             "topic": "T", "topicTag": "t", "topicColor": "#000", "summary": "s",
             "speeches": [{"memberId": m} for m in member_ids]}]


# --- Group A: the generator, against fixtures --------------------------------

def test_ministry_member_with_a_live_gov_page_gets_the_hub_and_a_search(tmp_path):
    members = {"m_a": {"id": "m_a", "name": "寺崎秀俊", "role": "総務省自治税務局長", "rank": "member"}}
    _, _, out = _run(members, {"d.json": _thread_with_speakers("m_a")}, tmp_path)
    links = out["m_a"]["links"]
    assert links[0]["url"] == "/gov/soumu"
    assert links[0]["label"] == "総務省の発言者一覧"
    assert "総務省" in links[1]["url"] or "%E7%B7%8F%E5%8B%99%E7%9C%81" in links[1]["url"]
    assert not any(_is_wikipedia(l["url"]) for l in links)


def test_a_ministry_with_no_speaker_in_threads_gets_no_gov_link(tmp_path):
    """/gov/[slug] is dynamicParams=false and only builds pages for ministries
    that have a speaking member, so linking on ministry resolution alone ships
    a 404 the day a ministry's only witness stops appearing."""
    members = {"m_a": {"id": "m_a", "name": "寺崎秀俊", "role": "総務省自治税務局長", "rank": "member"}}
    _, _, out = _run(members, {}, tmp_path)          # no threads at all
    links = out["m_a"]["links"]
    assert len(links) == 1
    assert links[0]["url"].startswith("https://www.google.com/search")


def test_a_ministry_that_resolves_but_is_not_live_gets_no_gov_link(tmp_path):
    """The sharper form of the test above: one ministry IS live and another
    resolves without a speaker, in the same run. This is the only shape that
    can fail — on the committed data every resolved ministry is live (37 of 37,
    measured), so the real-data guards are green for a reason that has nothing
    to do with the gate working. Delete `&& liveSlugs.has(...)` and this fails
    while its neighbours keep passing.
    """
    members = {
        "m_speaks": {"id": "m_speaks", "name": "寺崎秀俊",
                     "role": "総務省自治税務局長", "rank": "member"},
        "m_silent": {"id": "m_silent", "name": "野村竜一",
                     "role": "気象庁長官", "rank": "minister"},
    }
    _, _, out = _run(members, {"d.json": _thread_with_speakers("m_speaks")}, tmp_path)
    assert out["m_speaks"]["links"][0]["url"] == "/gov/soumu"
    silent = [l["url"] for l in out["m_silent"]["links"]]
    assert not any(u.startswith("/gov/") for u in silent), (
        f"linked to a /gov page with no speaker, i.e. a 404: {silent}")


def test_an_m_member_with_no_ministry_gets_one_search_and_no_wikipedia(tmp_path):
    members = {"m_b": {"id": "m_b", "name": "山田昌弘", "role": "中央大学文学部教授", "rank": "member"}}
    _, _, out = _run(members, {}, tmp_path)
    links = out["m_b"]["links"]
    assert len(links) == 1
    assert not any(_is_wikipedia(l["url"]) for l in links)


def test_a_non_m_member_gets_wikipedia_and_two_searches(tmp_path):
    members = {"morimotoshinji": {"id": "morimotoshinji", "name": "森本真治", "role": "議員", "rank": "member"}}
    _, _, out = _run(members, {}, tmp_path)
    links = out["morimotoshinji"]["links"]
    assert [l["label"] for l in links] == ["Wikipedia", "公式サイト検索", "X (Twitter) 検索"]
    assert links[0]["url"].startswith("https://ja.wikipedia.org/wiki/")


def test_a_validator_stub_with_no_id_and_no_role_still_gets_a_link(tmp_path):
    """validate-data.mjs --fix adds entries keyed m_... whose value has no `id`
    and an empty role, with name == the key. Scanning member.id misses all 31
    of them; the map key is the identifier of record."""
    members = {"m_f9112eed": {"name": "m_f9112eed", "role": "", "rank": {"score": 0}}}
    _, _, out = _run(members, {}, tmp_path)
    links = out["m_f9112eed"]["links"]
    assert len(links) >= 1
    assert not any(_is_wikipedia(l["url"]) for l in links)


def test_rank_minister_does_not_make_a_bureaucrat_elected(tmp_path):
    """m_40667609 野村竜一 is 気象庁長官 with rank "minister". ministry.mjs says
    in as many words not to use rank for this. A classifier that reads it puts
    /wiki/... on him — and on 記者 and 事務局, which resolve 200 to articles
    about the occupation and the org-chart term."""
    members = {"m_c": {"id": "m_c", "name": "野村竜一", "role": "気象庁長官", "rank": "minister"},
               "m_d": {"id": "m_d", "name": "記者", "role": "記者", "party": "記者", "rank": "member"},
               "m_e": {"id": "m_e", "name": "事務局", "role": "事務局", "party": "事務局", "rank": "member"}}
    _, _, out = _run(members, {}, tmp_path)
    for mid in ("m_c", "m_d", "m_e"):
        assert not any(_is_wikipedia(l["url"]) for l in out[mid]["links"]), mid


def test_running_twice_does_not_touch_the_file(tmp_path):
    """Idempotence has to be observable on the file, not just in the output:
    an atomic rename of identical text still changes the inode and mtime, and
    daily-batch commits whatever git sees as modified."""
    members = {"morimotoshinji": {"id": "morimotoshinji", "name": "森本真治", "role": "議員", "rank": "member"}}
    _, path, _ = _run(members, {}, tmp_path)
    before = os.stat(path)
    proc = subprocess.run(["node", str(SCRIPT), "--members-path", str(path)],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0
    after = os.stat(path)
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_only_links_is_written(tmp_path):
    """`links` is the only field this script owns; every other one belongs to
    scripts/pipeline/members.py or to validate-data.mjs --fix.

    The assertion is on the key SET, not on the absence of `id` specifically.
    An earlier draft asserted `"id" not in entry` — that made sense while the
    plan normalised the map key onto the member before resolving the ministry,
    because then a stray write-back was a real hazard. The plan stopped doing
    that (it made this script link to /gov pages the site never builds), so the
    hazard is gone and the assertion had become both wrong and unfixable: a
    fixture needs `id` for getMemberMinistry to resolve at all, so it is in the
    input, so it is in the output. Comparing key sets says what was actually
    meant and does not care whether the fixture carries an `id`.
    """
    members = {"m_a": {"id": "m_a", "name": "寺崎秀俊", "role": "総務省自治税務局長", "rank": "member",
                       "party": None, "bio": "", "stance": []}}
    _, _, out = _run(members, {"d.json": _thread_with_speakers("m_a")}, tmp_path)
    entry = out["m_a"]
    assert set(entry) == set(members["m_a"]) | {"links"}, (
        "enrich-members must add `links` and nothing else")
    # The values it does not own come through byte-identical.
    for field in ("id", "name", "role", "rank", "party", "bio", "stance"):
        assert entry[field] == members["m_a"][field], field


def test_one_malformed_member_does_not_stop_the_run(tmp_path):
    """This step runs under `bash -e` a few steps before `git add
    data/members.json`, so exiting on one bad row throws away the morning's
    threads — which is what #52 and #74 each closed once. A file that will not
    parse is still fatal (there is nothing to work from); one bad row is not.
    """
    members = {"m_a": {"id": "m_a", "name": "寺崎秀俊",
                       "role": "総務省自治税務局長", "rank": "member"},
               "m_bad": "これはオブジェクトではない"}
    proc, _, out = _run(members, {"d.json": _thread_with_speakers("m_a")}, tmp_path)
    assert proc.returncode == 0
    assert out["m_bad"] == "これはオブジェクトではない", "the bad row is passed through as-is"
    assert out["m_a"]["links"][0]["url"] == "/gov/soumu", "its neighbours are still enriched"
    assert "m_bad" in proc.stdout + proc.stderr, "and it is named in the log"


def test_a_broken_members_file_is_refused_without_writing(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "threads").mkdir(parents=True)
    path = data_dir / "members.json"
    path.write_text('{"a": ', encoding="utf-8")          # truncated
    proc = subprocess.run(["node", str(SCRIPT), "--members-path", str(path)],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode != 0
    assert path.read_text(encoding="utf-8") == '{"a": ', "the file must be untouched"


def test_an_unreadable_thread_file_does_not_stop_the_run(tmp_path):
    """threads/ is the input this script does not own. Refusing to run because
    one thread file is corrupt would take the morning's publish down for data
    that repairs itself on the next fetch."""
    members = {"m_a": {"id": "m_a", "name": "寺崎秀俊", "role": "総務省自治税務局長", "rank": "member"}}
    data_dir = tmp_path / "data"
    (data_dir / "threads").mkdir(parents=True)
    (data_dir / "members.json").write_text(json.dumps(members, ensure_ascii=False),
                                           encoding="utf-8")
    (data_dir / "threads" / "good.json").write_text(
        json.dumps(_thread_with_speakers("m_a"), ensure_ascii=False), encoding="utf-8")
    (data_dir / "threads" / "bad.json").write_text("{", encoding="utf-8")
    proc = subprocess.run(
        ["node", str(SCRIPT), "--members-path", str(data_dir / "members.json")],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
    out = json.loads((data_dir / "members.json").read_text(encoding="utf-8"))
    assert out["m_a"]["links"][0]["url"] == "/gov/soumu"


def test_the_mcp_server_hands_out_absolute_member_links():
    """apps/mcp returns Member objects straight to another LLM on another host,
    which cannot resolve /gov/{slug}. eslint ignores apps/** and no CI job
    builds the MCP server, so nothing else would notice this coming undone."""
    src = (REPO_ROOT / "apps" / "mcp" / "src" / "lib" / "mcp" / "tools.ts").read_text(
        encoding="utf-8")
    assert "function withAbsoluteLinks" in src
    assert "return { member: withAbsoluteLinks(member) };" in src
    assert "filtered.map(withAbsoluteLinks)" in src
