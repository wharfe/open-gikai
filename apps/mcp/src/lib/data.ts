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

// Parsing a data file must fail with the file's NAME (#57). A corrupt
// `data/threads/{date}.json` used to reach the Vercel build as a bare
// "Unexpected token ... in JSON", with nothing to say which of ~150 files it
// came from — a build failure nobody can act on in one step.
//
// Deliberately still fatal. The publish chain (validate-data / generate-feeds /
// generate-sitemap) names and SKIPS a broken file so the run can continue, and
// that is right for those; silently skipping here would drop a whole date from
// the site instead, which is a data loss that looks like a successful build.
// Since #57 the pipeline writes these files atomically, so one that is corrupt
// arrived by another route (a hand edit, a bad merge) and wants a person.
function parseJsonFile(filePath: string): unknown {
  const raw = fs.readFileSync(filePath, "utf-8");
  try {
    return JSON.parse(raw);
  } catch (e) {
    const detail = e instanceof Error ? e.message : String(e);
    throw new Error(`Corrupt JSON data file: ${filePath} — ${detail}`);
  }
}

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
    const data = parseJsonFile(path.join(THREADS_DIR, file));
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
  const data = parseJsonFile(MEMBERS_PATH);
  if (data && typeof data === "object" && !Array.isArray(data)) {
    _membersCache = data as Record<string, Member>;
    return _membersCache;
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
