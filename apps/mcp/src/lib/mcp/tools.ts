/**
 * MCP tool implementations for OpenGIKAI.
 *
 * Each tool is a deterministic read-only view over the static thread/member
 * data. No LLM calls, no scoring heuristics — what the daily batch wrote
 * is what these tools return. This is part of the project's
 * political-neutrality guarantee.
 */

import { getThreads, getThread, getMembers, getMember } from "@/lib/data";
import type { Thread, Member } from "@/types";

// ---------------------------------------------------------------------------
// Argument coercion — MCP clients may send arguments as JSON strings.
// ---------------------------------------------------------------------------

function asString(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

function asInt(v: unknown, fallback: number, min: number, max: number): number {
  let n: number;
  if (typeof v === "number") n = v;
  else if (typeof v === "string" && /^\d+$/.test(v)) n = parseInt(v, 10);
  else return fallback;
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(n)));
}

// Thread.date is stored as "YYYY.MM.DD"; normalize for comparison.
function normalizeDate(d: string): string {
  return d.replace(/\./g, "-");
}

// ---------------------------------------------------------------------------
// Search / detail
// ---------------------------------------------------------------------------

type SearchResultThread = {
  id: string;
  date: string;
  committee: string;
  house: string;
  topic: string;
  topicTag: string;
  summary: string;
  source?: string;
  sourceLabel?: string;
  speechCount: number;
  speakers: string[];
  keywords: string[];
  outcome?: Thread["outcome"];
  sourceUrls: string[];
};

function projectThreadForSearch(thread: Thread, members: Record<string, Member>): SearchResultThread {
  const speakerIds = [...new Set(thread.speeches.map((s) => s.memberId))];
  const speakers = speakerIds.map((id) => members[id]?.name ?? id);
  const keywordCounts = new Map<string, number>();
  for (const s of thread.speeches) {
    for (const k of s.keywords ?? []) {
      keywordCounts.set(k, (keywordCounts.get(k) ?? 0) + 1);
    }
  }
  const keywords = [...keywordCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([k]) => k);

  const sourceUrls = [
    ...new Set(thread.speeches.map((s) => s.sourceUrl).filter(Boolean)),
  ] as string[];

  return {
    id: thread.id,
    date: thread.date,
    committee: thread.committee,
    house: thread.house,
    topic: thread.topic,
    topicTag: thread.topicTag,
    summary: thread.summary,
    source: thread.source,
    sourceLabel: thread.sourceLabel,
    speechCount: thread.speeches.length,
    speakers,
    keywords,
    outcome: thread.outcome,
    sourceUrls,
  };
}

export function searchThreads(rawArgs: Record<string, unknown>): unknown {
  const query = asString(rawArgs.query)?.toLowerCase();
  const dateFrom = asString(rawArgs.date_from);
  const dateUntil = asString(rawArgs.date_until);
  const committee = asString(rawArgs.committee);
  const source = asString(rawArgs.source);
  const limit = asInt(rawArgs.limit, 20, 1, 100);

  const threads = getThreads();
  const members = getMembers();

  const matches: Thread[] = [];
  for (const t of threads) {
    const tDate = normalizeDate(t.date);
    if (dateFrom && tDate < dateFrom) continue;
    if (dateUntil && tDate > dateUntil) continue;
    if (committee && !t.committee.includes(committee)) continue;
    if (source && t.source !== source) continue;
    if (query) {
      const haystack = (
        t.topic +
        " " +
        t.summary +
        " " +
        t.committee +
        " " +
        t.speeches.flatMap((s) => s.keywords ?? []).join(" ")
      ).toLowerCase();
      if (!haystack.includes(query)) continue;
    }
    matches.push(t);
    if (matches.length >= limit) break;
  }

  return {
    total: matches.length,
    truncated: matches.length === limit,
    threads: matches.map((t) => projectThreadForSearch(t, members)),
    attribution: {
      summarized_by: "Claude AI summary",
      source_disclosure: "原文は各 thread の sourceUrls を参照してください",
      license_note:
        "国会会議録は著作権法13条により公有財産。要約はOpenGIKAIによる第三者派生物。",
    },
  };
}

export function getThreadDetail(rawArgs: Record<string, unknown>): unknown {
  const id = asString(rawArgs.id);
  if (!id) {
    throw { code: -32602, message: "id is required" };
  }
  const thread = getThread(id);
  if (!thread) {
    throw { code: -32602, message: `thread not found: ${id}` };
  }
  const members = getMembers();

  return {
    thread: {
      ...thread,
      speeches: thread.speeches.map((s) => ({
        ...s,
        member: members[s.memberId]
          ? {
              id: members[s.memberId].id,
              name: members[s.memberId].name,
              party: members[s.memberId].party,
              role: members[s.memberId].role,
            }
          : null,
      })),
    },
    attribution: {
      summarized_by: "Claude AI summary",
      summary_levels: "easy=やさしく / teen=標準 / adult=詳しく",
      original_text_field: "speeches[].raw",
      source_url_field: "speeches[].sourceUrl",
      license_note:
        "国会会議録は著作権法13条により公有財産。要約はOpenGIKAIによる第三者派生物。",
    },
  };
}

// ---------------------------------------------------------------------------
// Members
// ---------------------------------------------------------------------------

const SITE_ORIGIN = "https://open-gikai.net";

/**
 * A member's `links` may hold site-relative URLs (/gov/{slug}) because the web
 * UI renders those as internal pages. An MCP client is a different LLM on a
 * different host and cannot resolve them, so they leave here absolute.
 * Absolute URLs are returned untouched.
 */
function withAbsoluteLinks<T extends { links?: { label: string; url: string }[] }>(member: T): T {
  if (!member.links?.length) return member;
  return {
    ...member,
    links: member.links.map((link) =>
      link.url.startsWith("/") ? { ...link, url: SITE_ORIGIN + link.url } : link,
    ),
  };
}

export function getMemberDetail(rawArgs: Record<string, unknown>): unknown {
  const id = asString(rawArgs.id);
  if (!id) {
    throw { code: -32602, message: "id is required" };
  }
  const member = getMember(id);
  if (!member) {
    throw { code: -32602, message: `member not found: ${id}` };
  }
  return { member: withAbsoluteLinks(member) };
}

export function listMembersTool(rawArgs: Record<string, unknown>): unknown {
  const nameQuery = asString(rawArgs.name)?.toLowerCase();
  const partyQuery = asString(rawArgs.party)?.toLowerCase();
  const limit = asInt(rawArgs.limit, 50, 1, 500);

  const members = Object.values(getMembers());
  const filtered: Member[] = [];
  for (const m of members) {
    if (nameQuery && !m.name.toLowerCase().includes(nameQuery)) continue;
    if (partyQuery && !(m.party ?? "").toLowerCase().includes(partyQuery)) continue;
    filtered.push(m);
    if (filtered.length >= limit) break;
  }
  return {
    total: filtered.length,
    truncated: filtered.length === limit,
    members: filtered.map(withAbsoluteLinks),
  };
}

// ---------------------------------------------------------------------------
// Dates index
// ---------------------------------------------------------------------------

export function listDates(): unknown {
  const counts = new Map<string, number>();
  for (const t of getThreads()) {
    const d = normalizeDate(t.date);
    counts.set(d, (counts.get(d) ?? 0) + 1);
  }
  const dates = [...counts.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([date, threads]) => ({ date, threads }));
  return { count: dates.length, dates };
}

// ---------------------------------------------------------------------------
// Server metadata
// ---------------------------------------------------------------------------

export function serverInfo(): unknown {
  return {
    name: "open-gikai-mcp",
    version: "0.1.0",
    title: "OpenGIKAI 議事録 MCP server",
    description:
      "国会・首相会見・審議会の議論データへの読み取り専用アクセスを提供します。" +
      "全データは静的SSGの基となるJSONであり、要約はClaude AIによる第三者派生物です。" +
      "https://github.com/wharfe/open-gikai",
  };
}
