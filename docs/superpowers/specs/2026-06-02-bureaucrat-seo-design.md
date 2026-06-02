# 設計: 官僚(政府参考人)ページSEO強化 + Googleインデックス拡大

日付: 2026-06-02
ステータス: 承認済み

## 背景

GSC/GA4 の実測(2026-06-02 時点)に基づく施策。

- 検索流入は 2026-W21〜W22 で週56クリックに急伸(W19 比で約10倍)。GA4 セッションも W22 で155に到達
- 成長エンジンは **官僚(政府参考人)名のロングテール検索**。members.json 1,141人中551人がハッシュID=官僚で、局長・審議官クラスは Wikipedia 等の競合コンテンツが存在せず、掲載順位5〜15位に自然到達している
- 「海上保安庁 次長」「鉄道局長 五十嵐」のような **役職名を含むクエリ**も観測されているが、役職軸の受け皿ページが存在しない
- 流入元は Google organic 141 ≒ Bing organic 130(直近30日)。IndexNow により Bing が先行しており、Google のインデックス率は約10%(366 / 3,868 提出)で大きな伸びしろ
- GSC に登録されている sitemap が **www 付き URL**(`https://www.open-gikai.net/sitemap_index.xml`)のままで、canonical(非www)と不一致。旧 `sitemap.xml`(1 URL)も残置

## ゴール

1. 官僚名・役職名クエリの検索流入を伸ばす(実証済みの成長軸に投資)
2. Google 側のインデックス率を改善し、Bing との流入格差(伸びしろ)を回収する

成功指標(2〜4週間後に再計測):

- 役職系クエリ(「◯◯局長」「◯◯庁 次長」等)の GSC 表示回数
- GSC インデックス数(基準値: 約366 / 3,868)
- Google:Bing 流入比の変化

## スコープ

- ✅ 省庁別ハブページ新設(`/gov`, `/gov/[slug]`)
- ✅ メンバーページの description 動的化・パンくず拡張・役職リンク化
- ✅ sitemap / IndexNow への組み込み
- ✅ GSC sitemap 登録修正(手動運用)
- ❌ メンバーページ OGP 画像(見送り — ビルド時間リスク、検索順位への直接効果なし)
- ❌ 役職別独立ページ(「局長」一覧等 — thin content リスク、省庁ページ内テキストで代替)
- ❌ 参考人(大学教授・研究者等、約190人)のハブ — 将来検討
- ❌ Python パイプライン変更(フロントエンド完結)

## 設計

### 1. 省庁抽出ロジック (`src/lib/ministry.mjs`)

純関数のみの新規モジュール。LLM 不使用・完全決定論的。

**配置: plain ESM の `src/lib/ministry.mjs`(JSDoc 型注釈)を単一実装とし、隣に型宣言 `src/lib/ministry.d.mts` を添える。** 理由: build は `node scripts/*.mjs && next build` で tsx / ts-node / jiti は不在のため、TS ファイルは Node スクリプトから import できない。Next(TS)側は `@/lib/ministry.mjs`、`scripts/generate-sitemap.mjs` と `scripts/notify-indexnow.mjs` は相対パスで同じ実装を import する(ロジックの二重実装を作らない)。

- 省庁マスタ: `{ slug: string; name: string }[]` 約35件(例: `{ slug: "mlit", name: "国土交通省" }`)。府省だけでなく外局(海上保安庁・観光庁・文化庁・消防庁等)も独立エントリとする
- **公開 API は `getMemberMinistry(member: { id, role }): Ministry | null` とし、role 文字列のみを受ける prefix マッチは内部関数に隠蔽する。** `m_` ID 限定と blocklist を API 内部で強制し、呼び出し側(/gov ページ・sitemap・IndexNow の3箇所)のフィルタ忘れによる政治家混入の再発を型レベルで防ぐ
- **対象は ID が `m_` で始まる発言者(政府参考人系)に限定する。** slug ID の政治家には「内閣官房長官」「内閣府特命担当大臣」等、省庁名で始まる役職が13人存在し、prefix マッチだけでは省庁ハブに混入するため(実データで検証済み)
- 加えて防御層として、政治職名 blocklist(`内閣官房長官`・`内閣官房副長官`・`内閣府特命担当大臣`・`内閣府副大臣`・`内閣府大臣政務官` に一致する role)を除外する。将来 `m_` ID に政治家が混入した場合の保険
- ※ `rank` による除外は採用しない。rank データは「気象庁長官」「文部科学戦略官」等の官僚を `minister` と誤分類しており、正しい官僚を除外してしまうため(実データで検証済み)
- 内部の prefix マッチは **最長一致**(「内閣官房」vs「内閣府」のような前方重複対策のため、名前の長い順に評価)
- マッチしない者(参考人・衆参事務局・「委員」「大臣」等の汎用役職・role 空)は `null` を返し、ハブ非掲載。既存の表示には影響しない

検証済みデータ: 官僚551人中330人が機械的に分類可能(国交省31・厚労省28・内閣官房26・内閣府23・総務省23…)。

### 2. 新規ページ

#### `/gov`(省庁一覧)

- 各省庁カード: 省庁名・発言者数・発言総数、`/gov/[slug]` へのリンク
- title: 「省庁別 発言者一覧」

#### `/gov/[slug]`(省庁別発言者一覧)

- title: 「{省庁名}の国会発言者一覧(局長・審議官など)」
- 各発言者: 氏名・正式役職(フルテキスト)・発言数・直近発言日、`/m/{id}` へのリンク。発言数降順
- **発言1件以上のメンバーのみ掲載**(members.json には threads から参照されない member が3人存在するため、所属判定は members.json の role、統計は後述の `getMemberStats()` を正とする)
- `generateStaticParams`: 上記の結果、**掲載発言者が1人以上の省庁のみ**生成(空ページを作らない)
- JSON-LD: `ItemList` + `BreadcrumbList`
- 役職フルテキストがページ内に並ぶことで「{省庁} {役職}」型クエリの受け皿となる

### 3. 発言者別統計 (`src/lib/data.ts` に `getMemberStats()` を追加)

既存の `getThreadsSummary()` はスレッド単位の `speechCount` しか持たず、発言者別の集計は存在しない。`threads[].speeches` を走査して memberId ごとに以下を算出する共有関数を追加する(既存ローダーと同様にモジュールスコープでキャッシュ):

```
getMemberStats(): Map<memberId, { speechCount: number; latestDate: string; latestCommittee: string }>
```

/gov ページ(発言数・直近発言日)とメンバーページの description 動的化の両方がこれを使う。

### 4. 既存メンバーページの改善 (`src/app/m/[memberId]/page.tsx`)

- **description 動的化**: 現行の全員同文を廃止し、「{name}({role})の国会・審議会での発言{n}件をAI要約付きで掲載。直近は{date}の{committee}。」をビルド時に算出(データローダーはキャッシュ済みのためビルド時間影響は軽微)。role が空の場合は括弧ごと省略する(既存 title の `desc` 組み立てと同じ規則)
- **パンくず拡張**: 省庁マッチ者は「ホーム > 発言者一覧 > {省庁} > 人名」(表示・JSON-LD とも)。非マッチ者は現行どおり
- **役職リンク化**: プロフィールの役職表示を `/gov/{slug}` へのリンクにする(マッチ者のみ)。/m/ ↔ /gov/ の双方向内部リンクを形成

### 5. sitemap / IndexNow

- `scripts/generate-sitemap.mjs`: `sitemap-gov.xml` を追加し `sitemap_index.xml` に登録。lastmod は所属発言者の最新スレッド日
- `scripts/notify-indexnow.mjs`: **当日スレッドに出現した全 memberId**(既存実装が収集済み)を members.json と突合し、`getMemberMinistry()` で省庁 slug を導出して、該当する `/gov/{slug}` と `/gov` を送信対象に追加(既存発言者の発言でも省庁ページの発言数・直近発言日・並び順が変わるため、新規発言者に限定しない)

### 6. 手動運用(コード外)

GSC(sc-domain: open-gikai.net)で以下を実施:

1. `https://www.open-gikai.net/sitemap_index.xml` を削除
2. 旧 `https://open-gikai.net/sitemap.xml` を削除
3. `https://open-gikai.net/sitemap_index.xml` を登録

※ ローカルの OAuth トークンは readonly スコープのため API からは不可。GSC UI で実施する。

## エッジケース

- role 空(31人)/ 非マッチ(約190人) → ハブ非掲載、既存ページ表示は不変
- 省庁再編・新庁設置 → マスタリストへ1行追記で対応(コード内コメントで運用を明記)
- members.json の role は日次バッチ更新のスナップショット → 同一データから同一出力(決定論)。肩書の異動はデータ更新に自動追随
- 同一省庁 slug の重複・空 slug はマスタ定義時に静的に排除(ビルド時に検証)

## 中立性原則との整合

本施策は CLAUDE.md の Summary Layer Invariants に触れない補助情報レイヤー(auxiliary)である。発言の要約内容を変更せず、ビルド時の決定論的な集計・表示のみを追加する。LLM は使用しない。

## 検証

- `npm run lint` / `npm run build` 通過
- `out/gov/` 配下に期待した省庁ページが生成されること
- 省庁ページに実データ(例: 国土交通省 → 五十嵐徹人 鉄道局長)が表示されることをスポットチェック
- `sitemap-gov.xml` が生成され `sitemap_index.xml` から参照されること
- メンバーページの description が人物ごとに固有化されていること
