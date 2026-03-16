# OpenGIKAI — Opening Up the Diet

**[open-gikai.net](https://open-gikai.net)** | [🇯🇵 日本語版はこちら / Japanese](./README.ja.md)

**OpenGIKAI** (議会) is an open-source public media project that transforms Japanese parliamentary proceedings into a modern, accessible thread format — like social media, but with official sources.

## What It Does

- Fetches official Diet transcripts from the [NDL (National Diet Library) API](https://kokkai.ndl.go.jp/api.html)
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
| Data Source | [NDL Diet Records API](https://kokkai.ndl.go.jp/api.html) |

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
├── scripts/          # Python batch processing (NDL API → Claude API → JSON)
├── data/             # Generated JSON data for SSG
└── public/           # Static assets
```

## How It Works

```
NDL API → Fetch transcripts → Group by topic (Claude API)
       → Summarize at 3 levels → Generate static JSON → Deploy site
```

1. **Daily batch**: Fetches previous day's committee transcripts from NDL
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

Diet records are sourced from the [National Diet Library's Diet Records Search System](https://kokkai.ndl.go.jp/). These records are **not subject to copyright** under Japan's Copyright Act, Article 13.

AI-generated summaries are clearly attributed as such.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

## License

[MIT](./LICENSE)
