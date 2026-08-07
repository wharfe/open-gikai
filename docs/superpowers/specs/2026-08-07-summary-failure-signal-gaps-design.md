# summary-layer 障害シグナルの残穴を塞ぐ（#59 / #61）

- 日付: 2026-08-07
- 対象 issue: #59（resume 経路にシグナルが無い）、#61（組み立て不能な結果が成功と数えられる）
- 前提となる既存実装: #51 / #52 で導入した `EXIT_SYSTEMIC_FAILURE = 3` / `EXIT_SUSPECT_FAILURE = 4`

## 1. これは何を守る仕組みか

毎朝1つの問いに答えるためだけに存在する。

> **今朝スレッドが1本も出なかったのは、静かな国会だったからか、パイプラインが壊れているからか。**

この2つは publish 結果からは区別できない。区別できないまま緑で回り続けた結果が、
2026-06-04 を最後にサイトが約2ヶ月停止した #43〜#44 であり、全リクエストが 400 で
落ちても exit 0 だった 2026-08-05 の #51 である。

exit 3 / 4 はその区別を CI に運ぶための信号として入った。しかし現在、**信号が届かない
経路が2つ残っている**。

## 2. 残っている2つの穴

### 穴A（#61）— 「答えは返ったが thread にならなかった」が成功と数えられる

成功判定に使っている述語:

```python
def usable_result(val): return bool(val) and bool(val.get("speeches"))
```

実際の組み立て条件はこれより厳しい。`assemble_from_manifest()` はさらに

- 各 `speechOrder` が再取得した raw に存在すること
- スレッドごとの `input_hash` が一致すること
- `assemble_thread()` が `None` を返さないこと

を要求する。したがって「全件 parse でき `speeches` も非空だが `speechOrder` が raw と
対応しない」ケースは、カウンタ上は**全会議成功**、`failed` は 0、exit 0、sidecar 保持で
通過する。

永久に無音ではない（resubmit → retry 3回 → `hard_fail` で exit 1）。しかし赤くなるのは
数日後であり、exit 3 が狙った「その日のうちに気づく」を満たさない。

### 穴B（#59）— resume 経路にシグナルが存在しない

pending sidecar が1件でもあると、workflow は Summarize ステップ全体をスキップする
（`.github/workflows/daily-batch.yml`: `if: steps.collect.outputs.has_pending != 'true'`）。
その日の処理を担う `collect_pending_batches()` は `api_stats` を持たず、
`systemic_failure()` を呼ばず、bool の `hard_fail` だけを返す。

結果、再開したバッチが全件 400 で返るケースでは:

1. repair 失敗 → assembly 失敗 → resubmit
2. retry threshold（3回）に達するまで `hard_fail=False`
3. その間 Summarize はスキップされ続ける
4. **数日にわたり GREEN のまま、1スレッドも公開されない**

これは exit 3 が終わらせるために作られた失敗そのものが、別経路で温存されている状態。

## 3. 設計

### 3.1 カウンタを3フィールドにする

```python
api_stats = {"attempted": N, "failed": M, "unassembled": K}
```

| フィールド | 意味 | 対処 |
|---|---|---|
| `failed` | API に聞いたが使える答えが返らなかった | リクエスト形状 / モデル / クォータ |
| `unassembled` | 使える答えは返ったが thread にならなかった | raw 再取得 / sidecar 破棄 |

判定述語を `failed == attempted` から **`failed + unassembled == attempted`** に変更する
（`_everything_asked_for_failed()` の1箇所）。`systemic_failure` / `suspect_failure` の
シグネチャと exit code は変えない。

**`usable_result()` は変更しない。** 「parse できて speeches がある」という意味のまま残す。
#61 が警告していた「API 障害と raw/manifest 不整合の混同」は、述語を広げるのではなく
**フィールドを分けること**で回避する。混ざるのは警報（exit code）だけで、カウンタは混ざらない。

新しい exit code は作らない。exit code が答えるのは「今日を赤くすべきか」であり、
どちらの原因でも答えは同じ「発言がサイトに届かなかった」だから。原因の区別はカウンタと
診断文が担う。既に2ファイルに跨っている契約をこれ以上増やさない。

### 3.2 `unassembled` を計上する場所は1箇所だけ

`assemble_from_manifest(...)` が `ok=False` を返した直後。

assembly は all-or-nothing（最初の失敗で `return [], False`）なので、`ok=False` は
「この run でこの日付は1本も thread にならなかった」と同義。したがって

> askable かつリクエストを持ち、まだ `failed` に計上されていない会議を、すべて
> `unassembled` に計上する

で per-meeting の帰属を追跡せずに正確な数が出る。all-or-nothing という既存の粗さを、
そのまま前提として使える。

**この配置が構造的に保証すること**（codex 指摘への対応）:
バッチ進行中・raw 欠損・results 期限切れは、すべて assembly に到達する前に `return` /
`continue` する。よって「まだ答えが返っていないもの」は `unassembled` に決して入らない。
**実際に組み立てを試して失敗した場合だけ**が計上される。

対象となる2つの呼び出し元:

- `submit_and_collect_batch()`（新規バッチ経路、`summarize.py:1348` 付近）
- `collect_pending_batches()`（resume 経路、`summarize.py:1065` 付近）

### 3.3 resume 経路にシグナルを通す（#59）

`collect_pending_batches()` が sidecar ごとに `api_stats` を持ち、日付ごとに
`systemic_failure()` を判定する。1件でも systemic なら `EXIT_SYSTEMIC_FAILURE` を返す。
既存の `hard_fail` → exit 1 はそのまま併存させる（retry 枯渇は今も昔もクラッシュ相当）。

戻り値は bool から **int の exit code**（`0` / `1` / `EXIT_SYSTEMIC_FAILURE`）へ変える。
`run_pipeline()` が既に同じ理由で exit code を返しているので、2つの経路が同じ語彙で
main() に答える形に揃える。優先順位は **hard_fail(1) > systemic(3) > 0** — retry 枯渇は
今も昔もクラッシュ相当で、publish を止めてでも人を呼ぶべき事象だから。
`main()` の `--collect-pending` 分岐は、その値をそのまま `sys.exit()` に渡す。

**suspect（exit 4）の carve-out は resume 経路には持ち込まない。**

carve-out が存在する理由は「30日 lookback が公開済み日を毎朝再訪するので、1件の失敗で
毎朝赤くなってしまう」から。resume の sidecar は lookback が毎日作るものではなく、
バッチを投げたから存在する 0〜4 件であり routine ではない。**softener の成立理由が無い
場所に softener を形だけコピーすると、それは fail-open の穴にしかならない。**
resume 経路では「答えが返ってきて、全部使えなかった」は常に systemic。

### 3.4 workflow 側

`.github/workflows/daily-batch.yml` の Collect ステップ:

```bash
set -e
rc=0
python scripts/summarize.py --collect-pending --batch-budget 1800 --ci-commit || rc=$?
if [ "$rc" -eq 3 ]; then
  echo "collect_systemic=true" >> "$GITHUB_OUTPUT"
elif [ "$rc" -ne 0 ]; then
  exit "$rc"        # クラッシュ / retry 枯渇は従来どおり abort
fi
```

出力は `collect_systemic=true|false` の boolean 1つ。日付リストは持たない —
`summarize.py` 側のログと `::error::` annotation が既に日付を名指ししており、
YAML に2本目の集約ロジックを増やす価値がないため。

最終の "Fail the run on a systemic summary failure" ステップの条件:

```yaml
if: steps.summarize.outputs.systemic_dates != '' || steps.collect.outputs.collect_systemic == 'true'
```

診断文も、resume 経路由来の場合があることを含む表現にする。

## 4. テスト

`scripts/tests/test_systemic_failure.py` に追加:

1. `unassembled` 経路で exit 3 になること（全件 parse OK・speeches 非空・assembly 失敗）
2. 部分成功（1会議は組み立て成功、他は失敗）では鳴らないこと
3. resume 経路で全滅バッチが exit 3 を返すこと
4. resume 経路でバッチ進行中（`processing_status != "ended"`）のとき鳴らないこと
   — `unassembled` に入らないことの回帰ガード
5. 既存の `test_the_workflow_tolerates_exactly_these_exit_codes` を **Collect ステップにも**
   拡張。YAML をパースし、Collect が exit 3 で publish をブロックしない形になっているか
   検証する（Python 側と YAML 側は必ず同じコミットで動かすという既存ルールの機械化）

## 5. 正直に記す副作用

**exit 3 は今より鳴りやすくなる。** 複数会議を持つ日付で assembly が1箇所でも失敗すると、
その日付はその run で1本も publish できていないため systemic として報告される。

これは誤報ではなく事実の報告である（実際にその日付は何も publish していない）。ただし
頻度は上がる。もし運用上うるさすぎることが判明した場合、正しい調整点は
**判定述語ではなく workflow 側の閾値**（`SUSPECT_N -ge 2` と同じ位置）である。
判定を緩めると fail-open に戻る。

## 6. やらないこと

- `usable_result()` の意味を広げること（#61 の警告どおり）
- 新しい exit code の追加
- resume 経路への suspect / exit 4 の導入
- `has_question_for_the_api()` を outcome まで広げること（#60 の管轄。outcome の例外握り
  つぶしが直るまでは、広げると `attempted > 0, failed == 0` を作って**障害を隠す方向**に働く）
