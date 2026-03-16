import type { Member, Thread } from "@/types";
import fs from "fs";
import path from "path";

// Resolve data directories relative to project root
const THREADS_DIR = path.join(process.cwd(), "data", "threads");
const MEMBERS_PATH = path.join(process.cwd(), "data", "members.json");

// Fallback to legacy mock data if pipeline output doesn't exist yet
let _threadsCache: Thread[] | null = null;
let _membersCache: Record<string, Member> | null = null;

function loadThreads(): Thread[] {
  if (_threadsCache) return _threadsCache;

  if (fs.existsSync(THREADS_DIR)) {
    const files = fs
      .readdirSync(THREADS_DIR)
      .filter((f) => f.endsWith(".json") && !f.endsWith(".progress.json"))
      .sort();

    const threads: Thread[] = [];
    for (const file of files) {
      const raw = fs.readFileSync(path.join(THREADS_DIR, file), "utf-8");
      const data = JSON.parse(raw);
      // Pipeline outputs a Thread[] array per date file
      if (Array.isArray(data)) {
        threads.push(...data);
      }
    }

    if (threads.length > 0) {
      _threadsCache = threads;
      return threads;
    }
  }

  // Fallback: legacy mock data
  try {
    const { THREADS } = require("@/data/threads");
    _threadsCache = THREADS;
    return THREADS;
  } catch {
    _threadsCache = [];
    return [];
  }
}

function loadMembers(): Record<string, Member> {
  if (_membersCache) return _membersCache;

  if (fs.existsSync(MEMBERS_PATH)) {
    const raw = fs.readFileSync(MEMBERS_PATH, "utf-8");
    const data = JSON.parse(raw);
    if (data && typeof data === "object" && !Array.isArray(data)) {
      _membersCache = data;
      return data;
    }
  }

  // Fallback: legacy mock data
  try {
    const { MEMBERS } = require("@/data/members");
    _membersCache = MEMBERS;
    return MEMBERS;
  } catch {
    _membersCache = {};
    return {};
  }
}

export function getThreads(): Thread[] {
  return loadThreads();
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

export function getAllThreadIds(): string[] {
  return loadThreads().map((t) => t.id);
}

export function getAllMemberIds(): string[] {
  return Object.keys(loadMembers());
}
