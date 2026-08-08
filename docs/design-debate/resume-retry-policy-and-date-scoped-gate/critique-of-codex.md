# Critique of design-codex.md（Claude 側の敵対レビュー）

`design-codex.md` への相互批評。実コード（`3b9e7b2`）で裏取りした指摘を優先し、裏取りできなかったものは明記した。
レビュアーは `design-claude.md` を読んでいない独立サブエージェント。

---

### [critical] HOLD は 29 日で自壊し、`results_expired` 経由で「決定論的に doomed な再送」に戻る

- **主張**: `hash_mismatch` を HOLD にしても、batch results の保持期限（約29日）が切れた朝に観測される reason が `results_expired` に変わり、本案の policy 表ではそれが **RETRY** なので、課金を伴う再送が復活する。G1（再送を1回も行わない）が長期地平で破れ、最終的に §2.6 の retry threshold 到達 → Collect exit 1 → publish 全停止に着地する。
- **根拠**: `scripts/summarize.py:1272-1286` で `fetch_summary_results()` は **assembly より前**に呼ばれ、bare `AnthropicError`（= results 期限切れ）を捕捉して `_retry_or_hardfail(..., "results_expired", ...)` に落とす。`assemble_from_manifest()` の hash 検査（`summarize.py:738`）はその後（`summarize.py:1298`）。本案 §1.4 / §3.5 は「毎朝現在の raw/schema から同じ状態を再判定できる」ことを sticky state 不要の根拠にしているが、**再判定される reason は時間依存で変わる**。
- **失敗シナリオ**: 2026-05-14 の sidecar が hash_mismatch で HOLD される。day1〜28 は毎朝 HOLD（設計どおり）。day29、batch results が期限切れ → `fetch_summary_results` が bare `AnthropicError` → reason `results_expired` → RETRY → `_rebuild_requests_from_manifest()` は raw があるので成功 → **新バッチを丸ごと再送（課金）**・`retry_count` 1 → 翌朝 assemble → 保存済み hash は不変なので再び `hash_mismatch` → HOLD → 29日後にまた再送… retry 3 到達（約90日後）で `should_hard_fail` → `main` が exit 1（`summarize.py:2039`）→ Collect ステップが `set -e` で死に、その朝は全日付が publish されない。#65 の形が「3日で死ぬ」から「90日かけて死ぬ」に伸びただけで、終着点が同じどころか悪化している（人が忘れた頃に来る）。
- **検証状況**: コードで確認済み（呼び出し順序・reason 文字列・exit 経路すべて）。
- **修正方向**: HOLD は sidecar に記録するか（§3.5 の却下を撤回）、`resume_failure_action` に「その sidecar が既に HOLD 相当か」を判定させる（hash 検査を fetch より前に行う／`_rebuild_requests_from_manifest` の hash 突合を先に走らせる）。少なくとも「reason は時間で変わる」ことを設計に書かないと、この穴はテストにも現れない（T1 は1朝しか見ない）。

---

### [high] HOLD 時に `_record_resume_verdict` を呼ぶのか呼ばないのかが未定義。呼べば suspect 閾値を汚染する

- **主張**: 現在の hash_mismatch 経路は `_record_resume_verdict` → `_retry_or_hardfail` の2段。本案 §2.3 の擬似コードは後者だけを差し替えており、前者の去就が書かれていない。残すと同じ日付が `held_dates` と `suspect_dates`（または `systemic_dates`）の両方に載る。
- **根拠**: `summarize.py:1304-1313`。`_record_resume_verdict` の docstring（`summarize.py:1086-1090`）自身が「1日付を両リストに入れると workflow の `SUSPECT_N -ge 2` を単独で越えさせてしまう」と警告している。§2.4 は「混在させない」と書くが、それは新関数の責務の話であって既存呼び出しの削除を明言していない。
- **失敗シナリオ**: 既存 thread がある日付で 1-of-1 の hash_mismatch → `publication_blocked_verdict` が `EXIT_SUSPECT_FAILURE` を返し `suspect_dates` に載る。翌朝も同じ。別日付でもう1件 held が発生すると `SUSPECT_N=2` で閾値が誤って発火し、最終ステップが `::error::Nothing this run produced reached the site on: ...` を出す。この日付は「今日届かなかった」のではなく「29日前のバッチが人の判断待ち」であり、§4.6 が自ら禁じている誤診表現そのもの。
- **検証状況**: コードで確認済み（本案の記述が曖昧である点も含む）。T1 のアサーション一覧（§2.11）に `systemic_dates == []` / `suspect_dates == []` が無いため、このテストは両方に載っても通る。

---

### [high] §2.8 の最終ステップ書き換えが、既存テストの pin している文字列を壊す

- **主張**: §2.8 の shell スニペットは `FAIL_SYSTEMIC` / `FAIL_SUSPECT` を導入して `FAIL_DATES` を廃している。既存テストは `FAIL_DATES="$FAIL_DATES$SUSPECT"` というリテラルを要求するので落ちる。T5 の「既存テストを維持する」と自案 §2.8 が矛盾。
- **根拠**: `scripts/tests/test_systemic_failure.py:885-889`（`assert 'FAIL_DATES="$FAIL_DATES$SUSPECT"' in run` — コメントに「これが無いと suspect が集められて報告されて永久に job を落とさない状態が緑のまま通る」と明記）。`.github/workflows/daily-batch.yml:411-414`。
- **失敗シナリオ**: 実装者が §2.8 のコードをそのまま書く → `python -m pytest scripts/tests` が赤 → 実装者はテスト側を「リネームに追随」させて緩める（このテストは意図的に脆く作られている保険なので、緩め方を誤ると suspect のエスカレーション経路が無言で死ぬ）。
- **検証状況**: コードで確認済み。加えて §2.8 のスニペットは `FAIL_SYSTEMIC` / `FAIL_SUSPECT` を定義したまま一度も使っておらず、擬似コードとしても未完成。

---

### [high] 既存の運用テキスト3箇所が本案で「嘘」になるのに、更新対象に挙がっていない

- **主張**: 本案は挙動を変えるが、その挙動を説明している人間向けテキストを1つも更新対象にしていない。朝これを読んだ運用者は、存在しない現象を探しに行く。
- **根拠**:
  1. `.github/workflows/daily-batch.yml:434-436` — 「hash_mismatch will NOT fix itself — it recurs every morning **until the retry threshold hard-fails and the date is lost**. Fix: remove the sidecar」。本案では retry threshold には到達しない（§2.2）。
  2. `daily-batch.yml:429-431` — 「hash_mismatch/speech_gap/raw_missing point at raw」。本案ではこの3つは HOLD になり、扱いが `missing_result` と別レジームになるが、その区別がこの説明に無い。
  3. `daily-batch.yml:465`（notify-on-failure の issue 本文）— 「check the run summary for **"Nothing reached the site on"**」。held / abandoned だけで赤くなる run では、その文字列は run summary に**存在しない**。運用者は issue の指示どおり探して見つからず、原因不明の赤として放置する。CLAUDE.md も同様（「the resubmit it triggers is still deterministically doomed and still burns the retry budget down to permanent loss over three mornings. Do not wait it out; remove the sidecar. Tracked as #65」／stale-schema が「hard-fails --collect-pending」）。
- **失敗シナリオ**: 初回の held 発生日。job は赤、issue に自動コメントが付く。本文は「run summary の "Nothing reached the site on" を見ろ」と言う。summary には `Pending batches held for manual resolution` しかない。運用者は #65 の既知バグかと思って CLAUDE.md を引き、「3朝で恒久ロスト、今すぐ sidecar を消せ」と読む。実際には消さなくても publish は続いており、消すと回収可能な batch result への manifest が失われる（本案 §4.7 が自ら警告している事故）。**設計が防ごうとした誤操作を、更新し忘れたドキュメントが誘発する。**
- **検証状況**: コードで確認済み（YAML 実文・CLAUDE.md 実文）。

---

### [high] retry threshold の exit 1 を維持する判断が、§2.5 の自分の論拠と矛盾する

- **主張**: §2.5 は「日付単位ゲートになったので publish を止める必要はない」を根拠に stale-schema の hard_fail を撤回した。同じ論拠は retry threshold にもそのまま当たるのに、§2.6 は exit 1 を維持している。しかもゲート撤廃によって、その exit 1 のコストは**今回初めて実害になる**。
- **根拠**: 従来は sidecar が1件でもあれば Summarize 全体がスキップ（`daily-batch.yml:135`）だったので、Collect の exit 1 が奪う publish は「どうせ動かない朝」の publish だった。本案 §2.9 でゲートを外すと、他日付は本来処理できたはずになる。exit 1 は `main` の `sys.exit(1 if result["hard_fail"] else 0)`（`summarize.py:2039`）→ Collect ステップの `set -e` → Summarize/enrich/validate/commit/push/IndexNow が全部スキップ。
- **失敗シナリオ**: 2026-05-14 の batch が Anthropic 側で3回連続 canceled（正当な RETRY 分類）。4朝目、`should_hard_fail`（`summarize.py:1209`）が真 → Collect exit 1。この朝 NDL が新しく 3 日分公開していても、1件も publish されない。#52 の増幅そのもの。
- **検証状況**: コードで確認済み。本案はこのトレードオフを §3.8 で「Collect exit 1 は即時停止」と正しく説明しながら、retry 経路にだけそれを残す理由を述べていない。

---

### [high] 「毎朝赤」は 1 run 2 通知 × 無限。既存の notify-stuck-batch を見落としている

- **主張**: §4.1 は「`notify-on-failure` が既存 issue を再利用しない場合、連日 issue が作られる可能性がある」と書くが、事実は違う。既存 issue は再利用される。一方で見落とされている `notify-stuck-batch` ジョブが、held sidecar に対して**毎日**別コメントを打つ。HOLD が無期限になることで、この既存ノイズ源が初めて無限化する。
- **根拠**: `daily-batch.yml:470-477`（`gh issue list --label pipeline-failure` → 既存があれば `gh issue comment`。重複作成はしない）。`daily-batch.yml:479-510`（`notify-stuck-batch`、`if: always()`）→ `scripts/check_stuck_batches.py:19` が `bs.is_stuck`（`STUCK_AGE_DAYS = 2.0`）に該当する sidecar を毎回出力。held sidecar は 3 日目以降ずっと該当する。
- **失敗シナリオ**: held 発生から 3 日目以降、同じ `pipeline-failure` issue に毎日 2 本（run 赤コメント + stuck コメント）が積まれる。しかも `hash_mismatch` の held sidecar は `retry_count` が増えないので stuck コメントは毎日「retries 0」と表示し、「まだ何も試していない一時的な詰まり」に見える。abandon 経路（唯一の恒久ロスト）は sidecar を消すので stuck 通知からも消える。**通知の強度と事象の重大度が、またしても逆転する。**
- **検証状況**: コードで確認済み。#66 が直そうとした「重大度の逆転」が、別の経路で再生産される。

---

### [medium] reason 語彙が2系統に割れる。`raw_date_missing` が policy 表に無い

- **主張**: §2.2 の policy 表は「認識可能な集合全体を固定する」網羅テストを謳うが、実在する reason を1つ落としている。網羅を主張する allowlist が実際には網羅していない。
- **根拠**: `summarize.py:1264` は date scope の診断として `_diagnostic("raw_date_missing")` を出す（`assemble_from_manifest` の `raw_missing`（`summarize.py:721`）とは別物）。本案 §2.2 の `_HOLD_REASONS` には `raw_missing` はあるが `raw_date_missing` は無い。
- **失敗シナリオ**: 現状この reason は `_retry_or_hardfail` に届かないので即座には壊れない。しかし本案は「未知 reason は `ValueError`」を採るので、将来 raw-missing 分岐を（意味的には HOLD なので自然に）policy 経由に寄せた瞬間、Collect が例外死 → exit 1 → publish 全停止。CLAUDE.md が繰り返し戒めている「同じ概念の2つの綴りはいずれ食い違う」（`usable_result` / `has_question_for_the_api` の docstring）に真正面から当たる。
- **検証状況**: コードで確認済み。最低限、policy テストは `_diagnostic` を出す全呼び出し箇所を AST/grep で走査して集合を導出すべき（`test_determinism.py` に AST sweep の前例がある）。

---

### [medium] 「未知 reason → 例外 → exit 1」は、このリポジトリでは fail-closed の向きが逆

- **主張**: §1.2 は未知 reason を例外にすることを fail-closed と定義するが、Collect の例外は `set -e` で publish 全体を止める。守るべき資産（課金・データ）に対する fail-closed は HOLD であって、クラッシュではない。
- **根拠**: `summarize.py:2039` と `daily-batch.yml:115,121`。CLAUDE.md「An outage must be loud without blocking the publish」。本案自身が §3.8 で abandon について同じ理由で exit 1 を却下している。
- **失敗シナリオ**: 将来 `assemble_from_manifest` に新 reason（例: `outcome_missing`）を1行足した開発者が policy 表への登録を忘れる。その reason が発火した朝、Collect が `ValueError` で落ち、その日は 1 スレッドも publish されない。分類漏れという軽微なミスの罰が、サイト更新停止。
- **検証状況**: コードで確認済み。妥当な代替は「未知 → HOLD + `::error::`（再送も課金もしない）+ 別テストで語彙網羅を静的に強制」。実行時クラッシュではなく CI テストで落とす。

---

### [medium] 無期限 HOLD の間、`_repair_unusable_results` の同期課金コールが毎朝走り続ける

- **主張**: HOLD は sidecar を一切書き換えないので、翌朝も同じ地点まで再実行される。その途中にある `_repair_unusable_results` は同期 `messages.create` を最大 `REPAIR_LIMIT` 件、`SUMMARY_RETRY_MAX_TOKENS` で発行する。§2.2 の表は「HOLD = 再送しない = 課金しない」と読めるが、これは不正確。
- **根拠**: `summarize.py:1288` が assembly の**前**に無条件で呼ばれる。`_repair_unusable_results` は hash 不一致の custom_id だけをスキップし（`summarize.py:945-949`）、hash が一致する未使用 result は毎回再発行する。結果は `results` の in-memory 変更のみで、どこにも永続化されない（docstring: "Mutates `results` in place"）。
- **失敗シナリオ**: 5 thread の manifest のうち thread 3 だけが `hash_mismatch`、thread 1 が truncated で unusable。毎朝: poll → results 取得 → thread 1 を高 max_tokens で同期再発行（課金）→ 成功 → assembly が thread 3 で hash_mismatch → HOLD（何も保存されない）。翌朝また同じ課金。従来は3朝で終わっていたものが無期限化する。
- **検証状況**: コードで確認済み（金額規模は未検証）。

---

### [medium] §2.5 は CLAUDE.md の `SCHEMA_VERSION` 着地ルールの意味を変えるが、その扱いを書いていない

- **主張**: brief §4.1 は「bump は `git ls-files data/pending-batches/` が空のときにしか land できない」を制約として明示し、「変える設計を出す場合はこの制約を明示的に扱うこと」と要求している。本案 §1.1 は「`SCHEMA_VERSION` は bump しない」とだけ答え、**§2.5 が同じルールの根拠側を書き換えていること**に触れていない。
- **根拠**: CLAUDE.md「`is_current_schema` compares for equality, so a version change is a refusal in **both** directions: … hard-fails `--collect-pending` and therefore skips the whole Summarize step every day until someone removes the file by hand — and the batch's results expire in ~29 days. **Land a `SCHEMA_VERSION` change only when … empty.**」。§2.5 でこれは「hard-fail しない・publish も止まらない・その日付だけ HOLD」に変わる。
- **失敗シナリオ**: 将来の開発者が「in-flight sidecar があっても hard-fail しないなら bump してよい」と判断して bump を land。実際には held のまま29日で results が失効し（[critical] 参照）、その sidecar の thread は恒久ロスト。ルールを緩める意図がなくても、根拠が消えたルールは守られなくなる。本案はここで「HOLD になっても着地条件は据え置き」と明言し、CLAUDE.md を同一コミットで書き換える必要がある。
- **検証状況**: CLAUDE.md 実文で確認済み。

---

### [low] Collect の共有 budget（1800s）と、日付単位ゲートによる sidecar 蓄積の相互作用

- **主張**: §4.2 は「pending が積み上がりうる」と認めつつ「無制限増加は想定しない」で止めている。`collect_pending_batches` の予算は**全 sidecar で共有**の単一 deadline なので、蓄積は自己増殖する。
- **根拠**: `summarize.py:1181` `deadline = time.time() + budget_seconds`、`summarize.py:1217-1219` `remaining = max(0, int(deadline - time.time()))` を `poll_summary_batch` に渡す。`paths` は `sorted()`（= 日付昇順）なので、古い sidecar が先に予算を食う。
- **失敗シナリオ**: Anthropic 側が数日遅延。日付昇順の先頭数件のポーリングで 1800s を使い切り、後続 sidecar は `remaining=0` で「まだ in-flight」と判定されて次回送り。毎朝1件ずつ新規が増え、回収は先頭に偏る。従来はグローバルゲートが新規投函を止めていたのでこの形にならなかった。
- **検証状況**: コード経路は確認済み。実際に発生する頻度は未検証。

---

### [low] abandon の唯一の恒久記録が annotation のみで、後続ステップの失敗で最終ステップは skip される

- **主張**: abandon は sidecar を削除する不可逆操作だが、本案が残す記録は annotation と step output だけ。最終ステップは `if:` を持たない＝暗黙 `success()` なので、Summarize や commit が落ちれば実行されない。
- **根拠**: `test_systemic_failure.py:833-838` のコメントが「`if: always()` は意図的に不採用」と明記。`daily-batch.yml:386` に `if:` 無し。
- **失敗シナリオ**: abandon が起きた朝に別日付で Summarize がクラッシュ（rc≠0/3/4、`daily-batch.yml:169-180`）→ 以降のステップ全 skip → 最終ステップの「Permanently abandoned」error が出ない。job は赤いので issue コメントは付くが、本文は NDL 403 やクレジット切れを疑わせる汎用文（`daily-batch.yml:465`）で、恒久ロストには一言も触れない。90日後にログを掘らないと分からない。
- **検証状況**: コードで確認済み。#66 の趣旨（恒久ロストを見えるようにする）に照らすと、リポジトリ側に痕跡（tombstone ファイル or `gh issue create`）を残す案を少なくとも検討・却下理由の記載が要る。

---

### 総評

**根本的に良い点**
- policy を `batch_state.py` の純粋関数に置き、RETRY / HOLD の**両方を allowlist**にした判断は正しい。禁止リスト側だけを作ると新 reason が黙って課金対象になる、という CLAUDE.md の教訓を、この repo で唯一テストで固定できる場所に落としている。
- `held_dates` / `abandoned_dates` を `systemic_dates` に混ぜなかったこと。「今日届かなかった」と「31日前の分を今日諦めた」を同じ文言で報告しない、という §4.6 の言語規律は、この repo が繰り返し払ってきた誤診コストに正面から効く。
- §2.6 の「reason を policy で検証してから `record_terminal()`」という**実行順序の固定**は、retry 予算を消費した後で判定するという現行の穴を構造的に塞いでいる。

**根本的に危うい点**
- 「毎朝安全に再判定できる」という §3.5 の前提が**偽**。観測される reason は経過日数で変わり（results 失効）、HOLD は約29日で RETRY に戻って課金再送し、約90日で publish を止める exit 1 に着地する。設計の主目的が長期地平で反転する。
- 挙動を変えた後の**人間向けテキストを1行も更新対象に挙げていない**。workflow の help、notify issue 本文、CLAUDE.md が同時に嘘になり、運用者を「sidecar を今すぐ消せ」（＝自案 §4.7 が危険と警告する操作）へ誘導する。
- 停止判断が非対称に残っている。stale-schema の exit 1 は「ゲートが日付単位になったから不要」と撤回しながら、同じ論拠が当たる retry 閾値の exit 1 と、新設の「未知 reason → 例外」はそのまま publish を止める側に置いている。ゲート撤廃で初めて実害になる箇所を、撤廃と同じ設計の中で見落としている。
