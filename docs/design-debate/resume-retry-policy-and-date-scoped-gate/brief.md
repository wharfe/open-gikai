# Design brief — resume failure policy: retry によるレジーム分岐・恒久ロストの重大度・pending ゲートの日付単位化

対象 issue: **#65**（hash_mismatch の決定論的に doomed な再送）、**#66**（恒久ロストが warning）、
**#44 の第3項のみ**（pending gate を日付単位にする）。#44 の第1項（abandon 判定を lookback 窓ベースへ）と
第2項（raw の明示再取得 / lookback 自動拡張）は **今回の非目標**。

---

## 0. この文書の読み方

読み手は `scripts/summarize.py` / `scripts/pipeline/batch_state.py` /
`.github/workflows/daily-batch.yml` を読める前提。以下の記述はすべて 2026-08-08 時点の
`main`（`3b9e7b2`）に対するもの。行番号は目安。

---

## 1. 背景 — このパイプラインが守っているもの

OpenGIKAI は毎朝 GitHub Actions（`daily-batch.yml`）で国会会議録等を取得し、Claude の
Message Batches API で要約し、静的サイトへ publish する。Batches API は非同期なので、
1 run のバジェット内に終わらなかったバッチは **sidecar**（`data/pending-batches/{date}.json`）
に batch id と grouping manifest を書いてコミットし、翌朝の run が回収（resume）する。

manifest には thread ごとに `input_hash`（`model` + `thinking` + `system` + `messages` の
SHA256。`max_tokens` は除外）が入っている。resume 時は **現在の raw** から request を組み直し、
その hash が保存済み hash と一致することを確認してから batch 結果を組み立てる。これは
「バッチに投げたリクエストと、いま組み立てようとしているリクエストが同一である」ことの
**検証アンカー**であり、政治的中立性（同一入力 → 同一プロンプト）の実装上の要でもある。

このパイプラインは 2026-07-17 〜 2026-08-06 の約2ヶ月、1件の回収不能 sidecar が原因で
実質停止した（#43 / #44）。以降の一連の修正（#46, #51, #52, #59, #61）は一貫して
**「壊れても publish は止めない / 壊れたことは人間に見える」** を目指している。
本件はその残りである。

---

## 2. 現状の3つの穴

### 2.1 #65 — hash_mismatch で決定論的に失敗する再送を3回する

`assemble_from_manifest()`（`summarize.py:693-767`）は thread ごとに
`compute_input_hash(request["params"]) != mt["input_hash"]` を検査し、不一致なら
`(_, False, {"reason": "hash_mismatch", ...})` を返す。

呼び出し側（`collect_pending_batches`, `summarize.py:1300-1315`）は `ok` が False なら
`_record_resume_verdict(...)` で赤/黄の annotation を出したうえで
`_retry_or_hardfail(client, sidecar, path, "assemble_failed", ...)` を呼ぶ。

`_retry_or_hardfail`（`summarize.py:1119-1145`）は **reason を区別しない**:

1. `bs.record_terminal(sidecar, reason, now)` → `retry_count += 1`
2. `retry_count >= HARD_FAIL_RETRIES (=3)` なら hard_fail（exit 1）
3. そうでなければ `_rebuild_requests_from_manifest()` で **やはり現在の raw から** request を
   組み直し、新しいバッチとして投函する

`_rebuild_requests_from_manifest`（`summarize.py:805-827`）は組み直した hash を
保存済み hash と比較しない。sidecar の `input_hash` も更新されない。

したがって raw が変わった（NDL の訂正、fetcher の変更、`SCHEMA_VERSION` の bump 忘れ等）場合:

| 朝 | 起きること |
|---|---|
| 1 | assembly が hash_mismatch で失敗 → 赤 annotation → **バッチ1本分を丸ごと再課金して再送**（retry 1/3） |
| 2 | 保存済み hash は古いまま・raw は新しいまま → **同じ場所で同じ失敗** → 再送（retry 2/3） |
| 3 | 同上（retry 3/3） |
| 4 | `should_hard_fail` → exit 1 → `set -e` で **Collect ステップが落ち、publish 全体が止まる** |

再送は最初から決定論的に失敗すると分かっている。#46 の「同じ位置で切れるリクエストを
再投函し続ける」と同じ形が、別の理由で残っている。

**repo 内に既存の先例がある**: stale-schema 分岐（`summarize.py:1183-1208`）はまさに
この判断を実装済み ——「再送すれば毎回検証に失敗して retry 予算を焼き切り恒久ロストに至る。
一方バッチ結果と raw が保持期間内なら人間はまだ手で救える。だから触らず保持して赤くする」。
ただし stale-schema は `hard_fail = True`（exit 1、publish ごと停止）を **意図的に** 選んでいる。
その理由もコメントに明記されている ——「pending がある限り Summarize ステップは丸ごと
スキップされるので、ここで 0 を返すと『静かに何も処理しない緑の run』になる。この repo で
一番高くつく失敗モードだ」。

**この理由は §2.3 のゲートを日付単位にすると成立しなくなる**（他の日付は処理されるため）。
本件はその前提変更も同時に行う。

### 2.2 #66 — 恒久ロストだけが緑 + warning

`collect_pending_batches` の raw-missing 分岐（`summarize.py:1234-1262`）:

- `bs.is_abandonable(sidecar, now)`（= `age_days > ABANDON_AGE_DAYS (31.0)`）が真なら
  `print("::warning::Abandoned uncollectable ...")` + `bs.delete_sidecar(path)`。
  **ジョブは緑のまま。**
- 偽なら `_record_resume_verdict(...)` で赤/黄（#59/#61 で追加された同日警報）。

結果として重大度が逆転している:

| 経過日数 | 状態 | 現在の色 |
|---|---|---|
| 1〜30日 | まだ回収可能（＝直せる） | **赤**（systemic）または黄（suspect） |
| 31日 | 回収不能が確定（＝二度と直せない） | **緑 + warning** |

abandon は **このパイプラインで唯一、恒久的に取り返しがつかない事象** である。その日付の
スレッドは二度と公開されない。「もう手遅れです」の日だけ静かになっている。

なお `notify-on-failure` ジョブは run が赤いときに GitHub Issue を自動で立てる。緑のままだと
その経路にも乗らない。

### 2.3 pending ゲートのグローバル性（#44 の第3項）

`.github/workflows/daily-batch.yml:135`:

```yaml
if: steps.dates.outputs.list != '' && steps.collect.outputs.has_pending != 'true'
```

`has_pending` は `ls data/pending-batches/*.json` が1件でもヒットすれば `true`
（`daily-batch.yml:122-126`）。つまり **どの日付の sidecar であれ1件でも残っていれば、
その朝は全日付の Summarize が丸ごとスキップされる**。これが 2ヶ月停止の増幅器だった。

これは #65 の着地に直接効く。#65 の方針（hash_mismatch は再送禁止・sidecar 保持・人が判断）を
このゲートのまま入れると、hash_mismatch が起きた瞬間から人間が手で sidecar を消すまで
**毎朝サイトが更新されない**。CLAUDE.md に現在書かれている運用回避策
（"Do not wait it out; remove the sidecar"）がそのまま残ってしまう。

**別途注意**: Summarize ループの末尾には *run 内* の別の制限がある
（`daily-batch.yml:184-188`）:

```bash
if [ -f "data/pending-batches/$d.json" ]; then
  echo "Batch for $d is pending — stopping further submits this run"
  break
fi
```

これは「1 run で複数のバッチを同時に in-flight にしない」ための **run 内 break** であり、
§2.3 の cross-run ゲートとは別物。日付単位化はこの break を壊してはならない。

---

## 3. ゴール

1. **G1**: `hash_mismatch` が原因の assembly 失敗で、決定論的に失敗すると分かっている
   バッチ再送を **1回も** 行わない。retry 予算も消費しない。
2. **G2**: `hash_mismatch` が起きた朝は run が赤くなり、人間が「新しい raw で作り直す」か
   「捨てる」かを判断できるだけの情報が annotation に出る。
3. **G3**: 恒久ロスト（abandon）が起きた run は赤くなる。ただし publish はブロックしない
   （既存の exit 3 と同じ扱い）。`notify-on-failure` の自動 Issue 経路に乗る。
4. **G4**: sidecar が残っている日付だけ Summarize をスキップし、他の日付は通常どおり
   処理・publish される。run 内で新規に in-flight にするバッチは従来どおり最大1本。
5. **G5**: G4 によって §2.1 の stale-schema 分岐が `hard_fail`（exit 1）を選んでいる
   根拠が変わるので、その扱いを再検討し、選んだ結論をコメントに残す。

---

## 4. 制約

### 4.1 触ってはならない不変条件（CLAUDE.md「Summary Layer Invariants」）

- 要約層のリクエストは必ず3つの builder
  （`grouper.build_grouping_request` / `build_outcome_request` /
  `summarizer.build_summary_request`）のいずれかを通ること。手組み禁止。
  `scripts/tests/test_determinism.py` が AST sweep で監視している。
- 要約層に sampling params（`temperature` / `top_p` / `top_k`）を送らない。
  `claude-sonnet-5` は非既定値を 400 で拒否する（#51）。
- `thinking: {"type": "disabled"}` を全要約/grouping/outcome リクエストに付ける。
- `compute_input_hash` に食わせる param の集合を変えたら `SCHEMA_VERSION` を bump する。
  **ただし bump は `git ls-files data/pending-batches/` が空のときにしか land できない**
  （`is_current_schema` が等値比較なので、in-flight の sidecar があると両方向で hard-fail する）。
  → 本件は hash の対象 param を変えない想定。変える設計を出す場合はこの制約を明示的に扱うこと。
- `input_hash` を「新しい raw のもので黙って更新する」のは **禁止**。それは検証アンカーであり、
  書き換えれば検証が無意味になる。

### 4.2 exit code コントラクト（CLAUDE.md「summarize.py exit codes」）

| code | 意味 | workflow の扱い |
|---|---|---|
| 0 | 実行した。何も産まなくても正当な場合がある | 継続 |
| 1 | crash / usage error / `--collect-pending` の hard-fail | `set -e` で日付ループ中断 |
| 3 | systemic: 何もサイトに届かなかった | 記録して継続、最終ステップで job を赤に |
| 4 | suspect: 同上だが 1-of-1 かつ既存スレッドあり | 記録。1 run で **2日付以上** なら赤 |

- `--collect-pending` は多数の日付を1プロセスで代弁するので exit code では日付を特定できない。
  `systemic_dates` / `suspect_dates` step output + annotation で報告し、hard fail のときだけ 1。
- `EXIT_SYSTEMIC_FAILURE` / `EXIT_SUSPECT_FAILURE`（`summarize.py:64,72`）と
  `daily-batch.yml` の `-eq 3` / `-eq 4` は **同一コミットで動かす**こと。
  `test_systemic_failure.py::test_the_workflow_tolerates_exactly_these_exit_codes` が
  YAML をパースして drift を検出する。
- suspect の閾値（`SUSPECT_N -ge 2`）は **workflow の最終ステップにだけ** 置く。Python 側に
  持ち込まない。最終ステップに置いてあるのは、pending sidecar がある朝は Summarize ステップが
  そもそも走らず、Collect の報告しか存在しないため。

### 4.3 その他

- Python 3、外部依存の追加は避ける。既存の `anthropic` SDK / 標準ライブラリで。
- テストは `scripts/tests/` に pytest で置く。`python -m pytest scripts/tests` が通ること。
- sidecar のスキーマを増やすフィールドがある場合、既存 sidecar（フィールド無し）を
  読めること。読めない変更をするなら `SCHEMA_VERSION` の制約（§4.1）に従う。
- CI の annotation は `_annotate(level, message)`（`summarize.py:79`）を通す
  （#67 で残りの bare print を寄せる方針が既にある）。
- コード内コメントは英語。

---

## 5. 非目標

- #44 の第1項（abandon 判定を `submitted_at` からの経過日数ではなく
  「対象 date が lookback 窓内か」ベースにする）。
- #44 の第2項（回収不能検知時に `--lookback-days` を自動拡張して raw を明示再取得）。
- #60（`extract_meeting_outcome` の例外握りつぶし）。
- #57（破損 `{date}.json` の atomic write / commit 隔離）。
- 要約プロンプト・grouping ロジックそのものの変更。
- `ABANDON_AGE_DAYS` の値そのものの見直し（#44 第1項に含まれる）。

---

## 6. 設計上の論点（両案が答えるべき問い）

1. **retry policy をどこに置くか。** `_retry_or_hardfail` の中で reason を見て分岐するのか、
   呼び出し側が policy を決めて別関数を呼ぶのか、`batch_state.py` に純粋関数として
   policy テーブルを置くのか。`batch_state.py` は「pure: no Anthropic client, no network」
   と自称しており、単体テストの本体である。
2. **reason の語彙。** 現在 `_retry_or_hardfail` に渡る reason は
   `"canceled"` / `"expired"`（`TERMINAL_FAILURES`）、`"results_expired"`、`"assemble_failed"` の
   3系統で、**assembly の詳細な理由（`hash_mismatch` / `missing_result` / `speech_gap` /
   `raw_missing` / `thread_build_failed`）は `_diagnostic` にはあるのに
   `_retry_or_hardfail` には渡っていない**（一律 `"assemble_failed"`）。ここをどう繋ぐか。
3. **hash_mismatch の sidecar をどう「保持」するか。** 何もしないと毎朝
   同じ再検査 → 同じ赤 が繰り返される（それ自体は正しいが、
   `retry_count` を消費しないので `ABANDON_AGE_DAYS` に達するまで31日続く）。
   これは許容か、それとも「人間の判断待ち」であることを sidecar に記録して
   別の扱い（例: 毎朝赤くするが annotation の文面を変える、専用の held 状態を持つ）にすべきか。
4. **abandon を赤くする方法。** `collect_pending_batches` は exit code で日付を語れないので、
   `systemic_dates` に載せるのが素直に見える。しかし systemic の既存定義は
   「この run で何もサイトに届かなかった日付」であり、abandon は
   「31日前に投函したバッチが今日ついに諦められた」。同じリストに混ぜると
   最終ステップの `SUSPECT_N -ge 2` 相当の集計や、annotation の説明文が
   意味的にずれないか。新しい output（例: `abandoned_dates`）を足すべきか。
5. **G4 の実装形。** step-level の `if` を外して loop 内で `continue` するのか、
   `dates.outputs.list` から pending 日付を事前に引くのか。
   run 内 break（§2.3 末尾）との相互作用、および「in-flight バッチが run ごとに
   1本ずつ増えうる」（保持された sidecar 1本 + 新規1本）ことの是非。
6. **G5 — stale-schema 分岐の hard_fail をどうするか。** ゲートが日付単位になれば
   「緑なのに何も処理されない」は起きなくなる。exit 1 のままにするか、
   hash_mismatch と同じ「保持 + 赤 + publish 継続」に寄せて扱いを統一するか。
   統一するとしたら、`--collect-pending` が exit 1 を返すケースは
   「retry 閾値到達」だけになるが、その retry 閾値自体が
   hash_mismatch を除外した後に何を意味するか。
7. **可観測性。** 人間が hash_mismatch を見たとき、「新しい raw で作り直す」か「捨てる」かを
   判断するために何が annotation に必要か（対象 date、meeting_id、custom_id、
   sidecar の投函日時、バッチ結果の残り保持日数、取るべきコマンド）。
   誤診を招く表現を避けること —— CLAUDE.md 曰く「an operator sent to hunt a 400 that
   never happened, or to look for threads that were never lost, has had their morning taken」。

---

## 7. 受け入れ基準（機械判定）

```bash
python -m pytest scripts/tests            # 既存 + 新規テストが通る
npm run lint && npm run validate          # 既存の受け入れ基準
```

加えて、設計が満たすべきテスト可能な性質:

- **T1**: assembly が `hash_mismatch` で失敗したとき、バッチの再送が **行われない**
  （submit のフェイクが呼ばれない）ことと、`retry_count` が増えないことを検証するテスト。
- **T2**: `missing_result` など再送が正当な reason では、従来どおり再送され
  `retry_count` が増えることを検証するテスト（T1 の裏。fail-closed であること）。
- **T3**: abandon 経路で run が赤くなる（`systemic_dates` 相当に載る／`::error::` が出る）
  ことを検証するテスト。かつ hard_fail（exit 1）にはならないこと。
- **T4**: sidecar が1件ある状態で、sidecar を持たない日付の Summarize が
  スキップされないことを検証する（workflow YAML をパースするか、
  該当ロジックを Python 側に持つならその単体テスト）。
  既存の `test_systemic_failure.py` が YAML をパースする前例がある。
- **T5**: exit code の Python 側定数と YAML 側の分岐が一致し続けること
  （既存 `test_the_workflow_tolerates_exactly_these_exit_codes` を壊さない／必要なら拡張）。

fail-closed であること。CLAUDE.md の教訓:
「a forbid-list test approves what it can't recognize, and a passing test may reach its
assertion by another path — break it to check」。

---

## 8. 参考

- 直近の関連設計: `docs/superpowers/specs/2026-08-07-summary-failure-signal-gaps-design.md`
  （#59/#61 の同日警報。`_diagnostic` の reason 語彙と `_record_resume_verdict` はここで導入された）
- `docs/superpowers/specs/2026-06-12-batch-resume-design.md`（sidecar / resume の原設計）
- issue: #65, #66, #44
- 関連する過去の事故: #43（2ヶ月停止）、#46（同じ位置で切れるリクエストの再投函）、
  #51（sampling param の 400 で全滅）、#52（publish 停止の増幅）
