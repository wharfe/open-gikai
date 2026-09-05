# Member Links Rewiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `data/members.json` の全メンバー（1,298人）が、存在を確認できる着地先へのリンクを1本以上持ち、その生成が毎朝の経路に配線され、配線が外れたらテストが赤くなる状態にする。

**Architecture:** 判別は2軸のみ — `map key が m_ で始まるか` と `getMemberMinistry() が省庁を返すか`。新しい人物分類器を作らない。生成は純関数、I/O は CLI 側。JS 側 atomic writer を1つに集約し、`validate-data.mjs --fix` の直後に enrich を挟む。

**Tech Stack:** Node 22 ESM（`scripts/*.mjs`）、Python 3.12 + pytest（契約テスト）、GitHub Actions。

**Spec:** `docs/design-debate/member-links-rewiring/spec.md`（確定仕様の本文は同ディレクトリの `verdict.md` §3）

## Global Constraints

- **(A) 級 diff。** CI 定義（`daily-batch.yml` / `ci.yml`）に触る。Gate3 は `/code-gate` 必須、critical 0 まで。
- **識別子の正本は map key。** 走査は必ず `Object.entries(members)`。値の `id` は31件で欠けている。
- **`getMemberMinistry` は `src/lib/data.ts` と同じく `member` をそのまま渡す（key 正規化しない）。** `data.ts:352-354` は `Object.values` + `member.id` で解決するので、こちらだけ正規化すると **`data.ts` が生成しない `/gov` ページへリンクする**（`dynamicParams=false` なので hard 404）。Gate2 実測: 正規化しても解決件数は増えない（`id` を欠く31件は `role` も空で、`getMemberMinistry` は id を見る前に null を返す）。**触ってよいフィールドは `links` だけ。**
- **人・党派で分岐しない。** `rank` / `party` / 役職キーワードによる分岐を新コードに書かない。`is_elected_member` / `BUREAUCRAT_ROLES` を復活させない。
- **省庁判定の正本は `src/lib/ministry.mjs` ひとつ。** 写しも第二の判定も作らない。
- **JS 側 atomic writer は1つ。** 手順（同ディレクトリ temp → descriptor へ `writeFileSync` → file fsync → close → rename → dir fsync → 失敗時 unlink）を**変えない**。「改良」をこの diff に混ぜない。
- **LLM・外部 API・実行間の状態を使わない。** 同じ入力 → 同じ出力。
- **`|| true` / `continue-on-error: true` / 日付条件を新ステップに付けない。**
- 受け入れ: `npm run lint && npm run validate` と `cd scripts && python -m pytest -q`。**pytest の skip 数は 0**（PyYAML 未導入の静かな skip を合格にしない。`pip install -r requirements-dev.txt`、Python 3.12）。
- `npm run lint` は `scripts/**` を見ない（`eslint.config.mjs:17`）。**新コードは lint 対象外**であることを承知で進む。
- 全コミットメッセージの末尾に付ける:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0121LjSnhYPSzTYvFLiYPN1t
  ```
- **タスクの順序を入れ替えない。** UI(Task 3) と MCP(Task 3.5) はデータ再生成(Task 4) より前でなければならない（先にデータが出ると、`/gov` が外部リンクとして描画された状態で1回デプロイされ、MCP は解決できない相対 URL を配る）。配線(Task 5) は再生成の後（初回の朝が no-op になり、冪等性が本番で確認できる）。
- **ブランチ + PR で出荷する。main へ直接コミットしない。**
  ```bash
  git switch -c feat/member-links-rewiring
  ```
  Gate2 で同じ根の critical が2回出たため、出荷単位ごと替えた判断（記録は下の「なぜブランチなのか」）。
  **緑でなければならないのは PR の HEAD であって、途中のコミットではない。** タスク間の
  前方参照（あるタスクのテストが次のタスクの成果物を参照する）は、fence が本質的にタスクを
  またぐ以上避けられないので、またげる出荷単位を使う。
- **各タスクの最後に `git push` する**（`-u origin feat/member-links-rewiring`、以降は `git push`）。
  push しないと CI が一度も走らず、「緑を確認した」が観測に基づかない自己申告になる。
  途中の赤は許容する — **PR をマージする前に緑であればよい。**
- **`git pull --ff-only` を使わない。** ブランチは main より先行するので必ず失敗する。
  main を取り込む必要が出たら `git pull --rebase --autostash origin main`（`daily-batch.yml` が
  #82 で採ったのと同じ形）。

## File Structure

| ファイル | 役割 | 変更 |
|---|---|---|
| `scripts/lib/jsonio.mjs` | JS 側 atomic writer の正本 | **新規**（`validate-data.mjs` から手順ごと移設） |
| `scripts/enrich-members.mjs` | リンク生成の純関数 + CLI | **新規** |
| `scripts/validate-data.mjs` | 検証と `--fix`。writer を import に差し替え | 変更 |
| `scripts/enrich_members.py` | 旧 Python enricher | **削除** |
| `src/components/member/member-profile-view.tsx` | 相対 URL を内部リンクとして描画 | 変更 |
| `apps/mcp/src/lib/mcp/tools.ts` | MCP へ返す links を絶対 URL 化 | 変更 |
| `scripts/check_committable_json.py` | 消えるステップ名の参照を実態へ | 変更 |
| `.github/workflows/daily-batch.yml` | `Validate and generate` を3ステップに分割 | 変更 |
| `.github/workflows/ci.yml` | python-tests に `setup-node` | 変更 |
| `package.json` | `build` に enrich を挟む | 変更 |
| `scripts/tests/test_member_links.py` | 生成器の fixture テスト + 公開契約テスト | **新規** |
| `scripts/tests/test_jsonio.py` | JS 半分の fence を helper 本体へ向け直す | 変更 |
| `scripts/tests/test_systemic_failure.py` | 配線の契約テスト | 変更 |
| `data/members.json` | 全件再生成を1回コミット | 変更 |
| `CLAUDE.md` | atomic writer の所在の記述を更新 | 変更 |

---

### Task 1: JS 側 atomic writer を共有モジュールへ抽出する

`validate-data.mjs` の `writeJsonAtomic` は非 export のローカル関数で、`enrich-members.mjs` からは使えない。写しを増やさずに共有する。**同時に既存 fence を直す**: `test_jsonio.py::test_the_js_half_of_the_rule_holds_too` は現在**呼び出し行の文字列だけ**を見るので、本体を移しても通り続ける（＝空振りする）。

**Files:**
- Create: `scripts/lib/jsonio.mjs`
- Modify: `scripts/validate-data.mjs:14-65`（`fsyncDir` / `writeJsonAtomic` を削除し import に差し替え）
- Test: `scripts/tests/test_jsonio.py:170-178`（`test_the_js_half_of_the_rule_holds_too` を書き換え）

**Interfaces:**
- Consumes: なし
- Produces: `export function writeJsonAtomic(path: string, text: string): void` — `scripts/lib/jsonio.mjs` から。Task 2 が import する。**シグネチャは現行どおり `(path, text)`。Python 側の `write_json_atomic(path, obj)` と混ぜない（あちらはオブジェクトを取る）。**

- [ ] **Step 0: ブランチを切る**

```bash
git switch main && git pull --rebase origin main
git switch -c feat/member-links-rewiring
```
Expected: `main` の最新から新しいブランチができる。以降の全コミットはここに乗る。

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_jsonio.py` の `test_the_js_half_of_the_rule_holds_too` を丸ごと置き換える。

```python
def test_the_js_half_of_the_rule_holds_too():
    """The JS writers of committed JSON share one atomic helper (#57/#72).

    daily-batch.yml runs validate-data.mjs --fix and enrich-members.mjs
    immediately before `git add data/members.json`, so a job killed mid-write
    commits a truncated members.json — and src/lib/data.ts is deliberately
    fatal on that, i.e. a red Vercel build.

    The AST sweep below walks only *.py and is structurally blind to these two.
    The previous version of this test asserted on the CALL SITE string in
    validate-data.mjs, so moving the function body out of that file left the
    test passing while nothing checked the steps any more. Assert on the body,
    in the file that now holds it.
    """
    helper_path = os.path.join(SCRIPTS_DIR, "lib", "jsonio.mjs")
    assert os.path.exists(helper_path), (
        "scripts/lib/jsonio.mjs is the single JS-side atomic writer (#57)")
    helper = open(helper_path, encoding="utf-8").read()

    # The steps, not the outline. Each of these is load-bearing: a temp file in
    # the SAME directory (rename is only atomic within one filesystem), fsync
    # the file, rename, then fsync the directory so the rename itself is
    # durable rather than just the bytes it points at.
    assert "export function writeJsonAtomic" in helper
    assert "dirname(path)" in helper, "temp file must be in the target's own directory"
    assert "openSync(tmp" in helper
    assert "writeFileSync(fd" in helper, (
        "writeFileSync on the descriptor, not writeSync: writeSync can short-write")
    assert "fsyncSync(fd)" in helper
    assert "renameSync(tmp, path)" in helper
    assert "fsyncDir(" in helper, "the directory fsync makes the rename durable"
    assert "unlinkSync(tmp)" in helper, "a failed write must not strand its temp file"

    # The writer that exists today goes through it and does not write bare.
    # enrich-members.mjs joins this list in the task that creates it — asserting
    # on a file the next task writes makes THIS task's test unrunnable.
    src = open(os.path.join(SCRIPTS_DIR, "validate-data.mjs"), encoding="utf-8").read()
    assert "jsonio.mjs" in src, "validate-data.mjs must import the shared helper"
    assert "writeJsonAtomic(" in src, "validate-data.mjs must write through the helper"
    assert "writeFileSync(MEMBERS_PATH" not in src
    assert "function writeJsonAtomic" not in src, (
        "validate-data.mjs must not carry a second copy of the helper")
```

- [ ] **Step 2: 走らせて失敗を確認する**

Run: `cd scripts && python -m pytest tests/test_jsonio.py::test_the_js_half_of_the_rule_holds_too -v`
Expected: FAIL — `scripts/lib/jsonio.mjs is the single JS-side atomic writer (#57)`（ファイルがまだ無い）

- [ ] **Step 3: `scripts/lib/jsonio.mjs` を作る**

`validate-data.mjs:22-65` のコメントと関数を**手順を変えずに**移す。

```js
/**
 * The JS half of scripts/pipeline/jsonio.py's rule (#57/#72): a writer of a
 * file this repo commits must never leave a half-written one behind.
 * daily-batch.yml runs validate-data.mjs --fix and enrich-members.mjs
 * immediately before `git add data/members.json`, so a job killed mid-write
 * commits a truncated members.json — and src/lib/data.ts is deliberately fatal
 * on that, which is a red Vercel build.
 *
 * Same shape as the Python side, and that has to mean the same steps, not just
 * the same outline: temp file in the SAME directory (rename is only atomic
 * within one filesystem), fsync the file, rename, then fsync the directory so
 * the rename is durable and not merely the bytes it points at. The directory
 * fsync is best-effort — some filesystems refuse to open a directory, and
 * failing the whole write over a durability upgrade would be worse than the
 * crash window it closes.
 *
 * This module exists because two JS writers need it and node cannot import the
 * Python one. It takes TEXT, not an object — the Python `write_json_atomic`
 * takes an object and serialises it. Do not unify the signatures; the callers
 * control their own `JSON.stringify` spacing and a change there rewrites every
 * committed data file on the next run.
 */
import {
  writeFileSync, renameSync, unlinkSync, openSync, fsyncSync, closeSync,
} from "fs";
import { join, dirname, basename } from "path";

function fsyncDir(dir) {
  let fd;
  try {
    fd = openSync(dir, "r");
    fsyncSync(fd);
  } catch { /* best-effort */ } finally {
    if (fd !== undefined) { try { closeSync(fd); } catch { /* ignore */ } }
  }
}

export function writeJsonAtomic(path, text) {
  const dir = dirname(path);
  const tmp = join(dir, `.${basename(path)}.${process.pid}.tmp`);
  try {
    const fd = openSync(tmp, "w");
    try {
      // writeFileSync on the descriptor, not writeSync: writeSync returns a
      // byte count and can short-write, which would silently truncate exactly
      // the way this function exists to prevent. writeFileSync loops until the
      // whole buffer is out.
      writeFileSync(fd, text, "utf-8");
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
    renameSync(tmp, path);
    fsyncDir(dir);
  } catch (e) {
    try { unlinkSync(tmp); } catch { /* already gone */ }
    throw e;
  }
}
```

- [ ] **Step 4: `validate-data.mjs` を import に差し替える**

`scripts/validate-data.mjs` の import 行を変更する（`writeFileSync` 等は他でも使うので、実際に不要になったものだけ落とす — 差し替え後に `node scripts/validate-data.mjs` を走らせて `ReferenceError` が出ないことで確認する）。

```js
import { readFileSync, readdirSync, existsSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";
import { writeJsonAtomic } from "./lib/jsonio.mjs";
```

**`writeFileSync` / `renameSync` / `unlinkSync` / `openSync` / `fsyncSync` / `closeSync` /
`dirname` / `basename` は全部落とす。** Gate2 実測で、`validate-data.mjs` におけるこれらの実使用は
**移設する `writeJsonAtomic` の中だけ**だった。`scripts/**` は lint されない（`eslint.config.mjs:17`）
ので、未使用 import は誰も教えてくれない。落としすぎ／落とし足りないは次の Step で検出する。

そして `:22-65` のコメントブロック・`fsyncDir`・`writeJsonAtomic` を削除する。呼び出し側
（`:196` の `writeJsonAtomic(MEMBERS_PATH, ...)`）は**変えない**。

- [ ] **Step 5: テストと受け入れを走らせて緑を確認する**

**`npm run validate` だけでは足りない。** 正常なツリーでは `checkMembers` が
`referenced.size === 0` で早期 return し（`scripts/validate-data.mjs:165-169`）、
`writeJsonAtomic` にも `execSync` 分岐にも**到達しない** — 未使用 import も、落としすぎた import も、
その実行では検出できない。書き込み経路を実際に通す。

```bash
cd scripts && python -m pytest tests/test_jsonio.py -v

# 書き込み経路まで到達させる。repo の data/ は汚さない。
cd .. && TMP=$(mktemp -d) && cp -r data "$TMP/data" &&   python3 -c "
import json,pathlib
p=pathlib.Path('$TMP/data/members.json')
d=json.load(open(p,encoding='utf-8'))
k=next(iter(d)); del d[k]        # threads が参照するメンバーを1人消す
p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')" &&   (cd "$TMP" && node "$OLDPWD/scripts/validate-data.mjs" --fix) ; echo "EXIT=$?"
```
Expected: pytest PASS（skip 0）。`--fix` 側は `Added 1 missing members` 系の出力を出して
**`ReferenceError` なしで終了する**（＝ `writeJsonAtomic` 経路が実際に走った）。
終わったら `rm -rf "$TMP"`。

- [ ] **Step 6: fence が本当に効くか壊して確かめる**

`scripts/lib/jsonio.mjs` の `renameSync(tmp, path);` を一時的に `writeFileSync(path, text);` に書き換えて
`cd scripts && python -m pytest tests/test_jsonio.py::test_the_js_half_of_the_rule_holds_too -v` を走らせ、
**FAIL することを確認してから元に戻す**。緑のまま通ったらテストが空振りしている。

- [ ] **Step 7: コミット**

```bash
git add scripts/lib/jsonio.mjs scripts/validate-data.mjs scripts/tests/test_jsonio.py
git commit -m "refactor: give the two JS writers of committed JSON one atomic helper"
git push -u origin feat/member-links-rewiring
```

---

### Task 2: リンク生成器を作り、Python 版を削除する

**Files:**
- Create: `scripts/enrich-members.mjs`
- Create: `scripts/tests/test_member_links.py` — **Task 3.5 と Task 4 が後から追記する。**
  関数を末尾に足す形で書くこと（後続が丸ごと書き換えずに済むように）
- Modify: `scripts/tests/test_jsonio.py`（Task 1 が外した `enrich-members.mjs` 側の検査を戻す）
- Delete: `scripts/enrich_members.py`

**Interfaces:**
- Consumes: `writeJsonAtomic(path, text)`（Task 1）、`getMemberMinistry(member)`（`src/lib/ministry.mjs`、既存）
- Produces:
  - `export function buildMemberLinks(memberId: string, member: object, opts: { ministry: {slug,name}|null, liveSlugs: Set<string> }): Array<{label: string, url: string}>`
  - `export function enrichMembers(members: object, opts: { liveSlugs: Set<string> }): object` — 新しいオブジェクトを返す（入力を変更しない）
  - CLI: `node scripts/enrich-members.mjs [--members-path PATH]`

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_member_links.py` を新規作成する。**コミット済み `data/members.json` を正にしない** — 生成器を fixture に対して subprocess で走らせる（committed JSON だけを見るテストは、生成器が落ちてファイルが古いままでも緑になる）。

```python
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


```

- [ ] **Step 2: 走らせて失敗を確認する**

Run: `cd scripts && python -m pytest tests/test_member_links.py -v`
Expected: 全件 FAIL / ERROR（`scripts/enrich-members.mjs` がまだ無い）

- [ ] **Step 3: `scripts/enrich-members.mjs` を実装する**

```js
#!/usr/bin/env node
/**
 * Derive data/members.json's `links` deterministically.
 *
 * The discriminator is two facts and no more: whether the MAP KEY starts with
 * `m_`, and whether getMemberMinistry() resolves a ministry from the role. A
 * richer classifier was tried and rejected in Gate1 — reading `rank`/`party`
 * as evidence of elected office puts /wiki/記者, /wiki/事務局 and
 * /wiki/内閣総理大臣 on 34 members, and those resolve 200 to articles about the
 * occupation, the org-chart term and the office. src/lib/ministry.mjs says in
 * as many words not to use `rank` (気象庁長官 is ranked minister).
 *
 * The map key, not member.id, is the identifier of record: validate-data.mjs
 * --fix adds entries whose value carries no `id` at all (31 of them today).
 */
import { readFileSync, readdirSync, existsSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath, pathToFileURL } from "url";
import { getMemberMinistry } from "../src/lib/ministry.mjs";
import { writeJsonAtomic } from "./lib/jsonio.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

function google(q) {
  return "https://www.google.com/search?" + new URLSearchParams({ q }).toString();
}

function text(value) {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * The ministries whose /gov/{slug} page this build actually produces.
 *
 * /gov/[slug] sets dynamicParams = false and getMinistryRosters() keeps only
 * ministries with a member who has spoken, so resolving a ministry is NOT
 * enough to link to it — the page for a ministry whose only witness stopped
 * appearing is never built, and the link is a hard 404. data.ts is TypeScript
 * and this script cannot import it, so the rule is recomputed from the same
 * two sources it uses (members + threads), never from a copied slug list.
 */
export function computeLiveSlugs(members, threadsDir) {
  const spoken = new Set();
  if (existsSync(threadsDir)) {
    for (const name of readdirSync(threadsDir)) {
      // Same predicate as loadThreads: .json and not .progress.json.
      if (!name.endsWith(".json") || name.endsWith(".progress.json")) continue;
      let threads;
      try {
        threads = JSON.parse(readFileSync(join(threadsDir, name), "utf-8"));
      } catch {
        // threads/ is not this script's to own, and it is re-fetched on the
        // next run. Refusing here would take the morning's publish down for
        // data that repairs itself.
        console.warn(`enrich-members: skipping unreadable ${name}`);
        continue;
      }
      if (!Array.isArray(threads)) continue;
      for (const thread of threads) {
        for (const speech of thread?.speeches || []) {
          if (speech?.memberId) spoken.add(speech.memberId);
        }
      }
    }
  }
  const live = new Set();
  for (const [memberId, member] of Object.entries(members)) {
    // Speech lookup is by map key here; data.ts:352 uses member.id. They agree
    // on the committed data (0 mismatches, measured) — every entry either has
    // id === key or has no id at all, and the latter never resolves a ministry
    // anyway because its role is empty. Left asymmetric rather than "fixed" so
    // this reads the same key it iterates; the fence below catches divergence.
    if (!spoken.has(memberId)) continue;
    // getMemberMinistry(member), NOT a key-normalised copy. data.ts resolves
    // from the stored object (Object.values + member.id), so normalising here
    // would let this script link to a ministry whose page data.ts never builds
    // — a hard 404, because /gov/[slug] is dynamicParams=false. Gate2 measured
    // that normalising resolves zero extra members anyway: all 31 id-less
    // entries also have an empty role, and getMemberMinistry returns null on
    // that before it ever looks at the id. The normalisation bought nothing
    // and cost a latent 404, so the two sides resolve identically instead.
    const ministry = getMemberMinistry(member);
    if (ministry) live.add(ministry.slug);
  }
  return live;
}

export function buildMemberLinks(memberId, member, { ministry, liveSlugs }) {
  const name = text(member?.name);
  const role = text(member?.role);
  // A stub whose name is missing still gets a link rather than an empty array:
  // zero links is the state this whole change exists to end.
  const term = name || memberId;

  if (!memberId.startsWith("m_")) {
    return [
      { label: "Wikipedia", url: "https://ja.wikipedia.org/wiki/" + encodeURIComponent(term) },
      { label: "公式サイト検索", url: google(`${term} 公式サイト`) },
      { label: "X (Twitter) 検索", url: google(`${term} site:x.com OR site:twitter.com`) },
    ];
  }
  if (ministry && liveSlugs.has(ministry.slug)) {
    return [
      { label: `${ministry.name}の発言者一覧`, url: `/gov/${ministry.slug}` },
      { label: "所属・経歴を検索", url: google(`${term} ${ministry.name}`) },
    ];
  }
  return [{ label: "所属・経歴を検索", url: google(role ? `${term} ${role}` : term) }];
}

export function enrichMembers(members, { liveSlugs }) {
  const next = {};
  for (const [memberId, member] of Object.entries(members)) {
    // A malformed row is copied through untouched rather than failing the run
    // — see the note in main(). It keeps whatever links it had, including none.
    if (member === null || typeof member !== "object" || Array.isArray(member)) {
      next[memberId] = member;
      continue;
    }
    // Same resolution as computeLiveSlugs and as src/lib/data.ts. The map key
    // still decides `m_`-ness below; only the ministry lookup follows data.ts.
    const ministry = getMemberMinistry(member);
    // Field order is preserved and only `links` is replaced: everything else
    // in this file is owned by other writers.
    next[memberId] = { ...member, links: buildMemberLinks(memberId, member, { ministry, liveSlugs }) };
  }
  return next;
}

function parseArgs(argv) {
  const i = argv.indexOf("--members-path");
  if (i !== -1) {
    if (!argv[i + 1]) throw new Error("--members-path needs a value");
    return argv[i + 1];
  }
  // Anchored to this file, not the CWD: ci.yml runs pytest from scripts/.
  return join(SCRIPT_DIR, "..", "data", "members.json");
}

function main() {
  const membersPath = parseArgs(process.argv.slice(2));
  if (!existsSync(membersPath)) {
    console.error(`enrich-members: ${membersPath} does not exist`);
    process.exit(1);
  }
  const raw = readFileSync(membersPath, "utf-8");
  let members;
  try {
    members = JSON.parse(raw);
  } catch (e) {
    console.error(`enrich-members: ${membersPath} is not readable JSON: ${e.message}`);
    process.exit(1);
  }
  if (members === null || typeof members !== "object" || Array.isArray(members)) {
    console.error(`enrich-members: ${membersPath} is not a JSON object`);
    process.exit(1);
  }
  // A single member of the wrong shape is NOT fatal. This step sits under
  // `bash -e` with `git add data/members.json` a few steps later, so exiting
  // here throws away the morning's threads — and validate-data.mjs (:84-90)
  // and the metrics step (:262-268) both exist because that already happened
  // twice (#52, #74). The file as a whole being unreadable IS fatal, above:
  // that is not one bad row, it is nothing to work from. One bad row is left
  // exactly as it is, with its links untouched, and named in the log.
  const skipped = [];
  for (const [key, value] of Object.entries(members)) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      skipped.push(key);
    }
  }
  if (skipped.length) {
    console.warn(`enrich-members: leaving ${skipped.length} malformed ` +
                 `member(s) untouched: ${skipped.slice(0, 5).join(", ")}`);
  }

  const liveSlugs = computeLiveSlugs(members, join(dirname(membersPath), "threads"));
  const next = enrichMembers(members, { liveSlugs });

  // Compare the structures, not the raw text: a file that differs only in
  // trailing whitespace has not had its links change.
  if (JSON.stringify(next) === JSON.stringify(members)) {
    console.log(`Member links unchanged (${Object.keys(members).length} members)`);
    return;
  }
  writeJsonAtomic(membersPath, JSON.stringify(next, null, 2) + "\n");
  console.log(`Member links written for ${Object.keys(next).length} members ` +
              `(${liveSlugs.size} ministries with a built /gov page)`);
}

// Only when run as a program. The tests import computeLiveSlugs to check that
// this script and the site agree on which /gov pages exist, and an unguarded
// main() would run on that import — against the DEFAULT path, i.e. the repo's
// committed data/members.json, writing it from inside a test.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
```

**入力の失敗の扱い**（`verdict.md` §3 の該当節から1行だけ **意図的に発展**している。§3 は
「メンバー値が非オブジェクト」も「ファイル全体が壊れている」と同じ非ゼロ終了としていたが、
Gate2 でその行だけ覆された — 理由と実装箇所は §3 側の同日付の注記に記録済み。下の表は §3 の
現行版ではなく、その覆した後の姿):

| 入力 | 動作 |
|---|---|
| ファイルなし / JSON でない / トップレベルがオブジェクトでない | 書かない。非ゼロ終了（＝**朝の publish を止める**。作業対象そのものが無い） |
| 個別のメンバー値がオブジェクトでない | **止めない。** その1件を素通しし（`links` を触らない）、警告に名前を出す |
| 個別の `name` が空・非文字列 | 止めない。検索語を `memberId` にして1本出す |

**この線引きは意図的。** このステップは `bash -e` の下、`git add data/members.json` の数ステップ手前に
座る。1行の不正で `exit 1` すると、その朝に組み上がった threads ごと消える — `validate-data.mjs:84-90`
と metrics ステップ（`daily-batch.yml:262-268`）は、それが2度起きた（#52 / #74）から今の形をしている。
**ファイル全体が読めないことと、1行が変なことは違う。**

- [ ] **Step 4: atomic writer の fence に `enrich-members.mjs` を加える**

Task 1 の `test_the_js_half_of_the_rule_holds_too` は、当時まだ存在しなかったこのファイルを
検査対象から外してある（:120 のコメントがそう約束している）。**その約束をここで履行する。**
履行しないと、完成した PR に「`enrich-members.mjs` が atomic writer を通る」ことを見るものが
1つも無くなる — 毎朝 `git add data/members.json` の数ステップ手前で走るスクリプトが、
切り詰めた `members.json` をコミットしうる状態（#57 そのもの）。

`scripts/tests/test_jsonio.py` の `test_the_js_half_of_the_rule_holds_too` 末尾を、
両方の writer を回す形に戻す。

```python
    # Both writers of committed JSON go through it, and neither writes bare.
    # enrich-members.mjs joined this list in the task that created it.
    for name in ("validate-data.mjs", "enrich-members.mjs"):
        src = open(os.path.join(SCRIPTS_DIR, name), encoding="utf-8").read()
        assert "jsonio.mjs" in src, f"{name} must import the shared helper"
        assert "writeJsonAtomic(" in src, f"{name} must write through the helper"
        assert "writeFileSync(MEMBERS_PATH" not in src
        assert "function writeJsonAtomic" not in src, (
            f"{name} must not carry a third copy of the helper")
```

Run: `cd scripts && python -m pytest tests/test_jsonio.py -v`
Expected: PASS。そのうえで `enrich-members.mjs` の `import { writeJsonAtomic } ...` を消して
**FAIL することを確認してから戻す**。

- [ ] **Step 5: Python 版を削除する**

```bash
git rm scripts/enrich_members.py
```

2つの enricher を残さない。削除後に `git grep -n enrich_members` を走らせる（`--include` を並べた
grep は拡張子を1つ忘れるだけで穴が開き、`node_modules` も全走査する）。

Expected: ヒットは `docs/design-debate/**` の記録だけ。**コードとワークフローからのヒットはゼロ**
（どこからも呼ばれていなかったのがそもそもの原因なので、参照は無いはず）。

- [ ] **Step 5: テストを走らせて緑を確認する**

Run: `cd scripts && python -m pytest tests/test_member_links.py -v`
Expected: **全 PASS、skip 0。** このファイルは fixture しか読まないので、`data/members.json` が
まだ再生成されていなくても緑になる（実データを見るテストは Task 4 で足す）。

ここが赤いまま次へ進まないこと。ブランチなので途中の赤自体は許容されるが、**このタスクの成果物は
このタスクの中で検証できる** — 後のタスクに検証を先送りする理由が無い。

- [ ] **Step 6: 分類 fence が効くか壊して確かめる**

2つ壊す。どちらも「通っているテストが別経路で assert に達していないか」の確認。

**先に知っておくこと（Gate2 実測）**: `liveSlugs` ゲートは **今日の実データでは1件も弾いていない**。
省庁が解決する37 slug は全部 live で、`/gov` のダングリングはゼロ。1,298人中1,289人が発言済みなので、
`liveSlugs.has(...)` が false になるメンバーが現在いない。

したがって**実データ側の** `test_every_committed_gov_link_points_at_a_page_that_gets_built` は
今日 falsifiable ではない — **緑であることは「守られている」の証拠にならない**。

**だからこの fence の破壊確認は fixture でやる。** そこでは「省庁は解決するが発言が無い」状態を
作れるので、ゲートを外せば実際に 404 になるリンクが生成される。親セッションが実走で確認済み:

```
正常時   m_silent(気象庁長官・発言なし) -> 検索リンクのみ
ゲート除去後 m_silent -> /gov/jma        ← dynamicParams=false なので実際に404
```

1. `buildMemberLinks` の `if (!memberId.startsWith("m_"))` を一時的に
   `if (!memberId.startsWith("m_") || member?.rank === "minister")` に書き換え、
   `cd scripts && python -m pytest tests/test_member_links.py::test_rank_minister_does_not_make_a_bureaucrat_elected -v`
   が **FAIL することを確認してから元に戻す**。
2. `if (ministry && liveSlugs.has(ministry.slug))` から `&& liveSlugs.has(ministry.slug)` を落とし、
   ```bash
   cd scripts && python -m pytest tests/test_member_links.py -k "no_speaker or resolves_but_is_not_live" -v
   ```
   が **2本とも FAIL することを確認してから元に戻す**。これは 404 を止めている唯一の条件なので、
   ここを守るテストが空振りしていたら他の全部が緑でも意味がない。

- [ ] **Step 7: コミット**

```bash
git add scripts/enrich-members.mjs scripts/tests/test_member_links.py
git add -u scripts/enrich_members.py
git commit -m "feat: derive member links from the map key and the ministry, in node"
git push
```

---

### Task 3: 相対 URL を内部リンクとして描画する

`member-profile-view.tsx` は `links` を全部 `target="_blank"` + `open_in_new` アイコン + 素の `<a>` で描く。`/gov/{slug}` をそのまま入れると内部ページが外部リンクの見た目になり、クライアント遷移もしない。**このタスクは Task 4（データ再生成）より前でなければならない。**

**Files:**
- Modify: `src/components/member/member-profile-view.tsx:157-172`

**Interfaces:**
- Consumes: なし（`links` の形は既存の `MemberLink` 型のまま）
- Produces: なし

- [ ] **Step 1: 現状を確認する**

Run: `sed -n '157,172p' src/components/member/member-profile-view.tsx`
Expected: 全 links が `<a target="_blank">` で描かれていることを目視する

- [ ] **Step 2: 描画を分岐させる**

`{/* External links */}` ブロックを置き換える。`Link` は既に `:3` で import 済み。

```tsx
        {/* Links — relative URLs are internal pages, not external destinations */}
        {member.links && member.links.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-3">
            {member.links.map((link) => {
              const chip = "rounded-full border border-x-border px-3 py-1 text-[13px] text-x-accent transition-colors hover:bg-x-accent/10";
              return link.url.startsWith("/") ? (
                <Link key={link.label} href={link.url} className={chip}>
                  {link.label}
                </Link>
              ) : (
                <a
                  key={link.label}
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={chip}
                >
                  {link.label} <span className="material-symbols-rounded align-middle" style={{ fontSize: 14 }}>open_in_new</span>
                </a>
              );
            })}
          </div>
        )}
```

- [ ] **Step 3: lint とビルドを走らせる**

Run: `npm run lint && npm run build`
Expected: どちらも成功。`npm run lint` は `src/` を見るので、このファイルは**実際に検査される**（`scripts/` と違って）

- [ ] **Step 4: 実物を目で見る**

Run: `npm run dev` を起動し、`http://localhost:3000/m/m_f107fb47`（寺崎秀俊）を開く
Expected: 既存の links チップが今までどおり新規タブアイコン付きで出る（このメンバーは現在 links が0本なので、チップ行自体が出ないのが正常。**その場合は links を持つ `/m/morimotoshinji` を開いて確認する**）

- [ ] **Step 5: コミット**

```bash
git add src/components/member/member-profile-view.tsx
git commit -m "fix: render a member's relative links as internal pages, not new tabs"
git push
```

---

### Task 3.5: MCP が返す相対リンクを絶対 URL にする

`apps/mcp/src/lib/mcp/tools.ts:189` の `getMemberDetail` は `return { member }` で Member 全体を、
`:208` の `listMembersTool` は `members: filtered` でそのまま返す（`links` は
`apps/mcp/src/types/index.ts:20` に含まれる）。今日 links を持つ57人は**全員が絶対 URL の外部リンク**
だが、この変更後は **395人が `/gov/soumu` のようなホスト無しの相対 URL を持つ**。MCP クライアント
（別の LLM）はこれを解決できない。`copy-data.mjs` が毎朝バンドルし、`deploy-mcp` が毎朝デプロイする。

**このタスクは Task 4（データ再生成）より前でなければならない。**

**Files:**
- Modify: `apps/mcp/src/lib/mcp/tools.ts`（`getMemberDetail` と `listMembersTool` の返り値）
- Modify: `scripts/tests/test_member_links.py` — **末尾に1関数を追記**。既存の関数を消さないこと
  （Task 2 が作り、Task 4 がさらに追記する）

**Interfaces:**
- Consumes: なし
- Produces: なし（MCP の出力形は変わらない。`links[].url` の値だけが常に絶対 URL になる）

- [ ] **Step 1: 現状を確認する**

Run: `sed -n '175,212p' apps/mcp/src/lib/mcp/tools.ts`
Expected: `return { member }` と `members: filtered` が Member をそのまま返していることを目視する

- [ ] **Step 2: 絶対化するヘルパーを足す**

同ファイルの、2つのツール関数より前に置く。

```ts
const SITE_ORIGIN = "https://open-gikai.net";

/**
 * A member's `links` may hold site-relative URLs (/gov/{slug}) because the web
 * UI renders those as internal pages. An MCP client is a different LLM on a
 * different host and cannot resolve them, so they leave here absolute.
 * Absolute URLs are returned untouched.
 */
function withAbsoluteLinks<T extends { links?: { label: string; url: string }[] }>(member: T): T {
  if (!member.links?.length) return member;
  return {
    ...member,
    links: member.links.map((link) =>
      link.url.startsWith("/") ? { ...link, url: SITE_ORIGIN + link.url } : link,
    ),
  };
}
```

- [ ] **Step 3: 2つの返り値に通す**

```ts
  return { member: withAbsoluteLinks(member) };
```

```ts
    members: filtered.map(withAbsoluteLinks),
```

- [ ] **Step 4: ビルドして型が通ることを確認する**

Run: `cd apps/mcp && npm run build`
Expected: 成功。`prebuild` が repo ルートの `data/` をバンドルするので、ルートから走らせること

- [ ] **Step 5: 実際に相対 URL が絶対化されることを確かめる**

```bash
cd apps/mcp && node --input-type=module -e '
const { readFileSync } = await import("node:fs");
// 生成器がまだ走っていない段階なので、相対リンクを持つ Member を手で作って確かめる
const SITE_ORIGIN = "https://open-gikai.net";
const f = (m) => !m.links?.length ? m : {...m, links: m.links.map(l => l.url.startsWith("/") ? {...l, url: SITE_ORIGIN + l.url} : l)};
console.log(JSON.stringify(f({name:"x", links:[{label:"a",url:"/gov/mof"},{label:"b",url:"https://example.com"}]}).links));
'
```
Expected: `[{"label":"a","url":"https://open-gikai.net/gov/mof"},{"label":"b","url":"https://example.com"}]`

- [ ] **Step 6: 外れたら赤くなる fence を足す**

`eslint.config.mjs:19` は `apps/**` を無視し、`ci.yml` に MCP のジョブは無い。**この関数を消しても
呼び出しを外しても、今のままでは何も赤くならない。** Global Constraints の「配線が外れたら
テストが赤くなる」に反するので、ソース契約テストを1本足す。

`scripts/tests/test_member_links.py` に追加:

```python
def test_the_mcp_server_hands_out_absolute_member_links():
    """apps/mcp returns Member objects straight to another LLM on another host,
    which cannot resolve /gov/{slug}. eslint ignores apps/** and no CI job
    builds the MCP server, so nothing else would notice this coming undone."""
    src = (REPO_ROOT / "apps" / "mcp" / "src" / "lib" / "mcp" / "tools.ts").read_text(
        encoding="utf-8")
    assert "function withAbsoluteLinks" in src
    assert "return { member: withAbsoluteLinks(member) };" in src
    assert "filtered.map(withAbsoluteLinks)" in src
```

Run: `cd scripts && python -m pytest tests/test_member_links.py::test_the_mcp_server_hands_out_absolute_member_links -v`
Expected: PASS。そのうえで `withAbsoluteLinks(member)` の呼び出しを一時的に `member` へ戻し、
**FAIL することを確認してから戻す**。

- [ ] **Step 7: コミットして push**

```bash
git add apps/mcp/src/lib/mcp/tools.ts scripts/tests/test_member_links.py
git commit -m "fix: hand MCP clients absolute member links, not site-relative ones"
git push
```

---

### Task 4: `data/members.json` を全件再生成してコミットする

**Files:**
- Modify: `data/members.json`
- Modify: `scripts/tests/test_member_links.py` — **末尾に Group B を追記**し、冒頭に
  `COMMITTED_MEMBERS` 定数を足す。**既存の関数（Task 2 の Group A、Task 3.5 の MCP テスト）を
  消さないこと**

**Interfaces:**
- Consumes: `node scripts/enrich-members.mjs`（Task 2）
- Produces: Group B の契約テストが緑になる状態

- [ ] **Step 1: 再生成前の状態を記録する**

```bash
python3 -c "
import json;d=json.load(open('data/members.json',encoding='utf-8'))
n=sum(1 for v in d.values() if v.get('links'))
print(f'members={len(d)} with_links={n} without={len(d)-n}')"
```
Expected: `members=1298 with_links=57 without=1241` 付近（実測値を控える）

- [ ] **Step 2: 生成器を走らせる**

Run: `node scripts/enrich-members.mjs`
Expected: `Member links written for 1298 members (37 ministries with a built /gov page)` 付近

- [ ] **Step 3: 差分を規則で検算する（全行の目視に頼らない）**

**先に知っておく数字（Gate2 実測）**: `data/members.json` は **345,771 → 831,300 バイト（2.4倍）**。
毎朝コミットされ、`copy-data.mjs` で MCP バンドルにも入る。ブロッカーではないが、初回の diff を
見て驚かないための数字。

```bash
python3 -c "
import json,collections
from urllib.parse import urlparse
d=json.load(open('data/members.json',encoding='utf-8'))
c=collections.Counter()
for k,v in d.items():
    ls=v.get('links') or []
    c['no_links'] += 0 if ls else 1
    kind = 'non_m' if not k.startswith('m_') else ('gov' if any(l['url'].startswith('/gov/') for l in ls) else 'search_only')
    c[kind]+=1
    if k.startswith('m_'):
        for l in ls:
            h=urlparse(l['url']).hostname or ''
            if h.endswith('wikipedia.org'): c['BAD_wikipedia_on_m']+=1
print(dict(c))"
```
Expected: `no_links=0`、`BAD_wikipedia_on_m` がキーごと出ない（＝0）、`non_m` 652 / `gov` 395 / `search_only` 251 付近

- [ ] **Step 4: 公開契約テスト（Group B）を追加する**

`scripts/tests/test_member_links.py` の末尾に追加する。**このタスクで初めて足す** — Task 2 で足すと、
再生成前の `data/members.json` に対して赤いまま main に入り、`ci.yml` の pytest が落ちる。

oracle は **`computeLiveSlugs` を呼ばない**。リンクを作ったのと同じ関数で答え合わせをすると同語反復に
なり、その関数がズレる方向には原理的に空振りする。`src/lib/data.ts` の規則をテスト側で独立に組み直す。

```python
# --- Group B: the committed data ---------------------------------------------

def _roster_slugs_the_site_will_build():
    """Re-derive src/lib/data.ts's getMinistryRosters() rule independently.

    Deliberately does NOT call computeLiveSlugs: that is the function which
    produced the links under test, so using it as the oracle would be a
    tautology that cannot fail in the direction that matters. data.ts is
    TypeScript and this repo has no tsx/ts-node, so the rule is re-expressed
    here from data.ts:348-356 — resolve the ministry from the STORED member
    object (no key normalisation, exactly as Object.values gives it) and keep
    only ministries with a member who has at least one speech.
    """
    members = json.loads(COMMITTED_MEMBERS.read_text(encoding="utf-8"))
    threads_dir = REPO_ROOT / "data" / "threads"
    spoken = set()
    for path in threads_dir.glob("*.json"):
        if path.name.endswith(".progress.json"):
            continue
        try:
            threads = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            # Name the file, the way every other reader in this repo does. A
            # bare traceback here sends the investigator after the test.
            raise AssertionError(f"unreadable thread file {path.name}: {e}") from e
        for thread in threads:
            for speech in thread.get("speeches") or []:
                if speech.get("memberId"):
                    spoken.add(speech["memberId"])

    probe = subprocess.run(
        ["node", "--input-type=module", "-e", """
import { getMemberMinistry } from "./src/lib/ministry.mjs";
import { readFileSync } from "node:fs";
const members = JSON.parse(readFileSync("data/members.json", "utf-8"));
const out = {};
for (const [key, m] of Object.entries(members)) {
  const ministry = getMemberMinistry(m);      // stored object, as data.ts does
  // data.ts looks the speech count up by member.id, NOT by the map key. They
  // agree today (0 mismatches), but keying this on the map key would make the
  // oracle quietly more correct than the thing it checks.
  if (ministry) out[key] = { slug: ministry.slug, id: m.id ?? null };
}
console.log(JSON.stringify(out));
"""], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert probe.returncode == 0, probe.stderr
    resolved = json.loads(probe.stdout.strip().splitlines()[-1])
    return {v["slug"] for v in resolved.values() if v["id"] in spoken}


def test_every_committed_member_has_at_least_one_link():
    members = json.loads(COMMITTED_MEMBERS.read_text(encoding="utf-8"))
    missing = [k for k, v in members.items()
               if not isinstance(v.get("links"), list) or not v["links"]]
    assert missing == [], f"{len(missing)} members carry no links, e.g. {missing[:5]}"


def test_no_m_prefixed_member_links_to_any_wikipedia_host():
    """Host suffix, not the exact ja.wikipedia.org string: ja.m.wikipedia.org
    and en.wikipedia.org are the same mistake with a different spelling."""
    members = json.loads(COMMITTED_MEMBERS.read_text(encoding="utf-8"))
    bad = []
    for key, v in members.items():
        if not key.startswith("m_"):
            continue
        for link in v.get("links") or []:
            if _is_wikipedia(link["url"]):
                bad.append((key, link["url"]))
    assert bad == [], f"wikipedia links on m_ members: {bad[:5]}"


def test_every_committed_gov_link_points_at_a_page_that_gets_built():
    """/gov/[slug] is dynamicParams=false, so a slug outside the built set is a
    hard 404 for whoever clicks it — and nothing else in CI would notice.

    Read this one honestly: on today's data every resolved ministry is live
    (37 of 37), so this passes whether or not the gate that produces the links
    works. It is a tripwire for the day that stops being true, not evidence
    that anything is being stopped now. The test that can actually fail today
    is the fixture one — test_a_ministry_that_resolves_but_is_not_live...
    """
    members = json.loads(COMMITTED_MEMBERS.read_text(encoding="utf-8"))
    live = _roster_slugs_the_site_will_build()
    dangling = []
    for key, v in members.items():
        for link in v.get("links") or []:
            if link["url"].startswith("/gov/") and link["url"][len("/gov/"):] not in live:
                dangling.append((key, link["url"]))
    assert dangling == [], f"/gov links with no built page: {dangling[:5]}"


def test_the_generator_and_the_site_agree_on_which_gov_pages_exist():
    """The link side and the page side resolve the ministry from the same
    object. If they ever diverge — someone re-introduces key normalisation on
    one side — this fails before a reader finds the 404."""
    probe = subprocess.run(
        ["node", "--input-type=module", "-e", f"""
import {{ computeLiveSlugs }} from "{(REPO_ROOT / 'scripts' / 'enrich-members.mjs').as_uri()}";
import {{ readFileSync }} from "node:fs";
const members = JSON.parse(readFileSync("data/members.json", "utf-8"));
console.log(JSON.stringify([...computeLiveSlugs(members, "data/threads")].sort()));
"""], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert probe.returncode == 0, probe.stderr
    generator = set(json.loads(probe.stdout.strip().splitlines()[-1]))
    assert generator == _roster_slugs_the_site_will_build()
```

`COMMITTED_MEMBERS` をファイル冒頭の定数に足す:

```python
COMMITTED_MEMBERS = REPO_ROOT / "data" / "members.json"
```

- [ ] **Step 5: 契約テストを走らせて緑を確認する**

Run: `cd scripts && python -m pytest tests/test_member_links.py -v`
Expected: Group A・Group B ともに全 PASS、skip 0

- [ ] **Step 6: 独立 oracle が本当に独立か壊して確かめる**

`scripts/enrich-members.mjs` の `computeLiveSlugs` 内の `getMemberMinistry(member)` を一時的に
`getMemberMinistry({ ...member, id: memberId })` に戻し、
`cd scripts && python -m pytest tests/test_member_links.py::test_the_generator_and_the_site_agree_on_which_gov_pages_exist -v`
が **FAIL することを確認してから元に戻す**。緑のままなら oracle がまだ同語反復している。
（現行データでは差が出ない可能性があるので、差が出ない場合は `data/members.json` の一時コピーに
`role` を持ち `id` を持たないエントリを1件足して確認する。）

- [ ] **Step 7: 冪等性を本物のファイルで確認する**

```bash
stat -c '%i %Y' data/members.json
node scripts/enrich-members.mjs
stat -c '%i %Y' data/members.json
```
Expected: 2回目は `Member links unchanged (1298 members)` と出て、**inode と mtime が変わらない**

- [ ] **Step 8: ビルドが通ることを確認する**

Run: `npm run validate && npm run build`
Expected: どちらも成功。**`npm run build` は `validate-data.mjs --fix` を走らせて
`data/members.json` を書き換えうる**ので、この後に `git status` を見て、意図しない差分が
乗っていないことを確認する。

- [ ] **Step 9: コミット**

テストとデータを**同じコミットに入れる**（分けると、どちらか片方だけの中間状態で main が赤くなる）。

```bash
git add data/members.json scripts/tests/test_member_links.py
git commit -m "data: give every member a link that lands"
git push
```

---

### Task 5: 毎朝の経路と本番ビルドに配線する

**Files:**
- Modify: `.github/workflows/daily-batch.yml:245-249`
- Modify: `.github/workflows/ci.yml`（python-tests に `setup-node`）
- Modify: `package.json`（`build`）
- Modify: `scripts/tests/test_systemic_failure.py`（配線の契約テストを**末尾に追記**）
- Modify: `scripts/check_committable_json.py`（消えるステップ名を参照している段落を書き換え）

**Interfaces:**
- Consumes: `node scripts/enrich-members.mjs`（Task 2）
- Produces: なし

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_systemic_failure.py` の末尾に追加する。**存在だけを見ない** — 順序を固定しないと、Gate1 で棄却された誤順（enrich が `--fix` より前）が緑で通る。

```python
def test_the_workflow_enriches_member_links_after_the_validator_adds_members():
    """validate-data.mjs --fix adds members that appear only in threads, with
    no links and no `id`. Enriching before it therefore commits members with
    zero links on the very first morning — which is the state this whole
    change exists to end. Order, not presence, is the contract.

    No `|| true` either: enrich-news.py carries one because a missing news
    article must not stop the publish, and copying it here would restore the
    silent drift (nobody ran the enricher for months and nothing noticed).
    """
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "daily-batch.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["fetch-and-summarize"]["steps"]
    runs = [(s.get("name", ""), s.get("run", "") or "", s) for s in steps]

    def index_of(needle):
        for i, (_, run, _) in enumerate(runs):
            if needle in run:
                return i
        raise AssertionError(f"no step runs {needle!r}")

    validate_at = index_of("scripts/validate-data.mjs --fix")
    enrich_at = index_of("scripts/enrich-members.mjs")
    feeds_at = index_of("scripts/generate-feeds.js")
    assert validate_at < enrich_at < feeds_at, (
        "enrich must run after the validator adds members and before the feeds "
        f"are generated (validate={validate_at}, enrich={enrich_at}, feeds={feeds_at})")

    _, enrich_run, enrich_step = runs[enrich_at]
    assert "|| true" not in enrich_run
    assert enrich_step.get("continue-on-error") is not True
    assert "if" not in enrich_step, "the enrich step must not be conditional"


def test_the_production_build_enriches_too():
    """package.json's `build` is the other definition of this pipeline, and it
    is the one Vercel runs. Without enrich there, the --fix in a production
    build can add a member and render the deploy with no link for them."""
    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    build = pkg["scripts"]["build"]
    assert "scripts/enrich-members.mjs" in build
    assert build.index("validate-data.mjs") < build.index("enrich-members.mjs")


def test_ci_gives_the_python_tests_a_node_to_run():
    """test_member_links.py subprocesses the node CLI. Without setup-node the
    job leans on whatever node the runner image happens to ship — the same
    unpinned-dependency shape as #80."""
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["python-tests"]["steps"]
    node_steps = [s for s in steps if str(s.get("uses", "")).startswith("actions/setup-node@")]
    assert node_steps, f"python-tests has no setup-node: {[s.get('uses') for s in steps]}"
    # The version too, not just the action. Naming the action without pinning
    # the runtime is the #80 shape exactly: it resolves to whatever the runner
    # ships that week, and nobody wrote that number down.
    assert node_steps[0].get("with", {}).get("node-version") is not None, (
        "setup-node must pin node-version")
```

- [ ] **Step 2: 走らせて失敗を確認する**

Run: `cd scripts && python -m pytest tests/test_systemic_failure.py -k "enrich or production_build or node_to_run" -v`
Expected: 3本とも FAIL（`no step runs 'scripts/enrich-members.mjs'` 等）

- [ ] **Step 3: `daily-batch.yml` のステップを分割する**

`:245-249` を置き換える。

```yaml
      - name: Validate data
        run: node scripts/validate-data.mjs --fix

      # After the validator, never before it: --fix adds members that appear
      # only in threads, with no links and no `id`. Enriching first commits
      # those with zero links on the same morning. No `|| true` — the enricher
      # going quiet for months, with nobody noticing, is what this replaces.
      - name: Enrich member links
        id: enrich_members
        run: node scripts/enrich-members.mjs

      - name: Generate feeds and sitemaps
        run: |
          node scripts/generate-feeds.js
          node scripts/generate-sitemap.mjs
```

- [ ] **Step 4: `package.json` の `build` に挟む**

```json
    "build": "node scripts/validate-data.mjs --fix && node scripts/enrich-members.mjs && node scripts/generate-feeds.js && node scripts/generate-sitemap.mjs && next build",
```

- [ ] **Step 5: `ci.yml` の python-tests に node を入れる**

`- uses: actions/setup-python@v7` ブロックの直後に追加する。バージョンは repo の既存表記に合わせる（`actions/setup-node@v7`）。

```yaml
      # test_member_links.py subprocesses scripts/enrich-members.mjs, so this
      # job needs a node — and a pinned one. Leaning on the runner image's
      # default is the unpinned-dependency shape #80 was about.
      - uses: actions/setup-node@v7
        with:
          node-version: 22
```

- [ ] **Step 6: テストと受け入れを走らせて緑を確認する**

Run:
```bash
cd scripts && python -m pytest -q
cd .. && npm run lint && npm run validate && npm run build
```
Expected: pytest 全 PASS で **skip 0**、npm 3本とも成功

- [ ] **Step 7: 順序の fence が効くか壊して確かめる**

`daily-batch.yml` の `Enrich member links` ステップを `Validate data` の**前**へ一時的に移動し、
`cd scripts && python -m pytest tests/test_systemic_failure.py -k enrich -v` が **FAIL することを確認してから戻す**。

- [ ] **Step 8: `check_committable_json.py` の古くなった記述を直す**

`scripts/check_committable_json.py:74` は **`"Validate and generate"` というステップ名を名指しして**
「そこが members.json を3回 bare parse するので、このチェックには到達しない」と論証している。
Step 3 でその名前は消えた。論証自体は生き残る（3つの bare parse は分割後も
`continue-on-error` なしで残る）ので、**名前だけを実態に合わせる**。

**名前だけ直すと、段落の残りが今度こそ嘘になる。** 現在の論証は「`data/members.json` は
syntax error ではここに到達しない。**このチェックがまだ拾えるのは wrong-shape のケースで、
それらの reader は許容する**」。ところが新しい enrich ステップはトップレベルが dict でない場合に
`exit 1` する（実測: `echo '[]' > x.json && node scripts/enrich-members.mjs --members-path x.json`
→ `is not a JSON object` / EXIT=1）。つまり **wrong-shape も到達しなくなる**。

段落全体を書き換える。書くべき内容:

- ステップ名を `"Validate data"` / `"Enrich member links"` / `"Generate feeds and sitemaps"` に更新
- syntax error は従来どおり到達不能（3つの bare parse が先に死ぬ）
- **wrong-shape も到達不能になった** — `enrich-members.mjs` がトップレベル非 dict で exit 1 するため
- したがって `CHECKS` の `("data/members.json", dict)` は**現時点で何も拾わない**。残すのは
  `HEAD` 側の破損を見るためであって、**保護の主張として読まないこと**（この repo が元の段落で
  わざわざ注意している読み方そのもの）

Run: `grep -n "Validate and generate" scripts/check_committable_json.py`
Expected: 書き換え後はヒットゼロ。**追記ではなく置換**（追記は新しい嘘を作る）。

- [ ] **Step 9: 直前に daily-batch が走っていないか確かめ、走っていたら取り込む**

Task 4 のコミットからここまでの間に朝の `daily-batch` が走ると、まだ配線が無いので
`validate-data.mjs --fix` が links を持たない stub を追加してコミットしうる
（`scripts/validate-data.mjs:167-196`）。その状態で配線だけをコミットすると、
`data/members.json` は赤いまま残る。

```bash
git pull --rebase --autostash origin main
cd scripts && python -m pytest tests/test_member_links.py -v ; cd ..
```
`test_every_committed_member_has_at_least_one_link` が赤い場合は、**このコミットに取り込む**:

```bash
node scripts/enrich-members.mjs
cd scripts && python -m pytest tests/test_member_links.py -v ; cd ..
```
Expected: 緑に戻る。`data/members.json` を次の Step の `git add` に含める。

- [ ] **Step 10: コミット**

```bash
git add .github/workflows/daily-batch.yml .github/workflows/ci.yml package.json         scripts/tests/test_systemic_failure.py scripts/check_committable_json.py
git add data/members.json   # Step 9 で再生成した場合のみ
git commit -m "ci: run the member-link enricher every morning, after the validator"
git push
```

---

### Task 6: CLAUDE.md の記述を実態に合わせる

`CLAUDE.md` は「`scripts/validate-data.mjs` が temp→fsync→rename→fsync-dir の**自分の写しを持つ**」と書いている。Task 1 でそれは共有 helper に移った。挙動を変えたら説明文は欠陥箇所。

**Files:**
- Modify: `CLAUDE.md`（`jsonio.py` の段落内、`validate-data.mjs` を名指ししている箇所）

`scripts/tests/test_jsonio.py` は**読むだけ**（Task 1 と Task 2 が既に最終形にしてある）。

**Interfaces:** なし

- [ ] **Step 1: 該当箇所を特定する**

Run: `grep -n "validate-data.mjs" CLAUDE.md`
Expected: atomic writer の写しについて述べた段落が見つかる

- [ ] **Step 2: 記述を更新する**

「`validate-data.mjs` が自分の写しを持つ」を「JS 側は `scripts/lib/jsonio.mjs` が正本で、`validate-data.mjs` と `enrich-members.mjs` が import する」へ書き換える。**段落への追記ではなく置き換え**（追記は新しい嘘を作る）。二重化が残る理由（node が Python を import できない）と、`test_jsonio.py` がその番人であることは維持する。`test_the_js_half_of_the_rule_holds_too` が**呼び出し行ではなく helper 本体の手順**を見るようになったことも1文で書く。

- [ ] **Step 3: 記述と実装が食い違っていないか確認する**

Run: `grep -rn "jsonio" CLAUDE.md scripts/lib/jsonio.mjs scripts/tests/test_jsonio.py | head`
Expected: CLAUDE.md の記述がファイルの実態と一致している

- [ ] **Step 4: コミット**

```bash
git add CLAUDE.md
git commit -m "docs: point the JS atomic-writer rule at the shared helper it now lives in"
git push
```

---

### Task 7: PR を開き、緑を実際に見てから main へ入れる

出荷単位をブランチにした以上、**PR が緑になるまでが plan の中**。ここを省くと、「各コミットで緑」を
捨てた代わりに何も観測していないことになる。

**Files:** なし（git 操作のみ）

- [ ] **Step 1: 完成差分に対して受け入れコマンドを実測する**

```bash
cd scripts && python -m pytest -q ; cd ..
npm run lint && npm run validate && npm run build
```
Expected: pytest 全 PASS で **skip 0**。skip が出たらそれは合格ではなく**計測していない**意味
（PyYAML 未導入だと workflow 契約テスト群が `importorskip` で静かに消える）。
`pip install -r requirements-dev.txt`、Python 3.12。npm 3本とも成功。

- [ ] **Step 2: PR を開く**

```bash
gh pr create --base main --title "feat: give every member a link that lands" --body "$(cat <<'EOF'
GSC が示すこのサイトの検索流入はほぼ全部が官僚の人名検索で、着地ページ上位30件のうち29件が
`/m/{memberId}` だった。そのページには何も無い — 1,298人全員の bio が空で、官僚の98%はリンクを
1本も持たない。

原因は enricher が壊れていたことではない。`scripts/enrich_members.py` は links を書く（bio は
触らない）が、**どのワークフローからも呼ばれていなかった**。誰かが最後に手で走らせて以降に
追加された1,200人以上は一度も通っていない。

判別は2軸だけにした — map key が `m_` で始まるか、`getMemberMinistry` が省庁を返すか。
`rank` や `party` を選出職の証拠に読む分類器は Gate1 で試して棄却した: `/wiki/記者`・
`/wiki/事務局`・`/wiki/内閣総理大臣` を34人に付け、それらは404ではなく **200 で、職業や役職の
解説記事が開く**。

- Gate1: design debate（Claude 案 vs codex 案 → grok 裁定）— `docs/design-debate/member-links-rewiring/`
- Gate2: 敵対レビュー3周。同じ根の critical が2回出たため、出荷単位を main 直接コミットから
  このブランチへ替えた — `docs/superpowers/plans/2026-09-04-member-links-rewiring.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: CI が緑になるのを実際に見る**

```bash
gh pr checks --watch
```
Expected: lint / build / python-tests / e2e が全部緑。**赤があればここで直す。**
「ローカルで緑だった」は観測ではない。

- [ ] **Step 4: Gate3 を回す**（下の「完了後」節。critical 0 になるまでマージしない）

- [ ] **Step 5: マージする**

朝の `daily-batch`（23:11 UTC 開始・約1時間）と重ならない時間に入れる。走行中の main への push は
その run のデータコミットを殺しうる（#82、2026-08-23 に実際に236スレッドが失われた）。

```bash
gh run list --limit 3 --json status,name    # in_progress が無いことを確認
gh pr merge --squash --delete-branch
```

---

## 完了後（この plan の外）

- **Gate3 必須**（(A) 級 diff）:
  ```bash
  timeout 3600 claude -p "/goal /code-gate を実行し critical 0 を達成する。3回で打ち切り" \
    --permission-mode acceptEdits
  ```
  `run_in_background: true` で起動する（前面は 600s で殺される）。
- **配線を実走させるまで完了にしない。** CI 定義を変えたので、次の朝の `daily-batch` を実際に見て、
  `Enrich member links` が緑になることを確認する。**出力は2通りとも正常**: 前夜からメンバーが
  増えていなければ `Member links unchanged`（＝本番で冪等であることの確認）、`--fix` が新しい
  発言者を足した朝は `Member links written for N members`。後者を異常と読まないこと。
- 残存リスク（`verdict.md` §4）のうち、この plan が閉じないもの: 非 `m_` の Wikipedia は5%外れる /
  `m_` 空間の政治家重複（`石破 茂` が `m_b0533ae2` と `ishibashigeru` に両方いる）/ 検索1本だけの
  251人+stub 31人はページが薄いまま / `getMemberMinistry` の前方一致は省庁再編で静かに減る。
  いずれも別 issue。
