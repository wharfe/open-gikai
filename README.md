# OpenGIKAI — Opening Up Parliament

**[open-gikai.net](https://open-gikai.net)** | [🇯🇵 日本語版はこちら / Japanese](./README.ja.md)

**OpenGIKAI** (議会) is an open-source public media project that transforms Japanese parliamentary proceedings into a modern, accessible thread format — like social media, but with official sources. It ingests multiple sources including Diet records (NDL) and Prime Minister press conferences (kantei.go.jp).

## What It Does

- Fetches official transcripts from multiple sources: [NDL Diet Records API](https://kokkai.ndl.go.jp/api.html) and [kantei.go.jp](https://www.kantei.go.jp/) press conferences
- Uses AI (Claude) to summarize and structure speeches by topic
- Presents them in a thread-based UI with three reading levels:
  - 🌱 **Easy** — Simple language for everyone
  - 📖 **Standard** — Balanced detail with brief explanations
  - 📰 **Detailed** — Full political context, news-style

## Why

Parliamentary records are public but hard to read. OpenGIKAI makes them accessible without editorializing — every summary links back to the original transcript. The AI prompts and processing logic are fully open-source to ensure transparency and political neutrality.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15 (App Router), TypeScript, Tailwind CSS |
| Deployment | Vercel (Static Site Generation) |
| Data Pipeline | Python + Claude API |
| Data Sources | [NDL Diet Records API](https://kokkai.ndl.go.jp/api.html), [kantei.go.jp](https://www.kantei.go.jp/) |

## Getting Started

```bash
# Clone the repository
git clone https://github.com/wharfe/open-gikai.git
cd open-gikai

# Install dependencies
npm install

# Start development server
npm run dev
```

## Project Structure

```
├── src/
│   ├── app/          # Next.js App Router pages
│   ├── components/   # React components
│   ├── lib/          # Utilities and data fetching
│   └── types/        # TypeScript type definitions
├── scripts/          # Python batch processing (source adapters → Claude API → JSON)
│   └── sources/      # Source adapters (NDL, kantei, etc.)
├── data/             # Generated JSON data for SSG
└── public/           # Static assets
```

## How It Works

```
Sources (NDL, kantei, ...) → Fetch transcripts → Group by topic (Claude API)
                           → Summarize at 3 levels → Generate static JSON → Deploy site
```

1. **Daily batch**: Fetches previous day's content from configured sources (NDL, kantei, etc.) via the SourceAdapter abstraction
2. **AI processing**: Groups speeches by topic, classifies tension type (questioning, response, follow-up, etc.), generates summaries at three reading levels
3. **Static generation**: Outputs JSON files consumed by Next.js SSG
4. **Deployment**: Auto-deploys to Vercel

## Data Pipeline

```bash
# 1. Fetch speeches from NDL API
python scripts/fetch_ndl.py --date-from 2025-03-14

# 2. Summarize with Claude API (requires ANTHROPIC_API_KEY in .env)
python scripts/summarize.py --date 2025-03-14

# 3. Build and preview
npm run build && npx serve out
```

See `.env.example` for configuration.

## Design Principles

- **Political neutrality by design** — All speeches processed with identical algorithms. No editorial selection. Prompts are open-source.
- **Source transparency** — Every summary links to the original NDL transcript
- **AI transparency** — All AI-generated content is clearly labeled
- **Accessibility** — Three reading levels make Diet proceedings approachable for everyone

## Data Source

Diet records are sourced from the [National Diet Library's Diet Records Search System](https://kokkai.ndl.go.jp/). These records are **not subject to copyright** under Japan's Copyright Act, Article 13. Press conference transcripts are sourced from [kantei.go.jp](https://www.kantei.go.jp/).

AI-generated summaries are clearly attributed as such.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

## License

[MIT](./LICENSE)
