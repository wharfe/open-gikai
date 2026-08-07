# summary-layer 障害シグナルの残穴を塞ぐ 実装プラン（#59 / #61）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** summary レイヤーが「答えを返さなかった日」と「答えは返ったが1本も公開できなかった日」の両方を、新規バッチ経路と resume 経路の**どちらでも当日中に**赤くする。

**Architecture:** exit code は増やさず（3=systemic / 4=suspect のまま）、赤くする条件を
**2つの独立したトリガの OR** にする。トリガ1（既存）は「API に聞いた会議が全滅」、
トリガ2（新規）は「summary リクエストを投げたのに日付が1本も公開できなかった」。
トリガ2 は会議単位の tally ではなく**日付単位の boolean** として持つ（assembly が
all-or-nothing なので会議に配分できない）。原因は `assemble_from_manifest()` が返す
構造化 diagnostic が別に運ぶ。resume 経路は複数日付を扱うので exit code ではなく
`$GITHUB_OUTPUT` の日付リストで verdict を運び、workflow の最終ステップが両経路を union する。

**Tech Stack:** Python 3.8+, pytest, GitHub Actions (YAML), `anthropic` SDK。
テストは `scripts/tests/conftest.py` の `fake_client` フィクスチャを使う（実 API を叩かない）。

**Spec:** `docs/superpowers/specs/2026-08-07-summary-failure-signal-gaps-design.md`（rev6）

## Global Constraints

- **summary レイヤーの不変条件を壊さない**（CLAUDE.md「Summary Layer Invariants」）。
  この変更は**リクエストを一切変更しない** — `build_summary_request` /
  `build_grouping_request` / `build_outcome_request` とその params には触らない。
  sampling params を足さない。`thinking: {"type": "disabled"}` を外さない。
- **`SCHEMA_VERSION` を変更しない。** sidecar の形も `compute_input_hash` に渡す params 集合も
  変えない。したがって `data/pending-batches/` が空である必要はない。
- **`usable_result()` の意味を変えない**（「parse できて speeches がある」のまま）。
- **exit code を追加しない。** `EXIT_SYSTEMIC_FAILURE = 3` / `EXIT_SUSPECT_FAILURE = 4` のみ。
- **Python 側と `.github/workflows/daily-batch.yml` は同じコミットで動かす**
  （CLAUDE.md「Both halves have to move together」）。
- コード内コメントは英語。ドキュメントとコミットメッセージ本文は英語（既存コミットに合わせる）。
- 受け入れ基準（機械判定）: `python -m pytest scripts/tests -q` が全通過。
  最終タスク後に `npm run lint && npm run validate` も通ること。
- **テストを緩めて通さない。** 既存テストが落ちたら、それは仕様変更の合図なので
  テストの意図を読んでから直す（`test_the_workflow_tolerates_exactly_these_exit_codes` は
  意図的に Python と YAML を縛っている）。

---

## File Structure

| ファイル | 役割 | 変更 |
|---|---|---|
| `scripts/summarize.py` | パイプライン本体・verdict 判定・CLI | 修正（3タスクすべて） |
| `scripts/tests/test_systemic_failure.py` | 障害シグナルの回帰テスト（verdict 述語・新規バッチ経路・workflow） | 修正（3タスクすべて） |
| `scripts/tests/test_resume.py` | resume / sidecar / assembly の回帰テスト（723行） | **修正（Task 1・Task 2）** |
| `.github/workflows/daily-batch.yml` | 日次実行・publish・ジョブの赤化 | 修正（Task 3） |
| `CLAUDE.md` | exit code 契約の記述 | 修正（Task 3） |

### `test_resume.py` を無視できない理由（Gate2 で最初に出た指摘）

このプランの初版は `test_systemic_failure.py` しか見ておらず、**`test_resume.py` の 23 箇所の
呼び出し**を丸ごと落としていた。戻り値の形を変える3関数はすべてここから呼ばれている:

| 関数 | `test_resume.py` の行 | 壊れ方 |
|---|---|---|
| `assemble_from_manifest` | 86, 97, 105 | `ValueError: too many values to unpack`（Task 1 Step 6 の直後） |
| `run_batch_phase` | 127 | dict を 4 名前に展開（Task 1 Step 10 の直後） |
| `collect_pending_batches` | 161, 176, 202, 230, 252, 275, 330, 450, 478, 508, 549, 612, 637 | `assert hard is False` が dict に対して常に真（Task 2 Step 3 の直後） |

**新しいテストの置き場所も、これに合わせる。** `test_resume.py:42-78` には既に
`_sidecar_with_one_thread(input_hash)` / `_meeting()` / `_correct_hash()` があり、
sidecar に必須の `retry_count` と `attempts` も、正しい `input_hash` の作り方も揃っている。
**assembly と resume に関する新規テストは `test_resume.py` に書く**（自前で sidecar を
組み直すと `KeyError: 'retry_count'` になるか、`missing_result` を狙ったつもりが
`hash_mismatch` で落ちる — hash 検証は結果の有無より**前**にあるため。`summarize.py:646` と
`:651` を見よ）。

置き場所の切り分け:

- `test_resume.py` — `assemble_from_manifest` の diagnostic、`collect_pending_batches` の verdict
- `test_systemic_failure.py` — verdict 述語、`run_batch_phase`、`run_pipeline`、`main`、workflow

新規ファイルは作らない。verdict 判定は `summarize.py` の既存の述語群
（`systemic_failure` / `suspect_failure` / `_everything_asked_for_failed`、行 175-252）の
すぐ隣に置く — 読み手が「赤くする条件」を1箇所で読めることが、この変更が守る価値そのもの。

---

## Task 1: トリガ2（publication blocked）と構造化 diagnostic

**Files:**
- Modify: `scripts/summarize.py` — verdict 述語群（175-252 付近）、
  `assemble_from_manifest`（606-675）、`run_batch_phase`（1205-1358）、
  `run_pipeline`（1391-1656）
- Test: `scripts/tests/test_systemic_failure.py`（verdict 述語・`run_batch_phase`・`run_pipeline`）
- Test: `scripts/tests/test_resume.py`（assembly の diagnostic、および **86/97/105 行の
  3-tuple 化と 127 行の dict 化**。これをやらずに Step 15 のコミットに進まないこと）

**Interfaces:**
- Produces:
  - `rejection_verdict(api_stats: dict, published_threads: int) -> int`
  - `publication_blocked_verdict(summary_attempted: int, published_threads: int) -> int`
  - `worst_verdict(*verdicts: int) -> int`
  - `assemble_from_manifest(...) -> tuple[list, bool, Optional[dict]]`
  - `run_batch_phase(...) -> dict`（キーは Step 7 参照）
- Consumes: 既存の `systemic_failure` / `suspect_failure` / `EXIT_SYSTEMIC_FAILURE` /
  `EXIT_SUSPECT_FAILURE` / `_annotate`

---

- [ ] **Step 1: verdict 関数の失敗するテストを書く**

`scripts/tests/test_systemic_failure.py` の `test_the_two_verdicts_never_overlap`（119行付近）の
直後に追加する。

```python
@pytest.mark.parametrize("summary_attempted,published,expected", [
    # Nothing was asked of the summary phase — assembly failure is not our story.
    (0, 0, 0),
    (0, 5, 0),
    # A date that published nothing and had requests in flight: systemic.
    (1, 0, summarize.EXIT_SYSTEMIC_FAILURE),
    (4, 0, summarize.EXIT_SYSTEMIC_FAILURE),
    # Two or more meetings blocked is evidence about the layer, whatever is
    # already on disk — the same rule trigger 1 uses.
    (2, 9, summarize.EXIT_SYSTEMIC_FAILURE),
    # Exactly one meeting blocked on an already-published date is weak evidence.
    (1, 9, summarize.EXIT_SUSPECT_FAILURE),
])
def test_publication_blocked_verdict_boundaries(summary_attempted, published, expected):
    assert summarize.publication_blocked_verdict(summary_attempted, published) == expected


def test_worst_verdict_ranks_systemic_above_suspect():
    S = summarize.EXIT_SYSTEMIC_FAILURE
    P = summarize.EXIT_SUSPECT_FAILURE
    assert summarize.worst_verdict(0, 0) == 0
    assert summarize.worst_verdict(0, P) == P
    assert summarize.worst_verdict(P, S) == S
    assert summarize.worst_verdict(S, 0) == S


def test_rejection_verdict_reuses_the_existing_predicates():
    """The new wrapper must not invent a third opinion about trigger 1."""
    stats = {"attempted": 2, "failed": 2}
    assert summarize.rejection_verdict(stats, 0) == summarize.EXIT_SYSTEMIC_FAILURE
    assert summarize.rejection_verdict({"attempted": 1, "failed": 1}, 9) == \
        summarize.EXIT_SUSPECT_FAILURE
    assert summarize.rejection_verdict({"attempted": 2, "failed": 1}, 0) == 0
```

- [ ] **Step 2: 失敗することを確認する**

```bash
python -m pytest scripts/tests/test_systemic_failure.py -k "publication_blocked_verdict or worst_verdict or rejection_verdict" -v
```

Expected: FAIL — `AttributeError: module 'summarize' has no attribute 'publication_blocked_verdict'`

- [ ] **Step 3: verdict 関数を実装する**

`scripts/summarize.py` の `_everything_asked_for_failed`（250-252行）の直後に追加する。

```python
def rejection_verdict(api_stats: dict, published_threads: int) -> int:
    """Trigger 1 as an exit code: the API answered nothing usable.

    A thin wrapper over the two existing predicates so callers can combine this
    with trigger 2 below without re-deriving the ranking. Deliberately does not
    re-implement the boundaries: they carry hard-won carve-outs (see
    ``systemic_failure``) and a second copy would drift from them.
    """
    if systemic_failure(api_stats, published_threads):
        return EXIT_SYSTEMIC_FAILURE
    if suspect_failure(api_stats, published_threads):
        return EXIT_SUSPECT_FAILURE
    return 0


def publication_blocked_verdict(summary_attempted: int, published_threads: int) -> int:
    """Trigger 2: summary requests went out, and the date published nothing.

    Assembly is all-or-nothing — one bad speechOrder discards the whole date —
    so this is a fact about the DATE, not about the meetings it swept up. That
    is why it takes a count and not an ``api_stats``: charging the meetings
    would report meetings that were never even examined as having failed, and
    the cause diagnosis would be fiction. The cause travels separately, as the
    diagnostic ``assemble_from_manifest`` returns.

    ``summary_attempted`` is NOT ``api_stats["attempted"]``. That one counts
    every meeting that reached the API at all, including one whose grouping
    legitimately produced zero threads and therefore sent no summary request.
    Using it here lets a real outage hide behind a quiet meeting: with A quiet
    and B blocked, "everything asked for failed" is false and nothing fires.

    Same evidence rule as trigger 1: one meeting blocked on an already-published
    date is weak evidence and is kept as ``suspect`` for the workflow to
    threshold; anything else is systemic.
    """
    if summary_attempted <= 0:
        return 0
    if summary_attempted == 1 and published_threads > 0:
        return EXIT_SUSPECT_FAILURE
    return EXIT_SYSTEMIC_FAILURE


def worst_verdict(*verdicts: int) -> int:
    """The loudest verdict among several. systemic > suspect > clean.

    The two triggers are NOT exclusive: a fully rejected batch fires trigger 1
    (nothing usable came back) AND trigger 2 (assembly then failed on the very
    same missing results). Both are true and both get reported; this only picks
    the exit code.
    """
    if EXIT_SYSTEMIC_FAILURE in verdicts:
        return EXIT_SYSTEMIC_FAILURE
    if EXIT_SUSPECT_FAILURE in verdicts:
        return EXIT_SUSPECT_FAILURE
    return 0
```

- [ ] **Step 4: テストが通ることを確認する**

```bash
python -m pytest scripts/tests/test_systemic_failure.py -k "publication_blocked_verdict or worst_verdict or rejection_verdict" -v
```

Expected: PASS（6 パラメータ + 2 = 全通過）

- [ ] **Step 5: `assemble_from_manifest` の diagnostic のテストを書く（`test_resume.py`）**

**`scripts/tests/test_resume.py`** の `test_assemble_fails_on_missing_result`（105行付近）の
直後に追加する。**既存の `_sidecar_with_one_thread` / `_meeting` / `_correct_hash` をそのまま
使う** — 自前で sidecar を組むと `retry_count` / `attempts` 欠落や、`missing_result` を
狙ったのに `hash_mismatch` で落ちる（hash 検証が先、`summarize.py:646` vs `:651`）。

```python
def test_assembly_reports_a_missing_result_as_a_thread_scoped_diagnostic():
    sidecar = _sidecar_with_one_thread(_correct_hash())   # correct hash: we want
                                                          # to reach the result check
    threads, ok, diagnostic = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results={}, members={}, thread_counter=0,
    )
    assert (threads, ok) == ([], False)
    assert diagnostic["reason"] == "missing_result"
    assert diagnostic["scope"] == "thread"
    assert diagnostic["meeting_id"] == "M1"
    assert diagnostic["custom_id"] == "s_abc_00"


def test_assembly_reports_a_hash_mismatch_before_it_looks_for_results():
    """Order matters: the hash is checked first, so a stale sidecar reports
    hash_mismatch even when the results are also absent. An annotation that
    said missing_result here would send the reader to the API instead of raw."""
    sidecar = _sidecar_with_one_thread("sha256:deadbeef")
    threads, ok, diagnostic = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results={}, members={}, thread_counter=0,
    )
    assert diagnostic["reason"] == "hash_mismatch"
    assert diagnostic["scope"] == "thread"


def test_assembly_reports_a_missing_meeting_without_a_custom_id():
    """raw_missing happens before the thread loop, so there is no custom_id.

    Filling one in would point an annotation at a thread that was never
    examined — the same fiction this design refuses to put in the tally.
    """
    sidecar = _sidecar_with_one_thread(_correct_hash())
    threads, ok, diagnostic = summarize.assemble_from_manifest(
        sidecar, meetings_by_id={}, results={}, members={}, thread_counter=0,
    )
    assert (threads, ok) == ([], False)
    assert diagnostic["reason"] == "raw_missing"
    assert diagnostic["scope"] == "meeting"
    assert diagnostic["meeting_id"] == "M1"
    assert diagnostic["custom_id"] is None


def test_assembly_returns_no_diagnostic_when_it_succeeds():
    """The diagnostic must be None on success, not an empty dict."""
    sidecar = _sidecar_with_one_thread(_correct_hash())
    results = {"s_abc_00": {"speeches": [{"speechOrder": 1, "tension": "確認",
               "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
               "commitments": []}}
    threads, ok, diagnostic = summarize.assemble_from_manifest(
        sidecar, {"M1": _meeting()}, results, members={}, thread_counter=0,
    )
    assert ok is True
    assert diagnostic is None
```

- [ ] **Step 6: 失敗を確認してから `assemble_from_manifest` を実装する**

```bash
python -m pytest scripts/tests/test_resume.py -k "assembly_reports or assembly_returns_no_diagnostic" -v
```

Expected: FAIL — `ValueError: not enough values to unpack (expected 3, got 2)`

`scripts/summarize.py:606` の `assemble_from_manifest` を書き換える。docstring の
`Returns ``(threads, ok)``` も更新すること。

```python
def _diagnostic(reason: str, meeting_id: Optional[str] = None,
                custom_id: Optional[str] = None) -> dict:
    """One structured observation of why assembly stopped.

    Observation only, never a diagnosis. ``missing_result`` in particular is
    NOT evidence the API rejected anything: a result also goes missing on a
    fetch/parse/custom_id-mapping defect of ours. Naming a cause here would send
    the reader hunting a 400 that may never have happened.

    ``scope`` says which of the three levels the observation is about, so an
    annotation never points at a thread that was not examined. date-scope
    observations are raised by the caller, not by assembly.
    """
    scope = "thread" if custom_id else ("meeting" if meeting_id else "date")
    return {"scope": scope, "meeting_id": meeting_id,
            "custom_id": custom_id, "reason": reason}
```

`assemble_from_manifest` 内の 5 つの失敗 return を、それぞれ diagnostic 付きに変える
（`return [], False` → `return [], False, _diagnostic(...)`）:

| 現在の行 | 現在の return | 新しい return |
|---|---|---|
| 629 | `return [], False` | `return [], False, _diagnostic("raw_missing", meeting_id)` |
| 641 | `return [], False` | `return [], False, _diagnostic("speech_gap", meeting_id, custom_id)` |
| 649 | `return [], False` | `return [], False, _diagnostic("hash_mismatch", meeting_id, custom_id)` |
| 654 | `return [], False` | `return [], False, _diagnostic("missing_result", meeting_id, custom_id)` |
| 664 | `return [], False` | `return [], False, _diagnostic("thread_build_failed", meeting_id, custom_id)` |
| 675 | `return threads, True` | `return threads, True, None` |

既存の `log.error` はそのまま残す（ログと annotation は別の読み手に届く）。

- [ ] **Step 7: 呼び出し元 **5 箇所** を 3-tuple に合わせる**

`assemble_from_manifest` の呼び出し元は本体 2 箇所 + **既存テスト 3 箇所**。
**この時点では diagnostic を使わず、展開だけ合わせる**（使うのは Step 9 と Task 2）。

既存テスト側（Gate2 で見落としが発覚した箇所）— `scripts/tests/test_resume.py` の
86 / 97 / 105 行、いずれも `threads, ok = summarize.assemble_from_manifest(` を
`threads, ok, _ = summarize.assemble_from_manifest(` にする。

`scripts/summarize.py:1065`（`collect_pending_batches` 内）:

```python
        threads, ok, diagnostic = assemble_from_manifest(
            sidecar, meetings_by_id, results, members, thread_counter=0,
        )
```

`scripts/summarize.py:1348`（`run_batch_phase` 内）:

```python
    new_threads, ok, diagnostic = assemble_from_manifest(
        sidecar, meetings_by_id, results, members, thread_counter,
    )
```

- [ ] **Step 8: テストを通す**

```bash
python -m pytest scripts/tests -q
```

Expected: PASS。

**この時点で壊れるのは `test_resume.py:86/97/105` だけ**（Step 7 で直したはず）。
`test_systemic_failure.py:450` の `test_a_summary_that_cannot_be_assembled_also_counts_as_lost`
は**壊れない** — あれは同期経路のテストで `assemble_thread` を monkeypatch しており、
`assemble_from_manifest` を呼ばない。触らないこと。

- [ ] **Step 9: `run_batch_phase` の戻り値のテストを書く**

```python
def test_batch_phase_reports_publication_blocked_with_a_diagnostic(
        fake_client, tmp_path, monkeypatch):
    """A batch that answers usably but cannot be assembled must say so.

    This is #61: usable_result() only asks 'did it parse and carry speeches',
    while assembly also demands the speechOrders still exist in raw. Before this
    change the counter called such a meeting a success and the run exited 0.
    """
    result = _run_batch_phase_returning_dict(
        fake_client, tmp_path, monkeypatch,
        meetings=[_meeting("M1")],
        assembly_fails_with=summarize._diagnostic("speech_gap", "M1", "s_x_00"),
    )
    assert result["publication_blocked"] is True
    assert result["summary_attempted"] == 1
    assert result["diagnostic"]["reason"] == "speech_gap"


def test_batch_phase_does_not_report_blocked_when_nothing_was_summarized(
        fake_client, tmp_path, monkeypatch):
    """A quiet date must not be reported as blocked."""
    result = _run_batch_phase_returning_dict(
        fake_client, tmp_path, monkeypatch,
        meetings=[_procedural_meeting("P1")],
    )
    assert result["publication_blocked"] is False
    assert result["summary_attempted"] == 0
```

ヘルパは既存の `_run_batch_phase`（143行付近）を参考に、戻り値 dict をそのまま返す形で書く。
`assembly_fails_with` は `monkeypatch.setattr(summarize, "assemble_from_manifest", ...)` で
`([], False, diagnostic)` を返させる（`summarize.py:1348` はモジュールグローバル参照なので届く）。

**必須の下準備**（これが無いと assembly に到達せず `publication_blocked` が False のまま、
「別の理由で」テストが落ちる）:

```python
    fake_client.messages.batches.statuses["msgbatch_fake_0001"] = "ended"
```

`run_batch_phase` は `processing_status != "ended"` で `summarize.py:1328` から
pending として早期 return する。バッチ ID の綴りは `conftest.py` の `FakeBatches.create`
を見て合わせること。

- [ ] **Step 10: 失敗を確認してから `run_batch_phase` を dict 化する**

```bash
python -m pytest scripts/tests/test_systemic_failure.py -k "batch_phase_reports_publication_blocked or batch_phase_does_not_report_blocked" -v
```

Expected: FAIL — `TypeError: tuple indices must be integers or slices, not str`

`run_batch_phase` の戻り値をすべて dict に変える。**tuple の要素を増やさない**理由は
spec §3.6 のとおり（位置依存の展開が壊れやすく、この関数は既に out-parameter を持っている）。

ヘルパを関数の直前に置く:

```python
def _batch_phase_result(threads: list, thread_counter: int,
                        completed_meeting_ids: list, pending: bool,
                        summary_attempted: int = 0,
                        publication_blocked: bool = False,
                        diagnostic: Optional[dict] = None) -> dict:
    """The shape run_batch_phase answers with.

    A dict rather than a longer tuple: this function already carries an
    out-parameter (api_stats), and a 7-tuple unpacked positionally at the call
    site is the kind of thing that breaks silently when a field is inserted.
    """
    return {
        "threads": threads,
        "thread_counter": thread_counter,
        "completed_meeting_ids": completed_meeting_ids,
        "pending": pending,
        "summary_attempted": summary_attempted,
        "publication_blocked": publication_blocked,
        "diagnostic": diagnostic,
    }
```

`run_batch_phase` 内の各 return を置き換える:

| 現在の行 | 現在 | 新しい |
|---|---|---|
| 1269 | `return [], thread_counter, [], False` | `return _batch_phase_result([], thread_counter, [], False)` |
| 1308 | `return [], thread_counter, [], False` | `return _batch_phase_result([], thread_counter, [], False, summary_attempted)` |
| 1328 | `return [], thread_counter, [], True` | `return _batch_phase_result([], thread_counter, [], True, summary_attempted)` |
| 1343 | `return [], thread_counter, [], True` | `return _batch_phase_result([], thread_counter, [], True, summary_attempted)` |
| 1353 | `return [], thread_counter, [], True` | 下記 |
| 1358 | `return new_threads, thread_counter, completed_meeting_ids, False` | 下記 |

1267 行の `if not all_pending:` の直前で母数を確定させる:

```python
    # The denominator for trigger 2 — meetings that actually put a summary
    # request in this batch. Deliberately NOT api_stats["attempted"], which also
    # counts a meeting whose grouping legitimately produced zero threads; see
    # publication_blocked_verdict. Spelled identically to the submission-failure
    # counter below (:1302) on purpose: the two answer the same question, and
    # two spellings of it would eventually disagree.
    summary_attempted = sum(
        1 for p in prepared_meetings if p.get("askable") and p.get("pending")
    )
```

1351-1353 の assembly 失敗分岐:

```python
    if not ok:
        log.error("Batch %s ended but assembly incomplete — keeping sidecar", batch_id)
        return _batch_phase_result(
            [], thread_counter, [], True, summary_attempted,
            publication_blocked=True, diagnostic=diagnostic,
        )
```

1355-1358 の成功分岐:

```python
    thread_counter += len(new_threads)
    completed_meeting_ids = [m["meeting_id"] for m in sidecar["meetings"]]
    bs.delete_sidecar(path)
    return _batch_phase_result(new_threads, thread_counter,
                               completed_meeting_ids, False, summary_attempted)
```

- [ ] **Step 11: `run_pipeline` の呼び出し側を直し、トリガ2 を判定に入れる**

`scripts/summarize.py:1507`:

```python
    pending = False
    publication_blocked = False
    summary_attempted = 0
    assembly_diagnostic = None
    api_stats = new_api_stats()
    if batch:
        phase = run_batch_phase(
            client, meetings, progress, members, model, date_str,
            thread_counter,
            batch_timeout_seconds=batch_timeout_seconds,
            batch_poll_seconds=batch_poll_seconds,
            pending_dir=pending_dir,
            ci_commit=ci_commit,
            api_stats=api_stats,
        )
        new_threads = phase["threads"]
        thread_counter = phase["thread_counter"]
        completed_ids = phase["completed_meeting_ids"]
        pending = phase["pending"]
        publication_blocked = phase["publication_blocked"]
        summary_attempted = phase["summary_attempted"]
        assembly_diagnostic = phase["diagnostic"]
        all_threads.extend(new_threads)
```

（以降の `for mid in completed_ids:` 以下はそのまま）

- [ ] **Step 12: 最終の verdict 判定を2トリガに置き換える**

`scripts/summarize.py:1626-1656` を書き換える。**既存の annotation 文言は活かしつつ、
2つの観測を独立に併記する**（spec §3.8）。

```python
    # The two triggers are not exclusive. A fully rejected batch fires BOTH:
    # nothing usable came back (trigger 1), and assembly then failed on those
    # very same missing results (trigger 2). Report both observations — calling
    # it "answered but not assemblable" while the API was in fact rejecting
    # everything sends the reader away from the 400 that is actually there.
    rejection = rejection_verdict(api_stats, len(all_threads))
    blocked = (publication_blocked_verdict(summary_attempted, len(all_threads))
               if publication_blocked else 0)
    verdict = worst_verdict(rejection, blocked)

    if verdict:
        level = "error" if verdict == EXIT_SYSTEMIC_FAILURE else "warning"
        lines = [f"{date_str}: nothing this run produced reached the site "
                 f"({len(all_threads)} thread(s) on the date in total)"]
        if rejection:
            lines.append(
                f"all {api_stats['attempted']} meeting(s) asked about this run "
                f"produced no usable summary")
        if blocked:
            d = assembly_diagnostic or {}
            lines.append(
                f"assembly failed: {d.get('reason', 'unknown')} "
                f"(scope={d.get('scope')}, meeting={d.get('meeting_id')}, "
                f"custom_id={d.get('custom_id')})")
        if verdict == EXIT_SUSPECT_FAILURE:
            lines.append("on its own this is one bad meeting, but several in "
                         "one run is an outage")
        _annotate(level, " — ".join(lines))
        return verdict
    return 0
```

- [ ] **Step 13: 全テストを走らせる**

```bash
python -m pytest scripts/tests -q
```

Expected: PASS。**Step 10 の dict 化で壊れるのは、`run_batch_phase` を直接展開している
4 箇所だけ**（Gate2 で実測。当初の予測は誤りだった）:

| ファイル:行 | 直し方 |
|---|---|
| `test_systemic_failure.py:260` + 268/270 の assert | `phase = summarize.run_batch_phase(...)` にし、`phase["threads"]` / `phase["pending"]` で assert |
| `test_systemic_failure.py:310` + 318/319 | 同上 |
| `test_systemic_failure.py:335` + 343/344 | 同上 |
| `test_resume.py:127` | `phase = summarize.run_batch_phase(...)`、`assert phase["pending"] is True` |

**壊れないもの**（触らないこと）:
- `_run_batch_phase` ヘルパ（`test_systemic_failure.py:143`）— 戻り値を展開せずそのまま返して
  いるだけで、4 つの呼び出し元はすべて戻り値を捨てて `stats` を assert している。
- `_run_pipeline`（`:380`）と `test_run_pipeline_returns_the_verdict_on_a_total_batch_failure`
  — `run_batch_phase` の戻り値に触れない。

verdict の期待値そのものが変わったテストがあれば、**変わってよいのか spec §3.1 で確認してから**直す。

- [ ] **Step 14: 全件 rejection で両トリガが同時に立つことのテストを追加する**

spec §4 のテスト 0（最も起きやすく、最も誤診断しやすい経路）。

```python
def test_a_fully_rejected_batch_fires_both_triggers_and_says_so(
        fake_client, tmp_path, monkeypatch, capsys):
    """#51's shape: every summary request errors.

    Trigger 1 fires (nothing usable came back) and trigger 2 fires too, because
    assembly then cannot find those results. The date must be counted ONCE, and
    the annotation must not claim the answers arrived.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    exit_code = _run_pipeline_with_all_results_errored(
        fake_client, tmp_path, monkeypatch, meetings=[_meeting("M1"), _meeting("M2")],
    )
    assert exit_code == summarize.EXIT_SYSTEMIC_FAILURE
    out = capsys.readouterr().out
    annotations = [ln for ln in out.splitlines() if ln.startswith("::error::")]
    assert len(annotations) == 1, "the date must be annotated once, not per trigger"
    assert "produced no usable summary" in annotations[0]
    assert "assembly failed: missing_result" in annotations[0]
    assert "answered" not in annotations[0].lower(), (
        "must not claim the answers arrived while the API was rejecting everything"
    )
```

ヘルパ `_run_pipeline_with_all_results_errored` は既存の `_errored_entry`（359行）と
`_run_pipeline`（380行）を組み合わせて書く。

- [ ] **Step 15: テストを通し、コミットする**

**コミット前に `python -m pytest scripts/tests -q` が緑であることを必ず確認する。**
`test_resume.py` の修正（Step 7 と Step 13 の表）を飛ばしたまま進むと、ここで赤いまま
コミットすることになる。

```bash
python -m pytest scripts/tests -q
git add scripts/summarize.py scripts/tests/test_systemic_failure.py scripts/tests/test_resume.py
git commit -m "fix: report a date that answered but published nothing (#61)

usable_result() only asks whether a result parsed and carries speeches.
Assembly demands more: the speechOrders must still exist in re-fetched raw,
the input_hash must still match, every custom_id must have a result. A batch
that clears the first bar and fails the second was counted as a success, and
the run exited 0 with the sidecar kept — red days later, not that morning.

Add a second trigger rather than widening the counter. Assembly is
all-or-nothing, so 'answered but not published' is a fact about the DATE; a
per-meeting tally would report meetings that were never examined as having
failed. The denominator is meetings that actually sent a summary request, not
api_stats['attempted'] — that one includes a meeting whose grouping
legitimately produced nothing, which is enough to hide a real outage.

The triggers are not exclusive: a fully rejected batch fires both. Both
observations are reported, because calling it 'answered but not assemblable'
while the API is rejecting everything sends the reader away from the 400.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: resume 経路の verdict（#59）

**Files:**
- Modify: `scripts/summarize.py` — `collect_pending_batches`（950-1079）、
  `main`（1725-1737 の `--collect-pending` 分岐）
- Test: `scripts/tests/test_resume.py` — 新規 verdict テスト、および
  **`collect_pending_batches` の既存 13 箇所**（161, 176, 202, 230, 252, 275, 330, 450, 478,
  508, 549, 612, 637）を dict 受けに直す。`hard = summarize.collect_pending_batches(...)` →
  `result = summarize.collect_pending_batches(...)`、`assert hard is False` →
  `assert result["hard_fail"] is False`。**dict は常に truthy なので、直さないと
  `assert hard is True` 系が偶然通って回帰ガードが死ぬ**（fail-open）。
  戻り値を捨てている 367 / 396 / 664 / 715 行は変更不要。
- Test: `scripts/tests/test_systemic_failure.py` — `main()` の契約テスト（Step 8）

**Interfaces:**
- Consumes: Task 1 の `publication_blocked_verdict` / `worst_verdict` /
  `rejection_verdict` / `_diagnostic` / 3-tuple な `assemble_from_manifest`
- Produces:
  - `collect_pending_batches(...) -> dict`（`CollectResult`。キーは Step 3 参照）
  - `_write_github_output(systemic_dates: list, suspect_dates: list) -> None`

---

- [ ] **Step 1: resume verdict のテストを書く（`test_resume.py`）**

**`scripts/tests/test_resume.py`** に書く。既存の `_sidecar_with_one_thread(input_hash)` /
`_meeting()` / `_correct_hash()` を使うこと（sidecar には `retry_count` と `attempts` が
必須で、それらは既存フィクスチャが持っている）。

`_run_collect(...)` は約 40 行の実ヘルパで、既存テストのパターンを組み合わせて書く:
sidecar を `pending_dir` に書く、`fake_client.messages.batches.statuses[...]` を設定する、
raw を書く（`raw_present=False` なら書かない）、`existing_threads` は
`{threads_dir}/{date}.json` にリストとして書く、`sidecar_age_days` は
`attempts[-1]["submitted_at"]` と `monkeypatch.setattr(summarize, "_utcnow_iso", ...)` の
**両方**で作る（`test_resume.py:218/224` と同じ手口）。

```python
def test_resume_reports_a_fully_rejected_batch_as_systemic(
        fake_client, tmp_path, monkeypatch):
    """#59: with a sidecar present the workflow skips Summarize entirely, so a
    resumed batch that comes back fully rejected published nothing and said
    nothing. Several consecutive mornings could go green that way."""
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          results={}, existing_threads=0)
    assert result["systemic_dates"] == ["2026-05-14"]
    assert result["suspect_dates"] == []
    assert result["hard_fail"] is False
    assert result["diagnostics"][0]["reason"] == "missing_result"


def test_resume_keeps_a_lone_failure_on_a_published_date_as_suspect(
        fake_client, tmp_path, monkeypatch):
    """The softener applies here too. A sidecar is created by any batch that
    outruns the poll budget, so it is routine — treating resume as exceptional
    would promote the same failure from suspect to systemic purely because the
    batch took longer than one run."""
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          results={}, existing_threads=3)
    assert result["systemic_dates"] == []
    assert result["suspect_dates"] == ["2026-05-14"]


def test_resume_says_nothing_while_the_batch_is_still_running(
        fake_client, tmp_path, monkeypatch):
    """An unfinished batch has answered nothing yet. Counting it would make the
    alarm fire on every slow morning."""
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          batch_status="in_progress", existing_threads=0)
    assert result["systemic_dates"] == []
    assert result["suspect_dates"] == []


def test_resume_denominator_ignores_meetings_with_no_summary_request():
    """A manifest meeting with threads: [] asked nothing of this run."""
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["meetings"].append({"meeting_id": "M2", "outcome": {}, "threads": []})
    assert summarize._resume_summary_attempted(sidecar) == 1
```

- [ ] **Step 2: 失敗を確認する**

```bash
python -m pytest scripts/tests/test_systemic_failure.py -k "resume_reports or resume_keeps or resume_says_nothing or resume_denominator" -v
```

Expected: FAIL — `TypeError: 'bool' object is not subscriptable`（`collect_pending_batches`
がまだ bool を返すため）と `AttributeError: _resume_summary_attempted`

- [ ] **Step 3: `collect_pending_batches` を `CollectResult` に変える**

`scripts/summarize.py:950` のシグネチャは変えず、戻り値だけ変える。docstring も更新する。

母数のヘルパを関数の直前に置く:

```python
def _resume_summary_attempted(sidecar: dict) -> int:
    """Meetings this resume run actually has summary requests for.

    The manifest does not persist ``askable``, and it does not need to: by the
    time a sidecar exists, grouping and outcome are already done and the only
    question left for the API is the summary. So "asked about this run" is
    exactly "has a non-empty threads list" — no schema change, and therefore
    none of the SCHEMA_VERSION landing constraints (see CLAUDE.md).
    """
    return sum(1 for m in sidecar.get("meetings", []) if m.get("threads"))
```

関数の冒頭を書き換える:

```python
    hard_fail = False
    systemic_dates: list = []
    suspect_dates: list = []
    diagnostics: list = []
```

`assemble_from_manifest` の失敗分岐（1068-1073）を書き換える:

```python
        if not ok:
            log.error("Resume: assembly incomplete for %s (%s)", date_str,
                      (diagnostic or {}).get("reason", "unknown"))
            _record_resume_verdict(
                date_str, _resume_summary_attempted(sidecar),
                _existing_thread_count(threads_dir, date_str),
                diagnostic, systemic_dates, suspect_dates, diagnostics,
            )
            if _retry_or_hardfail(client, sidecar, path, "assemble_failed",
                                  raw_dir, model, ci_commit):
                hard_fail = True
            continue
```

判定と記録のヘルパ（`collect_pending_batches` の直前に置く）:

```python
def _existing_thread_count(threads_dir: str, date_str: str) -> int:
    """Threads already on disk for this date — the evidence the softener needs."""
    path = os.path.join(threads_dir, f"{date_str}.json")
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except (OSError, json.JSONDecodeError):
        return 0
    return len(existing) if isinstance(existing, list) else 0


def _record_resume_verdict(date_str: str, summary_attempted: int,
                           published_threads: int, diagnostic: Optional[dict],
                           systemic_dates: list, suspect_dates: list,
                           diagnostics: list) -> None:
    """Record one date's verdict and annotate it immediately.

    Annotated here, as the verdict is reached, rather than accumulated for a
    single write at the end: if a LATER sidecar hard-fails, the step fails and
    every step below it is skipped, so nothing that reached GITHUB_OUTPUT is
    ever read. An annotation is the one channel that survives a failed step —
    the same reason the Summarize loop annotates before it dies.
    """
    verdict = publication_blocked_verdict(summary_attempted, published_threads)
    if not verdict:
        return
    if diagnostic:
        diagnostics.append({**diagnostic, "date": date_str})
    d = diagnostic or {}
    detail = (f"assembly failed: {d.get('reason', 'unknown')} "
              f"(scope={d.get('scope')}, meeting={d.get('meeting_id')}, "
              f"custom_id={d.get('custom_id')})")
    if verdict == EXIT_SYSTEMIC_FAILURE:
        systemic_dates.append(date_str)
        _annotate("error", f"{date_str}: resumed batch published nothing "
                           f"({published_threads} thread(s) on the date) — {detail}")
    else:
        suspect_dates.append(date_str)
        _annotate("warning", f"{date_str}: the one meeting in the resumed batch "
                             f"published nothing (the date keeps its "
                             f"{published_threads} thread(s)) — {detail}")
```

最後の `return hard_fail`（1079）を置き換える:

```python
    return {
        "hard_fail": hard_fail,
        "systemic_dates": systemic_dates,
        "suspect_dates": suspect_dates,
        "diagnostics": diagnostics,
    }
```

- [ ] **Step 4: テストを通す**

```bash
python -m pytest scripts/tests/test_systemic_failure.py -k "resume_" -v
```

Expected: PASS

- [ ] **Step 5: `ended` + 日付全体 raw 不在のテストを書く**

```python
def test_resume_flags_a_finished_batch_whose_date_lost_its_raw(
        fake_client, tmp_path, monkeypatch):
    """The batch finished; the date's raw is gone but not yet old enough to be
    written off. Nothing can be assembled and the sidecar blocks Summarize for
    every date, so this stalls green for up to ABANDON_AGE_DAYS. That is the
    same failure #59 is about, arriving by a different door."""
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          raw_present=False, sidecar_age_days=2, existing_threads=0)
    assert result["systemic_dates"] == ["2026-05-14"]
    assert result["diagnostics"][0]["reason"] == "raw_date_missing"
    assert result["diagnostics"][0]["scope"] == "date"


def test_resume_stays_quiet_when_the_raw_is_legitimately_out_of_window(
        fake_client, tmp_path, monkeypatch):
    """Past the abandon age the raw is SUPPOSED to be gone. That path already
    warns and deletes the sidecar, so adding a second alarm would red the run
    for a date nobody can act on."""
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          raw_present=False, sidecar_age_days=40, existing_threads=0)
    assert result["systemic_dates"] == []
    assert result["suspect_dates"] == []
```

- [ ] **Step 6: 失敗を確認してから raw 不在分岐を実装する**

```bash
python -m pytest scripts/tests/test_systemic_failure.py -k "lost_its_raw or out_of_window" -v
```

Expected: FAIL — `systemic_dates` が空

`scripts/summarize.py:1020-1042` の `if not meetings_by_id:` 分岐の `else` 側を書き換える。
**再送も retry 消費もしない**（警報だけを足す）:

```python
            else:
                log.error("Resume: no raw for %s (outside window?) — keeping sidecar",
                          date_str)
                # Alarm, but no resubmit and no retry spent: the batch is fine
                # and re-fetching raw may still rescue it. What must not happen
                # is another silent morning — the sidecar keeps Summarize
                # skipped for EVERY date while this sits here.
                _record_resume_verdict(
                    date_str, _resume_summary_attempted(sidecar),
                    _existing_thread_count(threads_dir, date_str),
                    _diagnostic("raw_date_missing"),
                    systemic_dates, suspect_dates, diagnostics,
                )
```

- [ ] **Step 7: テストを通す**

```bash
python -m pytest scripts/tests -q
```

Expected: PASS

- [ ] **Step 8: `main()` の契約のテストを書く**

```python
def test_collect_pending_exits_zero_on_a_soft_verdict(monkeypatch, tmp_path):
    """--collect-pending handles MANY dates in one process, so a single exit
    code cannot carry the verdicts; the date lists do. Returning 3 here would
    only add a second transport and, under the workflow's set -e, would block
    the publish — the amplification #52 was about."""
    monkeypatch.setattr(summarize, "collect_pending_batches", lambda *a, **k: {
        "hard_fail": False, "systemic_dates": ["2026-05-14"],
        "suspect_dates": [], "diagnostics": [],
    })
    ...  # stub anthropic.Anthropic / load_members / save_members
    with pytest.raises(SystemExit) as e:
        summarize.main(["--collect-pending"])
    assert e.value.code in (0, None)


def test_collect_pending_exits_one_on_a_hard_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(summarize, "collect_pending_batches", lambda *a, **k: {
        "hard_fail": True, "systemic_dates": [], "suspect_dates": [], "diagnostics": [],
    })
    ...
    with pytest.raises(SystemExit) as e:
        summarize.main(["--collect-pending"])
    assert e.value.code == 1


def test_collect_pending_writes_deduplicated_dates_to_github_output(
        monkeypatch, tmp_path):
    """Duplicated dates would be counted twice by the workflow's SUSPECT_N
    threshold and could cross it on their own."""
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setattr(summarize, "collect_pending_batches", lambda *a, **k: {
        "hard_fail": False, "systemic_dates": [],
        "suspect_dates": ["2026-05-14", "2026-05-14", "2026-05-15"],
        "diagnostics": [],
    })
    ...
    with pytest.raises(SystemExit):
        summarize.main(["--collect-pending"])
    written = out.read_text()
    assert "systemic_dates=\n" in written
    assert "suspect_dates=2026-05-14 2026-05-15\n" in written
```

- [ ] **Step 9: 失敗を確認してから `main()` を実装する**

`scripts/summarize.py` に書き出しヘルパを追加する（`_annotate` の隣、88行付近）:

```python
def _write_github_output(**values: list) -> None:
    """Publish date lists as step outputs, deduplicated and sorted.

    Only the resume path writes these: it handles many dates in one process, so
    its verdicts cannot ride on an exit code the way a single-date run's do.
    Deduplicated because the workflow thresholds on how many DATES reported a
    suspect verdict, and the same date listed twice would cross that on its own.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for key, dates in values.items():
            f.write(f"{key}={' '.join(sorted(set(dates)))}\n")
```

`main()` の `--collect-pending` 分岐（1725-1737）を書き換える:

```python
    if args.collect_pending:
        client = anthropic.Anthropic()
        members = load_members(args.members_path)
        result = collect_pending_batches(
            client, members, args.model,
            pending_dir=args.pending_dir, threads_dir=args.output_dir,
            raw_dir=args.raw_dir, budget_seconds=args.batch_budget,
            poll_seconds=args.batch_poll, ci_commit=args.ci_commit,
        )
        save_members(members, args.members_path)
        # NOT wrapped in try/finally. A SystemExit raised from a finally block
        # REPLACES the exception that sent us there, so an unwritable
        # GITHUB_OUTPUT would exit 0 with no verdict transported and no
        # traceback — a fail-open in the one transport #59 depends on. Let it
        # raise: the annotations are already out, and a crash here is honest.
        _write_github_output(
            systemic_dates=result["systemic_dates"],
            suspect_dates=result["suspect_dates"],
        )
        # 1, never 3 or 4. This process speaks for many dates, so its exit code
        # cannot say WHICH date failed — the outputs above do that, and the
        # annotations already emitted survive even a failed step. What the code
        # still has to carry is "stop, a human is needed": a sidecar past its
        # retry threshold or written by an older schema.
        sys.exit(1 if result["hard_fail"] else 0)
```

**注意**: `main()` を呼ぶテストは、`--members-path` / `--output-dir` / `--pending-dir` を
**必ず `tmp_path` 配下に明示する**こと。既定値は `data/members.json` と `data/threads` で、
`save_members` が**リポジトリのコミット済みデータを書き換える**。あわせて
`monkeypatch.setattr(summarize.anthropic, "Anthropic", lambda *a, **k: object())` で
実クライアントの生成を止める（`load_dotenv()` 経由で本物の鍵を拾いうる）。

- [ ] **Step 10: テストを通してコミットする**

```bash
python -m pytest scripts/tests -q
git add scripts/summarize.py scripts/tests/test_systemic_failure.py
git commit -m "fix: give the --collect-pending resume path a failure signal (#59)

A pending sidecar makes the workflow skip the whole Summarize step, so on
those mornings collect_pending_batches() IS the run. It had no api_stats, never
asked systemic_failure(), and answered with a bare bool. A resumed batch coming
back fully rejected therefore resubmitted quietly until the retry threshold ran
out — several consecutive green runs publishing nothing, which is the failure
exit 3 was built to end.

Report per-date verdicts instead. The suspect carve-out applies here too: a
sidecar is created by any batch that outruns the poll budget, so it is routine,
and treating resume as exceptional would promote the same single failure from
suspect to systemic purely because the batch took longer than one run.

A finished batch whose date lost its raw is now flagged as well. It cannot be
assembled, it blocks Summarize for every other date, and it was previously
silent for up to ABANDON_AGE_DAYS. No resubmit and no retry is spent on it —
re-fetching raw may still rescue it; only the silence had to go.

The denominator is manifest meetings holding summary requests. By the time a
sidecar exists, grouping and outcome are done and the summary is the only
question left, so no askable flag needs persisting and SCHEMA_VERSION is
untouched.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: workflow の集約と文言、CLAUDE.md

**Files:**
- Modify: `.github/workflows/daily-batch.yml` — Collect ステップ（108-120）、
  Summarize ステップの閾値適用部（185-198）、Summary ステップ（338-365）、
  最終ステップ（373-388）
- Modify: `CLAUDE.md` — exit code 表と閾値の所在
- Test: `scripts/tests/test_systemic_failure.py` — `test_the_workflow_tolerates_exactly_these_exit_codes`

**Interfaces:**
- Consumes: Task 2 が `$GITHUB_OUTPUT` に書く `systemic_dates` / `suspect_dates`
  （Collect ステップの `id: collect` の出力になる）

---

- [ ] **Step 1: workflow を縛るテストを先に書く**

既存の `test_the_workflow_tolerates_exactly_these_exit_codes`（535行）の直後に追加する。

```python
def _workflow_steps():
    import yaml
    wf = yaml.safe_load(Path(".github/workflows/daily-batch.yml").read_text())
    return wf["jobs"]["fetch-and-summarize"]["steps"]


def test_the_collect_step_does_not_block_the_publish():
    """Collect exits 0 on a soft verdict, so it needs no rc capture — but it
    must also not have grown one that swallows a hard fail."""
    steps = _workflow_steps()
    collect = next(s for s in steps if s.get("id") == "collect")
    assert "|| rc=" not in collect["run"], (
        "Collect answers with outputs, not an exit code — an rc capture here "
        "would be a second transport and could swallow a hard fail"
    )
    assert "set -e" in collect["run"]


def test_the_final_step_reads_both_paths():
    """#59's acceptance condition: Summarize is SKIPPED on a pending morning,
    so a verdict that only travels through its outputs can never fail the job."""
    steps = _workflow_steps()
    final = steps[-1]
    run = final["run"]
    for ref in ("steps.summarize.outputs.systemic_dates",
                "steps.summarize.outputs.suspect_dates",
                "steps.collect.outputs.systemic_dates",
                "steps.collect.outputs.suspect_dates"):
        assert ref in str(final.get("env", {})), f"{ref} never reaches the final step"
    assert "exit 1" in run


def test_the_threshold_lives_in_the_final_step_and_dedupes():
    """The threshold counts DATES, so the union has to be a set. Concatenating
    two lists that name the same date would cross the threshold on its own."""
    steps = _workflow_steps()
    run = steps[-1]["run"]
    assert "-ge 2" in run, "the suspect threshold must be findable in one place"
    assert "sort -u" in run, "the union must deduplicate dates"


def test_the_summarize_step_no_longer_applies_the_threshold():
    """It cannot: on a pending morning it does not run at all, and it never
    sees Collect's dates."""
    steps = _workflow_steps()
    summarize_step = next(s for s in steps if s.get("id") == "summarize")
    assert "-ge 2" not in summarize_step["run"]
```

- [ ] **Step 2: 失敗を確認する**

```bash
python -m pytest scripts/tests/test_systemic_failure.py -k "collect_step_does_not_block or final_step_reads_both or threshold_lives or no_longer_applies" -v
```

Expected: FAIL（4件とも）

- [ ] **Step 3: Summarize ステップから閾値適用を外す**

`.github/workflows/daily-batch.yml:185-198` を置き換える。

```yaml
          # The threshold is NOT applied here any more. This step does not run
          # at all on a morning with a pending sidecar, and it never sees the
          # dates Collect reported — so a policy applied here would be blind to
          # half its input. It moved to the last step, which sees both.
          echo "systemic_dates=$SYSTEMIC" >> "$GITHUB_OUTPUT"
          echo "suspect_dates=$SUSPECT" >> "$GITHUB_OUTPUT"
```

（`SUSPECT_N` の加算とコメントは削除。`SYSTEMIC` / `SUSPECT` の収集ループはそのまま）

**同じ編集で、古くなるコメントも直すこと**（放置すると次の読み手が `SUSPECT_N` を
無い場所に探しに行く）:

- `daily-batch.yml:136-148` — 「This loop is the only place that sees every date, so the
  threshold lives here.」→ 閾値は最終ステップへ移ったので、「この loop は date ごとの
  verdict を集めるだけ。閾値は最終ステップ（Collect の日付も見える唯一の場所）」に書き換える。
- `daily-batch.yml:416` 付近（`notify-on-failure` の本文）— Summarize ステップのログを
  見ろと書いてあるが、pending の朝は Summarize が走っていない。Collect の annotation も
  指すように直す。

- [ ] **Step 4: Collect ステップにコメントを足す（rc 捕捉は足さない）**

`.github/workflows/daily-batch.yml:108-120` の `run:` の先頭コメントを更新する。

```yaml
        run: |
          set -e
          # No rc capture on purpose. This process speaks for MANY dates, so it
          # answers with systemic_dates/suspect_dates outputs (written from
          # Python) and exits 0 on a soft verdict. A non-zero code from here
          # still means "stop, a human is needed" — retry threshold reached, or
          # a sidecar from an older schema — and must still abort under set -e.
          python scripts/summarize.py --collect-pending --batch-budget 1800 --ci-commit
```

- [ ] **Step 5: 最終ステップを union + 閾値に書き換える**

`.github/workflows/daily-batch.yml:373-388` を置き換える。`if:` は**外す**（常に走らせ、
中で判定する。GitHub 式では集合演算も数値比較もできない）。

```yaml
      - name: Fail the run on a systemic summary failure
        env:
          # Through env, not inline expressions, per the same rule as
          # LOOKBACK_DAYS above. Four inputs, not two: on a morning with a
          # pending sidecar the Summarize step is skipped entirely and Collect
          # is the only one with anything to say (#59).
          SUMMARIZE_SYSTEMIC: ${{ steps.summarize.outputs.systemic_dates }}
          SUMMARIZE_SUSPECT: ${{ steps.summarize.outputs.suspect_dates }}
          COLLECT_SYSTEMIC: ${{ steps.collect.outputs.systemic_dates }}
          COLLECT_SUSPECT: ${{ steps.collect.outputs.suspect_dates }}
        run: |
          # Deduplicate: the threshold below counts DATES, and the same date
          # arriving from both paths must not cross it on its own.
          SYSTEMIC=$(echo $SUMMARIZE_SYSTEMIC $COLLECT_SYSTEMIC | tr ' ' '\n' \
                       | grep -v '^$' | sort -u | tr '\n' ' ')
          SUSPECT=$(echo $SUMMARIZE_SUSPECT $COLLECT_SUSPECT | tr ' ' '\n' \
                      | grep -v '^$' | sort -u | tr '\n' ' ')
          SUSPECT_N=$(echo $SUSPECT | wc -w)

          # THE policy, in one findable place. A suspect date is one meeting
          # failing on an already-published date: the 30-day lookback re-visits
          # published dates every morning, so failing on one would fail most
          # mornings and the alarm would get switched off. Several in ONE run is
          # different — a total outage can present as nothing but 1-of-1
          # failures. Change the threshold HERE, not in Python.
          FAIL_DATES="$SYSTEMIC"
          if [ "$SUSPECT_N" -ge 2 ]; then
            FAIL_DATES="$FAIL_DATES$SUSPECT"
          fi

          if [ -z "$(echo $FAIL_DATES | tr -d ' ')" ]; then
            echo "No systemic summary failure this run."
            exit 0
          fi
          echo "::error::Nothing this run produced reached the site on:${FAIL_DATES}"
          echo "This is an alert, not a publish failure — data already committed above is"
          echo "intact, and these dates may still hold threads from earlier runs."
          echo "The Summarize and Collect step annotations name which of these happened"
          echo "(they are not exclusive — a fully rejected batch reports both):"
          echo "  * 'produced no usable summary' — the requests were REJECTED. A 400 means"
          echo "    the request shape no longer matches the model's contract (#51);"
          echo "    429/529 means the API was overloaded."
          echo "  * 'assembly failed: <reason>' — the answers came back but could not be"
          echo "    turned into threads. The reason names the observation:"
          echo "    hash_mismatch/speech_gap/raw_missing point at raw, missing_result does not."
          exit 1
```

- [ ] **Step 6: Summary ステップも両経路を載せる**

`.github/workflows/daily-batch.yml:345-346` の env に 2 行足し、本文の条件を更新する。

```yaml
          SYSTEMIC_DATES: ${{ steps.summarize.outputs.systemic_dates }}
          SUSPECT_DATES: ${{ steps.summarize.outputs.suspect_dates }}
          COLLECT_SYSTEMIC_DATES: ${{ steps.collect.outputs.systemic_dates }}
          COLLECT_SUSPECT_DATES: ${{ steps.collect.outputs.suspect_dates }}
```

```bash
            if [ -n "${SYSTEMIC_DATES}${COLLECT_SYSTEMIC_DATES}" ]; then
              echo "- **Nothing reached the site on**:${SYSTEMIC_DATES}${COLLECT_SYSTEMIC_DATES}"
            fi
            if [ -n "${SUSPECT_DATES}${COLLECT_SUSPECT_DATES}" ]; then
              echo "- **One meeting failed on an already-published date**:${SUSPECT_DATES}${COLLECT_SUSPECT_DATES}"
              echo "  (not a failure on its own — watch for several of these in one run)"
            fi
```

- [ ] **Step 7: 既存の workflow テストを新しい形に合わせる**

`test_the_workflow_tolerates_exactly_these_exit_codes`（535行）が古い形を固定している。
**Gate2 で実測した、直すべき 5 箇所すべて**:

| 行 | 現在の assert | 対応 |
|---|---|---|
| 565 | `assert "SUSPECT_N=$((SUSPECT_N + 1))" in run` | **削除**（Summarize から閾値が外れた。新テスト `test_the_threshold_lives_in_the_final_step_and_dedupes` が最終ステップ側で見る） |
| 569 | `assert '[ "$SUSPECT_N" -ge 2 ]' in run` | **削除**（同上） |
| 572 | `assert 'FAIL_DATES="$FAIL_DATES$SUSPECT"' in run` | **削除**（`FAIL_DATES` は最終ステップへ移った） |
| 573 | `assert 'systemic_dates=$FAIL_DATES' in run` | `assert 'systemic_dates=$SYSTEMIC' in run` |
| 584 | `assert steps[-1]["if"] == "steps.summarize.outputs.systemic_dates != ''"` | 下記に置換 |

584 行の置換:

```python
    # No `if:` any more — the last step always runs and decides inside, because
    # a GitHub expression can neither union two lists nor compare a count. Note
    # this still leaves the step an implicit success(), so a Collect hard-fail
    # skips it — acceptable, since that already failed the job and the
    # annotations survive. `if: always()` is deliberately NOT the answer.
    assert "if" not in steps[-1]
```

565 / 569 / 572 を消したあと、このテストが**まだ何かを守っているか**を確認すること。
`rc -eq 3` / `rc -eq 4` の許容と `suspect_dates=$SUSPECT` の出力が残っていれば目的は果たす。

- [ ] **Step 8: テストを通す**

```bash
python -m pytest scripts/tests -q
```

Expected: PASS

- [ ] **Step 9: CLAUDE.md の exit code 表と閾値の所在を更新する**

`CLAUDE.md` の「`summarize.py` exit codes」節を編集する。

1. 表の 3 の行の説明を、2つのトリガを併記する形に変える:

```
| 3 | **nothing reached the site**: every meeting asked about this run produced nothing that
      became a thread, OR summary requests went out and the date assembled nothing. The two
      are not exclusive — a fully rejected batch reports both. | record the date, keep going,
      publish everything, fail the job in the last step |
```

2. 「Change the threshold in `daily-batch.yml`'s `SUSPECT_N -ge 2`, not in Python.」の直後に
   1文足す:

```
That line now lives in the **last** step, not the Summarize step: on a morning with a pending
sidecar Summarize does not run at all, so a policy applied there would be blind to every date
the resume path reported.
```

3. `--collect-pending` の非対称を注記する（表の直後）:

```
`--collect-pending` is the exception: it speaks for many dates in one process, so a single exit
code cannot say which one failed. It reports through `systemic_dates` / `suspect_dates` step
outputs plus annotations, and still exits 1 only for a hard fail (retry threshold, older schema).
```

- [ ] **Step 10: 受け入れ基準をすべて走らせる**

```bash
python -m pytest scripts/tests -q
npm run lint
npm run validate
```

Expected: 3つとも PASS

- [ ] **Step 11: コミット**

```bash
git add .github/workflows/daily-batch.yml CLAUDE.md scripts/tests/test_systemic_failure.py
git commit -m "ci: decide the systemic-failure policy where both paths are visible

The SUSPECT_N threshold sat inside the Summarize step, which does not run at
all on a morning with a pending sidecar and never sees the dates the resume
path reports. A policy applied there is blind to half its input, so the whole
resume-path signal added in the previous commit could never fail the job.

Move it to the last step, which reads all four outputs, unions them as a SET
(the threshold counts dates, and the same date arriving twice must not cross it
alone), and decides. The step loses its \`if:\` because a GitHub expression can
neither union two lists nor compare a count — the same reason the threshold was
in shell to begin with.

Collect deliberately gets no rc capture: it exits 0 on a soft verdict, so a
non-zero code from it still means what it always did — stop, a human is needed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review（プラン作成者による確認）

**Spec coverage:**

| spec 節 | 対応タスク |
|---|---|
| §3.1 トリガ1 / トリガ2 / suspect carve-out 共通化 | Task 1 Step 1-4, 12 |
| §3.2 diagnostic 構造化・scope・observation only | Task 1 Step 5-6 |
| §3.3 判定点（両呼び出し元） | Task 1 Step 7, 10-11 / Task 2 Step 3 |
| §3.3.1 `ended` + 日付全体 raw 不在 | Task 2 Step 5-6 |
| §3.4 母数（新規 = pending 非空 / resume = threads 非空） | Task 1 Step 10 / Task 2 Step 3 |
| §3.5 `CollectResult`・soft verdict は exit 0 | Task 2 Step 3, 9 |
| §3.6 annotation 第一・`run_batch_phase` の dict 化 | Task 1 Step 10, 12 / Task 2 Step 3 |
| §3.6.1 `$GITHUB_OUTPUT` 契約（書き手・形式・重複排除） | Task 2 Step 9 |
| §3.7 閾値の集約位置と重複排除 | Task 3 Step 3, 5 |
| §3.8 2トリガ併記の文言 | Task 1 Step 12, 14 / Task 3 Step 5 |
| §4 テスト 0-15 | Task 1 Step 1/5/9/14, Task 2 Step 1/5/8, Task 3 Step 1 |

spec §4 のテスト 2・3（repair で救われた場合は鳴らない / 20 中 1 件 repair 不能なら鳴る）は
Task 1 Step 9 のヘルパ（`assembly_fails_with`）でカバーされる形に含めた。実装時に
`_repair_unusable_results` を経由する版を 1 本足すこと。

**Type consistency:** `_diagnostic()` が返すキー（`scope` / `meeting_id` / `custom_id` /
`reason`）は Task 1・2・3 で同じ綴りを使っている。`CollectResult` のキー
（`hard_fail` / `systemic_dates` / `suspect_dates` / `diagnostics`）も同様。
`_batch_phase_result` のキー（`threads` / `thread_counter` / `completed_meeting_ids` /
`pending` / `summary_attempted` / `publication_blocked` / `diagnostic`）は Task 1 Step 10 で
定義し Step 11 で消費する。**`diagnostic`（単数、run_batch_phase）と `diagnostics`
（複数、CollectResult）は別物**なので取り違えないこと。

**既知の注意点:**

- Task 3 Step 1 のテストは `yaml` を import する。既存の
  `test_the_workflow_tolerates_exactly_these_exit_codes` が既に YAML をパースしているので
  依存は追加不要（同じ方法を使うこと）。
- 最終ステップの `SYSTEMIC=$(... | grep -v '^$' | ...)` は、全部空の朝に `grep` が 1 を返す。
  パイプライン全体の終了ステータスは最後の `tr`（0）なので `bash -e` でも死なない。
  **この block に `set -o pipefail` を足してはいけない**（足すと空の朝に毎回ステップが死ぬ）。

### Gate2（Claude 敵対レビュー）で潰した内容

初版のプランには、そのまま実行すると壊れる欠陥が 6 件あった。記録として残す:

1. **`test_resume.py` を丸ごと見落としていた**（23 呼び出し）。最大の抜け。
2. 壊れるテストの予測が誤り。`_run_batch_phase` ヘルパは戻り値を展開しないので壊れず、
   実際に壊れるのは直接展開している 4 箇所だった。
3. 自作 sidecar フィクスチャの `input_hash` がダミーだったため、`missing_result` を
   狙ったテストが `hash_mismatch` で落ちる（hash 検証が先）。既存 `_correct_hash()` を使う。
4. 同フィクスチャに `retry_count` / `attempts` が無く、`collect_pending_batches` に
   渡すと `KeyError`。既存 `_sidecar_with_one_thread()` を使う。
5. workflow テストの `SUSPECT_N` 断言 2 件（565/569 行）を見落としていた。
6. `main()` のテストがリポジトリの `data/members.json` を書き換えてしまう。

加えて `finally: sys.exit()` が fail-open（書き込み失敗を握り潰して exit 0）だった点、
`summary_attempted` が兄弟カウンタと綴りが違って drift する点を修正した。

**受け入れ条件（#59）の到達性は Gate2 で制御フローを追って検証済み** —
pending の朝: Collect が `::error::` を出しつつ `systemic_dates` を書いて exit 0 →
`set -e` は止まらない → `has_pending=true` で Summarize skip →
`steps.summarize.outputs.*` は空 → **`if:` を外した最終ステップは走る** →
`COLLECT_SYSTEMIC` が非空 → `exit 1`。ジョブが赤くなる。
