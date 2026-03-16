# OpenGIKAI - Project Instructions for Claude Code

## Project Overview

OpenGIKAI (議会) is a public media project that restructures Japanese parliamentary proceedings (Diet records) into a modern, social-media-like thread format. It fetches official transcripts from the NDL (National Diet Library) API, uses AI to summarize and structure them, and presents them on a static site.

## Tech Stack

- **Frontend**: Next.js 15 (App Router), TypeScript, Tailwind CSS
- **Deployment**: Vercel (SSG - Static Site Generation)
- **Data Pipeline**: Python scripts + Claude API (batch processing)
- **Data Source**: NDL Diet Records API (`https://kokkai.ndl.go.jp/api/speech`)

## Key Design Principles

1. **Political neutrality**: All speeches are processed with the same algorithm. No editorial selection. Prompts and logic are open-sourced for transparency.
2. **Source attribution**: Every summary links back to the original NDL transcript URL.
3. **AI transparency**: All AI-generated summaries are labeled as "Claude AI summary".
4. **Three reading levels**: `easy` (simple), `teen` (standard), `adult` (detailed) — each with specific tone and length rules.

## Summary Style Rules (Critical)

Summaries must express the **content of the speech itself**, not report on it.
- **Bad** (reporting style): "Pointed out that the AI definition is too vague and requested comparison with EU."
- **Good** (direct style): "The AI definition is too vague — recommendation engines could fall under regulation. Show the comparison with EU and impact estimates."

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
├── data/             Generated JSON data (SSG source)
└── public/           Static assets
```

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
- Rate limiting: be respectful of NDL API usage
