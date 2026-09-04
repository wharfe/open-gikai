# OpenGIKAI MCP server

Read-only [Model Context Protocol](https://modelcontextprotocol.io) server
that exposes OpenGIKAI's Japanese Diet transcript data to MCP-capable
clients (Claude Desktop, Cline, custom agents, etc.).

## Why it lives in a separate Next.js project

The OpenGIKAI frontend (repo root) is configured for full static export
(`output: "export"` in `next.config.ts`), which means it has no server
runtime and cannot host dynamic API routes. The MCP server needs to handle
POSTed JSON-RPC requests at runtime, so it ships as its own Next.js app
under `apps/mcp/` and is deployed as a separate Vercel project pointing at
the same GitHub repository.

Both projects serve the same `data/threads/*.json` and `data/members.json`,
so the daily-batch pipeline does not need to know the MCP server exists. The
frontend reads them from the repo root; this project gets a build-time copy
under `apps/mcp/data/`, because a serverless function bundle cannot reach
above its own project root (see Deployment below).

## Endpoint

- `POST /api/mcp` — JSON-RPC 2.0 over HTTP (the MCP "Streamable HTTP" transport).
- `GET /api/mcp` — discovery: returns `serverInfo`, `protocolVersion`, and the tool list.

## Tools

| name | description |
|------|-------------|
| `search_threads` | キーワード・日付範囲・委員会・ソースでスレッドを検索 |
| `get_thread` | スレッドの完全な内容 (全発言の3段階要約、原文引用、tension分類、採決) |
| `get_member` | 議員プロフィール |
| `list_members` | 議員一覧 (氏名・政党フィルタ可) |
| `list_dates` | データのある日付と各日のスレッド数 |

## Local development

```bash
cd apps/mcp
npm install
npm run dev   # serves on http://localhost:3100
```

Smoke test (assumes you ran `npm install` once at the repo root so the
`data/` directory has content):

```bash
# Discovery
curl http://localhost:3100/api/mcp

# initialize + tools/list
curl -X POST http://localhost:3100/api/mcp \
  -H 'Content-Type: application/json' \
  -d '[
    {"jsonrpc":"2.0","id":1,"method":"initialize"},
    {"jsonrpc":"2.0","id":2,"method":"tools/list"}
  ]'

# Search
curl -X POST http://localhost:3100/api/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_threads","arguments":{"query":"AI規制","limit":3}}}'
```

## Vercel deployment

This package is intentionally NOT covered by the root project's Vercel
configuration. Create a *second* Vercel project pointing at the same
GitHub repository, with these settings:

- **Root Directory**: `apps/mcp`
- **Build Command**: `next build` (default)
- **Output Directory**: leave default
- **Install Command**: leave default
- **Node.js Version**: 22 or higher
- **Production Branch**: `main`

Data reaches the function bundle through the `prebuild` script
(`scripts/copy-data.mjs`), which copies `data/threads/` and `data/members.json`
from the repo root into `apps/mcp/data/`. We deliberately do **not** use
`outputFileTracingRoot` pointing above the project root: Vercel double-prefixes
such absolute paths during deploy.

Two consequences worth knowing before a deploy:

- Running `vercel` from the CLI uploads `apps/mcp` alone, so the repo-root
  `data/` is not there to copy from. The prebuild then reuses the
  `apps/mcp/data/` in your upload — but only if it verifies against the
  `.bundle-manifest.json` inside it (#73). **Run the prebuild locally, with the
  repo-root `data/` present, before a CLI deploy.** A bundle that does not
  verify fails the build rather than shipping short.
- The same applies after any change to `MANIFEST_VERSION`: bundles written by
  the older script are refused, so the next deploy has to be built where the
  repo-root `data/` exists.

### Where this project's server actually answers

`https://open-gikai-mcp.vercel.app` — that is the address today, and it is the
one `scripts/check_mcp_freshness.py` asks every morning.

**No custom domain is attached yet.** `mcp.open-gikai.net` appears nowhere in
DNS; it was written here as a plan and read since as a fact. Attaching one is
still the right end state, because a Vercel host name published as a public API
window cannot be moved later without breaking every client config that copied
it. Two things have to happen together when it is:

1. the DNS record and the Vercel domain assignment (neither is in this repo), and
2. `DEFAULT_URL` in `scripts/check_mcp_freshness.py`, moved over in the same
   change as the example below.

Leaving the freshness check on the old host would keep it green against an
address nobody uses — which is the shape of failure #85 was about.

## Configuring Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "open-gikai": {
      "url": "https://open-gikai-mcp.vercel.app/api/mcp"
    }
  }
}
```

(Running your own copy? Use whatever domain you attached to your own Vercel
deployment instead.)

## Costs

OpenGIKAI does NOT pay for LLM inference triggered through this server —
the MCP client is the one calling Claude (or any other LLM). The server
itself just returns JSON. On Vercel Hobby, traffic from a handful of
clients sits well within free-tier limits.

## Political neutrality

Like everything else in OpenGIKAI, the MCP server is deterministic and
auditable:

- No LLM call inside the request path.
- Tool handlers are pure functions over the static thread/member JSON.
- The pipeline that produced those summaries (Claude API + open-source
  prompts) is documented at the repo root.
- Every response includes an `attribution` block making clear that
  summaries are Claude-generated derivative works over the public-domain
  NDL transcripts.
