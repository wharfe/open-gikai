設計裁定のため、まず brief 全文と両案・相互批評を読み、必要ならリポジトリで主張を照合します。両案の決定的な主張を、実際の resume / workflow 経路で照合します。コード経路を照合済み。裁定と統合仕様をまとめます。

---

## 1. 判定

**勝者は Design A（Claude）**。決定的な差は次の3点。

1. **長期地平での G1 保持** — Codex は sticky を却下し「毎朝再判定で足りる」としたが、`fetch_summary_results` が assembly より先のため、約29日後に観測 reason が `results_expired`（RETRY）へ化け、決定論的に doomed な再送が復活する。Claude の BLOCKED 概念と「検証を先に出す」方向は正しい（ただし批判どおり **results 取得より前**まで前倒しが必要）。
2. **publish を止めない一貫性** — ゲート日付単位化後、stale-schema だけ exit 1 をやめて retry 閾値だけ exit 1 を残す Codex は、#52 の増幅を別経路で温存する。Claude の「sidecar 状態では Collect を exit 1 にしない」は G4/G5 と整合する。
3. **未知 reason の向き** — 例外→exit 1 は課金には閉じるが、この repo の「壊れていても publish は止めない」と逆。未知は HOLD/BLOCKED（課金なし・赤）＋静的網羅テストが正しい。

Codex が勝っているのは、**シグナル分離**（`held_dates` / `abandoned_dates` を systemic に混ぜない）、**`_record_resume_verdict` を HOLD に使わない**、annotation の言語規律、運用ドキュメント更新の必然性、である。これらを A に移植する。

---

## 2. 移植する部品

| 元 | 移植内容 | 理由 |
|---|---|---|
| **Codex** | `held_dates` + `abandoned_dates` を独立 step output | G2/G3 の「無条件赤」を suspect 閾値から切り離す。Claude の `_record_resume_verdict` 再利用は 1-of-1 で黄のまま通過し G2 を破る |
| **Codex** | HOLD/BLOCKED 経路では `_record_resume_verdict` を呼ばない | 同一日付が `suspect_dates` と held の二重計上で `SUSPECT_N` を汚染する |
| **Codex** | 専用 `_record_held_sidecar`（annotation + held_dates + diagnostics のみ） | publication outcome と operator action を混在させない |
| **Codex** | abandon/held の文言規律（「date 全体が空」と断定しない） | 遅着 meeting の sidecar で既存 thread がある |
| **Codex** | 復旧 annotation に単純 `git rm` を出さない | lookback 外だと再要約されず恒久ロスト（#44 第2項は非目標） |
| **Codex** | policy 判定 → その後にだけ `record_terminal` | retry 予算を焼いてから拒否する穴を塞ぐ |
| **Codex 批判** | `raw_date_missing` を policy 語彙に含める | 実在 reason の欠落は allowlist の嘘 |
| **Codex 批判** | CLAUDE.md / workflow help / notify-on-failure 本文を同一コミットで更新 | 嘘の運用文が「今すぐ sidecar 削除」へ誘導する |
| **Codex 批判** | SCHEMA_VERSION 着地条件は維持と明記 | hard-fail 撤廃で「bump してよい」と誤解されるのを防ぐ |
| **Claude 批判** | `verify_manifest_against_raw` を **results 取得より前**に固定 | `results_expired → RETRY` の doomed 再送を構造的に潰す（sticky だけに頼らない） |
| **Claude 批判** | `mark_blocked` の 2 日目以降 no-op commit を禁止 | empty commit が偽の orphan error になる |
| **Claude** | retry 閾値到達も Collect exit 1 ではなく held/red | ゲート日付単位化と同論拠 |
| **Claude** | 未知 reason の実行時 default は BLOCKED（再送しない） | 静的テストで語彙漏れを CI で落とす |

---

## 3. 確定仕様

### 3.0 スコープ

- **対象**: #65（hash_mismatch の doomed 再送）、#66（abandon の重大度）、#44 第3項（pending ゲート日付単位化）
- **非対象**: #44 第1・2項、#60、#57、要約プロンプト/grouping、`ABANDON_AGE_DAYS` の値変更、`compute_input_hash` の入力集合変更
- **前提 commit**: `main` @ `3b9e7b2`（2026-08-08）
- **SCHEMA_VERSION**: bump **しない**（hash 対象 param を変えない）

### 3.1 根っことレジーム

resume 失敗時に一度だけ答える問い:

> **「明日また同じことをしたら、うまくいくのか？」**

| レジーム | 定義 | 再送 | retry 消費 | sidecar | Collect exit | 最終 job |
|---|---|---|---|---|---|---|
| **RESUBMIT** | バッチ側の失敗。新しい roll が意味を持つ | する | する | 更新（terminal + attempt） | 0（閾値到達時も 0） | 既存どおり / 閾値到達は held 経由で赤 |
| **HOLD** | 手元状態の欠落。後 run で復旧しうる | しない | **しない** | **変更しない** | 0 | `held_dates` で赤 |
| **BLOCKED** | 現入力に対し決定論的。人間判断が要る | **しない** | **しない** | `blocked` を記録（forensics） | 0 | `held_dates` で赤 |
| **LOST**（abandon） | 回収不能確定 | — | — | **削除** | 0 | `abandoned_dates` で赤 |

**全レジームで publish は継続する。** Collect の exit 1 は crash / usage error のみ。sidecar 状態を理由に `set -e` で後続を止めない（#52）。

### 3.2 `batch_state.py` — 純粋 policy

```python
RESUBMIT = "resubmit"
HOLD     = "hold"
BLOCKED  = "blocked"

FAILURE_POLICY = {
    "canceled":            RESUBMIT,
    "expired":             RESUBMIT,
    "results_expired":     RESUBMIT,
    "missing_result":      RESUBMIT,
    "thread_build_failed": RESUBMIT,  # 同一 result では同じ失敗だが新 response は未保証。頻発なら BLOCKED へ移す
    "raw_missing":         HOLD,
    "raw_date_missing":    HOLD,
    "speech_gap":          HOLD,
    "hash_mismatch":       BLOCKED,
    "stale_schema":        BLOCKED,   # 現行 is_current_schema 分岐。語彙は stale_schema に統一
    "retry_exhausted":     BLOCKED,   # 3 回 RESUBMIT 後。再送しない・exit 1 にしない
}

def failure_policy(reason: str) -> str:
    # Unknown → BLOCKED (never charge, never delete data, red via held_dates).
    # Coverage is enforced statically (T6); this default is the runtime safety net.
    return FAILURE_POLICY.get(reason, BLOCKED)
```

ヘルパ:

```python
def mark_blocked(sidecar, reason, at, meeting_id=None, custom_id=None) -> bool:
    """Write sidecar['blocked']. Returns True iff the on-disk content would change.
    ``since`` is sticky (first write wins). Idempotent for same reason/meta."""

def clear_blocked(sidecar) -> None:
    sidecar.pop("blocked", None)
```

- `blocked` は **制御フローの一次ソースにしない**（毎朝の無料検証が一次）。forensics と annotation（経過日数）用。
- 既存 sidecar は `sidecar.get("blocked")` で読む（フィールド無しでも読める）。
- `TERMINAL_FAILURES ⊆ RESUBMIT 理由` をテストで固定。
- `"assemble_failed"` は **policy 語彙から廃止**（詳細 reason を渡す）。

### 3.3 reason の伝播

現状 assembly 失敗は一律 `"assemble_failed"`。これを廃止し、`diagnostic["reason"]` をそのまま policy へ渡す。

呼び出し境界:

```python
reason = (diagnostic or {}).get("reason", "unknown")
outcome = _apply_failure_policy(..., reason, diagnostic, ...)
```

`_retry_or_hardfail` は `_apply_failure_policy` に置換（または内部で policy を必須チェック）:

1. `policy = failure_policy(reason)`
2. **RESUBMIT 以外なら `record_terminal` より前に return**（HOLD/BLOCKED）
3. RESUBMIT のみ: `record_terminal` → 閾値判定 → rebuild → submit

RESUBMIT 経路に HOLD/BLOCKED reason が到達したら `ValueError`（プログラミングエラー）。実行時の未知 reason は `failure_policy` が BLOCKED に落とすのでここには来ない。

### 3.4 BLOCKED の保持と毎朝の挙動

- 初回 BLOCKED: `mark_blocked` → content が変わったときだけ `save_sidecar` +（`--ci-commit` なら）git commit。**2 日目以降 content 不変なら save/commit しない**（empty commit の偽 error を防ぐ）。
- 翌朝も短絡しない: poll →（ended なら）raw ロード → **無料検証** → まだ BLOCKED なら赤 annotation（`blocked.since` から経過日数）→ 再送しない。
- raw が元に戻って検証が通れば、BLOCKED を抜けて通常 assembly へ。成功時 `clear_blocked` は不要（sidecar 削除）。途中で RESUBMIT 系失敗になった場合は `clear_blocked` してから RESUBMIT 処理。

### 3.5 検証順序（G1 の構造的保証）— 批判の Critical を反映

ended バッチで raw が存在するときの順序を **固定**:

```
1. poll → ended
2. load raw
3. raw 空 → abandon / HOLD(raw_date_missing)  [現状どおり、results を触らない]
4. verify_manifest_against_raw(sidecar, meetings_by_id)
     → 最初の問題で diagnostic を返す（hash_mismatch / raw_missing / speech_gap）
     → 失敗なら policy 適用して continue（**fetch_summary_results も repair も呼ばない**）
5. fetch_summary_results
     → bare AnthropicError → results_expired → RESUBMIT（この時点で verify は通過済み＝再送は意味がある）
6. _repair_unusable_results
7. assemble_from_manifest（内部でも verify 相当を再利用して二重定義を避ける）
```

これにより:

| 状況 | 観測 | 結果 |
|---|---|---|
| raw 変更 + results まだある | step 4 で `hash_mismatch` | BLOCKED。課金ゼロ |
| raw 変更 + results 期限切れ | step 4 で `hash_mismatch`（fetch 前） | BLOCKED。**`results_expired` に化けない** |
| raw 一致 + results 期限切れ | step 4 OK → step 5 `results_expired` | RESUBMIT（正当） |
| raw 欠落 | step 3 | HOLD / LOST |

`verify_manifest_against_raw` は builder（`build_summary_request` 等）経由のみ。手組み禁止（invariants + `test_determinism.py`）。

### 3.6 HOLD（retry を焼かない）

`raw_missing` / `raw_date_missing` / `speech_gap`:

- 再送しない
- `retry_count` を増やさない
- sidecar を書き換えない
- `held_dates` + error annotation
- `_record_resume_verdict` は **呼ばない**

現行バグ（`record_terminal` を rebuild 失敗前に呼ぶ）を構造的に解消する。本スコープに含める（T2b）。

### 3.7 G5 — stale-schema と retry 閾値

**stale-schema**（`is_current_schema` 偽）:

- `hard_fail = True` をやめる
- reason `stale_schema` → BLOCKED
- sidecar 非改変（または `mark_blocked` のみ。schema 自体は触らない）
- `held_dates` + `_annotate("error", ...)`
- コメントに「日付単位ゲート後、exit 1 で publish を止める根拠は消えた。再送は hash 体系が違い安全でないので保持」と残す

**retry 閾値**（`retry_count >= HARD_FAIL_RETRIES`）:

- Collect の `hard_fail` / exit 1 をやめる
- reason `retry_exhausted` → BLOCKED（再送しない、sidecar 保持）
- `held_dates` で最終ステップが赤
- 閾値未満の RESUBMIT 失敗だけ従来どおり再送

`CollectResult["hard_fail"]` は実質常に False（crash 以外）。フィールドは残して docstring を更新（rename はしない — 差分肥大を避ける）。`main(--collect-pending)` は hard_fail 時だけ exit 1 だった箇所を、**クラッシュ以外 0** に合わせる。

CLAUDE.md の exit code 表:

> `--collect-pending` は hard fail（retry threshold / older schema）で 1

を **「crash / usage error のみ 1。held/abandoned/systemic は output + 最終ステップで赤」** に同一コミットで更新。

### 3.8 abandon（#66）

```python
_annotate("error", ...)  # bare ::warning:: 廃止
abandoned_dates.append(date_str)
bs.delete_sidecar(path)
# hard_fail にしない。systemic_dates に載せない。
```

annotation が言ってよいこと:

- この sidecar が担当していた **未回収 threads** が恒久ロスト
- date / batch id / age
- API rejection ではない

言ってはいけないこと:

- 「この日付は空」「threads for this date were lost」「never be published」（既存 thread がありうる）

### 3.9 Collect の返却値と GitHub outputs

```python
{
    "hard_fail": bool,          # crash 経路以外 False 予定
    "systemic_dates": list,
    "suspect_dates": list,
    "held_dates": list,         # HOLD + BLOCKED（hash_mismatch, stale_schema, retry_exhausted, unknown）
    "abandoned_dates": list,
    "diagnostics": list,
}
```

`_write_github_output` に `held_dates` / `abandoned_dates` を追加。重複除去は既存と同型。

**held と systemic/suspect は互いに排他。** ある date を held に載せたら `_record_resume_verdict` で systemic/suspect に載せない。

### 3.10 最終ステップ（workflow）

`daily-batch.yml` 最終ステップに:

```yaml
COLLECT_HELD: ${{ steps.collect.outputs.held_dates }}
COLLECT_ABANDONED: ${{ steps.collect.outputs.abandoned_dates }}
```

集計（**既存 `FAIL_DATES` パターンを壊さない** — `test_systemic_failure.py` が pin している）:

```bash
# systemic / suspect: 従来どおり
FAIL_DATES="$SYSTEMIC"
if [ "$SUSPECT_N" -ge 2 ]; then
  FAIL_DATES="$FAIL_DATES$SUSPECT"
fi

# held / abandoned: 1 日付でも無条件で赤（suspect 閾値に混ぜない）
HELD=...
ABANDONED=...

if non-empty FAIL_DATES or HELD or ABANDONED; then
  # カテゴリ別 annotation（一括で「Nothing reached the site」にしない）
  [ -n FAIL_DATES ] && ::error::Nothing this run produced reached the site on: ...
  [ -n HELD ]       && ::error::Pending batches held for human decision: ...
  [ -n ABANDONED ]  && ::error::Permanently lost uncollected threads (sidecar abandoned): ...
  exit 1
fi
```

`SUSPECT_N -ge 2` は **最終ステップのみ**（変更なし）。

### 3.11 pending ゲート日付単位化（#44 第3項）

1. Collect の `has_pending` output を **削除**
2. Summarize step:

```yaml
if: steps.dates.outputs.list != ''
```

3. ループ先頭:

```bash
for d in $DATES_LIST; do
  if [ -f "data/pending-batches/$d.json" ]; then
    echo "Sidecar present for $d — Collect owns this date; skipping"
    continue
  fi
  # ... summarize ...
  if [ -f "data/pending-batches/$d.json" ]; then
    echo "Batch for $d is pending — stopping further submits this run"
    break
  fi
done
```

| 制御 | 役割 |
|---|---|
| 先頭 `continue` | 過去 run 由来 sidecar がある日付だけ除外（cross-run） |
| 末尾 `break` | この run の **新規** in-flight は最大 1 本（run 内） |

保持中 N 本 + 新規 1 本は **許容**（G4 の可用性のため）。容量上限は本スコープ外。

### 3.12 annotation 文面（hash_mismatch / BLOCKED）

必須要素:

- date, meeting_id, custom_id, sidecar path, batch id, submitted_at
- reason（`hash_mismatch` 等）
- **自動再送していない / retry_count 未消費**
- API rejection ではない
- batch result 保持の **推定**残日数（`submitted_at` + 約29日。「正確な期限」と断定しない）
- `blocked.since` があるなら経過 **日数**（「N mornings」と断定しない — 実行時刻ずれで不正確）
- 人間の選択肢（断定コマンドではなく制約付き）:
  1. **rescue**: 対象日の raw が lookback 内で再取得可能か確認 → 必要なら明示 fetch で raw を確保 → その後 sidecar を除去して再要約（次の自動 run に任せない場合は手動 `--date`）
  2. **discard**: 未回収分を捨てる判断のうえで sidecar 除去
  3. **wait/revert**: 原因が自分の schema/prompt 変更なら revert して次 Collect で通常回収

**禁止**: 単純な `git rm ... すれば次の朝が直す` の断言。lookback 外では再要約されない。

abandon 文面は §3.8 の規律に従う。

### 3.13 運用テキスト更新（同一コミット必須）

| 箇所 | 更新内容 |
|---|---|
| `CLAUDE.md` | exit 1 の意味、#65 の「3 朝で doomed・今すぐ消す」、stale-schema hard-fail 記述、SCHEMA_VERSION 着地条件は **維持**（held でも results は ~29 日で消える） |
| `daily-batch.yml` 最終ステップ help | hash_mismatch が retry で焼き切れる記述を削除。HOLD/BLOCKED と RESUBMIT の違い、held/abandoned の見方 |
| `notify-on-failure` issue 本文 | 「Nothing reached the site on」だけでなく held / abandoned / Collect annotations を見るよう追記 |
| Collect ステップ先頭コメント | hard-fail 列挙を実態に合わせる |

### 3.14 notify-stuck-batch との整合

held sidecar は `STUCK_AGE_DAYS`（2.0）超で毎日 stuck コメントが付く。

本スコープでの最低限:

- `check_stuck_batches.py`（または出力整形）で、`blocked` がある sidecar は **held for human decision (reason=..., since=...)** と明示し、「retries 0 = 未試行の一時詰まり」に見えないようにする
- issue dedup は既存の `pipeline-failure` ラベル流用のまま（連日コメントは許容。静かな放置より優先）。acknowledgement 状態機械は非目標

### 3.15 `_apply_failure_policy` の戻りと Collect ループ

戻り値例: `"resubmitted" | "held" | "blocked" | "retry_exhausted_held"`

Collect:

- BLOCKED/HOLD → `held_dates`、annotation、continue（次 sidecar へ）
- RESUBMIT で submit 成功 → 従来どおり
- RESUBMIT で閾値到達 → BLOCKED/`retry_exhausted`、held_dates、**exit 1 にしない**
- abandon → `abandoned_dates`
- ループ全体の成功時 `hard_fail` は False

### 3.16 テスト（受け入れ）

```bash
python -m pytest scripts/tests
npm run lint && npm run validate
```

| ID | 内容 |
|---|---|
| **T1** | `hash_mismatch`: submit フェイク 0 回、`retry_count` 不変、attempts 不変、sidecar 残存、`held_dates==[date]`、`systemic_dates`/`suspect_dates` に **載らない**、`hard_fail is False`、error annotation に reason と no resubmit |
| **T1b** | results 取得が期限切れを投げる条件でも、raw が mismatch なら **submit されない**（verify-before-fetch）。G1 長期地平 |
| **T2** | `missing_result`: 再送 1 回、`retry_count` +1、`held_dates` 空 |
| **T2b** | `speech_gap` / `raw_missing` / `raw_date_missing`: 再送なし・retry 不変・held |
| **T2c** | `stale_schema`: held、hard_fail False、次 sidecar も処理される |
| **T2d** | `retry_exhausted`: 再送なし、held、Collect exit 0 |
| **T3** | abandon: sidecar 削除、`abandoned_dates`、`::error::`、hard_fail False、systemic に混ぜない |
| **T4** | YAML: `has_pending` 無し、Summarize は `list != ''` のみ、**Python 呼び出し前**に sidecar `-f` + `continue`、**後**に sidecar + `break`、日付パスと invocation の位置関係まで固定 |
| **T5** | exit 3/4 と YAML 分岐の一致（既存維持）。held/abandoned 配線。`FAIL_DATES="$FAIL_DATES$SUSPECT"` パターン維持 |
| **T6** | policy 網羅: `_diagnostic` 第1引数リテラル + `TERMINAL_FAILURES` + 直渡し reason（`results_expired` 等）+ `stale_schema` / `retry_exhausted` がすべて `FAILURE_POLICY` に明示。未知 default の実行時は BLOCKED でも、**列挙漏れは CI 赤** |
| **T7** | `TERMINAL_FAILURES ⊆ RESUBMIT`、`"assemble_failed"` は未登録 |
| **T8** | `main(--collect-pending)`: hold/abandon のみなら process exit 0、4 種 output が GITHUB_OUTPUT に出る |

実装時 break-to-check:

- `hash_mismatch` を RESUBMIT にすると T1 が落ちる
- verify を fetch 後に戻すと T1b が落ちる
- loop 先頭 `continue` 削除で T4 が落ちる
- final から ABANDONED を外すと T3/T5 が落ちる

### 3.17 変更ファイル一覧

| ファイル | 変更 |
|---|---|
| `scripts/pipeline/batch_state.py` | policy テーブル、`failure_policy`、`mark_blocked` / `clear_blocked` |
| `scripts/summarize.py` | verify-before-fetch、`_apply_failure_policy`、reason 伝播、held/abandoned 記録、stale-schema/retry_exhausted、abandon error 化 |
| `scripts/check_stuck_batches.py` | held/blocked の表示（最低限） |
| `.github/workflows/daily-batch.yml` | ゲート日付単位、outputs、最終ステップ、help、notify 本文 |
| `CLAUDE.md` | exit 契約、#65/#schema 運用、SCHEMA_VERSION 着地条件維持 |
| `scripts/tests/test_batch_state.py` | policy 単体 |
| `scripts/tests/test_resume.py` または `test_resume_failure_policy.py` | T1–T3, T1b, T2b–d |
| `scripts/tests/test_systemic_failure.py` | T4, T5 |

### 3.18 データフロー要約

**hash_mismatch の朝**

1. Collect: poll ended → load raw → **verify 失敗** → BLOCKED →（初回のみ）mark_blocked/save → held_dates + error → exit 0  
2. Summarize: 当該 date のみ continue skip、他 date 処理  
3. commit/publish  
4. 最終ステップ: held で job 赤 → notify-on-failure  

**abandon の朝**

1. raw 無し + abandonable → error annotation → abandoned_dates → delete sidecar → exit 0  
2. publish 継続 → 最終ステップで赤  

**正当な results_expired**

1. verify OK → fetch 失敗 → RESUBMIT → 新 batch（hash は現 raw と一致する rebuild）  

### 3.19 実装順序（planner 向け）

1. `batch_state.py` に policy + tests（T6/T7）  
2. `verify_manifest_against_raw` + Collect 順序入替 + T1/T1b  
3. `_apply_failure_policy` と assemble_failed 廃止 + T2/T2b  
4. stale-schema / retry_exhausted を BLOCKED + T2c/T2d  
5. abandon → abandoned_dates + T3  
6. workflow ゲート + final step + T4/T5  
7. 運用テキスト + stuck 表示  
8. 全体 pytest / lint / validate  

---

## 4. 残存リスク

1. **held が最大 ~31 日毎朝赤い** — 意図的だがアラーム疲れ。経過日数と推定 result 期限を annotation に出す。#44 第1項で窓が短くなれば緩和。  
2. **in-flight 総数の上限なし** — 保持 N + 新規 1/run。Anthropic 長期障害時に共有 1800s budget が古い sidecar から消費され回収が偏る。容量 policy は非目標。  
3. **`thread_build_failed` = RESUBMIT の確度** — deterministic な assemble bug なら 3 回焼いて BLOCKED。頻発なら policy を BLOCKED へ（retry 回数増は禁止）。  
4. **abandon の恒久記録が annotation + step output のみ** — 後続ステップ crash で最終ステップが skip されると、issue 本文が汎用のまま。tombstone ファイルや `gh issue create` 専用経路は本スコープで **採用しない**（複雑さ vs 頻度）が、最終ステップを `if: always()` にすることは既存テストが意図的に拒否しているため触らない。残リスクとして運用でログ保持に依存。  
5. **notify-stuck + held の毎日コメント** — 最低限ラベル付けするが、コメント洪水の dedup 強化は別設計。  
6. **result 期限は推定のみ** — sidecar に正確な expiry が無い。  
7. **continue した日付の遅着 meeting** — その日の新規会議も要約されない（グローバルゲートよりは改善、完全解消ではない）。  
8. **repair の残存課金** — verify-before-fetch 後は hash_mismatch 朝の repair は走らない。verify 通過後の部分 unusable + 別 thread 失敗は別問題で、本仕様の一次対象外。  

---

**要約**: Claude 案を骨格に、Codex のシグナル分離と運用規律を移植し、両批判の Critical（verify-before-fetch / 無条件赤の held_dates / results_expired 化け / exit 1 の一貫撤廃 / ドキュメント同期）を仕様に固定した。これで planner は実装計画に落とせる。
