# summary-layer 障害シグナルの残穴を塞ぐ（#59 / #61）

- 日付: 2026-08-07
- 対象 issue: #59（resume 経路にシグナルが無い）、#61（組み立て不能な結果が成功と数えられる）
- 前提となる既存実装: #51 / #52 で導入した `EXIT_SYSTEMIC_FAILURE = 3` / `EXIT_SUSPECT_FAILURE = 4`
- 改訂: rev6（Gate1 codex 敵対レビュー 5 ラウンド。round 4 以降 critical 0、
  設計を単純化する important 指摘と事実誤りを取り込んで確定）
- 分離した issue: #65（`hash_mismatch` の決定論的に失敗する再送）、
  #66（恒久的損失が warning のままで重大度が逆転している）。いずれも §6 参照

## 0. この設計を貫く1つの分離

4ラウンドの敵対レビューが、毎回別の顔で同じ誤りを指摘した。根っこは1つ:

> **「今朝を赤くするか」と「何を調べるか」は別の問いであり、1つの数字に同居できない。**

rev1〜rev4 はこの原則を掲げながら、実は守りきれていなかった。「答えは返ったが公開できな
かった」を**会議単位の tally**（`unassembled` → `blocked`）で表そうとしていたからである。

しかし assembly は **all-or-nothing**（最初の失敗で日付全体を捨てる）なので、これは
**日付単位の事実**であって会議に配分できない。配分しようとすると、検査すらされていない
会議まで「失敗」と数えることになり、原因診断が捏造になる。rev4 まではその捏造を
「警報としては正しいので許容する」と言い訳していた。

**rev5 は tally をやめる。** 日付単位の事実は日付単位のフラグで表す。

- **警報**は日付単位の事実に答える — この run で、この日付は何か公開できたか。
- **診断**は会議・スレッド単位の観測に答える — どこで、何が理由で止まったか。

## 1. これは何を守る仕組みか

毎朝1つの問いに答えるためだけに存在する。

> **今朝スレッドが1本も出なかったのは、静かな国会だったからか、パイプラインが壊れているからか。**

区別できないまま緑で回り続けた結果が、2026-06-04 を最後にサイトが約2ヶ月停止した
#43〜#44 であり、全リクエストが 400 で落ちても exit 0 だった 2026-08-05 の #51 である。

exit 3 / 4 はその区別を CI に運ぶために入った。しかし**信号が届かない経路が2つ残っている**。

## 2. 残っている2つの穴

### 穴A（#61）— 「答えは返ったが thread にならなかった」が成功と数えられる

成功判定は `usable_result(val) = bool(val) and bool(val.get("speeches"))`。
実際の組み立て条件（`assemble_from_manifest()`）はこれより厳しく、各 `speechOrder` が
再取得した raw に存在すること・`input_hash` が一致すること・各 custom_id に結果が
存在すること・`assemble_thread()` が None を返さないこと、を要求する。

したがって「全件 parse でき `speeches` も非空だが `speechOrder` が raw と対応しない」
ケースは、カウンタ上は全会議成功、`failed` は 0、exit 0、sidecar 保持で通過する。
永久に無音ではない（resubmit → retry 3回 → exit 1）が、赤くなるのは数日後で、
exit 3 が狙った「その日のうちに気づく」を満たさない。

### 穴B（#59）— resume 経路にシグナルが存在しない

pending sidecar が1件でもあると、workflow は Summarize ステップ全体をスキップする
（`daily-batch.yml`: `if: steps.collect.outputs.has_pending != 'true'`）。その日を担う
`collect_pending_batches()` は `api_stats` を持たず `systemic_failure()` を呼ばず、
bool の `hard_fail` だけを返す。

再開バッチが全件 400 で返るケースでは、retry threshold に達するまで `hard_fail=False`、
その間 Summarize はスキップされ続け、**数日にわたり GREEN のまま 1 スレッドも公開されない**。

## 3. 設計

### 3.1 警報 — 2つの独立したトリガ

exit 3 / 4 を出す条件を、**2つのトリガの OR** にする。exit code は増やさない。

**トリガ1（既存・変更なし）— summary layer rejection**

```
attempted > 0 かつ failed == attempted
```

「API に聞いた会議が、1つ残らず使える答えを返さなかった」。`api_stats` は
`{"attempted", "failed"}` のまま。**`blocked` のような第3の tally は作らない**
（rev4 からの訂正 — §0）。

**トリガ2（新規）— publication blocked**

```
summary_attempted > 0 かつ assembly が ok=False
```

「summary リクエストを投げた会議が存在するのに、この run でこの日付は 1 本も公開できなかった」。
これは会議に配分せず、**日付単位の boolean** として持つ。

`summary_attempted` は `attempted` とは別に数える。**これが必要な理由**（rev4 の欠陥）:
`attempted` には「grouping が正当に 0 スレッドを返した会議」も含まれるため、それを母数に
すると `失敗数 < attempted` となってトリガが立たない。

> 会議A: grouping 成功・0 スレッド → `attempted += 1`、summary リクエストなし
> 会議B: summary は全件 usable だが `hash_mismatch` → assembly 失敗
> rev4 の式では `attempted=2, failed+blocked=1` で**鳴らない**。

トリガ2 の母数は「この run で summary リクエストを持った会議」だけ。

**suspect（exit 4）の carve-out は両トリガに共通**の証拠ルールで適用する:

```
そのトリガの母数が 1 かつ published_threads > 0 → suspect(4)、それ以外は systemic(3)
```

「公開済み日付での単独会議失敗は outage の証拠として弱い」という理由は、失敗が API 側でも
公開側でも変わらない。

**`usable_result()` は変更しない。**

### 3.2 診断 — assembly の失敗観測を構造化して返す

`assemble_from_manifest()` の戻り値を `(threads, ok)` から `(threads, ok, diagnostic)` に
変える。`diagnostic` は失敗時のみ非 None:

```python
{
  "scope": "date" | "meeting" | "thread",
  "meeting_id": str | None,   # scope="date" のとき None
  "custom_id": str | None,    # scope が "thread" 以外のとき None
  "reason": str,
}
```

**単数**である。assembly は最初の失敗で早期 return するので 1 回の呼び出しから得られる観測は
常に 1 件であり、リストにすると「網羅的な失敗一覧」だと誤読される。複数 sidecar・複数日付に
またがる蓄積は呼び出し元（§3.5）が行う。

`reason` は**観測事実のみ**を表す語彙:

| reason | scope | 観測 |
|---|---|---|
| `raw_date_missing` | date | その日付の raw が1件も無い（§3.3.1。assembly の外で観測） |
| `raw_missing` | meeting | manifest の会議に対応する raw が無い |
| `speech_gap` | thread | `speechOrder` が raw に存在しない |
| `hash_mismatch` | thread | `input_hash` が一致しない |
| `missing_result` | thread | custom_id に結果が無い |
| `thread_build_failed` | thread | `assemble_thread()` が None を返した |

`raw_date_missing` 以外はすべて現状 `log.error` の別文言として存在するので、**文言を語彙化
して返すだけ**で新しい判定ロジックは増えない。

**`missing_result` を「API 側の問題」とは診断しない。** 結果が欠ける原因は API の rejection
だけでなく、結果取得・parse・custom_id 対応・辞書構築の欠陥でも同じ状態になる。reason は
観測に留め、断定は人に委ねる。

### 3.3 トリガ2 の判定点

`assemble_from_manifest(...)` が `ok=False` を返した直後。両方の呼び出し元で同じ:

- `run_batch_phase()`（新規バッチ経路、`summarize.py:1348` 付近）
- `collect_pending_batches()`（resume 経路、`summarize.py:1065` 付近）

**構造的に保証されること:** バッチ進行中（`processing_status != "ended"`）と results 期限
切れは assembly に到達する前に `return` / `continue` する。よって「まだ答えが返っていない
もの」でトリガ2 は立たない。制御フローの将来変更に対しては §4 のテストが回帰ガードになる。

#### 3.3.1 `ended` かつ日付全体の raw 不在も、トリガ2 に含める

rev3 まではこのケースを「既存の keep/abandon 判定の管轄」として警報から外していたが、
**要件を満たしていなかった**。

現在の Collect はバッチが `ended` であることを確認した**後**に raw を読み、空なら sidecar を
保持して `continue` する（`summarize.py:1019-1042`）。sidecar が残る限り Summarize ステップ
全体がスキップされる。つまり:

> バッチは終了済み（`ended`。各リクエストが成功したという意味ではない）。しかし fetcher の
> 退行やファイル名変更で、結果を検査・組み立てるための raw がその日付に1件も無い。
> → 組み立て不能・新規処理停止・**GREEN のまま最大 31 日**（`ABANDON_AGE_DAYS`）。

これは「まだ in-flight」とは違い、**まさに穴B と同型の失敗**である。よって:

- `ended` 後に `_load_meetings_for_date()` が空で、かつ **abandonable でない**
  （＝ raw がまだ取得できて然るべき期間内）→ トリガ2 を立て、`reason = raw_date_missing`。
  sidecar は従来どおり保持し、**再送も retry 消費もしない**（警報だけを足す）。
  母数 `summary_attempted` は manifest の `threads` 非空の会議数を使う。
- `ended` 後に空で、かつ **abandonable**（raw が正当に窓から出た）→ 従来どおり abandon。
  既に `::warning::` を出して sidecar を削除しているので、この経路は既に可視である。

このとき既に thread がある日付で母数が 1 なら、§3.1 の共通ルールどおり suspect(4) になる。

### 3.4 resume 経路の母数

**新規バッチ経路** — `attempted` は既存どおり `has_question_for_the_api()` が真の会議。
`summary_attempted` は `prepared_meetings` のうち `pending` が非空の会議。

**resume 経路** — `attempted` も `summary_attempted` も
**manifest 内で `threads` が非空の会議**。

sidecar manifest が保持するのは `meeting_id` / `outcome` / `threads` だけで、`askable` は
永続化されていない（`build_manifest_meetings`、`summarize.py:598`）。しかしこれは**制約では
なく、正しい母数がそもそも違うことの反映**である。resume の run が API に投げる質問は summary
リクエストだけ（grouping と outcome は元の run で終わっている）。したがって「この run で API に
聞いた会議」＝「manifest に summary リクエストを持つ会議」であり、`threads` が非空かどうかで
完全に判定できる。

**sidecar の schema 変更は不要**（`SCHEMA_VERSION` を触らない）。CLAUDE.md が定めるとおり
`SCHEMA_VERSION` の変更は `data/pending-batches/` が空のときしか land できず、その制約を
この変更に持ち込まずに済む。

`published_threads` は `data/threads/{date}.json` に既に入っている件数を読む。assembly 失敗時
はこの run では何も追記されていないので、既存件数がそのまま判定材料になる。

### 3.5 resume 経路の verdict と戻り値（#59）

`collect_pending_batches()` は sidecar ごとに §3.1 の判定を行い、**結果オブジェクト**を返す:

```python
CollectResult = {
    "hard_fail": bool,          # schema mismatch / retry 枯渇 / クラッシュ相当
    "systemic_dates": [str],
    "suspect_dates": [str],
    "diagnostics": [dict],      # §3.2 の観測に date を足して蓄積
}
```

**`--collect-pending` は soft verdict では exit 0 を返す**（rev4 からの訂正）。理由:

- Collect は1回の実行で**複数日付**を扱うため、単一の exit code では verdict を表現できない。
  どのみち日付リストが必要であり、rc=3/4 は冗長な第2の輸送路になる。
- rc=3/4 を出さなければ、Collect ステップは `set -e` の下で**素の呼び出しのまま**でよい。
  rc 捕捉ロジックが不要になり、「soft verdict が publish を止める」事故の余地が消える。
- `main()` が `sys.exit(1)` するのは `hard_fail` のときだけ（従来どおり）。

`--date` 側（`run_pipeline`）は従来どおり per-date の exit code を返す。**1プロセス1日付だから
成立する**契約であり、複数日付を扱う `--collect-pending` に同じ形を強いる必要はない。
この非対称の理由を CLAUDE.md の exit code 表に注記する。

### 3.6 証拠の輸送

**annotation を第一の輸送路にする。** rev2 の「`finally` で `GITHUB_OUTPUT` に書けば残る」は
不十分だった — ステップが失敗すると後続ステップは既定の `success()` でスキップされ、
書けた output は誰にも読まれない（workflow 自身が `daily-batch.yml:165` で明記している）。

したがって:

1. **両経路とも**、verdict を得るたびに `::error::` / `::warning::` を print する。
   §3.2 の `reason` もここに載せる。失敗ステップでも annotation は run に残る。
2. run summary への reason 掲載は `GITHUB_STEP_SUMMARY` へ直接追記する
   （ステップ output に構造化 payload を詰めない）。

**新規バッチ経路の diagnostic の伝播**: 対象関数は **`run_batch_phase()`**
（`summarize.py:1205`。rev5 は `submit_and_collect_batch()` という存在しない名前を書いていた）。
現在 `(new_threads, thread_counter, completed_meeting_ids, pending)` の 4-tuple を返している。

**tuple を増やさず、名前付きの結果 dict に変える。** 5番目・6番目の要素を足す形は、
呼び出し側の展開が位置依存で壊れやすく、この関数は既に out-parameter（`api_stats`）も
持っていて読み手の負荷が高い。

```python
BatchPhaseResult = {
    "threads": list,
    "thread_counter": int,
    "completed_meeting_ids": list,
    "pending": bool,
    "publication_blocked": bool,   # トリガ2
    "summary_attempted": int,      # トリガ2 の母数
    "diagnostic": dict | None,     # §3.2
}
```

呼び出し元は `run_pipeline()` の 1 箇所のみ。そこが `publication_blocked` を §3.1 の判定に渡し、
`diagnostic` を annotation と run summary に載せる。

#### 3.6.1 `$GITHUB_OUTPUT` の書き込み契約（誰が・何を・いつ）

これを曖昧にしたままの実装は、Python 側で正しく検出してもジョブを赤くできず #59 を再現する。
以下を仕様として固定する。

- **書き手は `main()` のみ。** workflow shell は書かない（二重の輸送路を作らない）。
- **形式**: 空白区切りの日付文字列。`systemic_dates` / `suspect_dates` の2キー。
  **書き出し前に集合化して重複排除する**（同じ日付が2回載ると §3.7 の閾値を誤って超える）。
- **タイミング**: `--collect-pending` は soft verdict では exit 0 なので、通常の終了パスで書く。
  `hard_fail` による `sys.exit(1)` の場合は `try/finally` で書くが、**そのとき後続ステップは
  スキップされるので読まれない前提**とし、証拠は 1 の annotation が担う。
- **`--date` 側は書かない。** 従来どおり exit code で答え、workflow の date ループが集約する
  （1プロセス1日付なので exit code で足りる）。
- **最終判定**: 最終ステップが `steps.summarize.outputs.{systemic,suspect}_dates` と
  `steps.collect.outputs.{systemic,suspect}_dates` の**4つ**を読み、集合 union して
  §3.7 の閾値を適用し `should_fail` を決める。ステップの `if:` 条件に判定を書かない
  （GitHub 式では集合演算も数値比較もできない。現行が `SUSPECT_N -ge 2` を shell に置いて
  いるのと同じ理由）。

### 3.7 閾値の適用位置

`SUSPECT_N -ge 2` の閾値は現在 Summarize ステップの中にある。Collect も suspect を出す
ようになるため、閾値は**両者を集約した後**に適用する。

- Summarize / Collect の両ステップは、閾値を適用せず生の `systemic_dates` /
  `suspect_dates` のみを出力する。
- 最終ステップが両者を **集合として union（重複排除）** し、`SUSPECT_N -ge 2` を適用する。
  文字列連結にすると同じ日付を2件と誤カウントして閾値を超えうる。

**union の根拠**: 両者の verdict は実際には排他的である（resume で verdict が立つのは
assembly 失敗時だけで、そのとき sidecar は保持されるので `has_pending=true` となり
Summarize はスキップされる）。union が必要な本当の理由は、**Collect が1回の実行で複数
sidecar（＝複数日付）を扱うため**、閾値がその複数日付に対して適用されなければならないこと。
経路をまたぐ union は防御的な冗長として持つ。

CLAUDE.md の「閾値は `daily-batch.yml` の `SUSPECT_N -ge 2` で変える」は引き続き真だが
**ステップが変わる**ので、同じコミットで CLAUDE.md も更新する。

### 3.8 メッセージの文言

トリガ1 と トリガ2 は同じ exit code を共有するが、**同じ文言で報告してはならない**。

**2つのトリガは排他ではない**（rev6 での訂正。rev5 は「どちらだったかを機械が知っているので
言い切る」と書いたが誤り）。全件 rejection の場合、repair 後も全結果が unusable なので
トリガ1 が立ち、**その同じ結果に対して assembly が `missing_result` を返すのでトリガ2 も
立つ**。現行の実行順（unusable 集計 → assembly、`summarize.py:1344`）がそうなっている。

したがって原因を単一選択させず、**2つの観測を独立に併記する**:

```
::error::2026-08-05: この日付は1本も公開できませんでした
  - API に聞いた 5 会議すべてが使える答えを返しませんでした（rejection）
  - 組み立ても失敗しています: missing_result (meeting=..., custom_id=s_ab12cd34ef56_00)
```

片方だけ立つ場合はその行だけを出す。「answered but unassemblable」という表現は、
**結果が実在した場合に限って**使う（トリガ1 が立っていないとき）。トリガ1 と同時に立って
いるのに「答えは返ったが」と書くのは事実に反し、operator を 400 の捜索から遠ざける。

現行の最終ステップの文言は「REJECTED か / ANSWERED but not assemblable か」を operator に
**選ばせている**。rev6 ではどちらが立ったかを機械が知っているので、**該当する行だけを出す**
（排他の断定はしない）。CLAUDE.md の exit code 表の説明もこれに合わせる。

## 4. テスト

`scripts/tests/test_systemic_failure.py` に追加:

0. **全件 rejection で両トリガが同時に立ち、日付が1回だけ集計されること。**
   診断が「答えは返ったが」と矛盾表示しないこと（§3.8。rev6 で追加。
   これが最も起きやすく、最も誤診断しやすい経路）
1. トリガ2 で exit 3（全件 parse OK・speeches 非空・assembly 失敗）
2. **rejection が同期 repair で救われて assembly が成功した run では鳴らない**
   （rev5 で訂正。rev4 の「rejected が混じっていても assembly 成功」は成立しない —
   repair 後も rejection が残れば `results.get(custom_id)` が falsy になり assembly は即
   `ok=False`。「rejected が混在したまま成功」という状態は存在しない）
3. **20 thread 中 1 件だけ repair 不能 → 日付全体がトリガ2 で鳴る**（2 の対）
4. **`attempted` に 0 スレッド会議が含まれてもトリガ2 が立つこと**
   （§3.1 の `summary_attempted` の回帰ガード。rev4 が取りこぼしていた形）
5. resume で全滅バッチが `systemic_dates` に載る
6. resume でバッチ進行中（`processing_status != "ended"`）のときトリガ2 が立たない
7. resume・母数1・既存 thread あり → `suspect_dates` に載る
8. `ended` かつ日付全体 raw 不在で abandonable でないとき、`raw_date_missing` 付きで
   `systemic_dates` に載る。abandonable なときは従来どおり abandon して鳴らない
9. manifest 内の一部会議だけ raw 欠損 → `raw_missing`、`scope="meeting"`、`custom_id` が None
10. results expired でトリガ2 が立たない
11. **`--collect-pending` が soft verdict では exit 0 を返し、`hard_fail` でのみ 1 を返す**
    （§3.5 の契約）
12. systemic 検出後に別 sidecar が hard-fail しても、先の verdict が `::error::` annotation
    として出力されている
13. resume 経路の母数が「manifest の `threads` 非空の会議」であること
    — `threads: []` の会議が母数に入らない
14. **最終ステップの union が同一日付を1回だけ数えること**（§3.7 / §3.6.1）
14b. **Summarize がスキップされたまま、Collect の output だけでジョブが赤くなること**
    （#59 の受け入れ条件そのもの。これが通らなければこの変更は目的を果たしていない）
15. 既存の `test_the_workflow_tolerates_exactly_these_exit_codes` を拡張:
    Collect ステップが soft verdict で publish をブロックしないこと、最終ステップが
    両ステップの出力を参照していること。現行テストは最終ステップの `if` を文字列等価で
    固定している（`test_systemic_failure.py:583` 付近）ので、そこも更新する。

## 5. 正直に記す副作用

**exit 3 / 4 は今より鳴りやすくなる。** 100会議中99会議が完全に正常でも、最後の1 thread に
`speech_gap` があれば、assembly の all-or-nothing により**その日付は1本も公開されない**ので
トリガ2 が立つ。

これは誤報ではない（実際に何も公開されていない）。ただし**「summary layer が全滅した」とは
違う事実**なので、§3.8 の文言分離が必須である。「99会議が正常なのに summary 全滅と言われた」
は、次に人が信号を切るきっかけになる。

**systemic には閾値が無い**（1日で必ず赤）。suspect にしか `SUSPECT_N -ge 2` は無い。
うるさすぎると判明した場合、正しい調整点は判定述語ではなく、**トリガ2 専用の閾値を
§3.7 の位置に足すこと**である。判定を緩めると fail-open に戻る。

**自己修復するパスも、1回目で赤くなる。** #46 の truncation 対策のように「assembly 失敗 →
再送 → 翌朝は直っている」という**設計上自己修復する経路**でも、既存 thread が 0 の日付なら
初回の assembly 失敗の時点でトリガ2 が立ち、その run は赤くなる。

これは意図した挙動として受け入れる。理由: exit 3 は publish をブロックしないので、コストは
「赤い run と GitHub Issue が1本」であり、翌朝直れば終わる。一方、「どうせ次の run で直る」
という前提こそが、19 連続 GREEN で 2 ヶ月サイトを止めた #43〜#44 の前提だった。
**自己修復するはずのものが自己修復しなかったときに気づけない**のが、この仕組みが無い状態である。

うるさいと判明した場合の調整点は、やはり判定述語ではなく §3.7 の閾値側（トリガ2 専用の
「2 run 連続で同じ日付」条件を足す等）。

**残る無駄:** `hash_mismatch` の sidecar は、この変更後も従来どおり再送される。再送は
決定論的に同じ場所で失敗するので、バッチ 1 本分の課金と retry 枠を 3 回消費してから
exit 1 に至る（#65）。この変更が改善するのは**気づくまでの時間**（3日 → 当日）だけ。

## 6. やらないこと

- `usable_result()` の意味を広げること（#61 の警告どおり）
- 新しい exit code の追加
- 「公開できなかった」を会議単位の tally で表すこと（§0 — rev5 の中心的な訂正）
- sidecar への `askable` 永続化と `SCHEMA_VERSION` の変更（§3.4）
- assembly の原子性を変えること（部分 publish の導入）。これは公開データの一貫性に関わる
  別の設計判断であり、警報の変更に混ぜない
- **reason 別の retry policy（#65 へ分離）** — Gate1 で「原因ごとに remedy が違うと言うなら
  診断表示だけで終えるのは片手落ち」と指摘された。正しいが、再送の可否は**データ整合性の
  アンカー（`input_hash`）をどう扱うか**という別の判断を含む。同じ変更に混ぜると、警報の
  レビューと整合性のレビューが互いを薄める。この変更が入るだけでも doomed retry は
  初日に赤くなるので、#65 の被害は先に小さくなる。
- **abandon（恒久的損失）の重大度を変えること（#66 へ分離）** — この変更が入ると、
  回収可能な 1〜30 日目は毎朝赤いのに、回収不能が確定して sidecar を削除する 31 日目だけが
  `::warning::` で緑になる、という重大度の逆転が顕在化する。指摘は正しいが、abandon の
  赤化は「過去の回収不能 sidecar が複数同時に期限を迎えたとき毎朝赤い」運用リスクを持つ
  別判断なので分離する。
- `has_question_for_the_api()` を outcome まで広げること（#60 の管轄）

## 7. コミット分割

1件の PR / 一連のコミットとして land させる（片方だけ直すと同じ障害が経路依存で再び緑になる）。
レビュー可能性のため内部は3つに分ける:

1. **トリガ2 と診断** — `summary_attempted`、`publication_blocked`、
   `assemble_from_manifest` の diagnostic 化、両経路での判定
2. **resume verdict** — `collect_pending_batches` の `CollectResult` 化と systemic/suspect 判定、
   `raw_date_missing`
3. **workflow 集約** — annotation 輸送、閾値の集約位置移動と重複排除、文言分離、CLAUDE.md 更新
