# Resume 失敗レジーム実装プラン（#65 / #66 / #44 第3項）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** resume 失敗を RESUBMIT / HOLD / BLOCKED / LOST の4レジームに分類し、決定論的に失敗する再送をゼロにし、恒久ロストを赤くし、pending ゲートを日付単位にする。

**Architecture:** 純粋な policy テーブルを `batch_state.py` に置き、`summarize.py` の `collect_pending_batches` が reason ごとに分岐する。最重要は**順序**: raw 検証（無料）を batch results の取得より前に出すことで、29日後に reason が `results_expired` へ化けて再送が復活する経路を構造的に塞ぐ。全レジームで publish を止めない。

**Tech Stack:** Python 3.12（標準ライブラリ + `anthropic` SDK）、pytest、GitHub Actions YAML。

**Spec:** `docs/design-debate/resume-retry-policy-and-date-scoped-gate/spec.md`

---

## Global Constraints

以下は**全タスクの要件に暗黙に含まれる**。

- **要約層のリクエストは3つの builder 経由でのみ組む**（`grouper.build_grouping_request` / `build_outcome_request` / `summarizer.build_summary_request`）。手組み禁止。`scripts/tests/test_determinism.py` が AST sweep で監視。
- **要約層に sampling params（`temperature` / `top_p` / `top_k`）を送らない。** `claude-sonnet-5` は 400 を返す（#51）。`conftest.py` の fake が再現する。
- **`thinking: {"type": "disabled"}`** を全要約/grouping/outcome リクエストに付ける。
- **`SCHEMA_VERSION` は bump しない。** 本プランは `compute_input_hash` に食わせる param 集合を変えない。bump は `git ls-files data/pending-batches/` が空のときにしか land できない（`is_current_schema` が等値比較）。
- **sidecar の新フィールドは必ず `sidecar.get(...)` で読む。** 既存 sidecar（フィールド無し）が読めること。
- **コード内コメントは英語。** ユーザー向けテキストは日本語（本リポジトリでは annotation は英語のまま — 既存に合わせる）。
- **exit code の Python 側定数と `daily-batch.yml` の分岐は同一コミットで動かす。** `test_systemic_failure.py::test_the_workflow_tolerates_exactly_these_exit_codes` が YAML をパースする。
- **`FAIL_DATES="$FAIL_DATES$SUSPECT"` というリテラルを YAML から消さない。** `test_systemic_failure.py:886-888` が pin しており、意図的に脆い保険。
- 受け入れ: `python -m pytest scripts/tests` && `npm run lint` && `npm run validate`。
- 各タスク終了時点で**全テストが緑**であること。タスク境界で赤を残さない。

---

## File Structure

| ファイル | 責務 | 変更 |
|---|---|---|
| `scripts/pipeline/batch_state.py` | sidecar の永続化と**純粋な判断**（ネットワーク・クライアント無し） | policy テーブル、`failure_policy`、`mark_blocked` / `clear_blocked` |
| `scripts/summarize.py` | パイプライン本体。`collect_pending_batches` が resume を統括 | 検証の前倒し、`_apply_failure_policy`、`_record_held_sidecar`、reason 伝播、abandon の error 化、hard_fail 全廃 |
| `scripts/check_stuck_batches.py` | 2日超の sidecar を1行ずつ出力（`notify-stuck-batch` が消費） | held の表示 |
| `.github/workflows/daily-batch.yml` | 毎朝の実行順序と**失敗ポリシーの唯一の置き場所** | ゲート日付単位化、outputs、最終ステップ、運用ヘルプ文 |
| `CLAUDE.md` | exit code 契約と運用回避策 | 実態に合わせる |
| `scripts/tests/test_batch_state.py` | `batch_state.py` の単体 | policy 単体 + 網羅（T6/T7） |
| `scripts/tests/test_resume.py` | resume 経路の統合 | 既存5件の更新 + 新規（T1, T1b, T2, T2b, T3, T8, T9） |
| `scripts/tests/test_systemic_failure.py` | YAML をパースして workflow の配線を固定 | T4, T5 |

---

## Task 1: policy テーブル（`batch_state.py`）

**Files:**
- Modify: `scripts/pipeline/batch_state.py`（末尾、`is_abandonable` の後）
- Test: `scripts/tests/test_batch_state.py`

**Interfaces:**
- Consumes: なし（このタスクが起点）
- Produces:
  - `bs.RESUBMIT: str` = `"resubmit"` / `bs.HOLD: str` = `"hold"` / `bs.BLOCKED: str` = `"blocked"`
  - `bs.FAILURE_POLICY: dict[str, str]`
  - `bs.failure_policy(reason: str) -> str`
  - `bs.mark_blocked(sidecar: dict, reason: str, at: str, meeting_id: str | None = None, custom_id: str | None = None) -> bool`（True = ディスク上の内容が変わる）
  - `bs.clear_blocked(sidecar: dict) -> None`

- [ ] **Step 1: 失敗するテストを書く**

`scripts/tests/test_batch_state.py` の末尾に追記:

```python
# --- Failure regimes (#65) -------------------------------------------------

def test_failure_policy_classifies_the_three_regimes():
    assert bs.failure_policy("canceled") == bs.RESUBMIT
    assert bs.failure_policy("missing_result") == bs.RESUBMIT
    assert bs.failure_policy("speech_gap") == bs.HOLD
    assert bs.failure_policy("raw_date_missing") == bs.HOLD
    assert bs.failure_policy("hash_mismatch") == bs.BLOCKED
    assert bs.failure_policy("retry_exhausted") == bs.BLOCKED


def test_unknown_reason_is_blocked_not_resubmit():
    """The safe default is the one that never charges and never deletes data.
    Defaulting to RESUBMIT would silently bill a batch for a failure nobody has
    reasoned about; defaulting to an exception would take the publish down with
    it (Collect runs under `set -e`)."""
    assert bs.failure_policy("something_nobody_wrote_yet") == bs.BLOCKED


def test_terminal_failures_are_all_resubmittable():
    """TERMINAL_FAILURES are batch-side outcomes; a fresh submission is a new
    roll. If one ever needs a different regime it must be moved deliberately."""
    for status in bs.TERMINAL_FAILURES:
        assert bs.failure_policy(status) == bs.RESUBMIT, status


def test_assemble_failed_is_not_a_policy_reason():
    """The catch-all was what let hash_mismatch be treated as retryable. The
    detailed reason must reach the policy instead, so the old spelling must not
    quietly resolve to anything."""
    assert "assemble_failed" not in bs.FAILURE_POLICY


def test_mark_blocked_keeps_the_first_since_and_reports_no_change():
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    assert bs.mark_blocked(sc, "hash_mismatch", "2026-08-08T00:00:00Z",
                           "M1", "s_abc_00") is True
    assert sc["blocked"]["since"] == "2026-08-08T00:00:00Z"
    # Second morning, same finding: `since` must not advance, and the caller must
    # be told nothing changed so it does not run an empty `git commit` (which
    # reports itself as "the in-flight batch will be orphaned").
    assert bs.mark_blocked(sc, "hash_mismatch", "2026-08-09T00:00:00Z",
                           "M1", "s_abc_00") is False
    assert sc["blocked"]["since"] == "2026-08-08T00:00:00Z"


def test_mark_blocked_reports_a_change_when_the_reason_changes():
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    bs.mark_blocked(sc, "hash_mismatch", "2026-08-08T00:00:00Z")
    assert bs.mark_blocked(sc, "retry_exhausted", "2026-08-09T00:00:00Z") is True


def test_clear_blocked_is_safe_on_a_sidecar_that_was_never_blocked():
    sc = bs.new_sidecar("2026-05-14", "claude-x")
    bs.clear_blocked(sc)
    assert "blocked" not in sc
```

- [ ] **Step 2: 落ちることを確認**

Run: `python -m pytest scripts/tests/test_batch_state.py -k "failure_policy or blocked or terminal_failures or assemble_failed" -v`
Expected: FAIL — `AttributeError: module 'pipeline.batch_state' has no attribute 'failure_policy'`

- [ ] **Step 3: 実装**

`scripts/pipeline/batch_state.py` の `is_abandonable` の後に追記:

```python
# --- Failure regimes -------------------------------------------------------
# What a resume does when it cannot finish, answering one question: would doing
# the same thing tomorrow work? Before #65 there was no such question — one
# regime ("resubmit up to 3 times, then stop everything") was applied to every
# reason, so a hash_mismatch was billed three times for a rebuild that fails
# identically, and a missing raw file burned a retry slot without resubmitting
# anything.
RESUBMIT = "resubmit"   # the batch failed; a fresh submission is a new roll
HOLD     = "hold"       # local state is missing; a later run may restore it
BLOCKED  = "blocked"    # deterministic given current inputs; a human must decide

# Keyed by every ``reason`` that can reach _apply_failure_policy: the terminal
# batch statuses, the results-expiry marker, every reason _diagnostic produces,
# and the two states summarize.py names directly (stale_schema,
# retry_exhausted). test_failure_policy_covers_every_reason_in_summarize
# derives that set from the source and fails if an entry is missing — the
# default below is a runtime net, NOT a licence to skip this table.
FAILURE_POLICY = {
    # The batch itself ended badly. The manifest is still valid.
    "canceled":            RESUBMIT,
    "expired":             RESUBMIT,
    "results_expired":     RESUBMIT,
    # A result never arrived, or arrived unusable and repair could not fix it.
    # A fresh batch genuinely may answer.
    "missing_result":      RESUBMIT,
    # The result parsed but would not assemble. Least confident entry: a new
    # roll may well assemble, so we pay for one. If this becomes common, move it
    # to BLOCKED — do NOT raise HARD_FAIL_RETRIES to paper over it.
    "thread_build_failed": RESUBMIT,
    # Raw is not on disk this run. Nothing to resubmit and nothing is wrong with
    # the batch; the next fetch may bring it back.
    "raw_missing":         HOLD,
    "raw_date_missing":    HOLD,
    "speech_gap":          HOLD,
    # The request we would build today is not the request we submitted, so a
    # resubmit reproduces this exactly, every morning, forever.
    "hash_mismatch":       BLOCKED,
    # An older revision's hashes: same shape, different cause.
    "stale_schema":        BLOCKED,
    # Three genuine resubmits have failed. Stop paying; a human decides.
    "retry_exhausted":     BLOCKED,
}


def failure_policy(reason: str) -> str:
    """Which regime a failure reason falls into.

    Unknown reasons are BLOCKED, not RESUBMIT and not an exception. Every
    default is wrong in some direction; this one is wrong safely. BLOCKED never
    charges and never deletes data — it holds the sidecar and reds the run until
    a human looks, and since the pending gate is per-date the held date no
    longer stops the site. RESUBMIT would silently bill for a failure nobody has
    reasoned about; raising would abort Collect under ``set -e`` and take the
    whole morning's publish with it, which is the amplification #52 was about.
    """
    return FAILURE_POLICY.get(reason, BLOCKED)


def mark_blocked(sidecar: dict, reason: str, at: str,
                 meeting_id: Optional[str] = None,
                 custom_id: Optional[str] = None) -> bool:
    """Record that this sidecar needs a human. Returns True iff the content changed.

    ``since`` is sticky (first write wins) so an annotation can say how long this
    has been sitting there — the number that decides rescue-vs-discard, given
    batch results expire ~29 days after submission.

    The return value exists for one reason: the caller must not run
    ``_git_commit_sidecar`` on an unchanged file. An empty ``git commit`` exits
    non-zero, which that helper reports as ``::error:: the in-flight batch will
    be orphaned`` — a false alarm every morning from the second one on.

    Forensics only. The morning-to-morning decision is re-derived for free from
    raw each run (see verify_manifest_against_raw), so this must never become the
    primary source of control flow: a sidecar whose raw is restored has to
    recover on its own.
    """
    existing = sidecar.get("blocked") or {}
    new = {
        "reason": reason,
        "since": existing.get("since", at),
        "meeting_id": meeting_id,
        "custom_id": custom_id,
    }
    changed = existing != new
    sidecar["blocked"] = new
    return changed


def clear_blocked(sidecar: dict) -> None:
    """Drop the blocked marker — the sidecar is moving back to a live regime."""
    sidecar.pop("blocked", None)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python -m pytest scripts/tests/test_batch_state.py -v`
Expected: PASS（既存も含め全件）

- [ ] **Step 5: T6 — policy 網羅の静的テストを書く**

`scripts/tests/test_batch_state.py` の末尾に追記:

```python
def test_failure_policy_covers_every_reason_in_summarize():
    """The allowlist must be derived, not remembered.

    Three vocabularies reach the policy and they live in different files:
    _diagnostic's first argument (assembly's observations), the reasons
    summarize.py passes literally (results_expired, stale_schema,
    retry_exhausted), and TERMINAL_FAILURES. A table that covers only the first
    is an allowlist that lies — exactly the failure CLAUDE.md warns about with
    "a forbid-list test approves what it can't recognize".

    Break-to-check: delete any single FAILURE_POLICY entry and this must fail.
    """
    import ast
    import os

    src_path = os.path.join(os.path.dirname(__file__), "..", "summarize.py")
    with open(src_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    reasons = set(bs.TERMINAL_FAILURES)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name == "_diagnostic" and node.args:
            first = node.args[0]
            # Non-literal calls — _diagnostic(reason) where reason came from a
            # policy branch — are skipped, not rejected. Every such value
            # originates from a literal _diagnostic elsewhere or from
            # TERMINAL_FAILURES, so the derived set stays complete; rejecting
            # them outright would just forbid the indirection the collect loop
            # needs. The floor assertion below is what keeps skipping honest:
            # if the sweep ever stops finding literals it fails instead of
            # approving an empty set.
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                reasons.add(first.value)
        # The reasons summarize.py hands the policy directly.
        if name in ("_apply_failure_policy", "mark_blocked"):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    reasons.add(arg.value)

    # Sanity floor: a sweep that finds nothing approves everything. These names
    # are the ones the collect loop cannot work without, and the count catches a
    # refactor that turned the rest into indirection.
    assert {"hash_mismatch", "missing_result", "speech_gap", "raw_missing",
            "raw_date_missing", "thread_build_failed"} <= reasons, (
        "the AST sweep lost reasons it used to find — it is not looking where "
        "it thinks, or _diagnostic calls stopped being literal"
    )
    missing = sorted(reasons - set(bs.FAILURE_POLICY))
    assert not missing, f"reasons with no explicit policy: {missing}"
```

- [ ] **Step 6: 落ちることを確認してから通す**

Run: `python -m pytest scripts/tests/test_batch_state.py::test_failure_policy_covers_every_reason_in_summarize -v`
Expected: PASS（この時点の `summarize.py` は `_diagnostic` の6 reason + `TERMINAL_FAILURES` のみを出すので、Step 3 の表がすべて覆う）

**break-to-check（必須）**: `FAILURE_POLICY` から `"speech_gap"` の行を一時的に削除して同テストを実行し、FAIL することを確認してから戻す。

- [ ] **Step 7: コミット**

```bash
git add scripts/pipeline/batch_state.py scripts/tests/test_batch_state.py
git commit -m "feat: classify resume failures into resubmit/hold/blocked regimes"
```

---

## Task 2: 検証を results 取得より前に出す

**Files:**
- Modify: `scripts/summarize.py`（`assemble_from_manifest` の直前に新関数、`assemble_from_manifest` 本体、`collect_pending_batches` の順序）
- Test: `scripts/tests/test_resume.py`

**Interfaces:**
- Consumes: Task 1 の `bs.failure_policy`（このタスクではまだ使わない）
- Produces: `summarize.verify_manifest_against_raw(sidecar: dict, meetings_by_id: dict) -> Optional[dict]` — 問題があれば `_diagnostic` 形式の dict、無ければ `None`

**このタスクが #65 の本命。** 「再送しない」と決めるだけでは足りない: batch results は約29日で失効し、`fetch_summary_results` は現在 assembly より前にある（`summarize.py:1272-1286`）。検証が fetch の後だと、raw が変わった sidecar は day 29 に観測 reason が `hash_mismatch` から `results_expired`（= 再送してよい）へ**化ける**。

- [ ] **Step 1: 失敗するテストを書く（T1b）**

`scripts/tests/test_resume.py` の末尾に追記:

```python
def test_verification_happens_before_results_are_fetched(fake_client, tmp_path):
    """#65 の時限爆弾。

    Batch results expire ~29 days after submission. If the hash check runs after
    the fetch, then on the morning the results expire the observed reason stops
    being ``hash_mismatch`` and becomes ``results_expired`` — which is legitimately
    retryable — so a full batch is resubmitted and billed, fails identically the
    next morning, and the cycle repeats until the retry threshold. #65 would go
    from "dies in three days" to "dies in ninety", which is worse: it arrives
    after everyone has forgotten.

    Both conditions hold here: raw changed AND results expired. What this task
    can pin is the OBSERVATION — the reason recorded must be hash_mismatch, not
    results_expired, which is only possible if verification ran first. The
    consequence ("...and therefore nothing is submitted") arrives with the policy
    in Task 3 and is pinned by test_hash_mismatch_is_held_and_costs_nothing;
    until then the old _retry_or_hardfail still resubmits here, by design.
    """
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)

    sidecar = _sidecar_with_one_thread("sha256:stale")   # raw/prompt changed
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    b.expired_results.add("b1")          # ...and the results are gone too
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )

    assert result["diagnostics"][0]["reason"] == "hash_mismatch", (
        "a hash mismatch must be observed BEFORE the expired results are; once "
        "the fetch raises first, the same broken sidecar reports the retryable "
        "results_expired instead and gets billed for a doomed resubmit"
    )


def test_verify_manifest_against_raw_returns_none_when_everything_matches():
    sidecar = _sidecar_with_one_thread(_correct_hash())
    assert summarize.verify_manifest_against_raw(sidecar, {"M1": _meeting()}) is None


def test_verify_manifest_against_raw_names_the_first_problem():
    sidecar = _sidecar_with_one_thread("sha256:stale")
    diag = summarize.verify_manifest_against_raw(sidecar, {"M1": _meeting()})
    assert diag["reason"] == "hash_mismatch"
    assert diag["meeting_id"] == "M1"
    assert diag["custom_id"] == "s_abc_00"


def test_verify_manifest_against_raw_reports_a_missing_meeting():
    sidecar = _sidecar_with_one_thread(_correct_hash())
    diag = summarize.verify_manifest_against_raw(sidecar, {})
    assert diag["reason"] == "raw_missing"
    assert diag["scope"] == "meeting"
```

- [ ] **Step 2: 落ちることを確認**

Run: `python -m pytest scripts/tests/test_resume.py -k "verify_manifest or verification_happens" -v`
Expected: FAIL — `AttributeError: module 'summarize' has no attribute 'verify_manifest_against_raw'`、および `test_verification_happens_before_results_are_fetched` は `created_requests` が1件で FAIL

- [ ] **Step 3: `verify_manifest_against_raw` を実装**

`scripts/summarize.py` の `assemble_from_manifest` の直前に追加:

```python
def verify_manifest_against_raw(sidecar: dict,
                                meetings_by_id: Dict[str, dict]) -> Optional[dict]:
    """The free half of assembly: does the manifest still describe requests we
    can rebuild from today's raw? Returns the first problem as a ``_diagnostic``,
    or None if every thread verifies.

    Hoisted out of assemble_from_manifest so the caller can run it BEFORE
    fetching the batch's results. That ordering is not a micro-optimisation, it
    is what makes "never resubmit a doomed batch" hold over time: results expire
    ~29 days after submission, and once ``fetch_summary_results`` raises first,
    the very same broken sidecar reports ``results_expired`` — a retryable
    reason — instead of ``hash_mismatch``. See #65.

    Costs nothing but CPU: no network, no tokens. Uses build_summary_request,
    the one summary-request builder, so the hash it computes is the hash a real
    resume would compute (CLAUDE.md "Summary Layer Invariants" #2).
    """
    model = sidecar["model"]
    for m in sidecar["meetings"]:
        meeting_id = m["meeting_id"]
        meeting = meetings_by_id.get(meeting_id)
        if meeting is None:
            log.error("Resume: raw missing for %s — cannot assemble", meeting_id)
            return _diagnostic("raw_missing", meeting_id)
        raw_lookup = build_speech_lookup(meeting.get("speeches", []))
        for mt in m["threads"]:
            custom_id = mt["custom_id"]
            orders = mt["speechOrders"]
            thread_speeches = [raw_lookup[o] for o in orders if o in raw_lookup]
            if len(thread_speeches) != len(orders):
                log.error("Resume: speechOrder gap in %s/%s", meeting_id, custom_id)
                return _diagnostic("speech_gap", meeting_id, custom_id)
            request = build_summary_request(
                meeting, mt["thread_info"], thread_speeches, custom_id, model,
            )
            if bs.compute_input_hash(request["params"]) != mt["input_hash"]:
                log.error("Resume: input_hash mismatch for %s — raw/prompt changed",
                          custom_id)
                return _diagnostic("hash_mismatch", meeting_id, custom_id)
    return None
```

- [ ] **Step 4: `assemble_from_manifest` を検証済み前提にする**

`assemble_from_manifest` の冒頭（`threads: list = []` の直後）に:

```python
    # Verification first, for the whole manifest. The caller has normally run
    # this already (before fetching results — see verify_manifest_against_raw);
    # repeating it costs a few milliseconds of hashing and keeps this function
    # correct when called directly, e.g. from tests.
    #
    # NOTE this changes which problem is reported when a manifest has more than
    # one: a verification failure on thread 2 now wins over a missing result on
    # thread 1. That is deliberate — the deterministic problem is the one an
    # operator must act on, and reporting the retryable one first is what sent
    # 2026-06-16's investigation to the wrong place.
    verify_diag = verify_manifest_against_raw(sidecar, meetings_by_id)
    if verify_diag is not None:
        return [], False, verify_diag
```

続いて本体のループから、いま重複している検証3つを取り除く。`for mt in manifest_threads:` の中の
`if len(thread_speeches) != len(orders): ...` ブロックと、`request = build_summary_request(...)` +
`if bs.compute_input_hash(...) != mt["input_hash"]: ...` ブロックを削除する。

> **訂正（Gate3 のタスクレビューで判明）**: この行はもともと
> 「`thread_speeches` の算出自体は assemble に必要なので残す」と書いていたが、**事実誤認**だった。
> `assemble_thread` のシグネチャは `(meeting, thread_info, ai_speeches, raw_lookup, members,
> thread_id)`（`summarize.py:448-455`）で、`thread_speeches` を受け取らない。検証専用だったので、
> `orders = mt["speechOrders"]` と `thread_speeches = [...]` の**両方を削除する**。
> 残すと「assemble に必要だから残っている」と誤読される死んだ計算になる。

外側ループの `if meeting is None:` ブロックも削除する（検証済み）。ただし `meeting` の取得は残す:

```python
    for m in sidecar["meetings"]:
        meeting_id = m["meeting_id"]
        meeting = meetings_by_id[meeting_id]      # verified present above
        raw_lookup = build_speech_lookup(meeting.get("speeches", []))
```

- [ ] **Step 5: `collect_pending_batches` の順序を入れ替える**

`meetings_by_id = _load_meetings_for_date(date_str, raw_dir)` と
`if not meetings_by_id:` ブロックの**直後**、`try: results = fetch_summary_results(...)` の**前**に挿入:

```python
        # Verify BEFORE touching results. Both are needed to assemble, but only
        # one of them is free and only one of them changes meaning with age: the
        # batch's results expire ~29 days after submission, and if the fetch
        # raises first, a sidecar whose raw has changed reports the retryable
        # ``results_expired`` instead of the deterministic ``hash_mismatch`` —
        # and gets resubmitted for a rebuild that cannot verify. #65.
        verify_diag = verify_manifest_against_raw(sidecar, meetings_by_id)
        if verify_diag is not None:
            log.error("Resume: manifest does not verify for %s (%s)", date_str,
                      verify_diag["reason"])
            _record_resume_verdict(
                date_str, _resume_summary_attempted(sidecar),
                _existing_thread_count(threads_dir, date_str),
                verify_diag, systemic_dates, suspect_dates, diagnostics,
            )
            if _retry_or_hardfail(client, sidecar, path, "assemble_failed",
                                  raw_dir, model, ci_commit):
                hard_fail = True
            continue
```

> このステップでは `_retry_or_hardfail`（旧関数）をそのまま呼ぶ。レジーム分岐は Task 3 で入る。
> `api_stats=None` で `_record_resume_verdict` を呼ぶので、rejection 行は出ず assembly 行だけが出る
> ── 既存テスト `test_resume_omits_rejection_line_when_results_are_usable_but_hash_mismatches` が
> まさにそれを要求しているので、このタスクの時点で緑のまま。

- [ ] **Step 6: テストが通ることを確認**

Run: `python -m pytest scripts/tests -v`
Expected: PASS（全件）。特に:
- `test_verification_happens_before_results_are_fetched` PASS
- `test_repair_refuses_to_reissue_on_hash_mismatch` PASS（repair に到達しなくなるが `create_calls == []` は成立。より強く成立する）
- `test_assembly_reports_a_hash_mismatch_before_it_looks_for_results` PASS

- [ ] **Step 7: `test_repair_refuses_to_reissue_on_hash_mismatch` の docstring を実態に合わせる**

アサーションは変えない。docstring を差し替える:

```python
def test_repair_refuses_to_reissue_on_hash_mismatch(fake_client, tmp_path):
    """Raw revised since submission: nothing may be re-issued.

    Since #65 the collect path never even reaches repair in this state —
    verify_manifest_against_raw rejects the manifest before the results are
    fetched. The hash check inside _repair_unusable_results is kept as defence in
    depth (it guards direct callers), and this test now pins the outer guarantee:
    a revised raw costs zero synchronous calls.
    """
```

- [ ] **Step 8: コミット**

```bash
git add scripts/summarize.py scripts/tests/test_resume.py
git commit -m "fix: verify the manifest before fetching results so a doomed resume cannot age into a retryable one"
```

---

## Task 3: `_apply_failure_policy` と held シグナル

**Files:**
- Modify: `scripts/summarize.py`（`_record_resume_verdict` の後に `_record_held_sidecar`、`_retry_or_hardfail` を置換、`collect_pending_batches` の3つの呼び出し箇所と返却値）
- Test: `scripts/tests/test_resume.py`

**Interfaces:**
- Consumes: `bs.failure_policy` / `bs.mark_blocked` / `bs.clear_blocked`（Task 1）、`verify_manifest_against_raw`（Task 2）
- Produces:
  - `summarize._record_held_sidecar(date_str: str, sidecar: dict, diagnostic: dict, held_dates: list, diagnostics: list) -> None`
  - `summarize._apply_failure_policy(client, sidecar: dict, path: str, reason: str, diagnostic: Optional[dict], raw_dir: str, model: str, ci_commit: bool) -> str` — `"resubmitted" | "held" | "blocked"`
  - `collect_pending_batches` の返却 dict に `"held_dates": list` が加わる

- [ ] **Step 1: 失敗するテストを書く（T1 / T2 / T2b / T9）**

`scripts/tests/test_resume.py` の末尾に追記:

```python
def test_hash_mismatch_is_held_and_costs_nothing(fake_client, tmp_path, monkeypatch, capsys):
    """T1 — the whole point of #65. A resubmit built from the same raw that just
    failed verification fails identically, so it must not happen at all: no
    batch, no retry slot, no repair call. The date still reds the run, through
    held_dates rather than systemic_dates."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    sidecar = _sidecar_with_one_thread("sha256:stale")
    usable = {"s_abc_00": {"speeches": [{"speechOrder": 1, "tension": "確認",
              "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
              "commitments": []}}
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[sidecar], results=usable, existing_threads=3)

    assert fake_client.messages.batches.created_requests == []   # no resubmit
    assert fake_client.messages.create_calls == []               # no repair
    assert result["held_dates"] == ["2026-05-14"]
    # Held is its own axis. Reusing the systemic/suspect verdict would drop a
    # 1-of-1 failure on an already-published date to a WARNING (see
    # publication_blocked_verdict), i.e. exactly not-red, and would also let two
    # held dates trip the workflow's SUSPECT_N >= 2 threshold with the wrong text.
    assert result["systemic_dates"] == []
    assert result["suspect_dates"] == []
    assert result["hard_fail"] is False

    kept = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert kept is not None
    assert kept["retry_count"] == 0
    assert len(kept["attempts"]) == 1
    assert kept["blocked"]["reason"] == "hash_mismatch"
    assert kept["blocked"]["custom_id"] == "s_abc_00"

    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert len(errors) == 1
    assert "hash_mismatch" in errors[0]
    assert "not resubmitted" in errors[0].lower()


def test_missing_result_still_resubmits(fake_client, tmp_path, monkeypatch):
    """T2 — the other side of T1. If this ever goes green while T1 does too, the
    policy has collapsed into "never resubmit", which loses recoverable dates."""
    b = fake_client.messages.batches
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          results={}, existing_threads=0)
    assert len(b.created_requests) == 1
    assert result["held_dates"] == []
    sc = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert sc["retry_count"] == 1


def test_an_expired_batch_whose_raw_also_changed_submits_nothing(fake_client, tmp_path):
    """T1b's consequence half, now that the policy exists. This is the exact
    2-condition state that made #65 survive its own fix: on day 29 the fetch
    would raise first, the reason would become results_expired, and a doomed
    rebuild would be billed. Verification runs first (Task 2) AND the reason is
    BLOCKED (Task 3) — either one alone leaves the hole open."""
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"),
                    _sidecar_with_one_thread("sha256:stale"))
    b = fake_client.messages.batches
    b.statuses["b1"] = "ended"
    b.expired_results.add("b1")
    b.next_id = "msgbatch_resub_1"
    b.statuses["msgbatch_resub_1"] = "in_progress"

    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert b.created_requests == []
    assert result["held_dates"] == ["2026-05-14"]
    sc = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert sc["retry_count"] == 0


def test_a_manifest_meeting_absent_from_raw_holds(fake_client, tmp_path, monkeypatch):
    """T2b, meeting scope. raw_missing is the only meeting-scoped HOLD that
    reaches the policy, and neither the speech_gap nor the raw_date_missing test
    exercises it — wire it to RESUBMIT by mistake and both of those stay green."""
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["meetings"][0]["meeting_id"] = "M2"      # raw on disk only has M1
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[sidecar], results={}, existing_threads=0)
    assert fake_client.messages.batches.created_requests == []
    assert result["held_dates"] == ["2026-05-14"]
    assert result["diagnostics"][0]["reason"] == "raw_missing"
    sc = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert sc["retry_count"] == 0


def test_a_speech_order_gap_holds_without_spending_a_retry(fake_client, tmp_path, monkeypatch):
    """T2b — the defect that came out of the same root: _retry_or_hardfail called
    record_terminal BEFORE discovering it could not rebuild, so three raw-less
    mornings pushed a perfectly good batch to the hard-fail threshold having
    resubmitted nothing."""
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["meetings"][0]["threads"][0]["speechOrders"] = [1, 99]   # 99 not in raw
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[sidecar], results={}, existing_threads=0)
    assert fake_client.messages.batches.created_requests == []
    assert result["held_dates"] == ["2026-05-14"]
    sc = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert sc["retry_count"] == 0
    # HOLD does not mark the sidecar: nothing is wrong with it, the raw is just
    # not here yet.
    assert "blocked" not in sc


def test_a_second_blocked_morning_does_not_commit_an_unchanged_sidecar(
        fake_client, tmp_path, monkeypatch):
    """T9 — _git_commit_sidecar turns a no-op `git commit` (exit 1) into
    "::error:: the in-flight batch will be orphaned". Committing an unchanged
    file every morning would fire that false alarm forever."""
    commits = []
    monkeypatch.setattr(summarize, "_git_commit_sidecar",
                        lambda path, date_str: commits.append(date_str))
    sidecar = _sidecar_with_one_thread("sha256:stale")
    sidecar["blocked"] = {"reason": "hash_mismatch", "since": "2026-08-01T00:00:00Z",
                          "meeting_id": "M1", "custom_id": "s_abc_00"}
    pending_dir = str(tmp_path / "pending")
    raw_dir = str(tmp_path / "raw")
    os.makedirs(raw_dir)
    import json as _json
    with open(os.path.join(raw_dir, "ndl-2026-05-14.json"), "w", encoding="utf-8") as f:
        _json.dump({"meetings": [_meeting()]}, f, ensure_ascii=False)
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    fake_client.messages.batches.statuses["b1"] = "ended"

    summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"), raw_dir=raw_dir,
        budget_seconds=0, poll_seconds=0, ci_commit=True,
    )
    assert commits == []
```

- [ ] **Step 2: 落ちることを確認**

Run: `python -m pytest scripts/tests/test_resume.py -k "held or resubmits or speech_order_gap or second_blocked" -v`
Expected: FAIL — `KeyError: 'held_dates'`

- [ ] **Step 3: `_record_held_sidecar` を実装**

`_record_resume_verdict` の直後に追加:

```python
def _record_held_sidecar(date_str: str, sidecar: dict, diagnostic: dict,
                         held_dates: list, diagnostics: list) -> None:
    """Report a sidecar that is waiting on a human or on restored local state.

    Deliberately NOT ``_record_resume_verdict``. That one grades how far a
    failure spread — one meeting on an already-published date is weak evidence
    and comes out as a *warning* — which is the right question for "did today's
    work reach the site" and the wrong one here. A held sidecar is not weak
    evidence of anything: it is a request for a decision, and it is red at one
    date. Mixing the two also double-counted a date into ``suspect_dates``,
    where two of them would trip the workflow's SUSPECT_N >= 2 threshold under a
    message that does not describe what happened.

    The text must survive being read half-awake, so it says what was NOT done
    (no resubmit, no retry spent) as loudly as what was observed, and it offers
    causes in likelihood order without asserting one. It must never print a bare
    `git rm`: raw lives only on the runner, and past the lookback window a
    removed sidecar is not re-summarised — it is a permanent loss dressed up as
    a fix.
    """
    held_dates.append(date_str)
    diagnostics.append({**diagnostic, "date": date_str})

    reason = diagnostic.get("reason", "unknown")
    attempt = (sidecar.get("attempts") or [{}])[-1]
    submitted = attempt.get("submitted_at", "unknown")
    blocked = sidecar.get("blocked") or {}
    parts = [
        f"{date_str}: resume held — {reason}",
        f"meeting={diagnostic.get('meeting_id')} custom_id={diagnostic.get('custom_id')}",
        f"batch={attempt.get('batch_id')} submitted={submitted}",
        f"sidecar=data/pending-batches/{date_str}.json",
    ]
    if reason == "retry_exhausted":
        parts.append(
            f"three resubmits have failed ({sidecar.get('retry_count')} retries "
            f"spent); no further batch will be sent")
    elif bs.failure_policy(reason) == bs.RESUBMIT:
        # A retryable reason that still ended up here can only mean the rebuild
        # found no usable raw. Saying "rebuilding reproduces this exactly" would
        # be false — the batch is retryable and the next fetch may unblock it.
        parts.append(
            "NOT resubmitted and no retry spent: this reason IS retryable, but "
            "the requests could not be rebuilt from the raw on disk this run")
    else:
        parts.append(
            "NOT resubmitted and no retry spent: rebuilding from today's raw "
            "reproduces this failure exactly")
    # Only claim a history that belongs to THIS finding. A sidecar blocked last
    # week on hash_mismatch, whose raw then vanished, is reported today as
    # raw_date_missing — printing the old `since` next to the new reason invents
    # a story and sends the reader to look at raw revisions that are not the
    # problem.
    if blocked.get("since") and blocked.get("reason") == reason:
        parts.append(f"held since {blocked['since']}")
    # Every BLOCKED sidecar needs the clock, not just the hash ones: the results
    # expire on the same schedule regardless of why it is stuck, and deferring
    # the decision is how a held date becomes a lost one.
    if sidecar.get("attempts"):
        age = bs.age_days(sidecar, _utcnow_iso())
        parts.append(
            f"submitted about {age:.0f} day(s) ago; batch results are retained "
            f"roughly 29 days, so this decision has an expiry")
    if reason == "hash_mismatch":
        parts.append(
            "the request rebuilt from today's raw is not the one this batch was "
            "submitted with. Likely causes, in order: (1) compute_input_hash's "
            "param set changed without a SCHEMA_VERSION bump; (2) the raw was "
            "re-fetched and differs. This is not an API rejection")
        parts.append(
            "to act: confirm the date's raw is still re-fetchable inside the "
            "lookback window and secure it BEFORE removing "
            f"data/pending-batches/{date_str}.json — outside the window a removed "
            "sidecar is not re-summarised. Reverting the change that moved the "
            "hash lets the next run collect normally")
    elif reason in ("raw_missing", "raw_date_missing", "speech_gap"):
        parts.append(
            "the batch is fine; this date's raw is not on disk this run. A later "
            "run that re-fetches it collects normally")
    log.error("Resume held: %s (%s)", date_str, reason)
    _annotate("error", " — ".join(parts))
```

- [ ] **Step 4: `_retry_or_hardfail` を `_apply_failure_policy` に置き換える**

`_retry_or_hardfail` の定義全体を次で置き換える:

```python
def _apply_failure_policy(client, sidecar: dict, path: str, reason: str,
                          diagnostic: Optional[dict], raw_dir: str, model: str,
                          ci_commit: bool) -> str:
    """Apply the regime this failure falls into. Returns "resubmitted" | "held"
    | "blocked".

    Replaces _retry_or_hardfail, which applied ONE regime to every reason: it
    resubmitted a hash_mismatch three times — each a full batch's charge for a
    rebuild that cannot verify — and it called record_terminal BEFORE finding out
    whether it could rebuild at all, so a missing raw file burned a retry slot
    without sending anything (#65).

    The order below is the fix, not a style choice: the policy is consulted
    first, and only the RESUBMIT branch is allowed to touch retry_count.
    """
    policy = bs.failure_policy(reason)
    diagnostic = diagnostic or _diagnostic(reason)

    if policy == bs.BLOCKED:
        changed = bs.mark_blocked(sidecar, reason, _utcnow_iso(),
                                  diagnostic.get("meeting_id"),
                                  diagnostic.get("custom_id"))
        if changed:
            bs.save_sidecar(path, sidecar)
            if ci_commit:
                _git_commit_sidecar(path, sidecar["date"])
        return "blocked"

    if policy == bs.HOLD:
        # No write, no retry spent, nothing submitted. The state this needed may
        # simply not have been fetched yet, and the batch is untouched.
        return "held"

    # Rebuild BEFORE counting. The old order counted first and discovered it
    # could not rebuild afterwards, so a morning with no raw on disk spent a
    # retry slot having submitted nothing — three of those and a healthy batch
    # is a permanent human-decision case. This branch is reachable with raw
    # absent because the terminal-status check runs before raw is even loaded.
    # record_terminal is idempotent per attempt, so deferring it is safe.
    meetings_by_id = _load_meetings_for_date(sidecar["date"], raw_dir)
    requests = _rebuild_requests_from_manifest(sidecar, meetings_by_id, model)
    if requests is None:
        log.error("Resume: cannot rebuild %s for resubmit (raw missing/gap) — holding",
                  sidecar["date"])
        return "held"

    bs.record_terminal(sidecar, reason, _utcnow_iso())
    if bs.should_hard_fail(sidecar):
        # Three genuine resubmits have failed. Stop paying — but do NOT take the
        # publish down: since the pending gate is per-date, other dates can
        # still reach the site (#44/#52).
        return _apply_failure_policy(client, sidecar, path, "retry_exhausted",
                                     diagnostic, raw_dir, model, ci_commit)
    bs.clear_blocked(sidecar)
    new_batch_id = submit_summary_batch(client, requests)
    bs.add_attempt(sidecar, new_batch_id, _utcnow_iso())
    bs.save_sidecar(path, sidecar)
    if ci_commit:
        _git_commit_sidecar(path, sidecar["date"])
    log.warning("Resubmitted %s as %s after %s (retry %d)",
                sidecar["date"], new_batch_id, reason, sidecar["retry_count"])
    return "resubmitted"
```

- [ ] **Step 5: `collect_pending_batches` を配線し直す**

(a) 冒頭の集計リストに追加:

```python
    held_dates: list = []
```

(b) Task 2 で入れた verify 分岐を差し替える:

```python
        verify_diag = verify_manifest_against_raw(sidecar, meetings_by_id)
        if verify_diag is not None:
            outcome = _apply_failure_policy(
                client, sidecar, path, verify_diag["reason"], verify_diag,
                raw_dir, model, ci_commit)
            if outcome in ("held", "blocked"):
                _record_held_sidecar(date_str, sidecar, verify_diag,
                                     held_dates, diagnostics)
            continue
```

(c) `TERMINAL_FAILURES` 分岐:

```python
            if batch.processing_status in bs.TERMINAL_FAILURES:
                outcome = _apply_failure_policy(
                    client, sidecar, path, batch.processing_status, None,
                    raw_dir, model, ci_commit)
                if outcome in ("held", "blocked"):
                    _record_held_sidecar(
                        date_str, sidecar,
                        _diagnostic(batch.processing_status), held_dates, diagnostics)
```

(d) `results_expired` 分岐:

```python
            outcome = _apply_failure_policy(
                client, sidecar, path, "results_expired", None,
                raw_dir, model, ci_commit)
            if outcome in ("held", "blocked"):
                _record_held_sidecar(date_str, sidecar,
                                     _diagnostic("results_expired"),
                                     held_dates, diagnostics)
            continue
```

(e) assembly 失敗分岐（reason を実際に渡す）:

```python
        if not ok:
            reason = (diagnostic or {}).get("reason", "unknown")
            log.error("Resume: assembly incomplete for %s (%s)", date_str, reason)
            outcome = _apply_failure_policy(
                client, sidecar, path, reason, diagnostic,
                raw_dir, model, ci_commit)
            if outcome in ("held", "blocked"):
                _record_held_sidecar(date_str, sidecar, diagnostic or _diagnostic(reason),
                                     held_dates, diagnostics)
            else:
                # Still a publication outcome: summary requests went out for this
                # date and nothing reached the site.
                _record_resume_verdict(
                    date_str, api_stats["attempted"],
                    _existing_thread_count(threads_dir, date_str),
                    diagnostic, systemic_dates, suspect_dates, diagnostics,
                    api_stats=api_stats,
                )
            continue
```

(f) 返却 dict に `"held_dates": held_dates,` を追加。

(g) `collect_pending_batches` の docstring の返却値説明に `held_dates` を追記。

- [ ] **Step 6: 既存テストを新しい契約に合わせる**

`test_resume_omits_rejection_line_when_results_are_usable_but_hash_mismatches` を書き換える:

```python
def test_a_hash_mismatch_is_reported_as_held_not_as_a_publication_verdict(
        fake_client, tmp_path, monkeypatch, capsys):
    """Was: "omits the rejection line when results are usable but the hash
    mismatches". The distinction it protected still matters — a raw-side problem
    must not be reported as an API rejection — but since #65 the whole date
    leaves through held_dates instead of systemic_dates, and the results are
    never fetched, so there is no rejection evidence to omit in the first place.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread("sha256:deadbeef")],
                          results={"s_abc_00": {"speeches": [], "commitments": []}},
                          existing_threads=0)
    assert result["held_dates"] == ["2026-05-14"]
    assert result["systemic_dates"] == []
    assert result["suspect_dates"] == []
    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert len(errors) == 1
    assert "hash_mismatch" in errors[0]
    assert "produced no usable summary" not in errors[0]
```

`test_resume_flags_a_finished_batch_whose_date_lost_its_raw` の末尾3行を差し替える
（この経路は `raw_date_missing` = HOLD になった）:

```python
    assert result["held_dates"] == ["2026-05-14"]
    assert result["systemic_dates"] == []
    assert result["diagnostics"][0]["reason"] == "raw_date_missing"
    assert result["diagnostics"][0]["scope"] == "date"
```

同テストの docstring 末尾に一文追加:

```
    Reported through held_dates since #65: nothing is wrong with the batch and
    nothing was resubmitted, so calling it a publication verdict would charge
    meetings that were never examined.
```

`collect_pending_batches` の raw-missing（若い sidecar）分岐で `_record_resume_verdict` を
呼んでいる箇所を `_record_held_sidecar` に差し替える:

```python
            else:
                log.error("Resume: no raw for %s (outside window?) — keeping sidecar",
                          date_str)
                _record_held_sidecar(date_str, sidecar,
                                     _diagnostic("raw_date_missing"),
                                     held_dates, diagnostics)
```

- [ ] **Step 7: テストが通ることを確認**

Run: `python -m pytest scripts/tests -v`
Expected: PASS。`test_collect_hard_fails_at_retry_threshold` と
`test_collect_refuses_sidecar_from_an_older_schema` はまだ `hard_fail is True` のままで
**PASS する**（Task 4 で変わる。ここで壊さないこと）。

- [ ] **Step 8: break-to-check**

`FAILURE_POLICY["hash_mismatch"]` を一時的に `RESUBMIT` に変え、
`test_hash_mismatch_is_held_and_costs_nothing` が FAIL することを確認してから戻す。

- [ ] **Step 9: コミット**

```bash
git add scripts/summarize.py scripts/tests/test_resume.py
git commit -m "fix: stop resubmitting a resume that cannot verify, and stop spending retries on a missing raw"
```

---

## Task 4: stale-schema と retry 枯渇を BLOCKED にする（hard_fail 全廃）

**Files:**
- Modify: `scripts/summarize.py`（`collect_pending_batches` のループ先頭2分岐、`main()` の exit コメント）
- Test: `scripts/tests/test_resume.py`

**Interfaces:**
- Consumes: Task 3 の `_apply_failure_policy` / `_record_held_sidecar` / `held_dates`
- Produces: `collect_pending_batches` の `"hard_fail"` は crash 以外 False

**hard_fail の地点は2つある。** `_apply_failure_policy` 内の閾値判定（Task 3 で対処済み）と、
**ループ先頭の `bs.should_hard_fail(sidecar)`（`summarize.py:1209`）**。片方だけ直すと、
閾値到達 sidecar は翌朝この先頭チェックで exit 1 に戻る。

- [ ] **Step 1: 失敗するテストを書く（T2c / T2d）**

既存 `test_collect_hard_fails_at_retry_threshold` を次で**置き換える**:

```python
def test_a_retry_exhausted_sidecar_is_held_not_a_hard_fail(fake_client, tmp_path):
    """T2d, entry path. Three genuine resubmits have failed and a human is needed
    — but Collect exits 1 under `set -e`, which skips summarize/commit/push for
    every OTHER date too. That was tolerable while one sidecar skipped Summarize
    anyway; with the per-date gate it is the #52 amplification again."""
    pending_dir = str(tmp_path / "pending")
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["retry_count"] = 3
    bs.save_sidecar(os.path.join(pending_dir, "2026-05-14.json"), sidecar)
    result = summarize.collect_pending_batches(
        fake_client, members={}, model="claude-x",
        pending_dir=pending_dir, threads_dir=str(tmp_path / "t"),
        raw_dir=str(tmp_path / "r"),
        budget_seconds=0, poll_seconds=0, ci_commit=False,
    )
    assert result["hard_fail"] is False
    assert result["held_dates"] == ["2026-05-14"]
    assert fake_client.messages.batches.created_requests == []
    kept = bs.load_sidecar(os.path.join(pending_dir, "2026-05-14.json"))
    assert kept["blocked"]["reason"] == "retry_exhausted"


def test_the_third_failed_resubmit_becomes_held_in_the_same_run(fake_client, tmp_path, monkeypatch):
    """T2d, threshold path. The count reaches 3 inside _apply_failure_policy; it
    must convert to retry_exhausted there and not submit a fourth batch."""
    sidecar = _sidecar_with_one_thread(_correct_hash())
    sidecar["retry_count"] = 2
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[sidecar], results={}, existing_threads=0)
    assert result["hard_fail"] is False
    assert result["held_dates"] == ["2026-05-14"]
    assert fake_client.messages.batches.created_requests == []
    kept = bs.load_sidecar(str(tmp_path / "pending" / "2026-05-14.json"))
    assert kept["retry_count"] == 3
    assert kept["blocked"]["reason"] == "retry_exhausted"


def test_a_held_sidecar_does_not_stop_the_next_one(fake_client, tmp_path, monkeypatch):
    """T2c's real point. `hard_fail = True; continue` returned exit 1 at the end,
    so every later sidecar's work was thrown away with the run. Holding must be
    per-sidecar."""
    stale = _sidecar_with_one_thread(_correct_hash())
    stale["schema_version"] = 1
    stale["date"] = "2026-05-13"
    stale["attempts"][-1]["batch_id"] = "b0"
    good = _sidecar_with_one_thread(_correct_hash())
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[stale, good],
                          results={"s_abc_00": {"speeches": [{"speechOrder": 1,
                                   "tension": "確認",
                                   "summaries": {"easy": "e", "teen": "t", "adult": "a"}}],
                                   "commitments": []}},
                          existing_threads=0)
    assert result["hard_fail"] is False
    assert result["held_dates"] == ["2026-05-13"]
    # The good sidecar was collected and removed despite the held one above it.
    assert not os.path.exists(str(tmp_path / "pending" / "2026-05-14.json"))
```

- [ ] **Step 2: 落ちることを確認**

Run: `python -m pytest scripts/tests/test_resume.py -k "retry_exhausted or third_failed or held_sidecar_does_not_stop" -v`
Expected: FAIL — `assert True is False`（`hard_fail`）

- [ ] **Step 3: ループ先頭の2分岐を差し替える**

stale-schema 分岐（`if not bs.is_current_schema(sidecar):` の中身）:

```python
            log.error(
                "Sidecar %s has schema_version %r (expected %d) — holding; "
                "its input_hashes were computed by an older revision",
                path, sidecar.get("schema_version"), bs.SCHEMA_VERSION,
            )
            # Held, not hard-failed. The old comment justified exit 1 with "any
            # sidecar skips Summarize entirely, so exiting 0 would be a green run
            # that processes nothing" — the per-date gate (#44) removed that
            # premise. Resubmitting is still unsafe: the stored hashes come from a
            # different param set, so every thread would fail verification.
            _apply_failure_policy(client, sidecar, path, "stale_schema", None,
                                  raw_dir, model, ci_commit)
            _record_held_sidecar(sidecar["date"], sidecar,
                                 _diagnostic("stale_schema"), held_dates, diagnostics)
            continue
```

retry 閾値分岐（`if bs.should_hard_fail(sidecar):`）:

```python
        if bs.should_hard_fail(sidecar):
            # The SECOND hard-fail site. _apply_failure_policy converts the
            # threshold when it is crossed; this one catches a sidecar that
            # crossed it on an earlier run. Fixing only one puts the date back on
            # exit 1 the following morning.
            log.error("Sidecar %s exceeded retry threshold (%d) — holding",
                      path, sidecar["retry_count"])
            _apply_failure_policy(client, sidecar, path, "retry_exhausted", None,
                                  raw_dir, model, ci_commit)
            _record_held_sidecar(sidecar["date"], sidecar,
                                 _diagnostic("retry_exhausted"), held_dates, diagnostics)
            continue
```

- [ ] **Step 4: 既存の stale-schema テストを更新**

`test_collect_refuses_sidecar_from_an_older_schema` の
`assert result["hard_fail"] is True` を差し替え、docstring も直す:

```python
def test_collect_holds_a_sidecar_from_an_older_schema(fake_client, tmp_path):
    """v1 hashes were computed over a different param set, so every thread would
    fail verification. Silently resubmitting burns a retry slot per run until the
    date is permanently lost, so refuse loudly and leave it rescuable by hand.
    Loudly now means held + red, not exit 1: since #44 the per-date gate lets the
    other dates publish, so stopping the job buys nothing and costs a morning.
    """
    # ...setup unchanged (see the existing test)...

    assert result["hard_fail"] is False              # was: is True
    assert result["held_dates"] == ["2026-05-14"]    # new
    kept = bs.load_sidecar(path)
    # KEEP these three verbatim. BLOCKED writes the `blocked` marker, so the
    # sidecar file does change — but retry_count must still be untouched and
    # neither the batch API nor the sync API may be called. Deleting them
    # because "the sidecar is written now" would drop the guarantees the test
    # exists for.
    assert kept is not None and kept["retry_count"] == 0
    assert fake_client.messages.batches.created_requests == []
    assert fake_client.messages.create_calls == []
    assert kept["blocked"]["reason"] == "stale_schema"   # new
```

- [ ] **Step 5: `main()` の exit コメントを直す**

`summarize.py` の `sys.exit(1 if result["hard_fail"] else 0)` の上のコメントを差し替える。
現在「a sidecar past its retry threshold or written by an older schema」と書いてあるが、
どちらも exit 1 でなくなる。**exit 1 の意味を説明する唯一の場所**なので放置できない:

```python
        # 1, never 3 or 4 — and since #65, effectively never at all. This process
        # speaks for many dates, so its exit code cannot say WHICH one failed: the
        # outputs above do that, and the annotations survive even a failed step.
        # A sidecar that needs a human (stale schema, exhausted retries, a hash
        # that no longer verifies) is reported through held_dates and reds the job
        # in the final step, WITHOUT taking this morning's publish down with it.
        # hard_fail remains for a genuine crash path.
        sys.exit(1 if result["hard_fail"] else 0)
```

- [ ] **Step 6: テストが通ることを確認**

Run: `python -m pytest scripts/tests -v`
Expected: PASS（全件）

- [ ] **Step 7: break-to-check**

ループ先頭の `should_hard_fail` 分岐だけを元の `hard_fail = True` に戻して
`test_a_retry_exhausted_sidecar_is_held_not_a_hard_fail` が FAIL することを確認し、戻す。

- [ ] **Step 8: コミット**

```bash
git add scripts/summarize.py scripts/tests/test_resume.py
git commit -m "fix: hold a sidecar that needs a human instead of failing the whole publish"
```

---

## Task 5: abandon を赤くする（#66）

**Files:**
- Modify: `scripts/summarize.py`（abandon 分岐、返却 dict、`main()` の `_write_github_output`）
- Test: `scripts/tests/test_resume.py`

**Interfaces:**
- Consumes: Task 3 の `held_dates` 配線パターン
- Produces: `collect_pending_batches` の返却 dict に `"abandoned_dates": list`

- [ ] **Step 1: 失敗するテストを書く（T3 / T8）**

既存 `test_resume_stays_quiet_when_the_raw_is_legitimately_out_of_window` を置き換える:

```python
def test_an_abandoned_sidecar_reds_the_run_without_blocking_the_publish(
        fake_client, tmp_path, monkeypatch, capsys):
    """T3 / #66. Past the abandon age the threads in that batch are gone for good
    — the one thing in this pipeline that cannot be undone. It used to be the ONE
    outcome that left the run green: days 1-30, while it was still fixable, were
    red; day 31, when it stopped being fixable, was a warning. The severity was
    upside down.
    """
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = _run_collect(fake_client, tmp_path, monkeypatch,
                          sidecars=[_sidecar_with_one_thread(_correct_hash())],
                          raw_present=False, sidecar_age_days=40, existing_threads=0)
    assert result["abandoned_dates"] == ["2026-05-14"]
    assert result["systemic_dates"] == []      # a different claim, different text
    assert result["held_dates"] == []
    assert result["hard_fail"] is False        # loud, but the publish continues
    assert not os.path.exists(str(tmp_path / "pending" / "2026-05-14.json"))
    errors = [ln for ln in capsys.readouterr().out.splitlines()
              if ln.startswith("::error::")]
    assert len(errors) == 1
    assert "2026-05-14" in errors[0]
    # It must not overclaim: a sidecar can belong to a late meeting on a date
    # that already has published threads.
    assert "never be published" not in errors[0]
    assert "uncollected" in errors[0]


def test_a_morning_of_only_held_and_abandoned_dates_exits_zero(monkeypatch, tmp_path):
    """T8 — through main(), not around it.

    Two things have to hold together and only main() sees both: the four lists
    reach GITHUB_OUTPUT (a verdict that never gets there cannot red the final
    step, which is the only layer that sees every date), AND the process exits 0
    (exit 1 aborts Collect under `set -e`, skipping summarize/commit/push for
    every other date — what #65 removed).
    """
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    _stub_client(monkeypatch)
    monkeypatch.setattr(summarize, "collect_pending_batches", lambda *a, **k: {
        "hard_fail": False, "systemic_dates": [], "suspect_dates": [],
        "held_dates": ["2026-01-02"], "abandoned_dates": ["2026-01-03"],
        "diagnostics": [],
    })
    with pytest.raises(SystemExit) as e:
        summarize.main(_isolated_collect_argv(tmp_path))
    assert e.value.code == 0
    written = out.read_text(encoding="utf-8")
    assert "held_dates=2026-01-02\n" in written
    assert "abandoned_dates=2026-01-03\n" in written
    assert "systemic_dates=\n" in written
    assert "suspect_dates=\n" in written
```

> **置き場所**: この T8 は `_stub_client` / `_isolated_collect_argv` を使うので
> `scripts/tests/test_systemic_failure.py` の `--collect-pending` 契約セクションに置く
> （`test_resume.py` ではない）。

- [ ] **Step 2: 落ちることを確認**

Run: `python -m pytest scripts/tests/test_resume.py -k "abandoned_sidecar_reds or four_date_lists" -v`
Expected: FAIL — `KeyError: 'abandoned_dates'`

- [ ] **Step 3: abandon 分岐を実装**

`collect_pending_batches` 冒頭に `abandoned_dates: list = []` を追加。abandon 分岐を差し替える:

```python
            if bs.is_abandonable(sidecar, now_iso):
                age = bs.age_days(sidecar, now_iso)
                batch_id = bs.current_batch_id(sidecar)
                log.error(
                    "Resume: %s unrecoverable (raw out of window, age %.1fd) "
                    "— abandoning sidecar", date_str, age,
                )
                # The only permanently irreversible event in this pipeline, and
                # until #66 the only one that left the run green. Red, but not
                # hard_fail: nothing about a loss that already happened is a
                # reason to withhold today's other dates.
                #
                # Say only what is provable. This sidecar's UNCOLLECTED threads
                # are gone; the date itself may well have published threads from
                # earlier runs, and telling an operator the date is empty sends
                # them hunting for threads that were never lost.
                abandoned_dates.append(date_str)
                _annotate(
                    "error",
                    f"{date_str}: permanently lost — the uncollected threads from "
                    f"batch {batch_id} (age {age:.1f}d) can never be assembled: "
                    f"the raw aged out of the fetch window and the batch results "
                    f"have expired. No action recovers them; this is recorded so "
                    f"the loss is on the record. Threads published for this date "
                    f"by earlier runs are unaffected, and this is not an API "
                    f"rejection")
                bs.delete_sidecar(path)
```

返却 dict に `"abandoned_dates": abandoned_dates,` を追加し、docstring にも追記。

- [ ] **Step 3.5: `main()` を monkeypatch している既存3テストのスタブを4キーに揃える**

`scripts/tests/test_systemic_failure.py` の
`test_collect_pending_exits_zero_on_a_soft_verdict` /
`test_collect_pending_exits_one_on_a_hard_fail` /
`test_collect_pending_writes_deduplicated_dates_to_github_output` は
`collect_pending_batches` を `hard_fail` / `systemic_dates` / `suspect_dates` /
`diagnostics` の**4キーだけ**を返す lambda に差し替えている。Step 4 で `main()` が
`result["held_dates"]` を読むと `KeyError` で3件とも落ちる。**Step 4 より先に**
3つのスタブ dict それぞれへ次を追加する:

```python
        "held_dates": [], "abandoned_dates": [],
```

（`test_collect_pending_exits_one_on_a_hard_fail` はそのまま残す。`hard_fail` は
crash 経路のために残っており、その分岐が exit 1 を返すことは依然として契約。）

- [ ] **Step 4: `main()` の output 呼び出しを4本にする**

```python
        _write_github_output(
            systemic_dates=result["systemic_dates"],
            suspect_dates=result["suspect_dates"],
            held_dates=result["held_dates"],
            abandoned_dates=result["abandoned_dates"],
        )
```

- [ ] **Step 5: テストが通ることを確認**

Run: `python -m pytest scripts/tests -v`
Expected: PASS（全件）

- [ ] **Step 6: コミット**

```bash
git add scripts/summarize.py scripts/tests/test_resume.py
git commit -m "fix: report a permanently lost batch as an error instead of a warning"
```

---

## Task 6: workflow — ゲート日付単位化と最終ステップ

**Files:**
- Modify: `.github/workflows/daily-batch.yml`
- Test: `scripts/tests/test_systemic_failure.py`

**Interfaces:**
- Consumes: Task 3/5 の step outputs `held_dates` / `abandoned_dates`
- Produces: なし（最終層）

- [ ] **Step 1: 失敗するテストを書く（T4 / T5）**

`scripts/tests/test_systemic_failure.py` の末尾に追記。既存のヘルパ
（`_workflow_steps()` 等）を再利用すること。

```python
def test_the_pending_gate_is_per_date_not_per_run():
    """T4 / #44. One uncollectable sidecar used to skip Summarize for EVERY date
    — the amplifier that turned a single stuck batch into a two-month outage.
    Asserting only that the string is gone is not enough: the skip has to happen
    before the python call (or the date is summarized twice) and the run-internal
    break has to stay after it (or one run piles up several in-flight batches).
    """
    steps = _workflow_steps()
    summarize_step = next(s for s in steps if s.get("id") == "summarize")
    cond = summarize_step.get("if", "")
    assert "has_pending" not in cond, (
        "the global gate is what #44 is about; it must not gate the whole step"
    )
    assert "steps.dates.outputs.list" in cond

    run = summarize_step["run"]
    assert "has_pending" not in run

    skip_at = run.find('if [ -f "data/pending-batches/$d.json" ]')
    call_at = run.find("python scripts/summarize.py --date")
    break_at = run.rfind('if [ -f "data/pending-batches/$d.json" ]')
    assert skip_at != -1 and call_at != -1, "the loop no longer looks like itself"
    assert skip_at < call_at, "the per-date skip must precede the summarize call"
    assert break_at > call_at, "the run-internal single-batch break must remain"
    assert "continue" in run[skip_at:call_at], "the skip must continue, not break"
    assert "break" in run[break_at:], "the post-call guard must break"

    collect_step = next(s for s in steps if s.get("id") == "collect")
    assert "has_pending" not in collect_step["run"], (
        "an output nothing reads is a trap: a later reader assumes it still gates"
    )


def test_held_and_abandoned_dates_fail_the_run_without_a_threshold():
    """T5. Both are unconditional: a held sidecar is a request for a decision and
    an abandoned one is a permanent loss. Neither is 'weak evidence' that a
    threshold should soften, and neither may be folded into SUSPECT."""
    steps = _workflow_steps()
    final = next(s for s in steps
                 if s.get("name", "").startswith("Fail the run"))
    env, run = final.get("env", {}), final["run"]
    assert "steps.collect.outputs.held_dates" in str(env.values())
    assert "steps.collect.outputs.abandoned_dates" in str(env.values())
    # The existing suspect escalation must survive verbatim — it is pinned
    # elsewhere in this file for a reason.
    assert 'FAIL_DATES="$FAIL_DATES$SUSPECT"' in run
    # ...and held/abandoned must NOT be folded into it. Check the whole region
    # where FAIL_DATES is assembled, not one line: an earlier draft of this test
    # looked only at the SUSPECT_N= line, so `FAIL_DATES="$FAIL_DATES$SUSPECT$HELD"`
    # would have passed it.
    build = run[run.find("FAIL_DATES="):run.find('if [ -z')]
    assert "$HELD" not in build and "$ABANDONED" not in build, (
        "held and abandoned are unconditional; routing them through the suspect "
        "threshold would soften a permanent loss into 'needs a second occurrence'"
    )
    # Held or abandoned alone must be able to fail the run.
    assert 'if [ -z "$(echo "$FAIL_DATES$HELD$ABANDONED"' in run
    assert "Permanently lost" in run
    assert "held for a human decision" in run
```

- [ ] **Step 2: 落ちることを確認**

Run: `python -m pytest scripts/tests/test_systemic_failure.py -k "per_date or held_and_abandoned" -v`
Expected: FAIL

- [ ] **Step 3: Collect ステップから `has_pending` を削除**

`daily-batch.yml` の Collect ステップ（`id: collect`）の `run` から次の6行を削除:

```yaml
          if ls data/pending-batches/*.json >/dev/null 2>&1; then
            echo "has_pending=true" >> "$GITHUB_OUTPUT"
          else
            echo "has_pending=false" >> "$GITHUB_OUTPUT"
          fi
```

同ステップ先頭コメントの hard-fail 列挙も実態に合わせる:

```yaml
          # No rc capture on purpose. This process speaks for MANY dates, so it
          # answers with systemic/suspect/held/abandoned_dates outputs (written
          # from Python) and exits 0 for every sidecar state — including the ones
          # that need a human. Since #65 a non-zero code from here means a crash,
          # and must still abort under set -e.
```

- [ ] **Step 4: Summarize ステップのゲートをループ内へ移す**

`if:` 行:

```yaml
        if: steps.dates.outputs.list != ''
```

ループ先頭（`echo "::group::Summarize $d"` の**前**）に挿入:

```bash
            # Per-date, not per-run. A sidecar means THIS date already has a batch
            # in flight (or held for a human), so re-summarizing it would submit a
            # second one. It says nothing about the other dates — gating all of
            # them on one stuck sidecar is what turned a single uncollectable
            # batch into a two-month outage (#43/#44).
            if [ -f "data/pending-batches/$d.json" ]; then
              echo "Sidecar present for $d — Collect owns this date; skipping"
              continue
            fi
```

ループ末尾の `break` はそのまま残す。その上のコメントに一行足す:

```bash
            # Distinct from the per-date skip at the top: that one is cross-run
            # (Collect owns the date), this one is within-run (at most one NEW
            # in-flight batch per run).
```

- [ ] **Step 5: 最終ステップに held / abandoned を配線**

`env:` に追加:

```yaml
          COLLECT_HELD: ${{ steps.collect.outputs.held_dates }}
          COLLECT_ABANDONED: ${{ steps.collect.outputs.abandoned_dates }}
```

`run:` の `SUSPECT_N=...` の後、`FAIL_DATES` 判定の前に追加:

```bash
          # Neither of these is graded. A held sidecar is a request for a
          # decision and an abandoned one is a permanent loss; a threshold that
          # softens either would be answering a question nobody asked. They also
          # get their OWN lines: "Nothing reached the site" is a claim about
          # today's work, and neither of these is that.
          HELD=$(echo "$COLLECT_HELD" | tr ' ' '\n' | grep -v '^$' | sort -u | tr '\n' ' ')
          ABANDONED=$(echo "$COLLECT_ABANDONED" | tr ' ' '\n' | grep -v '^$' | sort -u | tr '\n' ' ')
```

既存の早期 return を差し替える:

```bash
          if [ -z "$(echo "$FAIL_DATES$HELD$ABANDONED" | tr -d ' ')" ]; then
            echo "No systemic summary failure this run."
            exit 0
          fi

          if [ -n "$(echo "$ABANDONED" | tr -d ' ')" ]; then
            echo "::error::Permanently lost uncollected threads on:${ABANDONED}"
            echo "Those batches can never be assembled: the raw aged out of the fetch"
            echo "window and the results expired. Nothing recovers them — this is on the"
            echo "record, not a request for action. Threads published for those dates by"
            echo "earlier runs are unaffected."
          fi

          if [ -n "$(echo "$HELD" | tr -d ' ')" ]; then
            echo "::error::Pending batches held for a human decision on:${HELD}"
            echo "Nothing was resubmitted and no retry was spent — rebuilding from today's"
            echo "raw would fail the same way. The Collect step annotation for each date"
            echo "names the reason and the choices. Publishing continued for every other"
            echo "date; these sidecars stay until someone decides."
          fi

          if [ -n "$(echo "$FAIL_DATES" | tr -d ' ')" ]; then
            echo "::error::Nothing this run produced reached the site on: ${FAIL_DATES}"
            ... (既存の説明ブロックをそのまま残す)
          fi
          exit 1
```

既存の説明ブロック内、hash_mismatch を説明している2行を差し替える
（retry で焼き切れなくなったので嘘になる）:

```bash
          echo "    hash_mismatch/speech_gap/raw_missing point at raw and are NOT"
          echo "    resubmitted: they appear under 'held for a human decision' above,"
          echo "    with no retry spent. missing_result is retryable and does appear here."
```

- [ ] **Step 6: `notify-on-failure` の本文を直す**

`BODY=$(printf ...)` の文言に追記（「Nothing reached the site on」しか案内していないと、
held/abandoned だけで赤い run で運用者が探すものが存在しない）:

```
... or a date that produced nothing that reached the site — check the run summary for "Nothing reached the site on", "held for a human decision", or "Permanently lost". ...
```

- [ ] **Step 7: テストが通ることを確認**

Run: `python -m pytest scripts/tests -v`
Expected: PASS（全件）

- [ ] **Step 8: break-to-check**

ループ先頭の `continue` ブロックを一時的に削除して
`test_the_pending_gate_is_per_date_not_per_run` が FAIL することを確認し、戻す。

- [ ] **Step 9: コミット**

```bash
git add .github/workflows/daily-batch.yml scripts/tests/test_systemic_failure.py
git commit -m "ci: skip only the dates that are actually stuck, and red the run on held or lost batches"
```

---

## Task 7: 運用テキストと stuck 通知

**Files:**
- Modify: `scripts/check_stuck_batches.py`, `CLAUDE.md`
- Test: `scripts/tests/test_batch_state.py`（stuck 出力の単体は不要 — 目視 + 既存 import で足りる）

**根っこ**: 挙動を変えて説明文を放置すると、**更新し忘れたドキュメントが、設計が防ごうとした誤操作を誘発する**。

- [ ] **Step 1: `check_stuck_batches.py` に held を表示**

`notify-stuck-batch` は `if: always()` で毎日走る。BLOCKED な sidecar は retry を消費しないので、
現状の出力は毎日「retries 0」= 「まだ何も試していない一時的な詰まり」に見える。
一方 abandon は sidecar を消すのでこの通知から消える —— #66 が直した重大度の逆転が再生産される。

```python
def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for path in sorted(glob.glob(os.path.join(bs.PENDING_DIR, "*.json"))):
        sc = bs.load_sidecar(path)
        if sc and sc.get("attempts") and bs.is_stuck(sc, now):
            blocked = sc.get("blocked")
            if blocked:
                # Without this, a held sidecar reads as "retries 0" — an untried,
                # transient jam — when it is the opposite: deliberately not
                # retried, waiting on a person. #65/#66.
                state = (f"HELD for a human decision "
                         f"(reason {blocked.get('reason')}, since "
                         f"{blocked.get('since')}; not retried by design)")
            else:
                state = f"in flight, retries {sc['retry_count']}"
            print(f"- {sc['date']}: {bs.current_batch_id(sc)} "
                  f"(age {bs.age_days(sc, now):.1f}d, {state})")
```

- [ ] **Step 2: 手で動作確認**

```bash
mkdir -p data/pending-batches && cat > data/pending-batches/2026-05-14.json <<'EOF'
{"schema_version": 4, "date": "2026-05-14", "model": "m", "retry_count": 0,
 "attempts": [{"batch_id": "b1", "submitted_at": "2026-01-01T00:00:00Z",
               "terminal_status": null, "terminal_at": null}],
 "meetings": [],
 "blocked": {"reason": "hash_mismatch", "since": "2026-08-01T00:00:00Z",
             "meeting_id": "M1", "custom_id": "s_abc_00"}}
EOF
python scripts/check_stuck_batches.py
rm data/pending-batches/2026-05-14.json
```

Expected: `HELD for a human decision (reason hash_mismatch, ...; not retried by design)` を含む1行

> **重要**: 確認後に必ずファイルを消すこと。`data/pending-batches/` に commit された sidecar が
> 残ると、毎朝の Collect がそれを回収しようとする。

- [ ] **Step 3: `CLAUDE.md` を更新**

3箇所。

(a) exit code 表の下の `--collect-pending` の段落を差し替える:

```
`--collect-pending` is the exception: it speaks for many dates in one process, so a single exit
code cannot say which one failed. It reports through `systemic_dates` / `suspect_dates` /
`held_dates` / `abandoned_dates` step outputs plus annotations, and **exits 1 only on a crash**.
A sidecar that needs a human — an older schema, exhausted retries, a hash that no longer
verifies — is *held*: reported, red in the final step, and left on disk, without taking the
morning's publish down with it. That changed in #65: exit 1 used to be justified by "a sidecar
skips Summarize for every date anyway, so a green run would process nothing", and the per-date
gate removed that premise.
```

(b) `batch_state.py` を説明している段落の末尾（`Tracked as #65.` で終わる文）を差し替える:

```
Since #59/#61 that mismatch reds the run on the **first** morning (the reason is named in the
annotation). Since #65 it is also *held*: no resubmit, no retry spent, and the sidecar is kept
so the batch stays rescuable. It no longer decays into permanent loss on its own — but the
results still expire ~29 days after submission, so it is a decision to make, not one to defer.
The annotation names the choices; **do not simply `git rm` the sidecar** — outside the lookback
window the date is not re-summarized, which converts a recoverable batch into the permanent loss
the hold was preventing.
```

(c) `is_current_schema` の段落末尾に一文追加（hard-fail 撤廃で着地条件が緩んだと誤解されるのを防ぐ）:

```
Since #65 that refusal is a *hold* rather than a hard fail — the other dates still publish — but
**the landing condition is unchanged**: a held sidecar's batch results still expire in ~29 days,
so a version change landed on top of one still ends in permanent loss, just more quietly. Land a
`SCHEMA_VERSION` change only when `git ls-files data/pending-batches/` is empty.
```

- [ ] **Step 4: 受け入れコマンドを流す**

Run: `python -m pytest scripts/tests && npm run lint && npm run validate`
Expected: すべて PASS

- [ ] **Step 5: コミット**

```bash
git add scripts/check_stuck_batches.py CLAUDE.md
git commit -m "docs: describe the held regime where the pipeline's behaviour is explained"
```

---

## Task 8: 通し検証

**Files:** 変更なし（検証のみ）

- [ ] **Step 1: 全受け入れ基準**

```bash
python -m pytest scripts/tests -v
npm run lint
npm run validate
```

Expected: すべて PASS

- [ ] **Step 2: sidecar が残っていないことを確認**

```bash
git ls-files data/pending-batches/
git status --porcelain
```

Expected: どちらも空。commit された sidecar があると毎朝の Collect がそれを回収しにいく。

- [ ] **Step 3: 実 workflow を目視で追う**

`.github/workflows/daily-batch.yml` を開き、次の3点を確認:

1. Summarize ステップの `if:` に `has_pending` が無く、ループ先頭に `continue`、末尾に `break` がある
2. 最終ステップの `env:` が4本（systemic / suspect / held / abandoned）
3. `has_pending` という文字列がファイル全体から消えている（`grep -c has_pending` が 0）

- [ ] **Step 4: 未使用コードの掃除確認**

```bash
grep -rn "_retry_or_hardfail\|assemble_failed" scripts/ --include='*.py' \
  | grep -v '^scripts/tests/'
```

Expected: ヒット無し（あれば移行漏れ）。
`scripts/tests/` を除外するのは、Task 1 の `test_assemble_failed_is_not_a_policy_reason`
が意図的にこの綴りを保持しているため — それは移行漏れではなくガード。

- [ ] **Step 5: 二重検証のコストを実測して記録する**

`verify_manifest_against_raw` は collect で1回、`assemble_from_manifest` の冒頭でもう1回
走る。90 スレッドの日付なら `build_summary_request` + SHA256 が180回。プランは
「数ミリ秒」と見積もっているが、canonical JSON 化はプロンプト全文を通すので未実測。

```bash
python - <<'EOF'
import sys, time, os
sys.path.insert(0, "scripts")
import summarize
from pipeline import batch_state as bs
meeting = {"meetingId": "M1", "house": "参議院", "meeting": "外交防衛委員会",
           "date": "2026-05-14", "source": "ndl",
           "speeches": [{"speechOrder": i, "speech": "あ" * 2000, "speaker": "X",
                         "speakerGroup": "G", "speakerPosition": "P",
                         "speechURL": "http://x"} for i in range(1, 91)]}
threads = [{"custom_id": f"s_a_{i:02d}", "thread_idx": i,
            "thread_info": {"topic": "T", "topicTag": "t", "topicColor": "#111",
                            "summary": "s", "speechOrders": [i + 1]},
            "speechOrders": [i + 1], "input_hash": "sha256:x"} for i in range(90)]
sc = {"schema_version": bs.SCHEMA_VERSION, "date": "2026-05-14", "model": "m",
      "retry_count": 0, "attempts": [], "meetings": [
          {"meeting_id": "M1", "outcome": {}, "threads": threads}]}
t0 = time.perf_counter()
summarize.verify_manifest_against_raw(sc, {"M1": meeting})
print(f"90-thread verification: {(time.perf_counter() - t0) * 1000:.0f} ms")
EOF
```

Expected: 数百ミリ秒未満。超えるようなら `assemble_from_manifest` に
`verified: bool = False` を足して二重実行を避ける（結合が増えるので、実測が
それを要求したときだけ）。**結果をこのステップのチェックボックス横に書き残すこと。**

- [ ] **Step 6: Gate3 へ**

```bash
timeout 3600 claude -p "/goal /code-gate を実行し critical 0 を達成する。5回で打ち切り" \
  --permission-mode acceptEdits
```

---

## Self-Review

**1. Spec coverage**

| spec 節 | タスク |
|---|---|
| §3.1 policy テーブル | Task 1 |
| §3.2 検証順序（verify-before-fetch） | Task 2 |
| §3.3 reason 伝播 / `_apply_failure_policy` | Task 3 |
| §3.4 BLOCKED の毎朝挙動 + empty-commit 回避 | Task 1 Step 3（`mark_blocked` の戻り値）+ Task 3 Step 4 |
| §3.5 HOLD | Task 3 |
| §3.6 シグナル分離（`_record_held_sidecar`） | Task 3 |
| §3.7.1 stale-schema | Task 4 |
| §3.7.2 retry 閾値 | Task 3 Step 4 + Task 4 |
| §3.7.3 ループ先頭の2つ目の hard_fail | Task 4 Step 3 |
| §3.8 abandon | Task 5 |
| §3.9 返却値と outputs | Task 3 / Task 5 |
| §3.10 最終ステップ | Task 6 |
| §3.11 ゲート日付単位化 | Task 6 |
| §3.12 annotation 文面 | Task 3 Step 3（held）+ Task 5 Step 3（abandon） |
| §3.13 運用テキスト6箇所 | workflow help/notify = Task 6、`main()` コメント = Task 4 Step 5、CLAUDE.md = Task 7 |
| §3.14 notify-stuck-batch | Task 7 Step 1 |
| §4 T1–T9 | T1/T2/T2b/T9 = Task 3、T1b = Task 2（観測）+ Task 3（帰結）、T2c/T2d = Task 4、T3 = Task 5、T8 = Task 5（`test_systemic_failure.py` に置く）、T4/T5 = Task 6、T6/T7 = Task 1 |

ギャップ無し。

---

## Gate2 敵対レビューで直したもの

このプランは Claude 敵対レビューを1周通している。以下は**指摘を受けて直した実際の欠陥**であり、
実装者が同じ罠を踏み直さないための記録。

| 重大度 | 指摘 | 対応 |
|---|---|---|
| critical | T6 の AST sweep が「`_diagnostic` の第1引数は文字列リテラル」を assert するが、Task 3 は `_diagnostic(reason)` と変数で呼ぶ。Task 3 終了時点で必ず赤 | 非リテラルは reject でなく skip し、代わりに既知6 reason の**下限集合**で fail-open を塞ぐ（Task 1 Step 5） |
| critical | Task 2 の T1b が `created_requests == []` を要求するが、Task 2 時点では旧 `_retry_or_hardfail` が再送する（raw があるので rebuild は成功する） | T1b を「観測された reason が `hash_mismatch` であること」に限定。「だから再送されない」は Task 3 の新テストへ分離 |
| critical | Task 5 の `main()` 変更で、`collect_pending_batches` を4キーの dict でスタブしている既存3テストが `KeyError` | Step 3.5 を新設し、Step 4 より**前**にスタブを4キー追加 |
| high | `TERMINAL_FAILURES` 分岐は raw ロード前にあるため、「canceled + raw 欠落」の朝は再送していないのに retry を焼き、3朝で `retry_exhausted`。しかも annotation は「no retry spent」と嘘をつく | `_apply_failure_policy` の RESUBMIT 分岐を **rebuild 先・`record_terminal` 後**に並べ替え。`retry_exhausted` のときだけ文面を「retries spent」に変える |
| high | T5 の `assert "$HELD" not in run.split("SUSPECT_N")[1].split("\n")[0]` は1行しか見ておらず、`FAIL_DATES="$FAIL_DATES$SUSPECT$HELD"` でも通る | `FAIL_DATES` 組み立て区間全体を見る形に変更 + held/abandoned 単独で exit 1 に到達する構造を assert |
| medium | spec §3.12 が必須とした sidecar path / 経過日数 / 推定残日数が hash_mismatch 分岐にしか出ない | BLOCKED 共通ブロックへ移動（`bs.age_days` を使用） |
| medium | `raw_missing`（manifest の meeting が raw に居ない）が未カバー。誤って RESUBMIT に配線しても T1/T2b は緑 | `test_a_manifest_meeting_absent_from_raw_holds` を追加 |
| medium | T8 が `_write_github_output` を直呼びしており、spec の「hold/abandon のみなら `main` が exit 0」を検証していない | `main()` 経由に書き換え、exit 0 と4本の output を同時に確認 |
| medium | BLOCKED → HOLD に遷移すると古い `blocked.since` が新しい reason と並んで印字され、誤誘導する | `blocked["reason"] == diagnostic["reason"]` のときだけ `held since` を出す |
| low | Task 8 の移行漏れ grep が、意図的に残す `test_assemble_failed_is_not_a_policy_reason` に必ずヒットする | `--include='*.py'` + `scripts/tests/` 除外 |
| low | 二重検証（collect と assemble）のコストが未実測 | Task 8 に 90 スレッド実測ステップを追加。数百ms を超えたら `verified` フラグを検討 |

**2. Placeholder scan** — 「適切なエラー処理を追加」等の曖昧記述なし。全コードステップに実コードあり。

**3. Type consistency** — `_apply_failure_policy` は全呼び出し箇所で `(client, sidecar, path, reason, diagnostic, raw_dir, model, ci_commit)` の8引数、戻りは str。`_record_held_sidecar` は全箇所で `(date_str, sidecar, diagnostic, held_dates, diagnostics)` の5引数。`mark_blocked` の戻り bool は Task 3 Step 4 の `changed` でのみ使用。`held_dates` / `abandoned_dates` の綴りは Python・YAML・テストで一致。

**既知の副作用（意図的）**: Task 2 Step 4 により、1つの manifest に検証エラーと `missing_result` が
同居する場合、報告される reason が後者から前者へ変わる。決定論的な問題を先に見せるのが正しいため意図的。
