結論: **Gate 1 不合格**。設計の中核となる分類規則と workflow 順序が brief の受け入れ条件を破ります。

## Critical

1. **brief の明示要件を勝手に差し替え、しかも代替分類が実データで破綻している**

   brief は「`m_` プレフィックスに Wikipedia を付けない」を共有 ground truth としています。しかし設計はこれを撤回し、`rank` または `party` があれば A とする規則へ変更しています（[design-claude.md:24](/home/feathach/dev/open-gikai/docs/design-debate/member-links-rewiring/design-claude.md:24)、[design-claude.md:104](/home/feathach/dev/open-gikai/docs/design-debate/member-links-rewiring/design-claude.md:104)）。

   これは単なる仕様解釈ではなく、具体的に誤分類します。

   - `m_5474c5dc` は名前・party・role がすべて「記者」なので、`party` 条件だけで A になります（[members.json:1518](/home/feathach/dev/open-gikai/data/members.json:1518)）。
   - `m_96b705cb` の「事務局」も同様です（[members.json:2036](/home/feathach/dev/open-gikai/data/members.json:2036)）。
   - `m_40667609` は気象庁長官ですが `rank: "minister"` なので A になります（[members.json:7761](/home/feathach/dev/open-gikai/data/members.json:7761)）。
   - `ministry.mjs` 自身が、まさに気象庁長官などを理由に「rank を使うな」と明記しています（[ministry.mjs:73](/home/feathach/dev/open-gikai/src/lib/ministry.mjs:73)）。

   「rank を先に見る」修正は林芳正だけを救う修正ではなく、官僚を大量に政治家扱いする修正です。さらに「rank 対象者全員を A にする」テストは、このバグを回帰防止ではなく恒久仕様にします。

   方向性は、まず brief どおり map key の `m_` を Wikipedia 除外境界にすることです。誤って `m_` になった政治家を救うなら、別途 brief を変更し、`rank` や汚れた `party` ではない信頼できる政治家識別情報を定義する必要があります。

2. **enrich → validate の順では、同じ朝に links なしメンバーが追加される**

   設計は enrich を `validate-data.mjs --fix` の直前に置きます（[design-claude.md:58](/home/feathach/dev/open-gikai/docs/design-debate/member-links-rewiring/design-claude.md:58)）。ところが validator は threads にしか存在しない memberId を検出し、`links` も `id` もないエントリを `members.json` に追加して書き戻します（[validate-data.mjs:145](/home/feathach/dev/open-gikai/scripts/validate-data.mjs:145)、[validate-data.mjs:168](/home/feathach/dev/open-gikai/scripts/validate-data.mjs:168)、[validate-data.mjs:196](/home/feathach/dev/open-gikai/scripts/validate-data.mjs:196)）。

   失敗シナリオは明快です。

   1. 新しい発言者が threads に追加される。
   2. enrich はその人物をまだ見ない。
   3. validate `--fix` が links なし人物を追加する。
   4. commit ステップがそのまま `members.json` を stage する。

   これで「全メンバーが1本以上持つ」が初回の日次実行から破れます。現在も validator 由来と思われる `id` 欠落エントリが31件存在します。

   順序は少なくとも `validate --fix` → enrich にする必要があります。enrich 後の検証も必要なら、再度非破壊検証を行ってください。workflow テストはステップの存在だけでなく、この順序を固定すべきです。

3. **A 全員への Wikipedia 直リンクは「404 に着地しない」を保証しない**

   設計は「Wikipedia 項目が期待できる」だけで、存在確認なしに直接 `/wiki/{氏名}` を生成します（[design-claude.md:19](/home/feathach/dev/open-gikai/docs/design-debate/member-links-rewiring/design-claude.md:19)）。選出職であることは、その表記どおりの記事が存在する証明ではありません。同姓同名、異体字、通称、改名、新人議員もあります。

   しかも現在の分類なら、`m_2527ecf9` の「平」のような省略名にも Wikipedia 直リンクを生成します（[members.json:2050](/home/feathach/dev/open-gikai/data/members.json:2050)）。

   Wikipedia API を non-goal にするなら、決定的に保証できるのは Wikipedia検索または一般検索リンクです。直接記事 URL は、既に確認済みの静的対応表など、存在が担保された場合だけに限定すべきです。現設計のテスト2は「誤分類した A に URL がある」ことしか確認せず、リンク先の存在を保証しません。

4. **分類がオブジェクト内の `id` に依存すると31人を取りこぼす**

   `members.json` の識別子の正本は map key ですが、validator が追加する値には `id` がありません（[validate-data.mjs:175](/home/feathach/dev/open-gikai/scripts/validate-data.mjs:175)）。一方、`getMemberMinistry` は `member.id` がないと即座に `null` を返します（[ministry.mjs:82](/home/feathach/dev/open-gikai/src/lib/ministry.mjs:82)）。

   現在、map key が `m_` なのに値の `id` がないエントリが31件あります。このまま設計表の「id が `m_` でない」を実装すれば、それらは A となり、`m_f9112eed` のような文字列を氏名として Wikipedia に送ります。

   enricher は必ず `Object.entries(members)` で map key を memberId として扱う必要があります。`getMemberMinistry` へ渡す際も、保存データを無断補正するかどうかとは分けて、判定用に正規化した `id` を渡す設計を明記すべきです。

## Important

1. **`POLITICAL_TITLE_PREFIXES` は import できない**

   設計は「node へ移すことでそのまま使える」としています（[design-claude.md:104](/home/feathach/dev/open-gikai/docs/design-debate/member-links-rewiring/design-claude.md:104)）。しかしこれは非 export のローカル定数です（[ministry.mjs:61](/home/feathach/dev/open-gikai/src/lib/ministry.mjs:61)）。型宣言も `MINISTRIES` と `getMemberMinistry` しか公開していません（[ministry.d.mts:1](/home/feathach/dev/open-gikai/src/lib/ministry.d.mts:1)）。

   共有が必要なら、生配列を公開するより `isPoliticalTitle(role)` のような意味を持つ predicate を exportし、型宣言とテストも更新する方向です。ただし、Critical 1の分類自体を撤回すれば、この追加 API は不要です。

2. **`MINISTRIES` に slug があるだけでは `/gov/{slug}` の存在を保証しない**

   `/gov` は `dynamicParams = false` で、実際に生成される slug は `getMinistrySlugs()` の結果だけです（[page.tsx:7](/home/feathach/dev/open-gikai/src/app/gov/[slug]/page.tsx:7)）。その一覧は `MINISTRIES` 全件ではなく、発言実績を持つ roster から生成されます（[data.ts:343](/home/feathach/dev/open-gikai/src/lib/data.ts:343)、[data.ts:384](/home/feathach/dev/open-gikai/src/lib/data.ts:384)）。

   したがって提案テスト3の「slug が `MINISTRIES` に存在する」は404防止テストになっていません。現時点ではたまたま該当 B のリンク先が生成されても、将来、発言実績を持たない ministry-matched member が残れば404になります。既存 member page はこの問題を認識し、`getMinistrySlugs()` との交差を取っています（[page.tsx:72](/home/feathach/dev/open-gikai/src/app/m/[memberId]/page.tsx:72)）。enricherも同じ「実際に生成される URL」の条件を使うべきです。

3. **提案されたテスト群では根本原因を fence できない**

   最終的な現在の `members.json` を検査するだけでは、validator が新規人物を後から追加するケースを再現できません。また workflow テストも「enrich がある」「`|| true` がない」だけでは、今回の誤った順序を緑にします。

   必要なのは少なくとも以下です。

   - threads にだけ新規 memberId がいる fixture で日次の post-processing 順序を実行し、最終 `members.json` に links があること。
   - workflow 上で validate-fix より enrich が後であること。
   - map key が `m_`、値の `id` が欠落している fixture。
   - `rank: minister` の官僚や、`party: 記者` の `m_` fixture。
   - 冪等テストは内容一致だけでなく、mtime・inodeまたは writer spy により2回目に書いていないことを確認すること。

## Minor

1. **「role先頭語」の定義がない**

   空文字、空白を含まない長い役職、括弧、複数肩書、`role: "関係者"` をどう処理するかが未定義です。「先頭語」が実装者依存だと、同じ brief から異なる検索 URL ができます。決定的な抽出規則か、単純に全文を使うことを明記してください。

2. **atomic helper のテスト方針が構造検査に寄りすぎている**

   「写しが1つだけ」は安全性そのものではありません。重要なのは同一ディレクトリの temp、全書き込み、file fsync、rename、directory fsync、失敗時の temp cleanup が共有 helper 経由で維持されることです。利用元の import 検査に加え、helper の失敗注入を含む振る舞いテストが必要です。

## Questions

1. `m_` の政治家例を例外扱いしたいなら、brief の受け入れ条件を正式に変更する権限と、その人物を判定する信頼できる一次情報は何ですか。現在の `rank` と `party` は実データ上、その役割を果たしません。
2. 「404 に着地しない」は検索結果ページだけを保証対象にするのか、それとも Wikipedia の直接記事 URL にも適用するのか。後者なら、現設計には存在保証の仕組みがありません。

**完了**: 設計文を現行コード・データ・workflow と照合し、Gate 1 不合格と判定しました。  
**次の一手**: 1. `m_` 境界を維持する 2. validate-fix→enrich の順に直す 3. Wikipedia直リンクの存在保証方針を決める。  
**検証**: read-only で `members.json`、`ministry.mjs`、member page、validator、daily workflow、既存テストを確認しました。
