/**
 * MCP (Model Context Protocol) server endpoint for OpenGIKAI.
 *
 * Implements the MCP "Streamable HTTP" transport: clients POST one or more
 * JSON-RPC requests, the server returns a JSON-RPC response (or a batched
 * array). We do not implement Server-Sent Events here — every method we
 * expose is short, synchronous, and request/response shaped.
 *
 * Read-only by design. Tools never call an LLM. All data is the same JSON
 * the static site already publishes.
 */

import { NextResponse } from "next/server";
import {
  dispatch,
  parseRequests,
  makeParseError,
  type JsonRpcRequest,
  type JsonRpcResponse,
} from "@/lib/mcp/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CORS_HEADERS: HeadersInit = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers":
    "Content-Type, Mcp-Session-Id, Mcp-Protocol-Version",
  "Access-Control-Max-Age": "86400",
};

function jsonResponse(body: unknown, status = 200): NextResponse {
  return NextResponse.json(body, { status, headers: CORS_HEADERS });
}

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
}

export async function GET() {
  // Helpful discovery endpoint for humans browsing the URL. Returns server
  // info and the tool list without requiring a JSON-RPC roundtrip.
  const initResponse = dispatch({ jsonrpc: "2.0", id: 0, method: "initialize" });
  const toolsResponse = dispatch({ jsonrpc: "2.0", id: 1, method: "tools/list" });
  const initResult = initResponse && "result" in initResponse ? initResponse.result : null;
  const toolsResult = toolsResponse && "result" in toolsResponse ? toolsResponse.result : null;
  return jsonResponse({
    note:
      "This is an MCP server. POST a JSON-RPC 2.0 payload to this URL " +
      "or configure your MCP client to use this endpoint as 'streamable HTTP'.",
    documentation: "https://github.com/wharfe/open-gikai#mcp-server",
    serverInfo: (initResult as { serverInfo?: unknown })?.serverInfo,
    protocolVersion: (initResult as { protocolVersion?: unknown })?.protocolVersion,
    tools: (toolsResult as { tools?: unknown })?.tools,
  });
}

export async function POST(request: Request): Promise<NextResponse> {
  let body: string;
  try {
    body = await request.text();
  } catch {
    return jsonResponse(makeParseError(), 400);
  }
  if (!body) {
    return jsonResponse(makeParseError(), 400);
  }

  const requests = parseRequests(body);
  if (!requests) {
    return jsonResponse(makeParseError(), 400);
  }

  const isBatch = body.trim().startsWith("[");
  const responses: JsonRpcResponse[] = [];
  for (const req of requests) {
    const res = dispatch(req as JsonRpcRequest);
    if (res !== null) responses.push(res);
  }

  if (responses.length === 0) {
    return new NextResponse(null, { status: 204, headers: CORS_HEADERS });
  }
  return jsonResponse(isBatch ? responses : responses[0]);
}
