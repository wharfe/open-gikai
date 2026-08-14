# Spec — resume 失敗レジームの3分類 / 恒久ロストの昇格 / pending ゲートの日付単位化

**Gate1 出力。** 対象 issue: #65, #66, #44 第3項。前提 commit: `main` @ `3b9e7b2`（2026-08-08）。
経緯: `brief.md` → `design-claude.md` / `design-codex.md` → `critique-of-*.md` → `verdict.md`（裁定: grok）。
本 spec は裁定の確定仕様に、Claude 側の裏取りで見つかった**裁定の抜け2件**（§3.7.3, §3.13）を加えたもの。

---

## 1. 根っこ

resume が失敗したとき、コードは一度もこの問いを立てていない:

> **「明日また同じことをしたら、うまくいくのか？」**

`_retry_or_hardfail` は理由を見ずに「3回まで再送、それ以上は hard fail」を適用する。だから
決定論的に失敗する `hash_mismatch` に3回課金し（#65）、何も再送していない raw 欠落でも
retry 予算を焼き、「もう二度と直らない」abandon だけが緑で通る（#66）。

答えは3レジーム + 1終端状態しかない。これをコードの一級概念にする。

| レジーム | 定義 | 再送 | retry 消費 | sidecar | Collect exit | job |
|---|---|---|---|---|---|---|
| **RESUBMIT** | バッチ側の失敗。新しい roll に意味がある | する | する | 更新 | 0 | 既存どおり |
| **HOLD** | 手元状態の欠落。後の run で復旧しうる | しない | **しない** | **不変** | 0 | `held_dates` で赤 |
| **BLOCKED** | 現在の入力に対し決定論的。人間判断が要る | **しない** | **しない** | `blocked` 記録 | 0 | `held_dates` で赤 |
| **LOST**（abandon） | 回収不能確定。恒久ロスト | — | — | **削除** | 0 | `abandoned_dates` で赤 |

**全レジームで publish は継続する。** Collect の exit 1 は crash / usage error のみ（#52）。
これを可能にするのが #44 第3項（ゲートの日付単位化）であり、3つの issue が1本になる理由。

---

## 2. スコープ

**対象**: #65 / #66 / #44 第3項。
**非対象**: #44 第1項（abandon 判定を lookback 窓ベースへ）・第2項（raw 自動再取得）、#60、#57、
要約プロンプト / grouping の変更、`ABANDON_AGE_DAYS` の値変更、`compute_input_hash` の入力集合変更。
**`SCHEMA_VERSION`**: bump **しない**（hash 対象 param を変えないため）。

---

## 3. 確定仕様

### 3.1 `batch_state.py` — 純粋な policy テーブル

```python
RESUBMIT = "resubmit"
HOLD     = "hold"
BLOCKED  = "blocked"

FAILURE_POLICY = {
    "canceled":            RESUBMIT,
    "expired":             RESUBMIT,
    "results_expired":     RESUBMIT,
    "missing_result":      RESUBMIT,
    "thread_build_failed": RESUBMIT,  # least confident entry — see §5.3
    "raw_missing":         HOLD,
    "raw_date_missing":    HOLD,
    "speech_gap":          HOLD,
    "hash_mismatch":       BLOCKED,
    "stale_schema":        BLOCKED,
    "retry_exhausted":     BLOCKED,
}

def failure_policy(reason: str) -> str:
    # Unknown -> BLOCKED: never charge, never delete data, red via held_dates.
    # Coverage is enforced statically (T6); this default is the runtime net,
    # NOT a licence to add a reason without a policy.
    return FAILURE_POLICY.get(reason, BLOCKED)
```

- **未知 reason は例外にしない。** Collect の例外は `set -e` で publish を止める。この repo の
  fail-closed は「課金せず・データを消さず・赤くする」であって、クラッシュではない。
  列挙漏れは CI（T6）で落とす。
- `"assemble_failed"` は policy 語彙から**廃止**（詳細 reason を渡す）。
- `TERMINAL_FAILURES ⊆ RESUBMIT` をテストで固定（T7）。

ヘルパ:

```python
def mark_blocked(sidecar, reason, at, meeting_id=None, custom_id=None) -> bool:
    """Write sidecar['blocked']. Returns True iff on-disk content would change.
    ``since`` is sticky (first write wins)."""

def clear_blocked(sidecar) -> None: ...
```

- `blocked` は**制御フローの一次ソースにしない**。一次は毎朝の無料検証（§3.2）。
  用途は forensics と annotation の経過日数のみ。
- 既存 sidecar は `sidecar.get("blocked")` で読む。フィールド無しで読めること。

### 3.2 検証順序の固定 — G1 の構造的保証（最重要）

ended バッチの処理順を**固定する**:

```
0. abandon ゲート（#69 で追加。regime 判定より前・poll より前）
     age（最新 attempt 起点）> ABANDON_AGE_DAYS かつ「rebuild の材料が無い」→ abandon(LOST)
     それ以外（判定できない場合を含む）→ 消さずに下の regime へ落とす（後述）
1. poll → ended
2. load raw
3. raw が空 → HOLD(raw_date_missing)                      ← results に触らない（現状どおり）
                                                             abandon は step 0 が持つ
4. verify_manifest_against_raw()                          ← 無料。builder 経由のみ
     失敗 → policy 適用して continue（fetch も repair も呼ばない）
5. fetch_summary_results()                                ← ここで初めて results に触る
     bare AnthropicError → results_expired → RESUBMIT（verify は通過済み＝再送に意味がある）
6. _repair_unusable_results()
7. assemble_from_manifest()                               ← 内部で verify を再利用（二重定義禁止）
```

**step 0 が raw の有無を「推測せず観測する」理由。** age が閾値を超えていれば「既定の lookback
（30日）では raw は窓の外」は正しい。しかし `lookback_days` は workflow_dispatch 入力で365まで
受け付け、**held sidecar を人間が救出する手順そのものが窓を広げて raw を取り直すこと**である。
年齢から raw 不在を導出すると、その救出ランは raw を復元した同じ job で sidecar を削除する
（次の定時ランは lookback 30 に戻るので、回収可能だった batch が恒久ロストになる）。
よって削除する側は disk を見る。逆に「age 超 + 材料有り」は results は失効済みなので
そのまま回収はできず、regime 判定に落として held（人間の判断）として報告する。

「材料が無い」の判定は `_reason_not_to_abandon` に1箇所だけ置き、**確立できないことは全部
「消さない」に倒す**（この pipeline で唯一の不可逆操作なので）。具体的に消さない条件:
- sidecar の `date` が filename と食い違う / `date` が文字列でない / filename が日付でない
  → どの日付の raw を見るべきかが不明。**filename は命名規約であって証拠ではない**（rename・
    merge 解決・コピーで容易にズレる）ので、単独では削除を許可しない。逆に `date` を無条件に
    信じるのも不可（raw が disk にあるのに別日付を見て「無い」と判定して消す）。両者が一致した
    ときだけ「その日付の raw」を見る。
- `date` フィールドが無い → filename を日付に**昇格させない**。代わりに日付を必要としない問い
  （manifest の meeting_id が raw のどこかに1件でもあるか）で判定する。残す場合は下流へ流さず
  `sidecar_has_no_date` として held にする（poll / rebuild / assemble / retry 閾値の各分岐が
  `sidecar["date"]` を参照するため、通すとクラッシュ位置が数行ずれるだけになる）。
  abandon する場合、記録する日付は filename 由来なので annotation に UNVERIFIED と明記する。
- raw が読めない（JSON 壊れ等）→ 「読めない」は「無い」ではない。例外を上げると `set -e` で
  Collect が落ちてその朝の publish が止まる（#65）ので握って hold。
- raw はあるが manifest が読めない → rebuild に何が必要かが不明。
- raw に manifest の meeting_id が1つ以上ある → rebuild の余地がある。

逆に「その日付の raw はあるが manifest の meeting が1つも無い」（例: NDL の batch に対して
kantei の raw だけがある）は abandon する。そこを held にすると #69 の無期限 hold が別の
入口から復活する。

**なぜ「repair の前」では足りないか。** `fetch_summary_results` は現状 assembly より前にあり
（`summarize.py:1272-1286`）、batch results は約29日で失効する。検証が fetch の後だと、
raw が変わった sidecar は day 1〜28 は `hash_mismatch` でも、day 29 に観測 reason が
`results_expired`（= RESUBMIT）へ**化ける**。そこで課金再送が復活し、翌朝また mismatch、
29日後にまた再送 … 約90日で retry 閾値に到達する。**#65 が「3日で死ぬ」から
「90日かけて死ぬ」に伸びるだけ**で、しかも人が忘れた頃に来る。

> **注意（設計途中の誤りの訂正）**: 検証前倒しの主目的は「repair の課金を防ぐこと」ではない。
> `_repair_unusable_results` は**すでに hash チェックを持っており**、不一致の custom_id は
> 再発行しない（`summarize.py:947-951`）。前倒しの主目的は上記のレジーム化けの阻止であり、
> repair 節約は副次効果（doomed な日付の *他の* thread への無駄な同期再発行が止まる）。

| 状況 | 観測 | 結果 |
|---|---|---|
| raw 変更 + results 健在 | step 4 で `hash_mismatch` | BLOCKED。課金ゼロ |
| raw 変更 + results 失効 | step 4 で `hash_mismatch`（fetch 前） | BLOCKED。**化けない** |
| raw 一致 + results 失効 | step 4 通過 → step 5 | RESUBMIT（正当） |
| raw 欠落 | step 3 | HOLD / LOST |

`verify_manifest_against_raw` は `build_summary_request` 経由でのみリクエストを組む
（CLAUDE.md「Summary Layer Invariants」#2、`test_determinism.py` の AST sweep）。

### 3.3 reason の伝播

assembly 失敗を一律 `"assemble_failed"` にするのをやめ、`diagnostic["reason"]` を policy に渡す。

`_retry_or_hardfail` → `_apply_failure_policy` に置換:

1. `policy = bs.failure_policy(reason)`
2. **RESUBMIT 以外は `record_terminal` より前に return**（HOLD / BLOCKED）
3. RESUBMIT のみ: `record_terminal` → 閾値判定 → rebuild → submit

これが現行バグ（rebuild 失敗の**前**に `record_terminal` を呼ぶので、何も再送していないのに
retry 予算が減る）を構造的に解消する。3朝続けば hard fail に達していた。

戻り値: `"resubmitted" | "held" | "blocked"`。

### 3.4 BLOCKED の毎朝の挙動

- 初回: `mark_blocked` → **content が変わったときだけ** `save_sidecar` +（`--ci-commit` なら）commit。
- **2日目以降 content 不変なら save も commit もしない。** `_git_commit_sidecar` は変更なしの
  `git commit` が exit 1 を返すと `_report_dead_net` 経由で
  `::error::「投函済みバッチが orphan する」` を出す（`summarize.py:130-163`）。毎朝の嘘の警報になる。
- 短絡しない: 翌朝も poll → raw ロード → 無料検証 → まだ BLOCKED なら赤 annotation → 再送しない。
  raw が元に戻れば自動で通常 assembly に復帰する。
- RESUBMIT 系失敗に転じた場合は `clear_blocked` してから RESUBMIT 処理。

### 3.5 HOLD

`raw_missing` / `raw_date_missing` / `speech_gap`: 再送しない・`retry_count` を増やさない・
sidecar を書き換えない・`held_dates` + error annotation。

### 3.6 シグナルの分離 — `_record_resume_verdict` を HOLD/BLOCKED に使わない

**これが Codex 案から移植した最重要部品。** 現行の hash_mismatch 経路は
`_record_resume_verdict` → `_retry_or_hardfail` の2段だが、前者は対象日に既存 thread があり
summary 対象 meeting が1件なら `EXIT_SUSPECT_FAILURE`（= 黄）に落とす（`summarize.py:286, 1109`）。
つまり**再利用すると G2（必ず赤）が破れる**。さらに同じ日付が held と suspect の両方に載り、
最終ステップの `SUSPECT_N -ge 2` を単独で越えさせる（`_record_resume_verdict` の docstring 自身が
警告している事象）。

したがって:

- HOLD / BLOCKED → 専用の `_record_held_sidecar`（annotation + `held_dates` + diagnostics のみ）
- **held に載せた日付は `systemic_dates` / `suspect_dates` に載せない**（相互排他）
- `held_dates` は閾値なし。**1件でも無条件で赤**

### 3.7 G5 — hard_fail の全廃

#### 3.7.1 stale-schema
`hard_fail = True` をやめ、reason `stale_schema` → BLOCKED → `held_dates` + `_annotate("error", ...)`。
bare `print("::error::...")` も `_annotate` へ（#67 の方針に沿う）。
コメントに残す: 「日付単位ゲート後、exit 1 で publish を止める根拠は消えた。ただし hash 体系が
違うので再送は安全でなく、保持する」。

#### 3.7.2 retry 閾値到達
`_apply_failure_policy` 内の `should_hard_fail` → reason `retry_exhausted` → BLOCKED。
再送しない・sidecar 保持・`held_dates` で赤・**exit 1 にしない**。

#### 3.7.3 【裁定の抜け①】ループ先頭の `should_hard_fail` も同じ扱いにする
`summarize.py:1209-1212` に**2つ目の hard_fail 地点**がある（poll する前のチェック）。
裁定はここを名指ししていない。ここも `retry_exhausted` → BLOCKED → `held_dates` にし、
`hard_fail = True` を消す。**片方だけ直すと、閾値到達 sidecar は翌朝この先頭チェックで
exit 1 に戻る。**

結果として `CollectResult["hard_fail"]` は crash 以外 False になる。フィールドは残し
docstring を更新する（rename は差分肥大なので行わない）。

### 3.8 abandon（#66）

```python
_annotate("error", ...)          # bare ::warning:: を廃止
abandoned_dates.append(date_str)
bs.delete_sidecar(path)
# hard_fail にしない。systemic_dates に混ぜない。
```

`abandoned_dates` は閾値なし・1件でも赤。sidecar を削除するので同じ日付が繰り返し赤くなることはない。

**言ってよいこと**: この sidecar が担当していた**未回収 threads** が恒久ロストしたこと、date、
batch id、age、API rejection ではないこと。
**言ってはいけないこと**: 「この日付は空」「never be published」。sidecar は既存公開日への
遅着 meeting でも作られるので、既存 thread がありうる（`summarize.py:1247` 付近の現行文言も同じ誤り）。

### 3.9 Collect の返却値と step outputs

```python
{
    "hard_fail": bool,        # crash 経路以外 False
    "systemic_dates": list,
    "suspect_dates": list,
    "held_dates": list,       # HOLD + BLOCKED（hash_mismatch / stale_schema / retry_exhausted / unknown）
    "abandoned_dates": list,
    "diagnostics": list,
}
```

`_write_github_output(**values)` は kwargs 受けなので（`summarize.py:90`）追加は呼び出し側1箇所
（`summarize.py:2031`）。

### 3.10 workflow 最終ステップ

```yaml
COLLECT_HELD: ${{ steps.collect.outputs.held_dates }}
COLLECT_ABANDONED: ${{ steps.collect.outputs.abandoned_dates }}
```

**既存の `FAIL_DATES` パターンを壊さない。** `test_systemic_failure.py:886-888` が
`'FAIL_DATES="$FAIL_DATES$SUSPECT"'` をリテラルで pin しており、コメントに
「これが無いと suspect が集められて報告されて永久に job を落とさない状態が緑のまま通る」と
明記されている。意図的に脆い保険なので、リネームで巻き込まない。

```bash
FAIL_DATES="$SYSTEMIC"
if [ "$SUSPECT_N" -ge 2 ]; then
  FAIL_DATES="$FAIL_DATES$SUSPECT"
fi
# held / abandoned は閾値なし・別カテゴリとして別行で報告
```

annotation はカテゴリ別に出す（一括で "Nothing reached the site" にしない）:

- `::error::Nothing this run produced reached the site on: …`
- `::error::Pending batches held for a human decision: …`
- `::error::Permanently lost uncollected threads (sidecar abandoned): …`

`SUSPECT_N -ge 2` は最終ステップのみ（変更なし）。

### 3.11 pending ゲートの日付単位化（#44 第3項）

1. Collect の `has_pending` output を削除（参照は `daily-batch.yml:135` の1箇所のみ）
2. Summarize step: `if: steps.dates.outputs.list != ''`
3. ループ:

```bash
for d in $DATES_LIST; do
  if [ -f "data/pending-batches/$d.json" ]; then
    echo "Sidecar present for $d — Collect owns this date; skipping"
    continue                       # cross-run: この日付だけ除外
  fi
  # ... summarize ...
  if [ -f "data/pending-batches/$d.json" ]; then
    break                          # run 内: 新規 in-flight は最大1本（既存、維持）
  fi
done
```

保持中 N 本 + 新規1本は**許容**（G4 の可用性のため）。容量上限は本スコープ外（§5.2）。

### 3.12 annotation の文面

**hash_mismatch / BLOCKED に必須の要素**: date / meeting_id / custom_id / sidecar path /
batch id / submitted_at / reason / **自動再送していないこと・retry を消費していないこと** /
API rejection ではないこと / batch results の**推定**残日数（`submitted_at` + 約29日。
「正確な期限」と断定しない） / `blocked.since` からの経過**日数**（「N mornings」と断定しない —
実行時刻のずれや手動 rerun で不正確）。

原因は**断定せず候補として**挙げる（CLAUDE.md:「an operator sent to hunt a 400 that never
happened … has had their morning taken」）。確度順:
(1) `compute_input_hash` の param 集合を変えたのに `SCHEMA_VERSION` を bump し忘れた
（CLAUDE.md がこの症状を明記している）、(2) raw が再取得されて差異が出た（NDL の訂正、fetcher の変更）。

**人間の選択肢は制約付きで示す。単純な `git rm` を断定してはならない**:

1. **rescue**: 対象日の raw が lookback 窓内で再取得可能か確認 → 必要なら明示 fetch で raw を
   確保 → その後 sidecar を除去して再要約
2. **discard**: 未回収分を捨てる判断のうえで sidecar 除去
3. **revert**: 原因が自分の schema / prompt 変更なら revert し、次の Collect で通常回収

理由: `data/raw` は runner 上の一時データで、次回の `DATES_LIST` はその朝に取得できた
lookback 内の日付だけ（`daily-batch.yml:100-105`）。lookback 境界付近で sidecar を消すと、
翌朝には窓外で再要約されず、検証可能な batch 結果も捨てた状態＝**恒久ロスト**になる。
#44 第2項が非目標である以上、自動復旧を約束できない。

### 3.13 運用テキストの同時更新（同一コミット必須）

挙動を変えて説明文を放置すると、**更新し忘れたドキュメントが、設計が防ごうとした誤操作を誘発する**。

| 箇所 | 現在の記述 | 更新内容 |
|---|---|---|
| `daily-batch.yml:434-436` | 「hash_mismatch は retry threshold で hard-fail してその日付は失われる。対処: sidecar を消せ（#65）」 | 到達しなくなる。HOLD/BLOCKED と RESUBMIT の違い、held/abandoned の読み方へ差し替え |
| `daily-batch.yml:429-431` | 「hash_mismatch/speech_gap/raw_missing point at raw」 | この3つがレジームとして別扱いになることを反映 |
| `daily-batch.yml:465`（notify-on-failure 本文） | 「run summary の "Nothing reached the site on" を見ろ」 | held / abandoned だけで赤い run ではその文字列が存在しない。Collect annotation と held/abandoned を見るよう追記 |
| Collect ステップ先頭コメント（`daily-batch.yml:115-121`） | hard-fail の列挙 | 実態に合わせる |
| **【裁定の抜け②】`summarize.py:2035-2040`** | `main()` の exit コメント「a sidecar past its retry threshold or written by an older schema」 | どちらも exit 1 でなくなる。裁定はこのコメントを列挙していないが、exit 1 の意味を説明している唯一の場所なので同時に直す |
| `CLAUDE.md` | exit code 表、#65 の「3朝で doomed・今すぐ sidecar を消せ」、stale-schema が hard-fail する記述 | 更新。**`SCHEMA_VERSION` の着地条件（`git ls-files data/pending-batches/` が空のときのみ）は維持**と明記 — hard-fail 撤廃で「bump してよい」と誤解されると、held のまま29日で results が失効して恒久ロストになる |

### 3.14 `notify-stuck-batch` との整合

`notify-stuck-batch`（`daily-batch.yml:479-510`、`if: always()`）は `check_stuck_batches.py` が
`STUCK_AGE_DAYS`（2.0）超の sidecar を出力すると毎日コメントする。BLOCKED な sidecar は
retry を消費しないので、出力は毎日「retries 0」= 「まだ何も試していない一時的な詰まり」に見える。
一方 abandon は sidecar を消すのでこの通知から消える。**#66 が直そうとした重大度の逆転が
別経路で再生産される。**

本スコープでの最低限: `check_stuck_batches.py` の出力で、`blocked` を持つ sidecar は
`held for human decision (reason=…, since=…)` と明示する。
issue dedup の強化・acknowledgement 状態機械は非目標（連日コメントは、静かな放置より良い）。

**#71 での追記（上の「最低限」は残すが、それだけでは足りなかった）。** ラベル付けは
重大度の誤読を防ぐが、**同じ日付が2つの通知経路から毎朝コメントされる**という重複は消えない
（held は run を赤くするので `notify-on-failure` が既に報告している）。加えて HOLD レジームは
意図的に sidecar を書き換えないので `blocked` マーカーが無いことがあり、ラベル付けだけでは
held を判別しきれない。したがって dedup は**日付**で行う: `fetch-and-summarize` の
`held_dates` / `abandoned_dates` を job outputs として公開し、`check_stuck_batches.py`
`--exclude-dates` に渡す。
`if: always()` は維持する。この job 固有のシグナル「2日以上 in-flight」は run を赤くしないので、
「緑の朝だけ喋る」方式にすると**他が壊れている間ずっと0回**になる — results が約29日で失効し
31日で abandon される、まさにその窓で警告が消える。`if: success()` は採らない。

---

## 4. 受け入れ基準とテスト

```bash
python -m pytest scripts/tests
npm run lint && npm run validate
```

| ID | 内容 |
|---|---|
| **T1** | `hash_mismatch`: submit フェイク0回、`retry_count` 不変、`attempts` 不変、sidecar 残存、`held_dates==[date]`、**`systemic_dates`/`suspect_dates` に載らない**、`hard_fail is False`、annotation に reason と「再送していない」旨 |
| **T1b** | results 取得が期限切れを投げる条件でも、raw が mismatch なら submit されない（verify-before-fetch。G1 の長期地平） |
| **T2** | `missing_result`: 再送1回、`retry_count` +1、`held_dates` 空 |
| **T2b** | `speech_gap` / `raw_missing` / `raw_date_missing`: 再送なし・`retry_count` 不変・held |
| **T2c** | `stale_schema`: held、`hard_fail is False`、**次の sidecar も処理される** |
| **T2d** | `retry_exhausted`: 再送なし・held・Collect exit 0。**ループ先頭経路と `_apply_failure_policy` 経路の両方**（§3.7.3） |
| **T3** | abandon: sidecar 削除、`abandoned_dates`、`::error::`、`hard_fail is False`、systemic に混ぜない |
| **T4** | YAML: `has_pending` が無い、Summarize の `if` は `list != ''` のみ、**Python 呼び出しより前**に sidecar `-f` + `continue`、**後**に sidecar + `break`。位置関係まで固定する（文字列の有無だけでは逆条件や順序破壊を検出できない） |
| **T5** | exit 3/4 と YAML 分岐の一致（既存維持）、held/abandoned の配線、`FAIL_DATES="$FAIL_DATES$SUSPECT"` パターン維持 |
| **T6** | policy 網羅: `_diagnostic` 第1引数リテラル + `TERMINAL_FAILURES` + 直渡し reason（`results_expired`）+ `stale_schema` / `retry_exhausted` が**すべて** `FAILURE_POLICY` に明示エントリを持つ。実行時 default は BLOCKED でも、**列挙漏れは CI 赤** |
| **T7** | `TERMINAL_FAILURES ⊆ RESUBMIT`、`"assemble_failed"` が未登録であること |
| **T8** | `main(--collect-pending)`: hold/abandon のみなら exit 0、4種の output が `GITHUB_OUTPUT` に出る |
| **T9** | BLOCKED 2日目: sidecar content 不変なら `_git_commit_sidecar` が呼ばれない（偽の dead-net error の回帰） |

**break-to-check（実装時に必ず実施）**: `hash_mismatch` を RESUBMIT にすると T1 が落ちる /
verify を fetch の後に戻すと T1b が落ちる / ループ先頭の `continue` を消すと T4 が落ちる /
最終ステップから ABANDONED を外すと T3・T5 が落ちる。
CLAUDE.md の教訓「a passing test may reach its assertion by another path — break it to check」。

---

## 5. 残存リスク

1. **held が最大約31日毎朝赤い。** 意図的（人間案件を静かにしない）だがアラーム疲れの種。
   緩和: annotation に経過日数と results の推定残日数を出す。#44 第1項で窓が短くなれば軽減。
   **この「最大約31日」は #69 まで成立していなかった**（見積もりの前提が誤っていた）。abandon 判定は
   raw 不在の分岐の中にしかなく、`retry_exhausted` / `stale_schema` / terminal+rebuild 失敗の3つは
   poll より前に return するのでそこへ到達せず、held が無期限に赤かった。#69 で判定を
   `collect_pending_batches` のループ先頭1箇所へ移し、レジームに依らず判定するようにして
   初めて上界が実在するようになった。年齢は**最新 attempt 起点**なので、再送され続けている sidecar は
   このゲートに掛からない（それが1箇所で全レジームを見て安全な理由）。
   ただし上界は**条件付き**である（§3.2 step 0）。成立するのは「既定 lookback（30日）運用」かつ
   「gate が判定できる sidecar」に限る:
   - 削除の条件は age 超**かつ manifest の meeting が raw に無い**。`lookback_days` を広げた
     救出ランでは raw が戻るので held が続く（年齢だけで削除すると救出そのものを壊す）。
   - gate が判定できない sidecar — raw / manifest が読めない、`date` が filename と食い違う、
     `date` が非文字列、age が計算できない — は raw が二度と戻らなくても**無期限に held** になる。
     これは意図的なトレードで、fail-closed の代償は「赤が終わらない」、逆側の代償は「データ消失」。
     人が直すまで終わらない、と明記しておく。
2. **in-flight の総数に上限がない。** 保持 N + 新規1/run。Anthropic 側の長期障害時、共有 1800秒
   budget（`summarize.py:1181`、`paths` は日付昇順）を古い sidecar から消費するので回収が偏る。
   容量 policy は非目標。
3. **`thread_build_failed` = RESUBMIT の確度が低い。** 決定論的な assemble バグなら3回焼いて
   BLOCKED に落ちる。頻発するなら policy を BLOCKED へ移すこと。**retry 回数を増やす方向へ逃げない。**
4. **abandon の恒久記録が annotation + step output のみ。** 最終ステップは暗黙 `success()` なので
   後続ステップの crash で skip されうる。`if: always()` は既存テストが意図的に拒否しているので
   触らない。tombstone ファイル / 専用 `gh issue create` は本スコープで採用しない。
5. **notify-stuck と held の毎日コメント。** ラベル付けはするが dedup 強化は別設計。
   #71 で重複だけ解消した: `notify-stuck-batch` は `if: always()` のまま、
   `held_dates` / `abandoned_dates`（job outputs）を `--exclude-dates` で渡して
   **日付単位で**重複を落とす（§3.14 の追記）。`if: success()` は検討して棄却した —
   「2日以上 in-flight」は run を赤くしないシグナルなので、緑限定にすると他が壊れている間
   ずっと0回になり、results 失効（約29日）〜abandon（31日）の窓で警告が消える。
   acknowledgement 機構は依然として本スコープ外。
6. **results の期限は推定のみ。** sidecar に正確な expiry を持っていない。
7. **`continue` した日付の遅着 meeting は要約されない。** 既存のグローバルゲートでも同じ
   （もっと悪い）性質なので後退ではないが、解消はしない。

---

## 6. 実装順序（Gate2 planner 向け）

1. `batch_state.py` に policy + `mark_blocked` / `clear_blocked` + T6/T7
2. `verify_manifest_against_raw` + Collect の順序入替 + T1/T1b
3. `_apply_failure_policy` と `"assemble_failed"` 廃止 + T2/T2b
4. stale-schema / retry_exhausted を BLOCKED（**2箇所**、§3.7.3）+ T2c/T2d
5. abandon → `abandoned_dates` + T3、`_record_held_sidecar` + シグナル分離
6. workflow ゲート + 最終ステップ + T4/T5
7. 運用テキスト（§3.13 の6箇所）+ `check_stuck_batches.py` 表示
8. 全体 `pytest` / `lint` / `validate`

---

## 7. 変更ファイル

| ファイル | 変更 |
|---|---|
| `scripts/pipeline/batch_state.py` | policy テーブル、`failure_policy`、`mark_blocked` / `clear_blocked` |
| `scripts/summarize.py` | verify-before-fetch、`_apply_failure_policy`、reason 伝播、`_record_held_sidecar`、stale-schema / retry_exhausted（2箇所）、abandon の error 化、`main()` の exit コメント |
| `scripts/check_stuck_batches.py` | held/blocked の表示 |
| `.github/workflows/daily-batch.yml` | ゲート日付単位化、outputs、最終ステップ、help 文3箇所、notify 本文 |
| `CLAUDE.md` | exit 契約、#65 の運用回避策、stale-schema、SCHEMA_VERSION 着地条件は維持と明記 |
| `scripts/tests/test_batch_state.py` | policy 単体（T6/T7） |
| `scripts/tests/test_resume_failure_policy.py`（新規） | T1, T1b, T2, T2b–d, T3, T8, T9 |
| `scripts/tests/test_systemic_failure.py` | T4, T5 |
