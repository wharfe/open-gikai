# OpenGIKAI — 議会をひらく

**[open-gikai.net](https://open-gikai.net)** | [🇬🇧 English](./README.md)

**OpenGIKAI（議会）** は、議会の審議内容を現代的なスレッド形式で再構築するオープンソースの公共メディアプロジェクトです。SNSのような読みやすさで、公式の一次情報に基づいた議会情報を届けます。国会会議録（NDL）、首相官邸の記者会見（kantei.go.jp）、審議会の議事録（cao.go.jp）を含む複数の公式ソースに対応しています。

## 概要

- [国立国会図書館（NDL）の会議録API](https://kokkai.ndl.go.jp/api.html)から公式議事録を取得
- [首相官邸](https://www.kantei.go.jp/)の記者会見を取得
- [内閣府](https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html)等の審議会議事録を取得
- AI（Claude）で発言をテーマ別に要約・構造化
- 3つの読みやすさレベルでスレッド形式のUIに表示：
  - 🌱 **やさしく** — 誰でもわかるシンプルな言葉
  - 📖 **標準** — バランスの取れた説明付き
  - 📰 **詳しく** — 政治的文脈を含むニュース文体

## なぜ作るのか

国会の議事録は公開されていますが、読みにくいのが現状です。OpenGIKAIは編集や論評を加えることなく、すべての要約を原文の議事録にリンクした形でアクセスしやすくします。AIプロンプトと処理ロジックをすべてオープンソースにすることで、透明性と政治的中立性を担保します。

## 技術スタック

| レイヤー | 技術 |
|----------|------|
| フロントエンド | Next.js 16 (App Router)、TypeScript、Tailwind CSS |
| デプロイ | Vercel — 同一リポジトリから2つの project: ルートが SSG フロントエンド、`apps/mcp/` が動的 MCP server |
| データパイプライン | Python + Claude API (Message Batches API + prompt caching) |
| データソース | [NDL 国会会議録検索システムAPI](https://kokkai.ndl.go.jp/api.html)、[首相官邸](https://www.kantei.go.jp/)、[内閣府 審議会](https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html) |
| 公開 API | Claude Desktop / Cline / その他エージェント向けの読み取り専用 [MCP server](./apps/mcp/README.md) |

## はじめかた

```bash
# リポジトリをクローン
git clone https://github.com/wharfe/open-gikai.git
cd open-gikai

# フロントエンドの依存パッケージをインストール
npm install

# フロントエンドの開発サーバーを起動
npm run dev
```

MCP server は `apps/mcp/` 配下の独立した Next.js project で、依存も独自管理です。

```bash
cd apps/mcp
npm install
npm run dev   # http://localhost:3100 で起動
```

MCP server のデプロイ詳細は [`apps/mcp/README.md`](./apps/mcp/README.md) を参照してください。

## プロジェクト構成

```
├── src/                  # フロントエンド（Next.js SSG — output: "export"）
│   ├── app/              # App Router ページ
│   ├── components/       # React コンポーネント
│   ├── lib/              # ユーティリティ・データ取得
│   └── types/            # TypeScript 型定義
├── apps/
│   └── mcp/              # MCP server（別 Vercel project、動的 Node ランタイム）
├── scripts/              # Python バッチ処理
│   ├── sources/          # ソースアダプター（NDL・官邸・審議会など）
│   └── pipeline/         # AI パイプライン（グルーピング・要約・ニュース ranker）
├── data/                 # SSG と MCP server 共有の生成済み JSON
│   ├── threads/          # 日付別スレッドファイル
│   └── members.json      # 蓄積される議員レジストリ
├── public/               # 静的アセット（sitemap, RSS feed 等）
└── .github/workflows/    # daily-batch.yml（6:00 JST cron）
```

フロントエンドは `output: "export"` を使うため、Node ランタイムを必要とするもの（Route Handler、動的 API 等）は `apps/` 配下に置く設計です。

## 仕組み

```
ソース（NDL、官邸、審議会）
   ├─► fetch（毎回 30 日のスライディングウィンドウ）
   │
   ├─► テーマ別グルーピング                ┐
   ├─► tension 分類                          │  Claude API
   ├─► 3レベル要約（Message Batches API）   │  + prompt caching
   ├─► コミットメント・採決結果抽出           ┘
   │
   ├─► 関連ニュース付与（Bing News + Claude 関連度判定）
   │
   └─► JSON 生成
         ├─► フロントエンド SSG → open-gikai.net (Vercel)
         └─► MCP server         → /api/mcp        (Vercel, apps/mcp)
```

1. **スライディングウィンドウ取得**: 各実行で過去 30 日を再取得。NDL は議事録を数日〜数週間遅れで掲載するため、「前日のみ」取得では遡及掲載分を取り逃す。
2. **AI 処理**:
   - **グルーピング** — 会議ごとに同期呼び出し。発言をテーマ別スレッドに分割。
   - **要約** — スレッド単位で Message Batches API（入出力 50% 割引）。prompt caching と併用でキャッシュ部分の入力コストは ~10% に。
   - **採決抽出** — 会議ごとに同期呼び出し。委員長発言から採決結果・附帯決議を抽出。
3. **ニュース付与**: Bing News でテーマ検索 → Claude Haiku ranker（`scripts/pipeline/news_ranker.py`）が候補から最も関連の高い 3 件を選別。これは補助情報レイヤー — 要約レイヤーとの境界は CLAUDE.md「Summary Layer Invariants」を参照。
4. **静的生成**: `data/threads/*.json` と `data/members.json` を Next.js SSG が消費して静的 HTML を生成。
5. **デプロイ**: 同一リポジトリから 2 つの Vercel project（ルート = SSG / `apps/mcp` = 動的 MCP server）。
6. **監視**: daily-batch のコミットメッセージに `(+N threads)` を付与。7 回連続で 0 が続くと CI warning を発火（緑チェックだけでは見えない fetcher リグレッションを構造的に検知）。ジョブ自体が失敗した場合（NDL API の 403、Anthropic クレジット切れなど）は `pipeline-failure` ラベルの GitHub Issue を起票/追記し、`gh run list` を見に行かずとも気づけるようにする。

## データパイプライン

```bash
# 1. スライディングウィンドウで取得（30日で NDL 遡及掲載を拾う）
python scripts/fetch_ndl.py     --lookback-days 30
python scripts/fetch_kantei.py  --lookback-days 30
python scripts/fetch_council.py --lookback-days 30 --council kisei
# (... 各 council 分繰り返し。フルリストは daily-batch.yml 参照)

# 2. Message Batches API で要約（既存 data/threads/ に対して auto-resume）
#    .env に ANTHROPIC_API_KEY が必要
python scripts/summarize.py --date 2026-04-22 --batch

# 3. ニュース付与 + Claude 関連度判定
python scripts/enrich-news.py --date 2026-04-22 --rank-with-claude

# 4. sitemap・feed 生成 + バリデーション + SSG ビルド
node scripts/validate-data.mjs --fix
node scripts/generate-feeds.js
node scripts/generate-sitemap.mjs
npm run build && npx serve out
```

設定は `.env.example` を参照してください。`.github/workflows/daily-batch.yml` が上記を 6:00 JST に毎日自動実行します。

## MCP Server

OpenGIKAI は読み取り専用の [Model Context Protocol](https://modelcontextprotocol.io) server としても公開しており、Claude Desktop / Cline / MCP 対応エージェントから直接議事録を検索できます。

| Tool | 用途 |
|------|------|
| `search_threads` | キーワード・日付範囲・委員会・ソースでスレッド検索 |
| `get_thread` | スレッドの完全な内容（3レベル要約・原文引用・tension 分類・採決結果） |
| `get_member` | 議員プロフィール |
| `list_members` | 議員一覧（氏名・政党フィルタ） |
| `list_dates` | データのある日付と各日のスレッド数 |

MCP server は `apps/mcp/` 配下にあり、同じリポジトリから 2 つ目の Vercel project としてデプロイされます。**OpenGIKAI 側は LLM 推論コストを負担しません** — クライアント（Claude Desktop 等）が自分の API キーで Claude を呼び、サーバーは JSON を返すだけです。エンドポイント URL と Claude Desktop の設定例は [`apps/mcp/README.md`](./apps/mcp/README.md) を参照してください。

## 設計原則

- **設計による政治的中立性** — すべての発言を同一のアルゴリズムで処理。編集上の取捨選択なし。プロンプトはオープンソース。要約レイヤーは stateless / deterministic / プロンプト挙動のみ（Memory tool・Agent ループ・対話 UI を要約レイヤーに導入しない）— 詳しくは [`CLAUDE.md`](./CLAUDE.md) の「Summary Layer Invariants」を参照。
- **出典の透明性** — すべての要約が NDL / 官邸 / 審議会の原文にリンク。
- **AI の透明性** — AI 生成コンテンツは明示的にラベル表示。MCP のレスポンスには `attribution` ブロックを必ず含め、下流エージェントにも明確に伝える。
- **アクセシビリティ** — 3 つの読みやすさレベルで国会審議を身近に。

## データソース

議事録データは[国立国会図書館 国会会議録検索システム](https://kokkai.ndl.go.jp/)から取得しています。国会会議録は著作権法第13条により著作権の対象外です。記者会見データは[首相官邸](https://www.kantei.go.jp/)から取得しています。審議会の議事録は[内閣府](https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html)等の公式サイトから取得しています。

AI生成の要約にはその旨を明記しています。

## コントリビューション

[CONTRIBUTING.md](./CONTRIBUTING.md)（英語）/ [CONTRIBUTING.ja.md](./CONTRIBUTING.ja.md)（日本語）をご覧ください。

## ライセンス

[MIT](./LICENSE)
