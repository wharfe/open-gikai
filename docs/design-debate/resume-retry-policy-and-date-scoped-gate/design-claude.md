# Design A (Claude) — 失敗レジームの3分類・恒久ロストの昇格・ゲートの日付単位化

対象: #65 / #66 / #44 第3項。brief: `./brief.md`。

---

## 1. 根っこ

3つの issue は別々の症状に見えるが、根は1つの欠落した問いである:

> **「明日また同じことをしたら、うまくいくのか？」**

resume が失敗したとき、コードはこの問いを一度も立てていない。`_retry_or_hardfail` は
理由を見ずに「3回まで再送、それ以上は hard fail」を適用する。だから:

- 決定論的に失敗する `hash_mismatch` にも3回課金する（#65）
- 「明日 raw が戻れば直る」だけの `speech_gap` でも retry 予算を1つ焼く（付随バグ、後述 §3.4）
- 「もう二度と直らない」abandon だけが緑で通る（#66）

答えは3つしかない。この3分類をコードの一級概念にするのが本設計の芯である。

| レジーム | 定義 | 再送 | retry 消費 | run の色 | publish |
|---|---|---|---|---|---|
| **RESUBMIT** | 失敗は *バッチ* の性質。投げ直せば別の目が出る | する | する | 既存どおり | 継続 |
| **HOLD** | 失敗は *手元の状態* の欠落。後の run で復旧しうる | しない | **しない** | 赤/黄 | 継続 |
| **BLOCKED** | 現在の入力に対して決定論的に失敗する。人間の判断が要る | **しない** | **しない** | 赤 | 継続 |

そして終端状態が1つ:

| **LOST**（abandon） | 回収不能が確定。恒久ロスト | — | — | **赤** | 継続 |

「publish 継続」が全レジームで共通なのが #52 の教訓であり、それを可能にするのが
#44 第3項（ゲートの日付単位化）である。3つの issue が1本にまとまる理由がこれ。

---

## 2. 変更の全体像

| ファイル | 変更 |
|---|---|
| `scripts/pipeline/batch_state.py` | 失敗レジームの純粋な policy テーブル + `blocked` 状態のヘルパ |
| `scripts/summarize.py` | `_retry_or_hardfail` → `_apply_failure_policy`／reason の伝播／raw 検証の前倒し／abandon を error 化／stale-schema を BLOCKED 化 |
| `.github/workflows/daily-batch.yml` | グローバル pending ゲートを日付単位へ／`abandoned_dates` を最終判定に追加 |
| `CLAUDE.md` | exit code 表と #65 の運用回避策の記述を更新 |
| `scripts/tests/` | T1–T5 + policy 網羅の AST テスト |

---

## 3. 詳細設計

### 3.1 `batch_state.py` — policy テーブル（純粋関数）

`batch_state.py` は「pure: no Anthropic client, no network」を自称し、単体テストの本体である。
policy はネットワークもクライアントも要らない純粋な判断なので、ここが正しい置き場所。

```python
# --- Failure regimes -------------------------------------------------------
# What a resume does when it cannot finish, answering one question: would doing
# the same thing tomorrow work?
RESUBMIT = "resubmit"   # the batch failed; a fresh submission is a new roll
HOLD     = "hold"       # local state is missing; a later run may restore it
BLOCKED  = "blocked"    # deterministic given current inputs; a human must decide

# Keyed by the ``reason`` strings that reach _apply_failure_policy: the terminal
# batch statuses, the results-expiry marker, and every reason _diagnostic can
# produce. test_failure_policy_covers_every_diagnostic_reason AST-sweeps
# summarize.py and fails if a reason exists without an entry here — the default
# below is a safety net, not a place to leave things.
FAILURE_POLICY = {
    # The batch itself ended badly. The manifest is still valid.
    "canceled":            RESUBMIT,
    "expired":             RESUBMIT,
    "results_expired":     RESUBMIT,
    # A result never arrived, or arrived unusable and repair could not fix it.
    # A fresh batch genuinely may answer.
    "missing_result":      RESUBMIT,
    # The result parsed but would not assemble. Least confident entry: a new
    # roll of the model may well assemble, so we pay for one — but if this ever
    # becomes common, it belongs in BLOCKED, not in a higher retry count.
    "thread_build_failed": RESUBMIT,
    # Raw is not on disk this run. Nothing to resubmit and nothing is wrong with
    # the batch; the next fetch may bring it back.
    "raw_missing":         HOLD,
    "raw_date_missing":    HOLD,
    "speech_gap":          HOLD,
    # The request we would build today is not the request we submitted. A
    # resubmit reproduces this exactly, forever.
    "hash_mismatch":       BLOCKED,
    "stale_schema":        BLOCKED,
}


def failure_policy(reason: str) -> str:
    """Which regime a failure reason falls into.

    Unknown reasons are BLOCKED, not RESUBMIT. Both defaults are wrong in some
    direction; this one is wrong safely. BLOCKED never spends money and never
    loses data — it holds the sidecar and reds the run until a human looks, and
    since the pending gate is per-date (see daily-batch.yml) one held date no
    longer stops the site. Defaulting to RESUBMIT would silently charge for a
    batch on a failure nobody has reasoned about.
    """
    return FAILURE_POLICY.get(reason, BLOCKED)
```

`blocked` 状態の記録（**制御フローではなく forensics のため** — §3.3 参照）:

```python
def mark_blocked(sidecar: dict, reason: str, at: str,
                 meeting_id=None, custom_id=None) -> None:
    """Record that this sidecar needs a human. Idempotent in ``since``.

    ``since`` is written once so the annotation can say how many mornings this
    has been sitting there — the single most useful number for deciding whether
    to rescue it or discard it, given batch results expire ~29 days after
    submission.
    """
    existing = sidecar.get("blocked") or {}
    sidecar["blocked"] = {
        "reason": reason,
        "since": existing.get("since", at),
        "meeting_id": meeting_id,
        "custom_id": custom_id,
    }


def clear_blocked(sidecar: dict) -> None:
    sidecar.pop("blocked", None)
```

**`SCHEMA_VERSION` は bump しない。** `blocked` は `compute_input_hash` に食わせる param 集合にも
hash 関数にも触れないので §4.1 の bump 条件に当たらない。むしろ bump は禁止に近い:
`is_current_schema` は等値比較なので、in-flight の sidecar がある状態で bump を land すると
両方向で hard-fail する（CLAUDE.md 明記）。新フィールドは必ず `sidecar.get("blocked")` で読み、
既存 sidecar（フィールド無し）が読めること。

### 3.2 `summarize.py` — reason を実際に届ける

現状 `collect_pending_batches` は assembly 失敗を一律 `"assemble_failed"` として
`_retry_or_hardfail` に渡している。`_diagnostic` には具体的な reason があるのに、
**policy を決める場所には届いていない**。ここが #65 の機械的な原因である。

```python
if not ok:
    reason = (diagnostic or {}).get("reason", "unknown")
    ...
    outcome = _apply_failure_policy(client, sidecar, path, reason, diagnostic,
                                    raw_dir, model, ci_commit)
```

`"assemble_failed"` という語は失われるが、`attempts[].terminal_status` に入るのは
より具体的な reason になるので、sidecar の forensics はむしろ良くなる。

`_retry_or_hardfail` を置き換える:

```python
def _apply_failure_policy(client, sidecar, path, reason, diagnostic,
                          raw_dir, model, ci_commit) -> str:
    """Apply the regime this failure falls into. Returns one of
    "resubmitted" | "held" | "blocked" | "hard_fail".

    Replaces _retry_or_hardfail, which applied one regime to every reason: it
    resubmitted a hash_mismatch three times (each a full batch's charge) even
    though the rebuild it sends is built from the same raw that just failed
    verification, so it fails identically — and then hard-failed, which used to
    take the publish down with it (#65).
    """
    policy = bs.failure_policy(reason)

    if policy == bs.BLOCKED:
        bs.mark_blocked(sidecar, reason, _utcnow_iso(),
                        (diagnostic or {}).get("meeting_id"),
                        (diagnostic or {}).get("custom_id"))
        bs.save_sidecar(path, sidecar)
        if ci_commit:
            _git_commit_sidecar(path, sidecar["date"])
        return "blocked"

    if policy == bs.HOLD:
        # No retry spent and no write: nothing was submitted, and the state this
        # needed may simply not have been fetched yet. Spending a retry slot here
        # was a real defect — three raw-less mornings could push a perfectly good
        # batch to the hard-fail threshold.
        return "held"

    bs.record_terminal(sidecar, reason, _utcnow_iso())
    if bs.should_hard_fail(sidecar):
        bs.save_sidecar(path, sidecar)
        log.error(...)
        return "hard_fail"
    bs.clear_blocked(sidecar)   # a previously blocked date that now fails for a
                                # resubmittable reason is no longer blocked
    ... existing resubmit path ...
    return "resubmitted"
```

### 3.3 blocked な日付は毎朝どうなるか（brief 論点3）

**専用の短絡はしない。** 翌朝も通常どおり poll → 検証 → BLOCKED → 赤 annotation。理由:

- 短絡すると raw が元に戻った場合（fetcher の修正、NDL の再訂正）に自動復旧しなくなる。
  検証自体はローカルで無料なので、毎朝やり直す価値がある。
- 追加コストは、既に ended なバッチへの poll 1回程度。トークン課金は発生しない
  （§3.5 で repair より前に検証するため）。

`blocked` フィールドは制御フローに使わず、**annotation に「何日目か」を出すため**と
sidecar を人が読んだときの手がかりのためだけに使う。状態機械を増やさない。

### 3.4 付随して直る欠陥 — HOLD が retry を焼いていた

現状 `_retry_or_hardfail` は `record_terminal`（= `retry_count += 1`）を
`_rebuild_requests_from_manifest` が None を返す**前**に呼んでいる。つまり
raw 欠落 / speechOrder gap で **何も再送していないのに retry 予算が1つ減る**。
3朝続けば hard fail に達する。`TRANSIENT_API_ERRORS` のコメントが警戒している
「burning a resubmit and spending one of the three retry slots」がまさにここで起きている。

HOLD レジームはこれを構造的に消す。スコープ外の別バグではなく、
同じ policy テーブルの直接の帰結なので本設計に含める（T2 で明示的に検証）。

### 3.5 検証を repair より前に出す

現在の順序は `_repair_unusable_results(...)` → `assemble_from_manifest(...)`。
repair は **同期 API 呼び出しを最大10本（900秒予算）** 発行しうる。hash_mismatch の朝は、
その支払いが確実に無駄になる（repair の再発行も現在の raw から組むので、同じ検証に落ちる）。

manifest の検証（raw の有無 / speechOrder の解決 / hash 一致）は **ローカルで無料**なので、
先に出す:

```python
def verify_manifest_against_raw(sidecar, meetings_by_id) -> Optional[dict]:
    """The free half of assembly: does the manifest still describe requests we
    can rebuild from today's raw? Returns a _diagnostic on the first problem,
    None if every thread verifies.

    Hoisted out of assemble_from_manifest and run BEFORE _repair_unusable_results
    because repair spends real money on synchronous re-issues, and on a
    hash_mismatch morning every one of those is wasted: repair rebuilds from the
    same raw that is about to fail verification.

    Uses build_summary_request, the one summary-request builder — see CLAUDE.md
    "Summary Layer Invariants" #2 and test_determinism.py's AST sweep.
    """
```

`assemble_from_manifest` はこの関数を先頭で呼ぶ形に整理する（二重計算になるが hash 計算は
CPU 数ミリ秒。ロジックの二重定義を避けることのほうが重要 —— `usable_result` が
module level に置かれているのと同じ理由）。

### 3.6 #66 — abandon を赤くする

`collect_pending_batches` の abandon 分岐:

```python
_annotate("error", f"{date_str}: permanently lost — ...")
```

- `print("::warning::...")` → `_annotate("error", ...)`（#67 の方針にも沿う）
- 新しい step output `abandoned_dates` に日付を積む（`_write_github_output` に追加）
- **`systemic_dates` には混ぜない。** systemic の定義は「この run で何もサイトに届かなかった」
  であり、最終ステップのメッセージは「これらの日付は以前の run のスレッドを
  持っているかもしれない」と言う。abandon にそれを言うのは嘘になる。
  別 output にすることで、最終ステップが正しい文面を出せる。
- 閾値は無し。**1件でも赤**。恒久ロストはこのパイプラインで唯一取り返しがつかない事象で、
  記録として最も価値が高い（issue #66）。sidecar は削除されるので同じ日付が
  繰り返し赤くなることはない。複数日が同時に期限を迎えれば1回の赤い run に列挙される。

workflow 最終ステップ:

```yaml
COLLECT_ABANDONED: ${{ steps.collect.outputs.abandoned_dates }}
```
```bash
FAIL_DATES="$SYSTEMIC$ABANDONED"
if [ "$SUSPECT_N" -ge 2 ]; then FAIL_DATES="$FAIL_DATES$SUSPECT"; fi
```
かつ abandoned 用の独立した説明ブロックを出す:
```
::error::Permanently lost — these dates will never be published: <dates>
No action can recover them: the raw aged out of the fetch window and the batch
results expired. This is recorded so the loss is on the record, not to ask for a fix.
```

### 3.7 #44 第3項 — ゲートの日付単位化

`daily-batch.yml:135` の step-level `if` から `has_pending` 条件を落とし、ループ先頭で判定:

```yaml
if: steps.dates.outputs.list != ''
```
```bash
for d in $DATES_LIST; do
  # Per-date, not per-run. A sidecar means THIS date already has a batch in
  # flight (or held for a human); re-summarizing it would double-submit. It says
  # nothing about the other dates — and gating all of them on one stuck sidecar
  # is what turned a single uncollectable batch into a two-month outage (#43/#44).
  if [ -f "data/pending-batches/$d.json" ]; then
    echo "::group::Summarize $d"
    echo "Sidecar present — Collect owns this date this run; skipping"
    echo "::endgroup::"
    continue
  fi
  ...
done
```

**run 内 break は残す**（ループ末尾）。両者の役割は別物:

- `continue`（新規、cross-run）: この日付は既に Collect の管轄
- `break`（既存、run 内）: 1 run で新規に in-flight にするバッチは1本まで

したがって in-flight バッチの総数は「保持中の sidecar 群 + 新規1本」。保持中が増えるのは
BLOCKED か HOLD のときだけで、どちらも毎朝赤いので放置されない。

`has_pending` output は他に参照が無いので削除する。**削除漏れ／参照漏れは
「ステップが永久に実行されない」という静かな失敗になる**ので T4 で YAML をパースして守る
（`test_systemic_failure.py` に YAML パースの前例あり）。

### 3.8 G5 — stale-schema と retry 閾値の hard_fail をやめる

stale-schema 分岐が `hard_fail`（exit 1 → `set -e` → publish 停止）を選んでいる根拠は
コメントに明記されている ——「pending がある限り Summarize は丸ごとスキップされるので、
0 を返すと『静かに何も処理しない緑の run』になる」。**§3.7 でこの前提が消える。**

よって:

- stale-schema → `mark_blocked(sidecar, "stale_schema", ...)` + `_record_resume_verdict(...)`
  で赤くする（BLOCKED レジームに統合）。bare `::error::` print も `_annotate` へ。
- retry 閾値到達（`should_hard_fail`）→ 同様に赤くするが publish は止めない。
  3回の再送が全て失敗したバッチは確かに人間案件だが、それは**サイトを止める理由にならない**。

結果、**`--collect-pending` は sidecar の状態を理由に exit 1 を返さなくなる**。
exit 1 は本来の意味（crash / usage error）だけに戻る。これは CLAUDE.md の
exit code 表（「`--collect-pending` ... still exits 1 only for a hard fail
(retry threshold, older schema)」）の変更なので、同じコミットで CLAUDE.md も直す。

> **これが本設計で最も後戻りコストが高い判断。** 保守案は「retry 閾値到達だけ exit 1 のまま」。
> ただしその場合、hard fail した日付が1つあるだけで毎朝 publish が止まり続けるので、
> #44 を直した意味が半分失われる。統一を推す。

### 3.9 annotation の文面（brief 論点7）

hash_mismatch:

```
2026-06-16: resume blocked — input_hash mismatch (meeting=..., custom_id=s_ab12cd34_00),
blocked since 2026-08-08 (3 mornings). The request rebuilt from today's raw does not match
the one this batch was submitted with, so the stored result cannot be verified against it.
NOT resubmitted: a resubmit is built from the same raw and fails identically.
Known causes, in order of likelihood: (1) a SCHEMA_VERSION bump was forgotten after
compute_input_hash's param set changed; (2) the raw data was re-fetched and differs
(an NDL correction, a fetcher change). Decide: discard with
`git rm data/pending-batches/2026-06-16.json` and let the next run re-summarize from
current raw, or revert the change and the next run collects normally.
Batch results expire ~29 days after 2026-06-14T21:03Z.
```

原因を断定せず、hash が実際に証明していること（「今日組めるリクエストと投函したリクエストが
違う」）だけを述べ、既知の原因を確率順に **候補として** 挙げる。CLAUDE.md の
「an operator sent to hunt a 400 that never happened ... has had their morning taken」に従う。
`SCHEMA_VERSION` 忘れを筆頭に挙げるのは、CLAUDE.md 自身が
「forgetting the bump ... surfaces as per-thread `input_hash mismatch`」と記録しているため。

---

## 4. テスト

| ID | 内容 | 置き場所 |
|---|---|---|
| T1 | `hash_mismatch` で submit のフェイクが**呼ばれず**、`retry_count` が増えず、`blocked` が立つ | `test_resume_failure_policy.py`（新規） |
| T2 | `missing_result` では従来どおり再送され `retry_count` が増える（T1 の裏＝fail-closed） | 同上 |
| T2b | `speech_gap` / `raw_missing` は再送せず **retry も消費しない**（§3.4 の回帰） | 同上 |
| T3 | abandon 経路が `abandoned_dates` に載り `::error::` を出し、`hard_fail` にならない | 同上 |
| T4 | pending sidecar が1件ある状態で、sidecar を持たない日付が Summarize ループでスキップされない（YAML パース） | `test_systemic_failure.py` 拡張 |
| T5 | Python 側 exit 定数と YAML の分岐の一致（既存を壊さない／`abandoned_dates` の配線を追加検証） | 既存拡張 |
| T6 | **policy 網羅**: `summarize.py` を AST 走査し `_diagnostic("...")` の第1引数リテラル全てが `FAILURE_POLICY` に明示エントリを持つ | `test_resume_failure_policy.py` |

T6 が本設計の fail-closed の要。`failure_policy` の default BLOCKED は実行時の安全網であって、
「新しい reason を追加したのに policy を決め忘れる」ことを許すためのものではない。
CLAUDE.md の教訓「a forbid-list test approves what it can't recognize」に対する回答:
本テストは **列挙して突き合わせる**ので、認識できないものを承認しない。
また「break it to check」に従い、実装時に reason を1つ削って T6 が落ちることを確認する。

---

## 5. 残存リスク

1. **blocked な日付が31日間毎朝赤い。** 意図的（人間案件なので静かにしない）だが、
   赤が常態化すると無視される。緩和: annotation に経過朝数と結果の残り保持日数を出し、
   放置コストを可視化する。#44 第1項（lookback 窓ベースの判定）が入れば期間は短縮される。
2. **in-flight バッチが増えうる。** 保持中 N 本 + 新規1本。N は BLOCKED/HOLD の日付数で、
   どちらも毎朝赤いので運用上は 0〜1 が想定。ただし上限は設けていない。
3. **`thread_build_failed` を RESUBMIT にした判断の確度が低い。** 頻発するなら BLOCKED へ
   移すべきで、retry 回数を増やす方向へ逃げないこと（policy テーブルにコメントで明記）。
4. **exit 1 をやめる判断（§3.8）は contract 変更。** CLAUDE.md・テスト・workflow を
   同一コミットで動かさないと、`test_the_workflow_tolerates_exactly_these_exit_codes` と
   ドキュメントが食い違う。
5. **`continue` した日付は、その日付に後から追加された新規会議も要約されない。**
   既存のグローバルゲートでも同じ（もっと悪い）性質なので後退ではないが、解消はしない。
