# バッチID永続化による run 横断 resume 設計

- 日付: 2026-06-12
- 対象: `scripts/summarize.py --batch` のサマリフェーズ
- 関連 Issue: #41 (`pipeline-failure`)
- 直前コミット: `58c0ed9 fix(pipeline): cancel timed-out summary batches and repair failure alerting`
- レビュー: codex external review 3回反映済み（後述「レビュー反映」）

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
- **`data/threads/*.json` の全 consumer は配列前提**:
  `validate-data.mjs:54-56`（`threads.push(...data)`）と `generate-sitemap.mjs:93-95`（`for (const t of parsed)`）は
  ディレクトリ内の全 `.json` を**配列として spread/反復**する。object JSON を置くと `TypeError` でビルドが壊れる。
- 既存 `{date}.progress.json` は **run 内専用**（成功時 `summarize.py:713-714` で削除、git 管理外）。
  run 横断完了判定はコミット済み `{date}.json` から再導出（`summarize.py:587-604`）。

→ batch_id を progress.json に置くと引き継げない。**意図的にコミットする専用 sidecar が必要**。
かつ sidecar は **`data/threads/` の外**へ置く（上記 consumer を壊さないため）。

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

**配置**: `data/pending-batches/{date}.json`（`data/threads/` の外）。`data/pending-batches/` を
`git add` 対象に追加する。これにより thread glob consumer（validate/generate）に一切触れない。

```json
{
  "schema_version": 1,
  "date": "2026-05-14",
  "model": "claude-...",
  "retry_count": 0,
  "attempts": [
    { "batch_id": "msgbatch_01HBjNG4...", "submitted_at": "2026-06-11T21:50:15Z",
      "terminal_status": null, "terminal_at": null }
  ],
  "meetings": [
    {
      "meeting_id": "参議院_外交防衛委員会_2026-05-14_第8号",
      "outcome": { "result": null, "resolution": null, "status": "ongoing" },
      "threads": [
        {
          "custom_id": "s_ab12cd34ef56_00",
          "thread_idx": 0,
          "thread_info": { "...submit 時の grouper 出力を full で...": "" },
          "speechOrders": [12, 13, 14],
          "input_hash": "sha256:…"
        }
      ]
    }
  ]
}
```

- **`thread_info` は full 永続化（I）**: `assemble_thread` は `topicTag` / `topicColor` / `summary` /
  `contextDescription` / `legislationName` 等を参照する（`summarize.py:224,246`）。subset では再構築できないため、
  submit 時点の `thread_info` を丸ごと保持する（speech 本文は含まない＝小さい）。
- **`input_hash`（C2 対策）**: submit 時に `build_summary_request` が送る **params 全体**
  （`model`, `max_tokens`, `SUMMARY_SYSTEM`, `SUMMARY_INSTRUCTIONS`, user input = thread_speeches 構成）を
  **canonical JSON**（キーソート・空白正規化・固定エンコード）にして取る SHA256。生成関数と
  canonicalization は1つに固定する（プロンプト変更後の誤 assemble、および key order/空白差による
  誤 mismatch の両方を防ぐ）。
- **`attempts[]`（I5 対策）**: バッチ投入履歴。再 submit 時に新 attempt を push（古い batch_id は履歴に残す）。
  現在の in-flight は `attempts[-1]`。

### 2. 永続化境界（orphan 防護 / C2・C3）

**問題**: submit 直後の sidecar 書き込みは workflow 末尾の commit & push まではリポジトリに残らない。
途中 kill / `set -e` 後続失敗で sidecar が残らないと、Anthropic 側にバッチは在るのに回収不能な
**orphan（課金済み）** になる。さらに `data/raw/` は非コミットなので resume 時の raw は別 fetch であり、
submit 時スナップショットと一致する保証がない。

**対策**:
1. **early commit（CI 専用）**: sidecar を書いた直後、長い poll の前に sidecar だけを
   `git add data/pending-batches/<date>.json && git commit && git push`。ローカル実行では push しない
   （`CI` env / 明示フラグで gate）。永続化境界を poll の前へ移す。
   - 同一 run 内でそのバッチが完了して sidecar 削除まで進む場合、最終 commit は early commit を打ち消す
     形になる。この履歴 churn は**許容**（運用上問題なし）。
2. **input_hash 照合（§4）**: resume 時に再 fetch raw から thread_speeches を再構築し input_hash を再計算、
   sidecar と一致した場合のみ assemble。raw 経路の silent corruption を閉じ「raw 不変」前提への依存を消す。
3. **concurrency ロック（C3）**: `daily-batch.yml` に `concurrency: { group: daily-batch, cancel-in-progress: false }`。
   さらに submit 直前に最新 sidecar を recheck（既に in-flight があれば submit しない）。

### 3. poll 予算と「pending 後 submit 停止」（C1）

別プロセスの日付ループでも per-run グローバル挙動を成立させるため workflow 側で制御する:

1. **pre-collect ステップ（run 冒頭・`DATES_LIST` 非依存）**: `data/pending-batches/*.json` を走査し、
   既存 in-flight バッチを retrieve。ended は assemble、in_progress はグローバル予算内で待つ（§4）。
   - **pending が1件でも残ったら workflow output `HAS_PENDING=true` を立て、後続 submit ループ全体を skip**
     （pre-collect の pending も submit を止める。break-on-pending を「自日付」だけに限定しない／C1 補強）。
   - **pending sidecar の日付を fetch 対象へ強制追加**（manual dispatch の短い lookback や 30日 window 外でも
     raw を確保。raw を確保できない場合は §4 の degraded/hard-fail へ）。
2. **submit ループ**: `HAS_PENDING` が偽のときのみ実行。ある日付の summarize.py が pending（sidecar 新規作成）を
   返した時点で以降の新規 submit を止める。
3. **pending signal は marker / workflow output に一本化**（exit 0 は維持。終了コードで pending を表さない）。
4. **グローバル予算**: 既定 **30分**（env 可変）。pre-collect ＋最初の submit-poll の合計を bound。
   30分 << 120分で後続 enrich/validate/commit/push に余裕を残す。

健全なバッチ（3〜7分）は同一 run で回収され、通常日のデータ遅延はゼロ。

### 4. resume / assemble フロー

pre-collect で各 sidecar の `attempts[-1].batch_id` を retrieve():

```
ended:
  各 meeting の raw を再 fetch → build_speech_lookup
    - raw が無い（window 外）→ degraded: sidecar 保持＋詰まり通知。規定 run 数を超えたら hard fail（§6）
  各 thread: speechOrders で thread_speeches を引き、input_hash を再計算
    - input_hash 不一致 / speechOrder 欠落 → assemble 失敗（§6 へ、sidecar 保持）
  fetch_summary_results(batch_id) を custom_id で対応付け
    - manifest の全 custom_id が結果に揃っているか確認（I6）。欠落 → completed にせず sidecar 保持（§6）
  全件揃い・全 hash 一致 → persisted full thread_info / outcome と合わせて thread 組み立て
    → threads 追記、meeting を completed、（全 meeting 完了で）sidecar 削除
in_progress: グローバル予算内で待つ。完了すれば上と同様。予算切れは sidecar 据え置き・HAS_PENDING。
canceled/expired: §6（retry）。
```

- assemble は **再グルーピングを行わない**。custom_id は manifest の対応表のみで解決。
- 不変条件: **1日付につき同時 in-flight バッチは1つ**（`attempts[-1]` のみ）。

### 5. exit 0 と「何を書くか」

- pending（予算切れで未完）でも **exit 0**（失敗でなく非同期待ち）。pending は workflow output/marker で伝える（§3-3）。
- 完了 thread が無い日付は **空の `{date}.json` を作らない**。sidecar だけを残す。
  auto-resume（`summarize.py:587-604`）は「sidecar あり / threads ファイル無し」を許容するよう拡張。

### 6. retry / 詰まり検知 / エスカレーション

- **assemble 失敗（input_hash 不一致 / custom_id 欠落 / 一部 request failed / raw 欠落）**: meeting を completed に
  せず sidecar を保持。
- **terminal 非成功（expired/canceled/assemble 失敗）**: `attempts[-1].terminal_status` が **`null → 非成功` に
  遷移した時だけ** `retry_count` を +1 し、その状態を commit してから次 attempt を作る
  （複数 run で同一 terminal を再観測しても二重カウントしない／状態遷移を明示）。
- **3回連続**（`retry_count >= 3`）→ **hard fail（exit 非ゼロ・CI 赤）**。`attempts[]` 履歴と `retry_count` を
  Issue #41 に明示し人間の介入を促す（緑のままのデータ停滞を防ぐ）。
- **詰まり検知（in_progress 長期化）**: `attempts[-1].submitted_at` が **2日超**なら、
  `data/pending-batches/*.json` を走査する軽量ステップが `gh issue comment`（Issue #41）で通知。
  - exit 0 化で `notify-on-failure`（`if: failure()`）は発火しないため別経路。
  - **この通知ステップ/ジョブには `issues: write` と `GH_TOKEN`・`GH_REPO` を明示**（I4。
    fetch-and-summarize job は `contents: write` のみのため専用 job 化するか job 権限を拡張）。

### 7. テスト（pytest 基盤を新設）

現状 Python テストは無し（`tests/unit/ministry.test.mjs` のみ）。最小 pytest を `scripts/tests/` に新設し、
fake Anthropic client を用いる:

- sidecar（attempts/manifest/full thread_info）の read/write/delete ラウンドトリップ
- poll: 予算切れで raise/cancel せず pending を返す
- resume assemble: sidecar + mocked `ended` → **再グルーピングなしで**同期パスと一致
- input_hash ガード（C2）: raw 改変で hash 不一致 → assemble せず sidecar 保持
- canonicalization: 同一入力で key order/空白差があっても hash 安定、prompt 変更で hash 変化
- 完全性（I6）: 一部 custom_id 欠落 → completed にせず sidecar 保持
- retry 状態遷移: 同一 terminal の再観測で二重カウントしない／3回で hard fail
- 詰まり検知: 経過2日超で通知経路が起動
- `ci.yml` に pytest ステップ追加

## 影響ファイル

| ファイル | 変更内容 |
| --- | --- |
| `scripts/pipeline/summarizer.py` | `poll_summary_batch` を「状態を返す」方式に。cancel 不要に |
| `scripts/summarize.py` | sidecar(attempts/manifest+full thread_info+input_hash) load/save/delete、early commit(CI gate)、resume assemble（再グルーピングなし・hash 照合・完全性チェック・raw 欠落 degraded）、HAS_PENDING signal、retry 状態遷移/hard-fail、auto-resume 拡張 |
| `.github/workflows/daily-batch.yml` | `concurrency:` 追加、pre-collect ステップ（HAS_PENDING output・pending 日付の fetch 強制追加）、submit ループの skip/break-on-pending、early commit 連携、`data/pending-batches/` の git add、詰まり検知ステップ（`issues: write` 付き）、グローバル予算 env |
| `scripts/tests/`（新規） | pytest + fake client |
| `.github/workflows/ci.yml` | pytest ステップ追加 |
| `README` 監視節 / メモリ `project_daily_batch_failures` | 根治済みに更新 |

備考: sidecar を `data/pending-batches/` に置くため、`validate-data.mjs` / `generate-sitemap.mjs` /
`summarize.py:682` の thread glob には**変更不要**（衝突を構造的に回避）。

## エラーハンドリング / エッジケース

- **GHA 120分 kill / set -e 後続失敗**: sidecar は early commit 済みのため永続。次 run が resume。
- **raw が submit 時から変化**: input_hash 不一致で assemble せず保持（silent corruption を遮断）。
- **pending 日付が lookback window 外**: fetch 強制追加で raw 確保。確保不能なら degraded（保持＋通知）→規定超で hard fail。
- **ended batch の一部欠落**: 全 custom_id 揃いを completion 条件にし欠落は保持（I6）。
- **overlapping run**: `concurrency` ロック＋submit 直前 recheck で重複 submit を防止（C3）。
- **expired/canceled の継続**: retry_count 3 回で hard fail（無限ループ/二重課金を遮断）。
- **夜間に完了**: resume run が `ended` を即検出 → 待機なしで assemble。

## 非目標（YAGNI）

- 1日付で複数 in-flight バッチを並走させること。
- 同期（非バッチ）フォールバック。
- speech 本文を含むフル文脈の永続化（manifest（thread_info）＋input_hash で代替）。
- `batches.list()` による orphan リコンシリエーション（early commit + concurrency + グローバル予算で窓を
  ほぼゼロにする方針。将来必要になれば追加）。

## レビュー反映

### 1回目（codex）
- Critical: custom_id 位置依存 silent corruption → §0 マニフェスト永続化。
- Critical: sidecar 永続化境界 → §2 early commit。
- Important: per-date poll 加算 → グローバル予算化。 / expired 再 submit 無限ループ → §6 retry 3 回 hard fail。

### 2回目（codex）
- C1 別プロセス予算不成立 → §3 pre-collect＋break-on-pending＋グローバル予算。
- C2 sidecar/raw 非原子 → §1 `input_hash`＋§4 照合。
- C3 concurrency 無し → §2 `concurrency:`＋recheck。
- I4 通知権限 / I5 attempts[] / I6 完全性 / I7 mismatch 保持 → 反映。

### 3回目（codex）
- **Critical**: sidecar が `data/threads/*.json` glob と衝突しビルド破壊 → §1 で `data/pending-batches/` へ移設。
- **Critical**: pre-collect pending が submit を止めない → §3 で `HAS_PENDING` output によるループ skip。
- **Important**: input_hash 正規化が曖昧 → §1 で full request params の canonical JSON に固定。
- **Important**: manifest が thread_info subset で assemble 不能 → §1 で full thread_info を永続化。
- **Important**: pending 日付が window 外で raw 欠落 → §3/§4 で fetch 強制追加＋degraded/hard-fail。
- **Important**: retry_count 二重カウント → §6 で `null→非成功` 遷移時のみ加算する状態機械に明示。
- **Question**: early commit の revert churn 許容 / pending signal は marker 一本化 → §2・§3 に明記。
