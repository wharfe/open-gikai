## Critical

1. **`BLOCKED = 赤` は実装案どおりでは成立しない。G2 を破る。**

   設計は `hash_mismatch` を常に赤と定義していますが、実際に呼ぶとしている `_record_resume_verdict()` は、対象日付に既存 thread があり summary 対象 meeting が1件なら `EXIT_SUSPECT_FAILURE` に落とし、warning にします。[design-claude.md:22](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:22>) [summarize.py:286](/home/feathach/dev/open-gikai/scripts/summarize.py:286) [summarize.py:1109](/home/feathach/dev/open-gikai/scripts/summarize.py:1109)

   具体例: 既に3 thread 公開済みの日付へ遅着 meeting が1件あり、その sidecar が `hash_mismatch` になった場合、単独なら黄色のまま最終ステップを通過します。設計の T1 も submit/retry/blocked しか検査せず、赤を検証していません。[design-claude.md:369](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:369>)

   `BLOCKED` は「障害の広がりを推定する suspect/systemic」と別軸です。`blocked_dates` のような無条件失敗 output を設けるか、`_record_resume_verdict(..., force_error=True)` 相当を明示し、hash mismatch・stale schema・retry exhausted が必ず最終ステップを赤くするテストを追加すべきです。

2. **hash 検証の前倒し位置が不十分で、`results_expired` 経由の doomed 再送が残る。G1 を破る。**

   設計が明記しているのは「repair より前」だけです。[design-claude.md:220](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:220>) 現行経路では raw 読み込み後、結果取得が失敗すると assembly/hash 検証へ到達せず、直ちに `results_expired` として再送します。[summarize.py:1269](/home/feathach/dev/open-gikai/scripts/summarize.py:1269)

   具体例: raw が変更済みで、保存済み batch の results も期限切れの場合、`fetch_summary_results()` が先なら `hash_mismatch` を観測できず、`results_expired → RESUBMIT` に分類されます。新 batch は現在の raw で組まれる一方、sidecar hash は古いままなので、翌朝必ず mismatch します。

   `verify_manifest_against_raw()` は、ended batch について raw をロードした直後、**results の取得より前**に必ず実行すると設計で固定すべきです。T1にも「results 取得が期限切れを投げる条件でも submit されない」ケースが必要です。

3. **提示している復旧コマンドが、回収可能なデータを本当に失わせうる。**

   annotation は `git rm sidecar` 後、「次の run が current raw で再要約する」と断言しています。[design-claude.md:344](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:344>) しかし `data/raw` は runner 上の一時データで、次回の `DATES_LIST` はその朝に取得できた lookback 内の日付だけです。[daily-batch.yml:105](</home/feathach/dev/open-gikai/.github/workflows/daily-batch.yml:105>)

   lookback 境界付近で sidecar を削除すると、次の朝には対象日が窓外となり、再要約されません。元 batch の検証可能な結果を捨て、代替生成も起きない、まさに恒久ロストです。これは #44 第2項が非目標である以上、自動復旧を約束できません。

   annotation は単純な `git rm` を推奨してはいけません。「対象日の raw が次回も取得可能か確認し、必要なら明示的に raw を再取得・保存してから sidecar を削除する」という安全な手順にする必要があります。自動再取得を実装しないなら、その制約を明記してください。

## Important

1. **`mark_blocked()` の毎朝 commit は、2日目から偽の “dead net” error を出す。**

   `blocked.since` は初回値を保持するため、2日目以降の sidecar 内容は通常変わりません。それでも設計は毎朝 `_git_commit_sidecar()` を呼びます。[design-claude.md:170](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:170>) 現行 helper は staged diff の有無を確認せず `git commit` し、変更なしの exit 1 を「batch が orphan する」と error annotation に変換します。[summarize.py:130](/home/feathach/dev/open-gikai/scripts/summarize.py:130) [summarize.py:156](/home/feathach/dev/open-gikai/scripts/summarize.py:156)

   初回に変更された場合だけ保存・early commit するか、helper 側で staged diff が空なら正常 no-op にすべきです。

2. **retry threshold の新状態遷移が未設計で、擬似コードと §3.8 が矛盾している。**

   `_apply_failure_policy()` の擬似コードは threshold 到達時に `"hard_fail"` を返しますが、後段では hard fail を廃止して「同様に赤くする」としか書いていません。[design-claude.md:186](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:186>) [design-claude.md:324](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:324>)

   さらに、以前 `hash_mismatch` で `blocked` が付いた sidecar が後日検証を通り、resubmittable failure で threshold に達すると、`clear_blocked()` より先に return するため、forensics は古い hash mismatch のままになります。

   `"retry_exhausted"` を明示的な BLOCKED reason として定義し、既存 blocked metadata を上書きするのか、試行履歴と現在の block reason を分けるのか決めるべきです。

3. **abandon のメッセージが実際に証明できる範囲を越えている。**

   「these dates will never be published」は、その日付全体が未公開だと誤認させます。[design-claude.md:276](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:276>) sidecar は既存公開日への遅着 meeting でも作られるので、既存 thread は既に公開済みかもしれません。現行コードも実際には「この sidecar の uncollected threads」の喪失です。[summarize.py:1247](/home/feathach/dev/open-gikai/scripts/summarize.py:1247)

   「pending batch に含まれた未回収 thread が恒久ロストした」と限定し、date、batch ID、meeting/custom ID、既存 thread 数を出すべきです。

4. **T6 は「policy reason 全体の網羅」を証明していない。**

   T6 が走査するのは `_diagnostic("...")` だけです。[design-claude.md:375](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:375>) しかし policy へ到達する語彙には以下もあります。

   - `TERMINAL_FAILURES` 由来の `canceled` / `expired`
   - 直接渡す `results_expired`
   - 新設する `stale_schema`
   - 未定義の retry-threshold reason

   したがって「認識できないものを承認しない」という主張は誤りです。policy reason を一箇所の enum/定数集合から生成するか、AST テストで `_apply_failure_policy()` の全 call site、terminal status 集合、明示的 BLOCKED reason を合わせて比較すべきです。

5. **「BLOCKED/HOLD は毎朝赤いので放置されない」という容量前提が成立しない。**

   設計自身の表では HOLD は赤/黄であり、現行 softener なら単独の既存公開日は黄色です。それなのに sidecar 総数の無上限を「毎朝赤い」ことで正当化しています。[design-claude.md:311](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:311>)

   黄色 HOLD が複数日蓄積すると、Collect は共有1800秒 budgetで全 sidecarを順番に pollします。[summarize.py:1180](/home/feathach/dev/open-gikai/scripts/summarize.py:1180) 先頭の in-progress batch が予算を消費し、後続は実質ゼロ秒 pollになります。サイト全体は止まらなくても、回収遅延と API poll 数は増え続けます。

   上限を必須にする必要まではありませんが、「赤いから N≈0–1」という前提は削除し、件数・最古 age の summary 出力や、ended/BLOCKED を先に処理する順序を検討すべきです。

## Minor

1. **「3 mornings」は timestamp だけでは算出できない。**

   `blocked.since` は時刻を1つ保存するだけです。経過日数を morning 数と呼ぶと、実行時刻のずれや手動 rerun で不正確になります。[design-claude.md:109](</home/feathach/dev/open-gikai/docs/design-debate/resume-retry-policy-and-date-scoped-gate/design-claude.md:109>) `blocked_runs` を明示的に増やすか、「blocked for N days」と表現してください。

2. **T4 の YAML ソース検査だけでは挙動を十分に守れない。**

   `has_pending` 文字列が消え、`continue` が存在するだけでもテストを通せます。`continue` が Python 呼び出し後に置かれた、pending 判定が逆になった、break が先に発火する、といった破壊を検出できません。少なくとも step の run script を fixture 化して、pending 日付と非pending 日付を与え、呼び出された日付列と新規 sidecar が最大1件であることを検証すべきです。

## Questions

1. BLOCKED の赤は `systemic_dates` に強制投入する想定ですか、それとも `blocked_dates` を新設しますか。現状の `_record_resume_verdict()` をそのまま使う案では要件を満たしません。

2. retry threshold 到達後の正式な reason、sidecar metadata、翌朝の処理は何ですか。現設計には policy table のエントリも状態遷移もありません。

3. `verify_manifest_against_raw()` は、ended batch の results 取得より前に実行する意図ですか。「repair より前」だけでは #65 を閉じられません。

**完了**: 現行 HEAD `3b9e7b2` の resume・sidecar・workflow 経路に照らし、設計を read-only で攻撃レビューしました。  
**次の一手**: 1. Critical 3件を設計へ反映 2. BLOCKED 専用の赤シグナルと results-expiry 回帰テストを追加。  
**検証**: コード・workflow・既存テストを静的確認。ファイル変更およびテスト実行はしていません。
