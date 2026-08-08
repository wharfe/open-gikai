# Resume failure policy 設計案

## 1. 前提と解釈

### 1.1 対象範囲

本設計は 2026-08-08 時点の `main`、commit `3b9e7b2` を前提とし、次の3点だけを扱う。

- #65: `hash_mismatch` 後の決定論的に失敗する再送を止める
- #66: abandon による恒久ロストを赤い run として通知する
- #44 第3項: pending gate を全体単位から日付単位へ変更する

以下は変更しない。

- abandon 判定の `ABANDON_AGE_DAYS = 31.0`
- raw の再取得方法・lookback の自動拡張
- summary/grouping/outcome request builder
- `compute_input_hash()` の入力集合
- sidecar の schema
- exit code 0/1/3/4 の数値と既存の意味
- 1 run で新しく pending にする batch は最大1本、という制限

したがって `SCHEMA_VERSION` は bump しない。

### 1.2 「fail-closed」の解釈

ここでの fail-closed は、未知の failure reason を「とりあえず再送可能」と扱わないこととする。

新しい assembly reason が追加されたのに policy への登録を忘れた場合は、課金を伴う再送や retry budget 消費を行わず、例外によって Collect を失敗させる。新しい failure mode を静かに HOLD として処理することもしない。未知 reason は設計漏れであり、明示的な分類を要求する。

### 1.3 障害シグナルの意味を分離する

既存の `systemic_dates` / `suspect_dates` は「この処理で summary がサイトに届かなかった」という観測を表す。

一方、次の2つは別の事象である。

- `held_dates`: sidecar を自動処理できず、人間の判断まで保持している日付
- `abandoned_dates`: 回収不能になり、未公開 thread を恒久的に失った日付

この2種類を `systemic_dates` に混ぜない。特に abandon は「今日投げた request が何も生まなかった」のではなく、「過去の request を今日永久に諦めた」という lifecycle event である。

最終的な job の赤化条件にはすべて含めるが、run summary と annotation では意味を分ける。

### 1.4 HOLD の扱い

`hash_mismatch` と stale schema は、sidecar に新しい状態を書かずに保持する。

理由は次のとおり。

- `input_hash` を書き換えてはならない
- `held_reason` の追加はなくても、毎朝現在の raw/schema から同じ状態を再判定できる
- sidecar schema の変更と migration を避けられる
- 障害が解消した場合に古い sticky state が処理を妨げない
- 人間が判断するまで毎朝赤くすることは、今回の要件に合っている

`retry_count`、`attempts`、`terminal_status` を含め、HOLD 時の sidecar は一切変更しない。

---

## 2. 設計案

### 2.1 全体像

```text
Collect pending
  |
  +-- schema mismatch --------------------------> HOLD
  |
  +-- batch canceled / expired ----------------> RETRY
  |
  +-- results expired --------------------------> RETRY
  |
  +-- assembly
       |
       +-- hash_mismatch -----------------------> HOLD
       +-- raw_missing / speech_gap ------------> HOLD
       +-- missing_result / thread_build_failed -> RETRY
       +-- unknown reason ----------------------> crash / exit 1

HOLD
  - sidecar を変更しない
  - submit しない
  - retry_count を増やさない
  - held_dates に記録
  - ::error:: annotation
  - Collect 自体は exit 0

ABANDON
  - sidecar を削除
  - abandoned_dates に記録
  - ::error:: annotation
  - Collect 自体は exit 0

Workflow
  - pending のある日付だけ Summarize を continue
  - 他の日付は処理
  - publish/commit 後の最終 step で held/abandoned/systemic を赤化
```

### 2.2 retry policy は `batch_state.py` の純粋関数に置く

対象: `scripts/pipeline/batch_state.py`

Anthropic client やファイル操作を持たない policy table を追加する。

概念的なインターフェースは次の形とする。

```python
RESUME_RETRY = "retry"
RESUME_HOLD = "hold"

_RETRYABLE_REASONS = frozenset({
    "canceled",
    "expired",
    "results_expired",
    "missing_result",
    "thread_build_failed",
})

_HOLD_REASONS = frozenset({
    "schema_mismatch",
    "hash_mismatch",
    "raw_missing",
    "speech_gap",
})


def resume_failure_action(reason: str) -> str:
    if reason in _RETRYABLE_REASONS:
        return RESUME_RETRY
    if reason in _HOLD_REASONS:
        return RESUME_HOLD
    raise ValueError(f"Unclassified resume failure reason: {reason}")
```

`TERMINAL_FAILURES` は現在の Anthropic status 判定にも使われているため残す。その集合が retry policy と乖離しないことをテストする。

#### reason ごとの判断

| reason | action | 根拠 |
|---|---|---|
| `canceled` | RETRY | batch attempt 自体が完遂していない |
| `expired` | RETRY | raw と manifest があれば新しい batch を作れる |
| `results_expired` | RETRY | 結果だけが失われ、元 request は再構築できる |
| `missing_result` | RETRY | request 単位の欠落・parse/custom ID 問題・API失敗等があり、再送で回復しうる |
| `thread_build_failed` | RETRY | 同一 prompt でも出力同一性は保証されず、新しい結果で回復しうる |
| `hash_mismatch` | HOLD | 保存済み結果と現在の request が一致せず、再送しても保存済み hash 検証に必ず失敗する |
| `raw_missing` | HOLD | 現在の raw では request を忠実に再構築できない |
| `speech_gap` | HOLD | `_rebuild_requests_from_manifest()` も同じ gap で失敗する |
| `schema_mismatch` | HOLD | hash の比較体系が異なり、自動再送・assembly とも安全に行えない |
| 未知 | 例外 | 誤った課金・データ喪失を防ぐ |

policy を `summarize.py` 内の条件分岐に閉じ込めず、純粋関数にすることで全 reason の網羅性を小さい単体テストで固定できる。

### 2.3 `"assemble_failed"` を廃止し、詳細 reason を policy に渡す

対象: `scripts/summarize.py`

現在の以下の呼び出しを廃止する。

```python
_retry_or_hardfail(..., "assemble_failed", ...)
```

代わりに、`assemble_from_manifest()` が返した `diagnostic["reason"]` をそのまま policy 判定へ渡す。

概念的には次の流れになる。

```python
reason = diagnostic["reason"]
action = bs.resume_failure_action(reason)

if action == bs.RESUME_HOLD:
    _record_held_sidecar(...)
else:
    hard_fail |= _retry_or_hardfail(..., reason, ...)
```

`_retry_or_hardfail()` 自身も、受け取った reason が `RESUME_RETRY` であることを冒頭で確認する。

```python
if bs.resume_failure_action(reason) != bs.RESUME_RETRY:
    raise ValueError(...)
```

これにより将来の呼び出し側が誤って `hash_mismatch` を `_retry_or_hardfail()` に渡しても、`record_terminal()` より前に停止する。retry budget を消費した後で判定してはならない。

### 2.4 HOLD 専用の記録経路

対象: `scripts/summarize.py`

`_record_resume_verdict()` は publication failure の severity 判定を担っているため、HOLD の記録を混在させない。新しく `_record_held_sidecar()` 相当を設ける。

責務は以下に限定する。

- `held_dates` に date を追加する
- `diagnostics` に structured diagnostic を追加する
- `_annotate("error", message)` を呼ぶ
- sidecar、retry count、batch attempt には触れない

`held_dates` はリストとして返し、重複は `_write_github_output()` で既存 output と同様に除去する。

#### hash mismatch annotation

少なくとも以下を含める。

- date
- `meeting_id`
- `custom_id`
- sidecar path
- batch ID
- current attempt の `submitted_at`
- `retry_count` が消費されていないこと
- batch result retention の残り目安
- observation が API rejection ではないこと
- 自動再送しないこと
- 人間が選べる復旧方法

例:

```text
Pending summary batch held: input hash mismatch
date=2026-05-14 meeting=M1 custom_id=s_abc_00
batch=msgbatch_... submitted_at=... result retention: at most about 18 days remaining
The stored batch result does not match the request rebuilt from current raw/prompt.
This is not evidence of an API rejection. No resubmit was made and retry_count was not changed.
Inspect data/pending-batches/2026-05-14.json.
To rebuild from current raw, remove the sidecar and rerun:
python scripts/summarize.py --date 2026-05-14 --batch
To discard the unpublished batch, remove the sidecar without rerunning.
```

保持日数は sidecar に正確な result expiry timestamp がないため、`submitted_at` と約29日という既存前提から算出した推定値と明記する。「N日残っている」と断定しない。

また annotation に「threads for the date are missing」と断定しない。既存 thread がある可能性があり、失われるのはこの sidecar が担当する未回収分だけである。

GitHub annotation の可読性や長さを考慮し、コマンドを含む詳細は複数の `_annotate()` に分割せず、1 failure eventにつき1つの error annotation とする。詳細な複数行説明は通常ログにも出してよい。

### 2.5 stale schema を HOLD に変更する

対象: `scripts/summarize.py` の `is_current_schema()` 分岐

現在の `hard_fail = True` をやめ、次の処理に変更する。

- reason: `schema_mismatch`
- action: HOLD
- sidecar を変更しない
- `held_dates` に追加
- `_annotate("error", ...)`
- 次の sidecar の回収へ進む
- Collect はこの理由だけでは exit 1 にしない

コメントには、判断根拠が変わったことを明記する。

```python
# Keep the sidecar untouched and require manual resolution. This used to be a
# collect hard-fail because any pending sidecar skipped Summarize for every
# date. The workflow now skips only this sidecar's date, so stopping publish is
# no longer necessary. Resubmission remains unsafe because the stored hashes
# were computed under a different schema.
```

これにより G5 を満たし、hash mismatch と同じ「自動処理不能だが、他の日付の publish は止めない」という regime に統一する。

### 2.6 retry threshold の意味は維持する

`HARD_FAIL_RETRIES = 3` と `should_hard_fail()` は維持する。

hash/schema/raw reconstruction failure を除外した後、この閾値が数えるのは「本来再送可能と分類した attempt が、3回連続で完遂・回収できなかった」状態になる。

具体的には次のようなもの。

- batch canceled/expired の反復
- results expiry 後の再送も失敗
- missing result / unusable output の反復
- `thread_build_failed` の反復

これは policy 分類自体が誤っている、Anthropic 側または parser に継続障害がある、あるいは人手で調査すべき状態を示す。したがって既存どおり `hard_fail=True`、Collect exit 1 とする。

ただし `_retry_or_hardfail()` の実行順序は次に固定する。

1. reason を policy で RETRY と検証
2. `record_terminal()`
3. threshold 判定
4. current raw から request を再構築
5. submit
6. `add_attempt()` と sidecar 保存

未知 reason や HOLD reason が 1 より後ろへ到達しないことが重要である。

### 2.7 abandon は専用 output で赤化する

対象:

- `scripts/summarize.py`
- `.github/workflows/daily-batch.yml`

`collect_pending_batches()` の返却値に以下を追加する。

```python
{
    "hard_fail": bool,
    "systemic_dates": list[str],
    "suspect_dates": list[str],
    "held_dates": list[str],
    "abandoned_dates": list[str],
    "diagnostics": list[dict],
}
```

abandon 分岐では次を行う。

1. age を記録
2. `_annotate("error", ...)`
3. `abandoned_dates` に date を追加
4. sidecar を削除
5. `hard_fail` は設定しない
6. loop を継続する

annotation 例:

```text
Permanently abandoned uncollectable summary batch:
date=2026-05-14 batch=msgbatch_... age=40.0d.
Raw input and batch results are no longer recoverable under the current retention policy.
The sidecar was deleted; its uncollected threads will not be published.
This is permanent data loss, not an API rejection observed in this run.
```

bare `print("::warning::...")` は残さず `_annotate("error", ...)` を使う。

`main(--collect-pending)` は `held_dates` と `abandoned_dates` も `_write_github_output()` に渡す。abandon/hold だけなら Python process は exit 0 とする。したがって後続の生成、commit、push、IndexNow は実行される。

Workflow の最終 step がこの output を読んで最後に exit 1 し、job と `notify-on-failure` を赤化する。

これは「Collect の hard-fail exit 1」とは異なる。

- Collect exit 1: 即時停止し publish をブロックする
- final step exit 1: publish 完了後に job だけを赤くする

### 2.8 final failure policy

`.github/workflows/daily-batch.yml` の最終 step に以下の入力を追加する。

- `COLLECT_HELD`
- `COLLECT_ABANDONED`

集計は意味を分離して行う。

```bash
SYSTEMIC=...
SUSPECT=...
HELD=...
ABANDONED=...

SUSPECT_N=$(echo "$SUSPECT" | wc -w)

FAIL_SYSTEMIC="$SYSTEMIC"
FAIL_SUSPECT=""
if [ "$SUSPECT_N" -ge 2 ]; then
  FAIL_SUSPECT="$SUSPECT"
fi
```

以下のいずれかが非空なら、publish 後に exit 1 とする。

- `SYSTEMIC`
- 2日付以上の `SUSPECT`
- `HELD`
- `ABANDONED`

ただし最終 annotation は一括して「Nothing this run produced reached the site」と表現しない。カテゴリごとに説明する。

```text
::error::Summary failures require attention.
Systemic publication failures: ...
Held pending batches requiring a decision: ...
Permanently abandoned batches: ...
```

`SUSPECT_N -ge 2` は従来どおり最終 workflow step にだけ置く。`held_dates` と `abandoned_dates` は1日付でも無条件で赤くし、suspect threshold の対象にしない。

run summary にも以下を別行で表示する。

- `Pending batches held for manual resolution`
- `Permanently abandoned pending batches`

### 2.9 pending gate の日付単位化

対象: `.github/workflows/daily-batch.yml`

Collect step の `has_pending` output を廃止する。

Summarize step の条件を以下へ変更する。

```yaml
if: steps.dates.outputs.list != ''
```

ループの先頭で、その日付に sidecar がある場合だけ skip する。

```bash
for d in $DATES_LIST; do
  if [ -f "data/pending-batches/$d.json" ]; then
    echo "Pending sidecar exists for $d — skipping this date"
    continue
  fi

  echo "::group::Summarize $d"
  ...
```

ループ末尾の既存 break は残す。

```bash
if [ -f "data/pending-batches/$d.json" ]; then
  echo "Batch for $d is pending — stopping further submits this run"
  break
fi
```

この2つは役割が異なる。

- ループ先頭の `continue`: 過去 run 由来の sidecar がある日付だけ除外する
- ループ末尾の `break`: この run が新しく pending batch を1本作ったら、それ以降の新規 submit を止める

これにより、保持中 sidecar が何件あっても、この run が新しく in-flight にする batch は最大1本である。

一方で repository 全体に存在する in-flight batch の総数は1本に制限しない。既存の held batch に加えて、別日付の新規 batch が1本増えることは許容する。これは G4 の可用性を得るための意図的な変更である。

### 2.10 データフロー

#### hash mismatch の朝

1. Collect が batch results を取得する
2. current raw から builder で request を再構築する
3. `compute_input_hash()` が不一致を返す
4. diagnostic reason `hash_mismatch`
5. policy は HOLD
6. submit しない
7. sidecar を保存し直さない
8. `held_dates` と error annotation を出す
9. Collect exit 0
10. Summarize はその date のみ skip
11. 別 date を通常処理する
12. commit/publish
13. 最終 step が `held_dates` により job を赤くする

#### abandon の朝

1. Collect が raw missing を検出する
2. `is_abandonable()` が true
3. error annotation を出す
4. `abandoned_dates` に追加する
5. sidecar を削除する
6. Collect exit 0
7. Summarize、commit、publish を継続する
8. 最終 step が job を赤くする

### 2.11 テスト設計

#### `scripts/tests/test_batch_state.py`

policy table の純粋テストを追加する。

- すべての RETRY reason が RETRY
- すべての HOLD reason が HOLD
- `TERMINAL_FAILURES <= _RETRYABLE_REASONS`
- `"assemble_failed"` は認識されない
- 未知 reason は `ValueError`
- `record_terminal()` は policy 判定を内包しないことも明示する

禁止リストだけでなく、認識可能な集合全体を期待値として固定する。

#### `scripts/tests/test_resume.py`

T1:

- wrong hash の sidecar
- usable batch result
- fake submit の call count が0
- `retry_count` が不変
- `attempts` の長さと内容が不変
- sidecar が残る
- `held_dates == [date]`
- `hard_fail is False`
- error annotation に date、meeting、custom ID、`hash_mismatch`、no resubmit が含まれる

T2:

- `missing_result` を発生させる
- synchronous repair も失敗する fake を使う
- new batch submit が1回
- `retry_count` が1増える
- attempt が1件増える
- `held_dates` は空

裏側として `raw_missing` / `speech_gap` でも submit と retry increment がないことを追加する。

stale schema:

- sidecar が変化しない
- `held_dates` に載る
- `hard_fail is False`
- Collect が次の sidecar も処理する

T3:

- raw missing、age > `ABANDON_AGE_DAYS`
- sidecar が削除される
- `abandoned_dates` に載る
- `hard_fail is False`
- `systemic_dates` に意味を混ぜない
- `::error::` が出る
- warning ではない

`main(--collect-pending)`:

- hold/abandon だけなら exit 0
-4つの output が `$GITHUB_OUTPUT` に書かれる
- retry threshold は従来どおり exit 1

#### `scripts/tests/test_systemic_failure.py`

T4 と T5 を既存 YAML parse テストへ追加する。

Summarize stepについて次を検証する。

- step-level `if` に `has_pending` が存在しない
- loop の `python scripts/summarize.py --date "$d"` より前に、日付 sidecar の `-f` 判定と `continue` がある
- Python 呼び出しより後に既存の sidecar 判定と `break` がある
- `continue` と `break` の両方が存在する
- Collect が `has_pending` output を生成しない

最終 stepについて次を検証する。

- `held_dates` と `abandoned_dates` の outputs を読む
- held/abandoned は suspect count に混ぜない
- それぞれ1日付で failure 条件になる
- `SUSPECT_N -ge 2` は依然として最終 step だけにある
- Python の exit code 3/4 と YAML の分岐が一致する既存テストを維持する

テストが別経路で偶然 pass しないよう、少なくとも以下の破壊確認を実装時レビューで行う。

- policy から `hash_mismatch` を RETRY に移すと T1 が失敗する
- loop 先頭の `continue` を削除すると T4 が失敗する
- loop 末尾の `break` を削除すると T4 が失敗する
- final step から `ABANDONED` を外すと T3/YAML test が失敗する
- unknown reason を RETRY にすると policy test が失敗する

### 2.12 実装後の検証

指定された順序で実行する。

```bash
python -m pytest scripts/tests
npm run lint
npm run validate
```

pipeline/workflow をまたぐ非自明な変更なので、プロジェクト規約どおり最後に `/code-gate` を実行し、critical 0 を確認する。

---

## 3. 代替案と却下理由

### 3.1 `_retry_or_hardfail()` 内だけで `if reason == "hash_mismatch"` とする

却下する。

- assembly reason が現在 `"assemble_failed"` に潰されており、結局呼び出し境界も変更が必要
- policy が Anthropic submit、sidecar mutation、retry count 更新と混在する
- `record_terminal()` より前に判定し損ねる危険がある
- reason が増えたときの fail-closed な網羅テストが作りにくい

pure module で reason classification を固定し、`summarize.py` は副作用の実行だけを担う方が安全である。

### 3.2 `batch_state.py` に retry counter 操作まで含む状態機械を置く

今回は採用しない。

`batch_state.py` は sidecar persistence の pure core だが、実際の RETRY には raw 読み込み、request builder、Anthropic submit、git commit が必要になる。action の判定だけを pure にし、副作用 orchestration は `summarize.py` に残す方が責務境界が明確である。

### 3.3 retryable reason の forbid-list を作る

例えば「`hash_mismatch` 以外はすべて retry」とする案は却下する。

新しい reason が自動的に課金・retry budget 消費の対象になり、今回と同じ障害が再発する。RETRY と HOLD の両方を allowlist にし、未知 reason は例外にする。

### 3.4 hash mismatch 時に sidecar の `input_hash` を更新する

禁止事項に反するため却下する。

これは古い batch result と新しい raw/prompt の対応を偽装し、検証アンカーを無効化する。新しい raw を採用する場合は、古い sidecar を人間が明示的に解消し、新しい batch と manifest を最初から作る必要がある。

### 3.5 HOLD 状態を sidecar に保存する

今回は却下する。

```json
{
  "hold_reason": "hash_mismatch",
  "held_at": "..."
}
```

のようなフィールドは後方互換にはできるが、現状は毎朝安全に再判定できる。sticky state を増やすと、raw を修復しても hold flag が処理を妨げる可能性がある。また sidecar mutation の commit が必要になり、今回の目的に対して利点が小さい。

将来 acknowledgement や issue ID、手動 resolve command を機械化する場合には再検討できる。

### 3.6 hash mismatch を `systemic_dates` だけで報告する

却下する。

既存 thread がある1-of-1 failure は `suspect_dates` になり、1日だけでは赤くならない可能性がある。G2 は hash mismatch の朝を無条件で赤くすることを要求している。

また systemic/suspect は publication outcome、HOLD は operator action state で意味が異なる。専用 `held_dates` が必要である。

### 3.7 abandon を `systemic_dates` に入れる

却下する。

最終的にはどちらも赤くするが、時制と意味が違う。run summary の「Nothing this run produced reached the site」と、31日前の batch の永久ロストを同一視すると誤診を招く。

専用 `abandoned_dates` なら suspect threshold との混同もなく、notify issue に恒久ロストを明示できる。

### 3.8 abandon で Collect を exit 1 にする

却下する。

Collect の exit 1 は workflow をその場で中断し、commit/publish を止める。G3 は赤化しつつ publish をブロックしないことを要求している。

Collect は output を残して exit 0、publish 後の最終 step で exit 1 とする。

### 3.9 stale schema を引き続き hard-fail とする

却下する。

現在の根拠は「sidecar が1件でもあると全日付の Summarize が止まり、exit 0 では緑の完全停止になる」である。日付単位 gate の導入後、その前提は消える。

stale sidecar の日付は処理できないが、他の日付の生成・publish は可能である。HOLD + error + final red が、危険な自動再送を避けながら可用性を保つ。

### 3.10 pending 日付を `dates.outputs.list` から事前に除外する

loop 内 `continue` を選ぶ。

事前の文字列集合差は shell の空白区切りリスト処理を増やし、日付の重複排除・escaping・output transport という別の failure surface を作る。日付ごとの sidecar path は決定的なので、loop 内の `-f` が最も直接的である。

さらに「既存 sidecar は continue、新規 sidecar は break」という2つの制御を同じ loop 内で明示できる。

### 3.11 pending sidecar が1件でもあれば新規 submit を全面禁止する

却下する。

それは現在の global gate を名前だけ変えて残すことになる。held sidecar が人手で解消されるまで新しい日付が更新されず、G4を満たさない。

新規 submit を1 run 1本に制限することで、コストと同時 in-flight 増加には上限を残す。

---

## 4. リスクと未解決点

### 4.1 held batch が毎朝新しい Issue を作る可能性

`held_dates` は人間が解消するまで毎朝赤くなる。`notify-on-failure` が既存 issue を再利用しない場合、同じ hash mismatch で issue が連日作られる可能性がある。

今回の要件上は、静かな放置より繰り返し通知を優先する。issue deduplication や acknowledgement 状態は別設計とするのが妥当だが、運用者は通知頻度を確認する必要がある。

### 4.2 repository 全体の in-flight batch 数は増えうる

global gate を外すため、held sidecar が残る間にも、別日付の batch が各 run 最大1本ずつ pending になりうる。

通常の batch は翌朝 Collect されるため無制限増加は想定しないが、Anthropic 側の長期障害時には pending ファイルが積み上がる可能性がある。今回の要件は可用性を優先してこれを許容する。

必要なら将来、次のような独立した capacity policy を追加できる。

- HOLD は submit capacity に数えない
- in-progress sidecar が一定数を超えたら新規 submit を止める
- ただし synchronous processing や既存 thread の publish は止めない

今回これを入れると G4 と別の設計判断になるため非対象とする。

### 4.3 result retention の残日数は推定しかできない

sidecar は batch の正確な expiration timestamp を保持していない。`submitted_at` から約29日を引いた値は目安であり、APIの実際の retention 起点と一致する保証がない。

annotation は必ず `estimated` または `at most/about` と表現する。正確な deadline が必要なら Anthropic batch metadata の利用可否を別途確認し、sidecar schema 変更を検討する必要がある。

### 4.4 `thread_build_failed` を RETRY とする判断

`thread_build_failed` は同じ result に対して再 assembly しても同じ失敗になるが、新しい model response が同一である保証はないため RETRY に分類した。

一方、実際の原因が deterministic な `assemble_thread()` bug なら3回とも失敗して hard-fail する。この reason を HOLD にすべきかは、人間が「同一 prompt の再生成を recovery として許容するか」を最終確認すべき論点である。

本案では既存挙動を保つため RETRY を選ぶ。

### 4.5 `raw_missing` / `speech_gap` の回復可能性

本案は現在の raw から `_rebuild_requests_from_manifest()` が成功しないため HOLD とする。翌朝 fetch によって raw が直れば、再検査で自動的に HOLD を抜け、元 batch result の assembly に進める。

ただし batch result がその間に期限切れになる可能性がある。annotation の保持期限情報をもとに、人間が raw 再取得を判断する必要がある。raw 自動再取得は明示的な非目標である。

### 4.6 abandon と既存 thread の表現

abandon は「その date の全 thread が存在しない」とは限らない。過去に公開済み thread があり、今回の sidecar が追加 meeting だけを担当している場合がある。

したがって annotation、run summary、Issue は一貫して以下のように表現する必要がある。

- 正: `the sidecar's uncollected threads were permanently lost`
- 誤: `threads for this date were lost`
- 誤: `the date is empty`
- 誤: `the API rejected the batch`

### 4.7 HOLD の手動解消手順

「新しい raw で作り直す」と「捨てる」は、どちらも sidecar の削除を伴う。誤操作すると、まだ取得可能な古い batch result を参照する manifest が失われる。

実装前に運用者は以下を決める必要がある。

1. sidecar 削除前に backup branch / artifact を残すか
2. 再生成時に date 全体を処理するか、meeting filter を使うか
3. 既存 `data/threads/{date}.json` との重複をどう確認するか
4. annotation に直接 `git rm` を出すか、runbook URL を出すか

本案は自動解消コマンドを実行しない。annotation は選択肢と対象 path を示すだけとし、人間の明示判断を要求する。

### 4.8 `hard_fail` という名称の意味が狭くなる

stale schema を HOLD に移すため、`CollectResult["hard_fail"]` は実質的に retry threshold 到達だけを表すようになる。

現状の名称を維持しても機能上は問題ないが、将来ほかの fatal validation が追加される可能性を考えると、今回 `retry_exhausted` へ rename するのは避ける。rename は main/テスト/docstring を広く変更し、今回の目的に対して差分を増やす。

docstring は「retry threshold or an unclassified/crash condition」の実態に合わせて更新する。

### 4.9 workflow shell test の脆さ

YAML parse 後に shell script の文字列順序を検査するテストは、semantic parser ではないため多少脆い。ただし既存 `test_systemic_failure.py` が同じ方式を採用しており、今回の事故は workflow の制御位置そのものに起因する。

少なくとも以下の順序を文字位置で固定する価値がある。

```text
pending check + continue
    <
python summarize --date
    <
new sidecar check + break
```

単なる `"continue" in run` / `"break" in run` だけでは別の loop に存在しても pass するため、日付 sidecar path と Python invocation の位置関係まで検証する。
