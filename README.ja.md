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
| フロントエンド | Next.js 15 (App Router)、TypeScript、Tailwind CSS |
| デプロイ | Vercel（静的サイト生成） |
| データパイプライン | Python + Claude API |
| データソース | [NDL 国会会議録検索システムAPI](https://kokkai.ndl.go.jp/api.html)、[首相官邸](https://www.kantei.go.jp/)、[内閣府 審議会](https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html) |

## はじめかた

```bash
# リポジトリをクローン
git clone https://github.com/wharfe/open-gikai.git
cd open-gikai

# 依存パッケージをインストール
npm install

# 開発サーバーを起動
npm run dev
```

## プロジェクト構成

```
├── src/
│   ├── app/          # Next.js App Router ページ
│   ├── components/   # Reactコンポーネント
│   ├── lib/          # ユーティリティ・データ取得
│   └── types/        # TypeScript型定義
├── scripts/          # Pythonバッチ処理（ソースアダプター → Claude API → JSON）
│   └── sources/      # ソースアダプター（NDL、官邸、審議会など）
├── data/             # SSG用の生成済みJSONデータ
└── public/           # 静的アセット
```

## 仕組み

```
ソース（NDL、官邸、審議会等） → データ取得 → テーマ別グルーピング（Claude API）
                      → 3レベル要約生成 → 静的JSON出力 → サイトデプロイ
```

1. **デイリーバッチ**: SourceAdapterを通じて各ソース（NDL、官邸、審議会等）から前日のデータを取得
2. **AI処理**: テーマ別にグルーピング、テンション分類（追及・答弁・再追及など）、3レベルの要約を生成
3. **静的生成**: Next.js SSG用のJSONファイルを出力
4. **デプロイ**: Vercelに自動デプロイ

## データパイプライン

```bash
# 1. 各ソースからデータを取得
python scripts/fetch_ndl.py --date-from 2025-03-14
python scripts/fetch_kantei.py --date-from 2025-03-14
python scripts/fetch_council.py --date-from 2025-12-24  # 審議会議事録（PDF）

# 2. Claude APIで要約（.envにANTHROPIC_API_KEYが必要）
python scripts/summarize.py --date 2025-03-14

# 3. ビルド・プレビュー
npm run build && npx serve out
```

設定は `.env.example` を参照してください。

## 設計原則

- **設計による政治的中立性** — すべての発言を同一のアルゴリズムで処理。編集上の取捨選択なし。プロンプトはオープンソース。
- **出典の透明性** — すべての要約がNDLの原文議事録にリンク
- **AIの透明性** — AI生成コンテンツは明確にラベル表示
- **アクセシビリティ** — 3つの読みやすさレベルで国会審議を身近に

## データソース

議事録データは[国立国会図書館 国会会議録検索システム](https://kokkai.ndl.go.jp/)から取得しています。国会会議録は著作権法第13条により著作権の対象外です。記者会見データは[首相官邸](https://www.kantei.go.jp/)から取得しています。審議会の議事録は[内閣府](https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html)等の公式サイトから取得しています。

AI生成の要約にはその旨を明記しています。

## コントリビューション

[CONTRIBUTING.md](./CONTRIBUTING.md)（英語）/ [CONTRIBUTING.ja.md](./CONTRIBUTING.ja.md)（日本語）をご覧ください。

## ライセンス

[MIT](./LICENSE)
