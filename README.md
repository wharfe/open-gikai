# OpenGIKAI — Opening Up Parliament

**[open-gikai.net](https://open-gikai.net)** | [🇯🇵 日本語版はこちら / Japanese](./README.ja.md)

**OpenGIKAI** (議会) is an open-source public media project that transforms Japanese parliamentary proceedings into a modern, accessible thread format — like social media, but with official sources. It ingests multiple sources including Diet records (NDL), Prime Minister press conferences (kantei.go.jp), and government council meeting minutes (審議会).

## What It Does

- Fetches official transcripts from multiple sources: [NDL Diet Records API](https://kokkai.ndl.go.jp/api.html), [kantei.go.jp](https://www.kantei.go.jp/) press conferences, and [government council](https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html) meeting minutes
- Uses AI (Claude) to summarize and structure speeches by topic
- Links each thread to related news articles with image previews (Bing News)
- Presents them in a thread-based UI with three reading levels:
  - 🌱 **Easy** — Simple language for everyone
  - 📖 **Standard** — Balanced detail with brief explanations
  - 📰 **Detailed** — Full political context, news-style

## Why

Parliamentary records are public but hard to read. OpenGIKAI makes them accessible without editorializing — every summary links back to the original transcript. The AI prompts and processing logic are fully open-source to ensure transparency and political neutrality.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind CSS |
| Deployment | Vercel — two projects from one repo: SSG frontend at the root, dynamic MCP server at `apps/mcp/` |
| Data Pipeline | Python + Claude API (Message Batches API + prompt caching) |
| Data Sources | [NDL Diet Records API](https://kokkai.ndl.go.jp/api.html), [kantei.go.jp](https://www.kantei.go.jp/), [cao.go.jp councils](https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html) |
| Public API | Read-only [MCP server](./apps/mcp/README.md) for Claude Desktop / Cline / custom agents |

## Getting Started

```bash
# Clone the repository
git clone https://github.com/wharfe/open-gikai.git
cd open-gikai

# Install frontend dependencies
npm install

# Start the frontend dev server
npm run dev
```

The MCP server is a separate Next.js project under `apps/mcp/` with its own dependencies:

```bash
cd apps/mcp
npm install
npm run dev   # serves on http://localhost:3100
```

See [`apps/mcp/README.md`](./apps/mcp/README.md) for MCP deployment details.

## Project Structure

```
├── src/                  # Frontend (Next.js SSG — output: "export")
│   ├── app/              # App Router pages
│   ├── components/       # React components
│   ├── lib/              # Utilities and data fetching
│   └── types/            # TypeScript type definitions
├── apps/
│   └── mcp/              # MCP server (separate Vercel project, dynamic Node runtime)
├── scripts/              # Python batch processing
│   ├── sources/          # Source adapters (NDL, kantei, council, …)
│   └── pipeline/         # AI pipeline (grouping, summarization, news ranker)
├── data/                 # Generated JSON consumed by both frontend SSG and MCP server
│   ├── threads/          # Per-date thread files
│   └── members.json      # Accumulated Diet member registry
├── public/               # Static assets (incl. sitemap, RSS feed)
└── .github/workflows/    # daily-batch.yml (6:00 AM JST cron)
```

The frontend uses `output: "export"`, so anything requiring a Node runtime (Route Handlers, dynamic APIs) lives under `apps/`.

## How It Works

```
Sources (NDL, kantei, council)
   ├─► fetch (sliding 30-day window per run)
   │
   ├─► group by topic                  ┐
   ├─► classify tension                │  Claude API
   ├─► summarize at 3 levels (Batches) │  + prompt caching
   ├─► extract commitments / outcomes  ┘
   │
   ├─► enrich with related news (Bing News + Claude relevance ranker)
   │
   └─► generate JSON
         ├─► frontend SSG  → open-gikai.net (Vercel)
         └─► MCP server     → /api/mcp       (Vercel, apps/mcp)
```

1. **Sliding-window fetch**: Each run re-fetches the last 30 days from every source. NDL publishes transcripts with a multi-day to multi-week lag, so a yesterday-only fetch silently misses retroactively-published meetings.
2. **AI processing**:
   - **Grouping** — sync call per meeting; clusters speeches into thematic threads.
   - **Summarization** — Message Batches API per thread (50% cost discount, stackable with prompt caching for ~90% input savings on the cached prefix).
   - **Outcome extraction** — sync call per meeting; reads procedural speeches for vote results / attached resolutions.
3. **News enrichment**: Searches Bing News by topic, then a Claude Haiku ranker (`scripts/pipeline/news_ranker.py`) picks the most-relevant 3 articles from the candidate pool. Auxiliary information layer — see CLAUDE.md "Summary Layer Invariants" for the boundary.
4. **Static generation**: `data/threads/*.json` and `data/members.json` are consumed by the Next.js SSG to produce static HTML pages.
5. **Deployment**: Two Vercel projects pointing at the same repo — root (SSG frontend) and `apps/mcp` (dynamic MCP server).
6. **Monitoring**: Daily batch commits include `(+N threads)` in the message; the workflow emits a CI warning when 7+ consecutive runs add 0 threads (catches fetcher regressions that the green checkmark alone wouldn't).

## Data Pipeline

```bash
# 1. Fetch speeches across a sliding window (30 days catches retroactive NDL uploads)
python scripts/fetch_ndl.py     --lookback-days 30
python scripts/fetch_kantei.py  --lookback-days 30
python scripts/fetch_council.py --lookback-days 30 --council kisei
# (... repeat per council, see daily-batch.yml for the full list)

# 2. Summarize via Message Batches API (auto-resumes against existing data/threads/)
#    Requires ANTHROPIC_API_KEY in .env.
python scripts/summarize.py --date 2026-04-22 --batch

# 3. Enrich with news, filtered through Claude relevance ranker
python scripts/enrich-news.py --date 2026-04-22 --rank-with-claude

# 4. Generate sitemaps, feeds, and validation; build the SSG
node scripts/validate-data.mjs --fix
node scripts/generate-feeds.js
node scripts/generate-sitemap.mjs
npm run build && npx serve out
```

See `.env.example` for configuration. The daily batch workflow at `.github/workflows/daily-batch.yml` runs all of these in sequence at 6:00 JST.

## MCP Server

OpenGIKAI exposes its dataset as a read-only [Model Context Protocol](https://modelcontextprotocol.io) server so Claude Desktop, Cline, or any MCP-capable agent can query Diet discussions directly.

| Tool | Purpose |
|------|---------|
| `search_threads` | Find threads by keyword, date range, committee, or source |
| `get_thread` | Fetch a full thread with 3-level summaries, original quotes, tension classifications, and outcomes |
| `get_member` | Diet member profile |
| `list_members` | Paginate members, filter by name/party |
| `list_dates` | Index of dates with available threads |

The server lives in `apps/mcp/` and is deployed as a second Vercel project from the same repository. OpenGIKAI does NOT pay for LLM inference — the MCP client (Claude Desktop, etc.) calls Claude with its own API key, and the server only returns JSON. See [`apps/mcp/README.md`](./apps/mcp/README.md) for the endpoint URL and Claude Desktop configuration snippet.

## Design Principles

- **Political neutrality by design** — All speeches processed with identical algorithms. No editorial selection. Prompts are open-source. See "Summary Layer Invariants" in [`CLAUDE.md`](./CLAUDE.md) for the non-negotiable rules (stateless, deterministic, prompt-only) and the boundary between the summary layer and auxiliary layers (news enrichment, MCP server) where LLM/agent patterns are allowed.
- **Source transparency** — Every summary links to the original NDL/kantei/council transcript.
- **AI transparency** — All AI-generated content is clearly labeled. MCP responses include an `attribution` block making this explicit for downstream agents.
- **Accessibility** — Three reading levels make Diet proceedings approachable for everyone.

## Data Source

Diet records are sourced from the [National Diet Library's Diet Records Search System](https://kokkai.ndl.go.jp/). These records are **not subject to copyright** under Japan's Copyright Act, Article 13. Press conference transcripts are sourced from [kantei.go.jp](https://www.kantei.go.jp/). Government council meeting minutes (審議会) are sourced from [cao.go.jp](https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html) and other ministry websites.

AI-generated summaries are clearly attributed as such. Related news articles are linked from Bing News RSS; only URLs, source names, publication dates, and OGP preview images are displayed — no article content is reproduced.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

## License

[MIT](./LICENSE)
