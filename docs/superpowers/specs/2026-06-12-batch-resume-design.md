# バッチID永続化による run 横断 resume 設計

- 日付: 2026-06-12
- 対象: `scripts/summarize.py --batch` のサマリフェーズ
- 関連 Issue: #41 (`pipeline-failure`)
- 直前コミット: `58c0ed9 fix(pipeline): cancel timed-out summary batches and repair failure alerting`
- レビュー: codex external review 反映済み（後述「レビュー反映」）

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

混雑が続く限りデータが一切前進しない。根治は **batch_id の永続化による run 横断 resume**。

## 不変条件（プロジェクト制約）

- サマリ層は **stateless / deterministic / prompt-only**（CLAUDE.md「Summary Layer Invariants」）。
  本設計は run 横断状態を sidecar に持つが、それは「未完バッチの回収先」を指すポインタ＋
  「submit 時に確定した入力の写し」であり、サマリ**内容**を run 間で変化させない（同一入力→同一出力を保つ）。
- GitHub Actions runner は使い捨て。run 横断で状態を渡すには **リポジトリへ commit & push** する必要がある。
- daily-batch.yml は `set -e` のシェルループで日付ごとに `summarize.py --date $d --batch` を呼ぶ。
  非ゼロ終了でループ全体が失敗する。GHA `timeout-minutes: 120`。

## 既存の永続化メカニズム（重要な区別）

- `{date}.progress.json`: **run 内専用**。成功時に `summarize.py:713-714` で `os.remove` され、git 管理外。
- **run 横断の状態**: コミット済みの `{date}.json`（threads ファイル）。
  既に表現されている meeting＝完了、として `summarize.py:587-604` の auto-resume が再導出する。

→ batch_id を progress.json に置くと成功時に消える/コミットされないため引き継げない。
**意図的にコミットする専用 sidecar が必要。**

## 設計

### 0. なぜ「再グルーピング」ではなくマニフェスト永続化か（最重要）

`make_batch_custom_id(meeting_id, idx)`（`summarize.py:338-346`）= `s_{sha256(meeting_id)[:12]}_{idx:02d}`。
custom_id は **meeting_id とスレッド位置インデックスのみ**で、スレッド内容・speechOrders・topic を含まない。

したがって resume 時に「再グルーピングして custom_id を再構築」する案は危険:
グルーピング結果が1つでもズレると、custom_id は**位置ベースで常にマッチする**ため
`results.get(custom_id)` は成功し、**古いサマリ結果を新しい thread_info に貼り付ける
silent data corruption** になる（skip されない）。temperature=0 でも完全な決定論は保証されず、
モデル更新では確実に破綻する。これはサマリ層の中立性・透明性保証を破る。

**解決**: submit 時点で確定したグルーピング・マニフェストを sidecar に永続化し、
resume 時は**再グルーピングせず**マニフェストどおりに assemble する。
これは ①決定論依存を完全排除 ②resume 時のグルーパー/outcome LLM 呼び出し（コスト/時間）を省略
③speech 本文は持たず sidecar を小さく保つ、を同時に満たす。

### 1. 永続化フォーマット（sidecar）

日付ごとに `data/threads/{date}.pending-batch.json`:

```json
{
  "batch_id": "msgbatch_01HBjNG4...",
  "model": "claude-...",
  "submitted_at": "2026-06-11T21:50:15Z",
  "retry_count": 0,
  "last_terminal_status": null,
  "meetings": [
    {
      "meeting_id": "参議院_外交防衛委員会_2026-05-14_第8号",
      "outcome": { "result": null, "resolution": null, "status": "ongoing" },
      "threads": [
        { "custom_id": "s_ab12cd34ef56_00", "thread_idx": 0, "topic": "…", "speechOrders": [12, 13, 14] }
      ]
    }
  ]
}
```

- speech 本文は持たない。`speechOrders` は raw_lookup への int 参照。resume 時に
  raw data（コミット済みで不変）から `build_speech_lookup` を再構築して thread_speeches を引く。
- `submit_summary_batch` の **直後**（ポーリング前）に書き込む。
- `data/threads/` は `git add data/threads/` 対象。回収完了時・恒久放棄時に削除する。
- `summarize.py:682` の `.json` クリーンアップ glob が拾わないよう、`.progress.json` と同様に
  `.pending-batch.json` を明示除外する。

### 2. 永続化境界（orphan 防護）

**問題**: submit 直後の sidecar 書き込みは、workflow 末尾の commit & push に到達して初めて
リポジトリに残る。途中で kill / `set -e` の後続失敗 / push 衝突が起きると、Anthropic 側に
バッチは在るが sidecar が残らず **orphan（課金済み・回収不能）** になる。
「poll 予算が短いから commit に到達する」だけでは保証にならない。

**対策**:
1. **early commit**: sidecar を書いた直後、**長い poll に入る前に** sidecar だけを対象に
   `git add <sidecar> && git commit && git push` を実行する（永続化境界を poll の前に移す）。
   - 実装機構は writing-plans で確定。候補: (a) summarize.py が submit 後に targeted git push、
     (b) workflow を「submit パス → sidecar commit → poll/collect パス」に分割。
     最小変更は (a)。push 競合時は fast-forward リトライ。
2. **グローバル poll 予算**: 後述（§3）。poll を per-date 加算ではなく per-run 全体で bound し、
   120分上限到達前に確実に collect/commit ステップへ戻れるようにする。

これにより orphan 窓はほぼゼロ。万一 early commit 前に kill されても、再 submit は次 window で
冪等に行われる（同一 window の meeting が再取得されるため）。

### 3. poll 予算（per-run グローバル）

- **per-run 全体**で1つの予算。デフォルト **30分**（env 可変）。per-date 加算にしない。
- 健全なバッチは 3〜7分で完了 → 従来通り **同一 run で回収**（通常日のデータ遅延ゼロ）。
- 予算を使い切ったら、未完バッチの sidecar を据え置き **exit 0** で抜ける（翌 run で resume）。
- pending が1件でも発生したら、その run ではそれ以上の**新規 submit を行わない**
  （複数 in-flight の積み増しを防ぎ、予算と orphan 窓を抑える）。
- 30分 << GHA 120分。fetch/enrich/validate/commit/push に十分な余裕を残す。

### 4. resume フロー

run 開始時、コミット済み sidecar を走査する。日付ごとに:

```
sidecar.batch_id を retrieve():
  ended            → マニフェストどおり assemble:
                       各 meeting の raw data を再取得 → build_speech_lookup
                       → 各 thread の speechOrders で thread_speeches を引く
                       → fetch_summary_results(batch_id) の結果を custom_id で対応付け
                       → persisted topic / outcome と合わせて thread 組み立て
                       → threads 追記、meeting を completed に、sidecar 削除
  in_progress      → グローバル poll 予算内で待つ。完了すれば上と同様に assemble。
                       予算切れなら sidecar 据え置き、batch_pending=True を立てる
  canceled/expired → retry 処理（§6）
```

- assemble は**再グルーピングを行わない**（§0）。custom_id は sidecar の対応表のみで解決する。
- 不変条件: **1日付につき同時に in-flight バッチは1つ**。

### 5. exit 0 と「何を書くか」

- `run_batch_phase` が `batch_pending=True` を返したら、main は **exit 0**（pending は失敗でなく非同期待ち）。
- pending のみで完了 thread が無い日付は、**空の `{date}.json` を作らない**。sidecar だけを残す。
  auto-resume（`summarize.py:587-604`）は「sidecar あり / threads ファイル無し」を許容するよう拡張する。
- `os.remove(progress_path)` 等のクリーンアップで sidecar を削除しないこと。

### 6. retry / 詰まり検知 / エスカレーション

- **terminal 非成功（expired/canceled）**: 該当バッチを放棄して sidecar の `retry_count` を +1、
  `last_terminal_status` を記録し、同 window の meeting を次の submit 機会で投げ直す。
- **3回連続**（同一日付で expired/canceled が 3 回）→ **hard fail（exit 非ゼロ・CI 赤）**。
  Issue #41 に batch 履歴と retry 回数を明示し、人間の介入を促す。緑のままデータ停滞を防ぐ。
- **詰まり検知（in_progress が長期化）**: sidecar の `submitted_at` が **2日超**なら、
  daily-batch.yml の軽量ステップが `data/threads/*.pending-batch.json` を走査して
  `gh issue comment`（Issue #41 / `pipeline-failure`）で通知。
  既存 `notify-on-failure`（`if: failure()`）は exit 0 化で発火しなくなるため、この別経路が必要。
  `GH_REPO: ${{ github.repository }}` を流用。

### 7. テスト（pytest 基盤を新設）

現状 Python テストは無し（`tests/unit/ministry.test.mjs` のみ）。最小 pytest を `scripts/tests/` に新設し、
fake Anthropic client を用いる:

- sidecar（マニフェスト含む）の read/write/delete ラウンドトリップ
- poll: 予算切れで raise/cancel せず pending を返す
- resume assemble: sidecar + mocked `ended` バッチ → **再グルーピングなしで** 同期パスと一致する thread を生成
- silent-corruption ガード: マニフェストの custom_id 対応のみで解決し、グルーパーを呼ばないこと
- retry: expired を 3 回で hard fail（exit 非ゼロ）
- 詰まり検知: 経過2日超で warning/issue 経路が起動
- `ci.yml` に pytest ステップを追加

## 影響ファイル

| ファイル | 変更内容 |
| --- | --- |
| `scripts/pipeline/summarizer.py` | `poll_summary_batch` を「状態を返す」方式に。cancel は expired 時のみ |
| `scripts/summarize.py` | sidecar(マニフェスト) load/save/delete、early commit、resume assemble（再グルーピングなし）、グローバル poll 予算、batch_pending→exit 0、retry/hard-fail、クリーンアップ除外、auto-resume 拡張 |
| `.github/workflows/daily-batch.yml` | early sidecar commit 機構、詰まり検知ステップ（sidecar 走査→Issue #41）、グローバル予算の env |
| `scripts/tests/`（新規） | pytest + fake client |
| `.github/workflows/ci.yml` | pytest ステップ追加 |
| `README` 監視節 / メモリ `project_daily_batch_failures` | 根治済みに更新 |

## エラーハンドリング / エッジケース

- **GHA が poll 中に 120分で kill**: sidecar は early commit 済みのため永続。次 run が resume。
- **set -e で後続ステップが失敗**: sidecar は early commit 済みのため永続。
- **バッチが run 間で完了済み（夜間）**: resume run が `ended` を即検出 → 待機なしで assemble。
- **expired/canceled の継続**: §6 の retry_count で 3 回到達時 hard fail（無限ループ/二重課金を遮断）。
- **raw data の不変性**: 過去日付の raw data は fetch 後に確定・コミットされ変化しない、という前提に依存。
  この前提が崩れる（source adapter の正規化変更等）と speechOrders 参照がズレる可能性があるため、
  spec の前提として明記する。万一マニフェストの speechOrder が raw_lookup に存在しなければ
  その thread は skip ログ（既存挙動）に落とし、meeting は次 window で再取得される。
- **同日付に新規 meeting が増える**: in-flight 中は新規 submit しない（§3）。解決後の run で拾う。

## 非目標（YAGNI）

- 1日付で複数 in-flight バッチを並走させること。
- 同期（非バッチ）フォールバック。
- speech 本文を含むフル文脈の永続化（マニフェストで代替）。
- `batches.list()` による orphan リコンシリエーション（early commit + グローバル予算で窓をほぼゼロにする方針を採用。
  将来必要になれば別途追加）。

## レビュー反映（codex external review, 2026-06-12）

- **Critical**: custom_id 位置依存による silent corruption → §0 で再グルーピング案を撤回しマニフェスト永続化に変更。
- **Critical**: sidecar 永続化境界 → §2 で early commit を導入。
- **Important**: per-date poll 加算 → §3 でグローバル予算化＋pending 後の新規 submit 停止。
- **Important**: expired 再 submit の二重課金/無限ループ → §6 で retry_count 3 回 hard fail。
- **Important**: 「commit 到達保証」の言い切り → §2 で永続化境界を明示する記述に修正。
- **Question**: raw payload 再取得の安定性 → エッジケースで前提を明記。
- **Question**: batch_pending 時の出力 → §5 で「空 `{date}.json` を作らず sidecar のみ」と明示。
