# バッチID永続化による run 横断 resume 設計

- 日付: 2026-06-12
- 対象: `scripts/summarize.py --batch` のサマリフェーズ
- 関連 Issue: #41 (`pipeline-failure`)
- 直前コミット: `58c0ed9 fix(pipeline): cancel timed-out summary batches and repair failure alerting`

## 背景と問題

daily-batch ワークフローが 2026-06-10・06-11 と2日連続で約1時間34分かけて失敗した。
原因は Anthropic Message Batches API のバッチ（54リクエスト）が 90分（5400s）のポーリング
上限を超えても `in_progress` のまま終わらず、`58c0ed9` で導入したタイムアウト処理が
**設計通りに** バッチを cancel して `TimeoutError` で exit 1 → CI 赤になったこと。

`58c0ed9` は「タイムアウトしたバッチが課金され続ける」問題を解いたが、その代償として
**「90分待てば完了したはずのバッチを毎回捨てる」** 挙動になった。`batch_id` は
`run_batch_phase`（`scripts/summarize.py:443`）のローカル変数で、次回 run に引き継がれない。

この結果、Anthropic 側の混雑が続く間は次のループに陥る:

```
submit → 90分待つ → cancel → 失敗 → 翌日また同じ meeting を submit → …
```

混雑が続く限りデータが一切前進しない。これは構造的な弱点であり、根治は
**batch_id の永続化による run 横断 resume**（メモ `project_daily_batch_failures` が既に指摘）。

## 不変条件（プロジェクト制約）

- サマリ層は **stateless / deterministic / prompt-only**（CLAUDE.md「Summary Layer Invariants」）。
  グルーピングと outcome 抽出は temperature=0 で決定論的。本設計はこの決定論性を活用する。
- GitHub Actions runner は使い捨て。run 横断で状態を渡すには **リポジトリへ commit & push** する必要がある。
- daily-batch.yml は `set -e` のシェルループで日付ごとに `summarize.py --date $d --batch` を呼ぶ。
  非ゼロ終了でループ全体が失敗する。GHA `timeout-minutes: 120`。

## 既存の永続化メカニズム（重要な区別）

- `{date}.progress.json`: **run 内専用**。成功時に `summarize.py:713-714` で `os.remove` され、git 管理外。
  プロセスクラッシュ/リトライ時の run 内 resumability 用。
- **run 横断の状態**: コミット済みの `{date}.json`（threads ファイル）。
  既に表現されている meeting＝完了、として `summarize.py:587-604` の auto-resume が再導出する。

→ batch_id を progress.json に置くと成功時に消える/コミットされないため引き継げない。
**意図的にコミットする専用 sidecar が必要。**

## 設計

### 1. 永続化フォーマット（sidecar）

日付ごとに `data/threads/{date}.pending-batch.json`:

```json
{
  "batch_id": "msgbatch_01HBjNG4...",
  "meeting_ids": ["参議院_外交防衛委員会_2026-05-14_第8号", "..."],
  "model": "claude-...",
  "submitted_at": "2026-06-11T21:50:15Z"
}
```

- `submit_summary_batch` の **直後**（ポーリング前）に書き込む。
- `data/threads/` は既に `git add data/threads/` 対象なので自動でコミット & push される。
- 結果の回収・assemble 完了時、または恒久放棄（expired/canceled）時に削除する。
- `summarize.py:682` の `.json` クリーンアップ glob が拾わないよう、`.progress.json` と同様に
  `.pending-batch.json` を明示除外する。

### 2. `poll_summary_batch` の挙動変更（`scripts/pipeline/summarizer.py`）

- 現状: タイムアウト時に `client.messages.batches.cancel` + `raise TimeoutError`。
- 変更後: タイムアウト時は **raise も cancel もせず**、終了状態を返す
  （例: 戻り値で `ended` 完了か、予算切れの `pending` かを呼び出し側が判別できるようにする）。
- `cancel` は恒久放棄（`expired` 等）時のみ。
- `58c0ed9` の cancel を巻き戻すが正当: resume で結果を実際に使うため「無駄課金」懸念は消える。

### 3. poll 予算（per-run）

- デフォルト **20分**（1200s）。env で可変（既存の `batch_timeout_seconds` 系パラメータを流用/改名）。
- 健全なバッチは 3〜7分で完了 → 従来通り **同一 run で回収**（通常日のデータ遅延ゼロ）。
- 混雑時は 20分で exit 0 → 翌 run で resume。
- 複数日付が未処理でも 3日付 × 20分 = 60分 < GHA 120分上限。commit/push ステップに必ず到達する。

### 4. resume フロー（`run_batch_phase` 冒頭に追加）

```
sidecar を load。存在すれば:
  retrieve(batch_id):
    ended            → meeting_ids を再 prepare（決定論で同一 custom_id を再構築）
                       → fetch_summary_results → assemble → threads 追記
                       → meetings を completed に → sidecar 削除
                       → その後、新規 meeting があれば通常の prepare + submit へ
    in_progress      → poll 予算まで待つ。完了すれば上と同様に assemble。
                       未完なら sidecar 据え置き・batch_pending=True で返す（同日付の新規 submit はしない）
    canceled/expired → warning, sidecar 削除（放棄。次回 window fetch で再取得・再 submit）
```

- 不変条件: **1日付につき同時に in-flight バッチは1つ**。in-flight 中は同日付の新規 submit をしない。

### 5. exit 0 のシグナリング（`scripts/summarize.py`）

- `run_batch_phase` が `batch_pending=True` を返したら、main は出力を書き、sidecar を残し **exit 0**。
- `os.remove(progress_path)` 等のクリーンアップで sidecar を削除しないこと。
- pending 状態は「失敗」ではなく「正常な非同期待ち」として扱う（CI 緑）。

### 6. 詰まり検知アラート

- sidecar の `submitted_at` が **2日以上前**（daily run 2回を越えて未完）なら「本当に詰まった」と判断。
- 実装: daily-batch.yml に軽量ステップを追加し、`data/threads/*.pending-batch.json` の経過日数を
  スキャン → 2日超なら `gh issue comment`（既存 Issue #41 / `pipeline-failure` ラベル）で
  batch_id と経過を通知。
- 既存 `notify-on-failure`（`if: failure()`）は exit 0 化のため発火しなくなるので、この別経路が必要。
  `GH_REPO: ${{ github.repository }}` 設定を流用する。

### 7. テスト（pytest 基盤を新設）

現状 Python テストは無し（`tests/unit/ministry.test.mjs` のみ）。最小 pytest を `scripts/tests/` に新設し、
fake Anthropic client を用いる:

- sidecar の read/write/delete ラウンドトリップ
- poll: タイムアウトで raise/cancel せず pending を返す
- 決定論: 同一 meeting の再 prepare が同一 custom_id を生む
- resume: sidecar + mocked `ended` バッチ → assemble 結果が同期パスと一致
- 詰まり検知: 経過2日超で warning が出る
- `ci.yml` に pytest ステップを追加

## 影響ファイル

| ファイル | 変更内容 |
| --- | --- |
| `scripts/pipeline/summarizer.py` | `poll_summary_batch` を「状態を返す」方式に変更。cancel は expired 時のみ |
| `scripts/summarize.py` | sidecar load/save/delete、resume 分岐、再 prepare パス、batch_pending → exit 0、クリーンアップ除外 |
| `.github/workflows/daily-batch.yml` | 詰まり検知ステップ追加（sidecar 経過スキャン → Issue #41 コメント） |
| `scripts/tests/`（新規） | pytest + fake client |
| `.github/workflows/ci.yml` | pytest ステップ追加 |
| `README` 監視節 / メモリ `project_daily_batch_failures` | 根治済みに更新 |

## エラーハンドリング / エッジケース

- **GHA が poll 中に 120分で kill**: sidecar は submit 直後に書くが、commit/push に到達しないと永続しない。
  poll 予算 20分はこの 120分上限に対し十分な余裕を残すため到達は保証される。
- **バッチが run 間で完了済み（夜間）**: resume run が `ended` を即検出 → 待機なしで assemble。
- **expired（24h 超）**: 毎日 run するので通常は到達しない。到達時は放棄 + 再 submit（一度きりの無駄、稀）。
- **同日付に新規 meeting が増える**: 旧日付（例 2026-05-14）への新規追加は稀。in-flight 解決後に拾う。
- **モデル更新で再 prepare の custom_id が変わる**: 同一モデル & 1〜2日以内なので決定論性は保たれる。
  不一致が起きた custom_id はマッチせず skip ログ（既存挙動）に落ち、次 window で再取得される。

## 非目標（YAGNI）

- 1日付で複数 in-flight バッチを並走させること。
- 同期（非バッチ）フォールバック。
- prepared context のフル永続化（決定論的 再 prepare で代替するため不要）。
