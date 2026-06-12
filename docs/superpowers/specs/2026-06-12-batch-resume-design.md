# バッチID永続化による run 横断 resume 設計

- 日付: 2026-06-12
- 対象: `scripts/summarize.py --batch` のサマリフェーズ
- 関連 Issue: #41 (`pipeline-failure`)
- 直前コミット: `58c0ed9 fix(pipeline): cancel timed-out summary batches and repair failure alerting`
- レビュー: codex external review 2回反映済み（後述「レビュー反映」）

## 背景と問題

daily-batch ワークフローが 2026-06-10・06-11 と2日連続で約1時間34分かけて失敗した。
原因は Anthropic Message Batches API のバッチ（54リクエスト）が 90分（5400s）のポーリング
上限を超えても `in_progress` のまま終わらず、`58c0ed9` のタイムアウト処理が **設計通りに**
バッチを cancel して `TimeoutError` で exit 1 → CI 赤になったこと。

`58c0ed9` は「タイムアウトしたバッチが課金され続ける」問題を解いたが、その代償として
**「90分待てば完了したはずのバッチを毎回捨てる」** 挙動になった。`batch_id` は
`run_batch_phase`（`scripts/summarize.py:443`）のローカル変数で次回 run に引き継がれず、
混雑が続く間は `submit → 90分待つ → cancel → 失敗 → 翌日また同じ meeting を submit` の
デッドロックに陥る。根治は **batch_id の永続化による run 横断 resume**。

## 不変条件 / 現状把握（実コード検証済み）

- サマリ層は **stateless / deterministic / prompt-only**（CLAUDE.md「Summary Layer Invariants」）。
  sidecar は「未完バッチの回収先ポインタ＋submit 時に確定した入力の写し」であり、サマリ**内容**を
  run 間で変化させない（同一入力→同一出力を保つ）。
- GitHub Actions runner は使い捨て。run 横断状態は **commit & push** でしか渡せない。
- `daily-batch.yml` は1つの job 内で `for d in $DATES_LIST; do python summarize.py --date "$d" --batch; done`
  と **日付ごとに別プロセス**で呼ぶ（`set -e`）。GHA `timeout-minutes: 120`。
- job permissions は `contents: write` のみ。`issues: write` は別 job `notify-on-failure` だけが持つ。
- `data/raw/` は **リポジトリにコミットされていない**（追跡0件）。raw は run ごとに NDL 等から再 fetch される。
- `concurrency:` 設定は無い（overlapping run があり得る）。
- 既存 `{date}.progress.json` は **run 内専用**（成功時 `summarize.py:713-714` で削除、git 管理外）。
  run 横断完了判定はコミット済み `{date}.json` から再導出（`summarize.py:587-604`）。

→ batch_id を progress.json に置くと引き継げない。**意図的にコミットする専用 sidecar が必要。**

## 設計

### 0. なぜ「再グルーピング」ではなくマニフェスト永続化か

`make_batch_custom_id(meeting_id, idx)`（`summarize.py:338-346`）= `s_{sha256(meeting_id)[:12]}_{idx:02d}`。
custom_id は **meeting_id とスレッド位置インデックスのみ**で、内容を含まない。

resume 時に「再グルーピングして custom_id を再構築」すると、グルーピング結果が1つでもズレた場合に
custom_id は**位置ベースで常にマッチ**し、`results.get(custom_id)` が成功して
**古いサマリを新しい thread_info に貼り付ける silent corruption** になる（skip されない）。
temperature=0 でも完全な決定論は保証されず、モデル更新では確実に破綻する。

**解決**: submit 時点で確定したグルーピング・マニフェストを sidecar に永続化し、resume 時は
**再グルーピングせず**マニフェストどおりに assemble する。決定論依存を排除し、resume 時の
グルーパー/outcome LLM 呼び出しも省ける。

### 1. 永続化フォーマット（sidecar）

日付ごとに `data/threads/{date}.pending-batch.json`:

```json
{
  "schema_version": 1,
  "model": "claude-...",
  "retry_count": 0,
  "attempts": [
    {
      "batch_id": "msgbatch_01HBjNG4...",
      "submitted_at": "2026-06-11T21:50:15Z",
      "terminal_status": null,
      "terminal_at": null
    }
  ],
  "meetings": [
    {
      "meeting_id": "参議院_外交防衛委員会_2026-05-14_第8号",
      "outcome": { "result": null, "resolution": null, "status": "ongoing" },
      "threads": [
        {
          "custom_id": "s_ab12cd34ef56_00",
          "thread_idx": 0,
          "topic": "…",
          "speechOrders": [12, 13, 14],
          "input_hash": "sha256:…"
        }
      ]
    }
  ]
}
```

- **`input_hash`（C2 対策）**: submit 時にそのスレッドの batch request 入力（thread_speeches の本文＋
  プロンプト構築に使う確定要素）を正規化して取った SHA256。speech 本文そのものは持たない（sidecar を小さく保つ）。
- **`attempts[]`（I5 対策）**: バッチ投入の履歴。再 submit 時は新しい attempt を push（古い batch_id は履歴に残す）。
  現在の in-flight は `attempts[-1]`。`retry_count` は terminal 非成功で +1。
- `data/threads/` は `git add data/threads/` 対象。回収完了時・恒久放棄時に sidecar を削除。
- `summarize.py:682` の `.json` クリーンアップ glob が拾わないよう `.pending-batch.json` を明示除外。

### 2. 永続化境界（orphan 防護 / C2・C3）

**問題**: submit 直後の sidecar 書き込みは workflow 末尾の commit & push まではリポジトリに残らない。
途中 kill / `set -e` 後続失敗 / push 衝突で sidecar が残らないと、Anthropic 側にバッチは在るのに
回収不能な **orphan（課金済み）** になる。さらに `data/raw/` は非コミットなので、resume 時の raw は
別 fetch であり、submit 時スナップショットと一致する保証がない。

**対策**:
1. **early commit（CI 専用）**: sidecar を書いた直後、長い poll の前に sidecar だけを
   `git add <sidecar> && git commit && git push`。ローカル実行では push しない（`CI` env / 明示フラグで gate）。
   永続化境界を poll の前へ移す。
2. **input_hash 照合（§4）**: resume 時に再 fetch した raw から thread_speeches を再構築し input_hash を再計算、
   sidecar と一致した場合のみ assemble。raw 経路の silent corruption を閉じ、「raw 不変」前提への依存を消す。
3. **concurrency ロック（C3）**: `daily-batch.yml` に `concurrency: { group: daily-batch, cancel-in-progress: false }`
   を追加。さらに submit 直前に最新 sidecar を recheck（既に in-flight があれば submit しない）。

### 3. poll 予算と「pending 後 submit 停止」（C1）

per-run のグローバル挙動を、**別プロセスの日付ループでも成立させる**ため workflow 側で制御する:

1. **pre-collect ステップ（run 冒頭）**: `DATES_LIST` とは独立に `data/threads/*.pending-batch.json` を
   走査し、既存 in-flight バッチを retrieve。ended は assemble、in_progress はグローバル予算内で待つ
   （§4）。ここで予算を消費する。
2. **submit ループ**: その後 `for d in $DATES_LIST` を回すが、ある日付の summarize.py が
   **pending を返した（sidecar 新規作成）時点で shell ループを break**（以降の新規 submit をしない）。
   summarize.py は「pending か否か」を終了コードまたは marker で workflow に伝える（exit 0 維持、
   別経路で signal）。
3. **グローバル予算**: 既定 **30分**（env 可変）。pre-collect と最初の submit-poll の合計をこの予算で bound。
   30分 << 120分で、後続 enrich/validate/commit/push に余裕を残す。

健全なバッチ（3〜7分）は同一 run で回収され、通常日のデータ遅延はゼロ。

### 4. resume / assemble フロー

pre-collect で各 sidecar の `attempts[-1].batch_id` を retrieve():

```
ended:
  各 meeting の raw を再 fetch → build_speech_lookup
  各 thread: speechOrders で thread_speeches を引き、input_hash を再計算
    - sidecar の input_hash と不一致、または speechOrder 欠落 → assemble 失敗（§6 へ）
  fetch_summary_results(batch_id) を custom_id で対応付け
    - manifest の全 custom_id が結果に揃っているか確認（I6）
      欠落があれば meeting を completed にせず sidecar を保持（§6 へ）
  全て揃い・全 hash 一致 → persisted topic/outcome と合わせて thread 組み立て
  → threads 追記、meeting を completed、（全 meeting 完了で）sidecar 削除
in_progress: グローバル予算内で待つ。完了すれば上と同様。予算切れは sidecar 据え置き・pending signal。
canceled/expired: §6（retry）。
```

- assemble は **再グルーピングを行わない**。custom_id は manifest の対応表のみで解決。
- 不変条件: **1日付につき同時 in-flight バッチは1つ**（`attempts[-1]` のみ）。

### 5. exit 0 と「何を書くか」

- pending（予算切れで未完）でも **exit 0**（失敗でなく非同期待ち）。workflow へは別 signal で pending を伝える（§3-2）。
- 完了 thread が無い日付は **空の `{date}.json` を作らない**。sidecar だけを残す。
  auto-resume（`summarize.py:587-604`）は「sidecar あり / threads ファイル無し」を許容するよう拡張。
- クリーンアップで sidecar を削除しないこと。

### 6. retry / 詰まり検知 / エスカレーション

- **assemble 失敗（input_hash 不一致 / custom_id 欠落 / 一部 request failed）**: meeting を completed にせず
  sidecar を保持。該当 attempt を terminal 失敗として記録（`attempts[-1].terminal_status`）。
- **terminal 非成功（expired/canceled/assemble失敗）**: `retry_count` +1。次の submit 機会で投げ直し、
  新しい attempt を push。
- **3回連続**（同一日付で terminal 非成功が 3 回）→ **hard fail（exit 非ゼロ・CI 赤）**。
  `attempts[]` 履歴と `retry_count` を Issue #41 に明示し人間の介入を促す（緑のままのデータ停滞を防ぐ）。
- **詰まり検知（in_progress 長期化）**: `attempts[-1].submitted_at` が **2日超**なら、
  `data/threads/*.pending-batch.json` を走査する軽量ステップが `gh issue comment`（Issue #41）で通知。
  - exit 0 化で `notify-on-failure`（`if: failure()`）は発火しないため別経路。
  - **この通知ステップ/ジョブには `issues: write` と `GH_TOKEN`・`GH_REPO` を明示**（I4。
    fetch-and-summarize job は `contents: write` のみのため、専用 job 化するか job 権限を拡張する）。

### 7. テスト（pytest 基盤を新設）

現状 Python テストは無し（`tests/unit/ministry.test.mjs` のみ）。最小 pytest を `scripts/tests/` に新設し、
fake Anthropic client を用いる:

- sidecar（attempts/manifest）の read/write/delete ラウンドトリップ
- poll: 予算切れで raise/cancel せず pending を返す
- resume assemble: sidecar + mocked `ended` → **再グルーピングなしで**同期パスと一致
- **input_hash ガード（C2）**: raw を改変したケースで hash 不一致 → assemble せず sidecar 保持
- **完全性（I6）**: 一部 custom_id 欠落 → meeting を completed にせず sidecar 保持
- silent-corruption ガード: custom_id 対応のみで解決しグルーパーを呼ばない
- retry: terminal 非成功 3 回で hard fail（exit 非ゼロ）
- 詰まり検知: 経過2日超で通知経路が起動
- `ci.yml` に pytest ステップ追加

## 影響ファイル

| ファイル | 変更内容 |
| --- | --- |
| `scripts/pipeline/summarizer.py` | `poll_summary_batch` を「状態を返す」方式に。cancel は不要に |
| `scripts/summarize.py` | sidecar(attempts/manifest+input_hash) load/save/delete、early commit(CI gate)、resume assemble（再グルーピングなし・hash照合・完全性チェック）、pending signal、retry/hard-fail、クリーンアップ除外、auto-resume 拡張 |
| `.github/workflows/daily-batch.yml` | `concurrency:` 追加、pre-collect ステップ、submit ループの break-on-pending、early commit 連携、詰まり検知ステップ（`issues: write` 付き）、グローバル予算 env |
| `scripts/tests/`（新規） | pytest + fake client |
| `.github/workflows/ci.yml` | pytest ステップ追加 |
| `README` 監視節 / メモリ `project_daily_batch_failures` | 根治済みに更新 |

## エラーハンドリング / エッジケース

- **GHA が 120分 kill / set -e 後続失敗**: sidecar は early commit 済みのため永続。次 run が resume。
- **raw が submit 時から変化**: input_hash 不一致で assemble せず保持（silent corruption を遮断、§6）。
- **ended batch の一部欠落**: 全 custom_id 揃いを completion 条件にし、欠落は保持（I6/§6）。
- **overlapping run**: `concurrency` ロック＋submit 直前 recheck で重複 submit を防止（C3）。
- **expired/canceled の継続**: retry_count 3 回で hard fail（無限ループ/二重課金を遮断）。
- **夜間に完了**: resume run が `ended` を即検出 → 待機なしで assemble。

## 非目標（YAGNI）

- 1日付で複数 in-flight バッチを並走させること。
- 同期（非バッチ）フォールバック。
- speech 本文を含むフル文脈の永続化（manifest＋input_hash で代替）。
- `batches.list()` による orphan リコンシリエーション（early commit + concurrency + グローバル予算で窓を
  ほぼゼロにする方針。将来必要になれば追加）。

## レビュー反映

### 1回目（codex, 2026-06-12）
- Critical: custom_id 位置依存 silent corruption → §0 マニフェスト永続化へ変更。
- Critical: sidecar 永続化境界 → §2 early commit 導入。
- Important: per-date poll 加算 → グローバル予算化。
- Important: expired 再 submit の二重課金/無限ループ → §6 retry 3 回 hard fail。
- Question: raw 安定性 / batch_pending 出力 → 明文化。

### 2回目（codex, 2026-06-12）
- **Critical C1**: 別プロセス日付ループでグローバル予算/submit 停止が不成立 → §3 で workflow pre-collect ＋
  break-on-pending ＋グローバル予算に変更。
- **Critical C2**: sidecar/raw 非原子（raw 非コミット）→ raw 経路の silent corruption → §1 `input_hash`＋
  §4 照合で遮断。
- **Critical C3**: concurrency ロック無し → 重複 submit/orphan → §2 `concurrency:`＋submit 直前 recheck。
- **Important I4**: 詰まり通知 step の `issues: write` 不足 → §6 で権限明示/専用 job 化。
- **Important I5**: retry schema 不足 → §1 `attempts[]` 履歴化。
- **Important I6**: ended batch 一部欠落で thread 永久消失 → §4 全 custom_id 揃いを completion 条件に。
- **Important I7**: raw mismatch で completed 化され再取得されない → §6 で assemble 失敗＝保持/retry。
- **Question**: early commit のローカル push → §2 CI 専用 gate。sidecar 走査位置 → §3 pre-collect で
  `DATES_LIST` 非依存に全 sidecar を走査。
