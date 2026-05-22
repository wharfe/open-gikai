# OpenGIKAI - Project Instructions for Claude Code

## Project Overview

OpenGIKAI (議会) is a public media project that restructures Japanese parliamentary proceedings into a modern, social-media-like thread format. It supports multiple official sources — NDL (National Diet Library) Diet records, kantei.go.jp Prime Minister press conferences, and government council (審議会) meeting minutes — uses AI to summarize and structure them, and presents them on a static site.

## Tech Stack

- **Frontend**: Next.js 15 (App Router), TypeScript, Tailwind CSS
- **Deployment**: Vercel (SSG - Static Site Generation)
- **Data Pipeline**: Python scripts + Claude API (batch processing), with a SourceAdapter abstraction for multi-source ingestion (`scripts/sources/`)
- **Data Sources**: NDL Diet Records API (`https://kokkai.ndl.go.jp/api/speech`), kantei.go.jp PM press conferences (`https://www.kantei.go.jp/`), cao.go.jp council meeting minutes (`https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html`)

## Key Design Principles

1. **Political neutrality**: All speeches are processed with the same algorithm. No editorial selection. Prompts and logic are open-sourced for transparency.
2. **Source attribution**: Every summary links back to the original NDL transcript URL.
3. **AI transparency**: All AI-generated summaries are labeled as "Claude AI summary".
4. **Three reading levels**: `easy` (simple), `teen` (standard), `adult` (detailed) — each with specific tone and length rules.

## Summary Style Rules (Critical)

Summaries must express the **content of the speech itself**, not report on it.
- **Bad** (reporting style): "Pointed out that the AI definition is too vague and requested comparison with EU."
- **Good** (direct style): "The AI definition is too vague — recommendation engines could fall under regulation. Show the comparison with EU and impact estimates."

## Summary Layer Invariants (Critical for Neutrality)

The summary pipeline (`scripts/summarize.py` + `scripts/pipeline/grouper.py` + `scripts/pipeline/summarizer.py` + `scripts/pipeline/prompts.py`) is the core of OpenGIKAI's political-neutrality guarantee. The following are **non-negotiable**:

1. **Stateless**: No Memory tool, no carrying state across runs. A speech summarized today must produce the same output if re-summarized tomorrow on the same model.
2. **Deterministic**: temperature=0, no agent loops, no tool calls that branch on intermediate results. Same input → same output.
3. **Prompt-only behavior**: All summarization rules live in `prompts.py` and are open-source. No retrieval-augmented prompts, no examples sourced from prior runs.
4. **No conversational UI inside OpenGIKAI's domain**: Chat answers are non-deterministic and break the "Claude AI summary" transparency label. External MCP server (separate Vercel project) is fine because clients bring their own LLM and we expose only deterministic JSON.

### What IS allowed
Auxiliary information layers (news enrichment, members extraction, sitemap generation, OGP image generation) may use LLM/agent patterns. They affect *which* context surfaces alongside a thread, not *what* a speech says. Example: `scripts/pipeline/news_ranker.py` uses Claude with temperature=0 and prompt caching to filter Bing News candidates — that is auxiliary, not summary.

When adding any new Claude-using script, ask yourself: **does this change what a speech is summarized to say?** If yes → must obey the invariants above. If no → reasonable freedom (still keep it deterministic where practical).

## Development Commands

```bash
# Install dependencies
npm install

# Development server
npm run dev

# Build
npm run build

# Lint
npm run lint
```

## Project Structure

```
/                     Project root
├── CLAUDE.md         This file
├── src/              Source code (Next.js App Router)
│   ├── app/          Pages and layouts
│   ├── components/   React components
│   ├── lib/          Utilities and data fetching
│   └── types/        TypeScript type definitions
├── scripts/          Python batch processing scripts
│   └── sources/      Source adapters (NDL, kantei, council, etc.)
├── data/             Generated JSON data (SSG source)
└── public/           Static assets
```

## Git Conventions

- **Conventional Commits**: All commit messages must follow the format:
  - `feat:` new feature
  - `fix:` bug fix
  - `ci:` CI/CD changes
  - `docs:` documentation
  - `refactor:` code restructuring
  - `test:` test additions/changes
  - `chore:` maintenance/deps
- **Issue titles**: English (body can be Japanese)
- **Branch strategy**: Direct to main for now; PRs for larger changes

## Coding Conventions

- Use TypeScript strict mode
- Components use functional style with hooks
- Code comments in English
- User-facing text in Japanese
- Use Tailwind CSS for styling (no CSS modules)
- Data types follow the schema defined in HANDOFF.md (see `Thread`, `Speech`, `Member` types)

## API Notes

- NDL API responses are in Japanese; field names are in camelCase English
- Diet records are public domain (Copyright Act Article 13)
- kantei.go.jp press conferences are scraped from the PM's official website
- Council adapter uses PyMuPDF for PDF text extraction and BeautifulSoup for HTML scraping
- Rate limiting: be respectful of NDL API, kantei.go.jp, and cao.go.jp usage
