# OpenGIKAI - Project Instructions for Claude Code

## Project Overview

OpenGIKAI (議会) is a public media project that restructures Japanese parliamentary proceedings into a modern, social-media-like thread format. It supports multiple official sources — NDL (National Diet Library) Diet records, kantei.go.jp Prime Minister press conferences, and government council (審議会) meeting minutes — uses AI to summarize and structure them, and presents them on a static site.

## Tech Stack

- **Frontend**: Next.js 16 (App Router), TypeScript, Tailwind CSS. `output: "export"` — fully static, no server runtime at the root.
- **Deployment**: Two Vercel projects from one repo. Root → SSG frontend at `open-gikai.net`. `apps/mcp/` → dynamic MCP server.
- **Data Pipeline**: Python scripts + Claude API
  - Sliding 30-day lookback per run (NDL publishes transcripts with multi-day lag — see `scripts/fetch_ndl.py --lookback-days`).
  - `summarize.py --batch` uses Anthropic's Message Batches API for the summary phase (50% input/output discount).
  - System+user prompt instructions cached with `cache_control: ephemeral` (`scripts/pipeline/prompts.py` + `summarizer.py` / `grouper.py`).
  - `scripts/pipeline/news_ranker.py` (Haiku 4.5) filters Bing News candidates — auxiliary layer only.
  - SourceAdapter abstraction lives in `scripts/sources/`.
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
   `temperature=0` must be **sent explicitly** on every summary/grouping/outcome request — omitting it runs at
   the API default (this was #47: an identical re-request produced 8,527 then 9,603 output tokens).
   Every request goes through one of three builders — `grouper.build_grouping_request` /
   `build_outcome_request` and `summarizer.build_summary_request` — and the synchronous paths reuse the same
   message builders, so the daily run and a manual recovery run send byte-identical prompts. Build a request
   by hand and that guarantee is gone. `scripts/tests/test_determinism.py` guards both halves: it calls the
   builders for real values, and separately AST-sweeps every summary-layer module for a hand-rolled request
   that bypassed them. The recurring failure is a sibling script drifting from the rule, so it also *imports*
   `batch.py` / `bulk_batch.py` — they had been dead on import for months while a source-reading test
   reported them compliant.
3. **Prompt-only behavior**: All summarization rules live in `prompts.py` and are open-source. No retrieval-augmented prompts, no examples sourced from prior runs.
4. **No conversational UI inside OpenGIKAI's domain**: Chat answers are non-deterministic and break the "Claude AI summary" transparency label. External MCP server (separate Vercel project) is fine because clients bring their own LLM and we expose only deterministic JSON.

### What IS allowed
Auxiliary information layers (news enrichment, members extraction, sitemap generation, OGP image generation, ministry hub pages, lex-diff cross-links) may use LLM/agent patterns. They affect *which* context surfaces alongside a thread, not *what* a speech says. Example: `scripts/pipeline/news_ranker.py` uses Claude with temperature=0 and prompt caching to filter Bing News candidates — that is auxiliary, not summary.

`src/lib/ministry.mjs` is a related auxiliary module: it deterministically maps a government-witness (政府参考人) member to a ministry from their `role` string (no LLM), powering the `/gov` hub pages, member-page breadcrumbs, sitemap-gov, and IndexNow. It is **plain ESM (+ `ministry.d.mts`)** rather than TS so the node-run build scripts (`scripts/generate-sitemap.mjs`, `scripts/notify-indexnow.mjs`) can import it — the repo has no tsx/ts-node. Politicians are excluded inside its API (m_-prefixed IDs only + political-title blocklist; `rank` is NOT used — it misclassifies bureaucrats). `data/lexdiff-mapping.json` (outbound law cross-links, consumed by `summarize.py`) is similarly auxiliary.

`scripts/pipeline/batch_state.py` is auxiliary persistence, not summary logic: it records an in-flight summary batch's id + grouping manifest (with per-thread `input_hash`) to a committed sidecar (`data/pending-batches/{date}.json`) so a timed-out batch resumes on a later run and assembles **without re-grouping** — same input → same output, so it upholds the invariants above rather than affecting them. The hash covers the content params only (`max_tokens` is excluded via `HASH_EXCLUDED_PARAMS`): a ceiling cannot change a response that fit, and hashing it made re-issuing a truncated request impossible. Anything that steers generation must stay hashed. **Bump `SCHEMA_VERSION` whenever `compute_input_hash` or the set of params fed to it changes** — pinning `temperature` took it to v3, since v2 hashes cover a param set ours no longer matches. `test_summary_request_param_set_is_pinned` fails when the param set moves, because forgetting the bump does not surface as a version error: it surfaces as per-thread `input_hash mismatch — raw/prompt changed`, which sends the investigator to the raw data while the retry budget burns down to permanent loss.

`is_current_schema` compares for equality, so a version change is a refusal in **both** directions: merging one while a sidecar is in flight, or reverting one after a sidecar was written, hard-fails `--collect-pending` and therefore skips the whole Summarize step every day until someone removes the file by hand — and the batch's results expire in ~29 days. **Land a `SCHEMA_VERSION` change only when `git ls-files data/pending-batches/` is empty.**

When adding any new Claude-using script, ask yourself: **does this change what a speech is summarized to say?** If yes → must obey the invariants above. If no → reasonable freedom (still keep it deterministic where practical).

## Development Commands

```bash
# --- Frontend (repo root) ---
npm install
npm run dev      # localhost:3000
npm run build    # static export → out/
npm run lint

# --- MCP server (apps/mcp) ---
cd apps/mcp
npm install
npm run dev      # localhost:3100
npm run build    # prebuild script copies data/ from repo root

# --- Data pipeline (Python) ---
# Daily-batch.yml runs these in sequence each morning. To replay locally:
python scripts/fetch_ndl.py --lookback-days 30
python scripts/summarize.py --date YYYY-MM-DD --batch
python scripts/enrich-news.py --date YYYY-MM-DD --rank-with-claude
```

## Project Structure

```
/                     Project root (frontend SSG project — `output: "export"`)
├── CLAUDE.md         This file
├── src/              Source code (Next.js App Router)
│   ├── app/          Pages and layouts (incl. /m member pages, /gov ministry hubs)
│   ├── components/   React components
│   ├── lib/          Utilities and data fetching (incl. ministry.mjs — see below)
│   └── types/        TypeScript type definitions
├── apps/
│   └── mcp/          MCP server (separate Vercel project, dynamic Node runtime)
├── scripts/          Python batch processing scripts
│   └── sources/      Source adapters (NDL, kantei, council, etc.)
├── data/             Generated JSON data (consumed by both frontend SSG and MCP server)
└── public/           Static assets
```

The repo hosts **two Vercel projects** pointing at the same GitHub repo:

- Root → frontend SSG (open-gikai.net). `next.config.ts` sets `output: "export"`.
- `apps/mcp` → dynamic MCP server. A `prebuild` script
  (`apps/mcp/scripts/copy-data.mjs`) copies `data/threads/` and
  `data/members.json` from the repo root into `apps/mcp/data/` so they ship
  with the serverless function bundle. The runtime reads from
  `process.cwd()/data` — see `apps/mcp/src/lib/data.ts`. We deliberately
  avoid `outputFileTracingRoot` pointing above the project root because
  Vercel double-prefixes such absolute paths during deploy.

Because the frontend uses static export, **server-side features (Route
Handlers, dynamic API routes, middleware) cannot be added under `src/app/`**.
Anything requiring a runtime belongs under `apps/`.

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

## 品質ゲート（Gate3 — 実装完了時の標準フロー）

- 非自明な変更（複数ファイル・設計判断・データ取得アダプタ / ビルドパイプラインに触れる変更）の
  完了時は `/code-gate` を実行し **critical 0 まで**ループする。標準起動は headless `/goal` 経由:
  ```bash
  timeout 3600 claude -p "/goal /code-gate を実行し critical 0 を達成する。5回で打ち切り" \
    --permission-mode acceptEdits
  ```
- 受け入れ基準（機械判定）: `npm run lint && npm run validate`。
  `scripts/` の Python を触ったら `python -m pytest scripts/tests`（resume/batch_state の
  ユニットテストがある — フロントには無い）。表示に影響する変更は `npm run test:e2e` を追加実行。
- 単発の小修正（数十行以下）は Gate 省略可。ただしスクレイパーのレート制御・公開データの
  生成ロジックに触れる diff はサイズによらず Gate 対象。
