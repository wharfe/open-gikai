/**
 * Minimal MCP (Model Context Protocol) server for OpenGIKAI.
 *
 * Implements just enough of the JSON-RPC 2.0 transport and MCP method
 * surface to expose read-only access to the Diet transcript data —
 * specifically: initialize, tools/list, tools/call. No external SDK is
 * required.
 *
 * Audited as part of the project's political-neutrality guarantee:
 * nothing here calls an LLM, applies editorial judgement, or transforms
 * speech data beyond filtering and pagination.
 */

import {
  searchThreads,
  getThreadDetail,
  getMemberDetail,
  listMembersTool,
  listDates,
  serverInfo,
} from "./tools";

// ----- JSON-RPC types -----------------------------------------------------

export type JsonRpcId = string | number | null;

export type JsonRpcRequest = {
  jsonrpc: "2.0";
  id?: JsonRpcId;
  method: string;
  params?: unknown;
};

export type JsonRpcSuccess = {
  jsonrpc: "2.0";
  id: JsonRpcId;
  result: unknown;
};

export type JsonRpcError = {
  jsonrpc: "2.0";
  id: JsonRpcId;
  error: { code: number; message: string; data?: unknown };
};

export type JsonRpcResponse = JsonRpcSuccess | JsonRpcError;

// ----- MCP protocol constants --------------------------------------------

const MCP_PROTOCOL_VERSION = "2024-11-05";

// ----- Tool registry ------------------------------------------------------

type ToolDefinition = {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  handler: (args: Record<string, unknown>) => unknown;
};

const TOOLS: ToolDefinition[] = [
  {
    name: "search_threads",
    description:
      "国会・首相会見・審議会の議論スレッドを検索します。" +
      "キーワード・日付範囲・委員会名・ソース種別でフィルタ可能。" +
      "AI要約済みのスレッド本文・キーワード・採決結果を返します。",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "キーワード検索 (topic, summary, keywords を対象)" },
        date_from: { type: "string", description: "開始日 YYYY-MM-DD" },
        date_until: { type: "string", description: "終了日 YYYY-MM-DD" },
        committee: { type: "string", description: "委員会名の部分一致 (例: 文部科学)" },
        source: {
          type: "string",
          enum: ["ndl", "kantei", "council"],
          description: "ソース種別: ndl=国会会議録, kantei=首相記者会見, council=審議会",
        },
        limit: { type: "integer", default: 20, minimum: 1, maximum: 100 },
      },
    },
    handler: (args) => searchThreads(args),
  },
  {
    name: "get_thread",
    description:
      "スレッドIDで指定された議論スレッドの完全な内容を返します。" +
      "全発言の3段階要約 (easy/teen/adult)、原文引用、tension分類、" +
      "コミットメント、採決結果を含みます。",
    inputSchema: {
      type: "object",
      properties: { id: { type: "string", description: "Thread ID (例: t_20260408_a1b2c3_01)" } },
      required: ["id"],
    },
    handler: (args) => getThreadDetail(args),
  },
  {
    name: "get_member",
    description:
      "議員IDで指定された議員の情報を返します。" +
      "氏名、所属政党、選挙区、肩書、政策スタンス、関連リンクを含みます。",
    inputSchema: {
      type: "object",
      properties: { id: { type: "string", description: "Member ID" } },
      required: ["id"],
    },
    handler: (args) => getMemberDetail(args),
  },
  {
    name: "list_members",
    description: "登録済みの議員一覧を返します。氏名・政党でフィルタ可能。",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string", description: "氏名の部分一致" },
        party: { type: "string", description: "政党名の部分一致" },
        limit: { type: "integer", default: 50, minimum: 1, maximum: 500 },
      },
    },
    handler: (args) => listMembersTool(args),
  },
  {
    name: "list_dates",
    description: "議論データのある日付一覧と各日のスレッド数を返します。",
    inputSchema: { type: "object", properties: {} },
    handler: () => listDates(),
  },
];

// ----- Request dispatch ---------------------------------------------------

const ERR_PARSE = -32700;
const ERR_INVALID_REQUEST = -32600;
const ERR_METHOD_NOT_FOUND = -32601;
const ERR_INVALID_PARAMS = -32602;
const ERR_INTERNAL = -32603;

function rpcError(id: JsonRpcId, code: number, message: string, data?: unknown): JsonRpcError {
  return { jsonrpc: "2.0", id, error: { code, message, ...(data !== undefined ? { data } : {}) } };
}

function rpcOk(id: JsonRpcId, result: unknown): JsonRpcSuccess {
  return { jsonrpc: "2.0", id, result };
}

function handleInitialize(): unknown {
  return {
    protocolVersion: MCP_PROTOCOL_VERSION,
    capabilities: { tools: {} },
    serverInfo: serverInfo(),
  };
}

function handleToolsList(): unknown {
  return {
    tools: TOOLS.map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
    })),
  };
}

function handleToolsCall(params: unknown): unknown {
  if (!params || typeof params !== "object") {
    throw { code: ERR_INVALID_PARAMS, message: "params must be an object" };
  }
  const { name, arguments: rawArgs } = params as { name?: string; arguments?: unknown };
  if (typeof name !== "string") {
    throw { code: ERR_INVALID_PARAMS, message: "tool name is required" };
  }
  const tool = TOOLS.find((t) => t.name === name);
  if (!tool) {
    throw { code: ERR_METHOD_NOT_FOUND, message: `unknown tool: ${name}` };
  }
  const args = (rawArgs && typeof rawArgs === "object" ? rawArgs : {}) as Record<string, unknown>;
  const data = tool.handler(args);
  return {
    content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
  };
}

/**
 * Dispatch a single JSON-RPC request. Returns the response, or null for
 * notifications (no `id`).
 */
export function dispatch(request: JsonRpcRequest): JsonRpcResponse | null {
  if (request.jsonrpc !== "2.0") {
    return rpcError(request.id ?? null, ERR_INVALID_REQUEST, "invalid JSON-RPC version");
  }
  const isNotification = request.id === undefined;
  const id = request.id ?? null;

  try {
    let result: unknown;
    switch (request.method) {
      case "initialize":
        result = handleInitialize();
        break;
      case "notifications/initialized":
        return null;
      case "tools/list":
        result = handleToolsList();
        break;
      case "tools/call":
        result = handleToolsCall(request.params);
        break;
      case "ping":
        result = {};
        break;
      default:
        if (isNotification) return null;
        return rpcError(id, ERR_METHOD_NOT_FOUND, `unknown method: ${request.method}`);
    }
    if (isNotification) return null;
    return rpcOk(id, result);
  } catch (err) {
    if (isNotification) return null;
    if (err && typeof err === "object" && "code" in err && "message" in err) {
      const { code, message, data } = err as { code: number; message: string; data?: unknown };
      return rpcError(id, code, message, data);
    }
    const message = err instanceof Error ? err.message : String(err);
    return rpcError(id, ERR_INTERNAL, message);
  }
}

export function parseRequests(body: string): JsonRpcRequest[] | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return null;
  }
  if (Array.isArray(parsed)) return parsed as JsonRpcRequest[];
  if (parsed && typeof parsed === "object") return [parsed as JsonRpcRequest];
  return null;
}

export function makeParseError(): JsonRpcError {
  return rpcError(null, ERR_PARSE, "Parse error");
}
