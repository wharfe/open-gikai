# GIKAI 実装指示書
## Claude Code ハンドオフドキュメント

---

## プロジェクト概要

**GIKAI（議会）** は、国会の審議内容を現代的なSNS的フォーマットで再構築する公共メディアプロジェクト。

### 基本コンセプト
- 複数の公式ソースから一次情報を取得（NDL国会会議録、首相官邸 記者会見、審議会議事録など）
- AIによる要約・構造化を加え、「スレッド形式」で再表現
- 出典（議事録URL・発言者・日付）を常に担保した「翻訳メディア」
- 政治的中立性を設計上担保する（要約ロジック・プロンプトをOSSとして公開）

### ターゲット
政治に関心を持ち始めた一般市民。特にXの政治クラスタが起点となり、一般層へ拡散するモデルを想定。

### 運営方針
- 基本無料・OSS（公共財として）
- 将来的に地方メディア・ニュースメディアへのAPIデータ提供による収益化を検討
- Xアカウントで自動投稿し、サイトへの流入装置として機能させる

---

## データソース

### 国会会議録検索システム（NDL API）
```
エンドポイント: https://kokkai.ndl.go.jp/api/speech
主なパラメータ:
  - recordPacking=json
  - maximumRecords=10
  - from / until（日付範囲）
  - nameOfHouse（衆議院 / 参議院）
  - nameOfMeeting（委員会名）
  - any（全文検索キーワード）

レスポンスの主なフィールド:
  - speechRecord[].speaker（発言者名）
  - speechRecord[].speakerGroup（会派・党名）
  - speechRecord[].speakerPosition（役職）
  - speechRecord[].speech（発言全文）
  - speechRecord[].date（日付）
  - speechRecord[].nameOfMeeting（会議名）
  - speechRecord[].session（国会回次）
  - speechRecord[].meetingURL（議事録URL）
```

**著作権上の扱い**: 国会会議録は著作権法第13条により保護対象外。AIによる要約には「Claude AIによる要約」と明示すること。

### 首相官邸 記者会見（kantei.go.jp）
```
ソース: https://www.kantei.go.jp/
取得方式: Webスクレイピング（記者会見ページ）
主なコンテンツ:
  - 総理大臣記者会見
  - 官房長官記者会見
```

### 審議会議事録（cao.go.jp 等）
```
ソース: https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html（規制改革推進会議）
取得方式: HTMLインデックスページ → PDF議事録ダウンロード → PyMuPDFでテキスト抽出
主なコンテンツ:
  - 規制改革推進会議（本会議）
  - 各ワーキング・グループ（デジタル・AI、健康・医療・介護、地域活性化 等）
特記事項:
  - 発言者は○マーカーで識別（○落合座長、○城内大臣 等）
  - PDF冒頭の出席者リストからフルネームを復元し、名寄せに利用
  - 議事進行発言（「次に○○委員、お願いします」等）は自動フィルタ
  - CouncilConfig で他の審議会を追加可能（現在は規制改革推進会議のみ）
```

---

## AIパイプライン設計

### SourceAdapterアーキテクチャ

データ取得は `scripts/sources/` の SourceAdapter 抽象クラスで統一されている。各ソース（NDL、kantei、council等）はこのアダプターを実装し、共通インターフェースでパイプラインに統合される。

### 処理フロー（デイリーバッチ）

```
1. 各SourceAdapterから前日のデータを取得（NDL議事録、官邸記者会見、審議会議事録など）
2. 発言をテーマ単位でグルーピング（Claude APIで分類）
3. 各テーマについて：
   a. 発言者・所属・役職を構造化
   b. 質疑の往復構造を特定（誰が誰に問い、誰が答えたか）
   c. 3レベルの要約を一括生成（easy / teen / adult）
   d. キーワード抽出（最大3つ）
   e. テンション分類（追及 / 答弁 / 再追及 / 確認 / 割込み）
4. 静的JSONとして書き出し（要約は事前確定・静的に保持）
5. サイトをデプロイ
```

### 要約の文体ルール（最重要）

要約は**発言内容そのもの**を簡潔に表現する。報告文体（「〜と述べた」「〜と確認した」）は使わない。

```
❌ NG（報告文体）
「AI定義が曖昧すぎると指摘し、EUとの比較を求めた。」
「影響試算を提出すると約束した。」

✅ OK（発言内容そのもの）
「AI定義が曖昧すぎて、レコメンドエンジンまで規制対象になりうる。EUとの比較と試算を出せ。」
「採決前に影響試算を必ず提出する。国会報告義務の明記も検討する。」
```

レベル別の具体的な指示：

**easy（やさしく）**
- 難しい言葉を言い換える（ふりがなは不要）
- 「〜だ」「〜する」の断定調
- 比喩・たとえ話OK
- 60〜80字

**teen（標準）**
- 専門用語には括弧で短い補足（例：法案（これから作る法律の案））
- 「〜です・ます」不要、体言止めOK
- 80〜100字

**adult（詳しく）**
- 政治・法律用語そのまま
- 発言の政治的含意・論点を端的に
- 体言止めOK、ニュース文体
- 60〜80字

### Claude APIプロンプト（要約生成）

```python
SUMMARY_PROMPT = """
以下の国会発言を指定レベルで要約してください。

発言者：{name}（{role}・{party}）
委員会：{committee}
発言種別：{tension}
発言内容：{raw_speech}

レベル：{level}
指示：{level_instruction}

重要なルール：
- 「〜と述べた」「〜と答えた」「〜と求めた」などの報告文体は使わない
- 発言の内容そのものを、読者が直接受け取るような文章にする
- 出典URLは含めない（別途UIで表示する）

JSONのみで返してください：
{{"summary": "...", "note": "..."}}
note は補足が必要な場合のみ（20字以内）、不要なら空文字
"""
```

---

## データ構造（フロントエンド用JSON）

```typescript
type Level = "easy" | "teen" | "adult"

type Member = {
  id: string
  name: string
  party: string | null       // null = 無所属・大臣など
  role: string               // 「衆議院議員」「経済産業大臣」など
  district: string | null
  since: number | null       // 初当選年
  bio: string
  stance: string[]           // 政策スタンスキーワード（3つ程度）
  rank: "minister" | "viceminister" | "member"  // バッジ制御用
  ndlId?: string             // NDL議員IDがあれば
}

type Speech = {
  memberId: string
  tension: "追及" | "答弁" | "再追及" | "確認" | "割込み"
  keywords: string[]         // 最大3つ
  quote: string              // 原文からの重要な一文（adult用）
  raw: string                // 原文（議事録テキスト）
  sourceUrl: string          // NDL議事録URL
  summaries: Record<Level, string>
}

type Thread = {
  id: string                 // "t_YYYYMMDD_委員会ID_連番"
  date: string               // "YYYY.MM.DD"
  committee: string
  house: "衆議院" | "参議院" | "内閣" | "審議会" | string
  source?: string            // ソース識別子（"ndl" | "kantei" | "council" など、省略時はndl）
  sourceLabel?: string       // 表示用ラベル（"国会会議録" | "首相記者会見" | "規制改革推進会議" など）
  topic: string              // テーマ名（AI規制法案、など）
  topicTag: string           // 短縮タグ（AIルール、など）
  topicColor: string         // HEXカラー
  summary: string            // スレッド全体の一行サマリー
  speeches: Speech[]
}
```

---

## フロントエンド仕様

### 技術スタック（推奨）
```
Next.js 15（App Router）
TypeScript
Tailwind CSS
静的生成（SSG）：毎日バッチでJSONを更新 → デプロイ
Vercel（無料枠で十分）
```

### 画面・ルーティング
```
/                    トップ（スレッドフィード）
/t/[threadId]        スレッド詳細
/m/[memberId]        議員プロフィール
/trends              トレンド一覧
/about               このサービスについて（透明性ページ）
```

### OGP画像（シェア拡散に重要）
- スレッド単位・発言単位でOGP画像を動的生成
- `@vercel/og` を使用
- テンプレート：委員会名・テーマ・発言者名・要約（adult）・GIKAIロゴ

### Xシェアテキスト構造
```
⚡【{committee}・{date}】
{memberName}（{party}）

{summary_adult}

📄 全スレッド → https://gikai.jp/t/{threadId}
#GIKAI #国会 #{topicTag}
```

---

## 議員バッジ仕様

| rank | バッジ | 対象 |
|------|--------|------|
| `minister` | 🔷 閣僚バッジ | 大臣・副大臣 |
| `viceminister` | 🔹 政務官バッジ | 大臣政務官 |
| `member` | なし | 一般議員 |

閣僚は任期が変わるため、バッジはJSONで動的に管理する。
首相・官房長官は特別扱い（別色）も検討。

---

## 政治的中立性の担保

以下を全てGitHubで公開することを必須とする：

1. **NDL APIからの取得ロジック**（どの委員会をどの基準で選ぶか）
2. **テーマグルーピングのプロンプト**
3. **要約生成のプロンプト全文**
4. **テンション分類のプロンプト**
5. **キュレーション基準**（発言の取捨選択をしないこと）

原則：「全発言を同じアルゴリズムで処理する。与党・野党の扱いは同一。」

---

## フェーズ計画

### Phase 1（MVP）：1〜2週間
- [ ] NDL APIから1週間分の予算委員会・主要委員会を取得するPythonスクリプト
- [ ] Claude APIで要約・構造化するバッチ処理
- [ ] Next.js静的サイト（フィード・スレッド詳細・議員プロフィール）
- [ ] Vercelデプロイ
- [ ] Xシェア機能（静的テキスト）

### Phase 2：〜1ヶ月
- [ ] 全委員会カバー
- [ ] OGP画像自動生成
- [ ] フォロー機能（localStorage）
- [ ] トレンド（今週・今国会・今年）
- [ ] GIKAIのXアカウント自動投稿

### Phase 3：〜3ヶ月
- [ ] 議員検索・絞り込み
- [ ] キーワード検索
- [ ] 議員の発言傾向分析（過去比較）
- [ ] メディアAPI提供の検討

---

## 参考・リソース

- NDL国会会議録API仕様: https://kokkai.ndl.go.jp/api.html
- 衆議院議員情報: https://www.shugiin.go.jp/
- 参議院議員情報: https://www.sangiin.go.jp/
- e-Gov法令API: https://laws.e-gov.go.jp/api/1/

---

## プロトタイプの場所

`kokkai-v5.jsx` — ReactアーティファクトとしてClaude.aiで動作確認済み。
フロントエンドの設計・UXは概ねこれに準じる。
要約テキストの文体・バッジ仕様は本ドキュメントが正とする。
