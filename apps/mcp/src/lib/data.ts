/**
 * Data loader for the MCP server.
 *
 * Reads data from a local apps/mcp/data/ directory that the build step
 * (scripts/copy-data.mjs, wired via prebuild/predev npm hooks) populates
 * from the repo-root data/. We use a local copy rather than reaching
 * across the monorepo so the Vercel serverless function bundle is
 * self-contained and so Next.js' file tracing does not need to look
 * above the project root — that path-resolves incorrectly on Vercel.
 */

import fs from "fs";
import path from "path";
import type { Member, Thread } from "@/types";

const DATA_DIR = path.join(process.cwd(), "data");
const THREADS_DIR = path.join(DATA_DIR, "threads");
const MEMBERS_PATH = path.join(DATA_DIR, "members.json");

let _threadsCache: Thread[] | null = null;
let _membersCache: Record<string, Member> | null = null;

function loadThreads(): Thread[] {
  if (_threadsCache) return _threadsCache;
  if (!fs.existsSync(THREADS_DIR)) {
    _threadsCache = [];
    return _threadsCache;
  }

  const files = fs
    .readdirSync(THREADS_DIR)
    .filter((f) => f.endsWith(".json") && !f.endsWith(".progress.json"))
    .sort();

  const threads: Thread[] = [];
  for (const file of files) {
    const raw = fs.readFileSync(path.join(THREADS_DIR, file), "utf-8");
    const data = JSON.parse(raw);
    if (Array.isArray(data)) threads.push(...data);
  }
  _threadsCache = threads;
  return threads;
}

function loadMembers(): Record<string, Member> {
  if (_membersCache) return _membersCache;
  if (!fs.existsSync(MEMBERS_PATH)) {
    _membersCache = {};
    return _membersCache;
  }
  const raw = fs.readFileSync(MEMBERS_PATH, "utf-8");
  const data = JSON.parse(raw);
  if (data && typeof data === "object" && !Array.isArray(data)) {
    _membersCache = data;
    return data;
  }
  _membersCache = {};
  return _membersCache;
}

export function getThreads(): Thread[] {
  return loadThreads().sort((a, b) => b.date.localeCompare(a.date));
}

export function getThread(id: string): Thread | undefined {
  return loadThreads().find((t) => t.id === id);
}

export function getMembers(): Record<string, Member> {
  return loadMembers();
}

export function getMember(id: string): Member | undefined {
  return loadMembers()[id];
}
