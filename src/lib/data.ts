import type { Member, Thread } from "@/types";
import fs from "fs";
import path from "path";

const THREADS_DIR = path.join(process.cwd(), "data", "threads");
const MEMBERS_PATH = path.join(process.cwd(), "data", "members.json");

function loadThreads(): Thread[] {
  if (!fs.existsSync(THREADS_DIR)) return [];

  const files = fs
    .readdirSync(THREADS_DIR)
    .filter((f) => f.endsWith(".json") && !f.endsWith(".progress.json"))
    .sort();

  const threads: Thread[] = [];
  for (const file of files) {
    const raw = fs.readFileSync(path.join(THREADS_DIR, file), "utf-8");
    const data = JSON.parse(raw);
    if (Array.isArray(data)) {
      threads.push(...data);
    }
  }
  return threads;
}

function loadMembers(): Record<string, Member> {
  if (!fs.existsSync(MEMBERS_PATH)) return {};

  const raw = fs.readFileSync(MEMBERS_PATH, "utf-8");
  const data = JSON.parse(raw);
  if (data && typeof data === "object" && !Array.isArray(data)) {
    return data;
  }
  return {};
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

export function getAllThreadIds(): string[] {
  return loadThreads().map((t) => t.id);
}

export function getAllMemberIds(): string[] {
  return Object.keys(loadMembers());
}

export type SearchEntry = {
  threadId: string;
  topic: string;
  committee: string;
  house: string;
  date: string;
  summary: string;
  topicTag: string;
  keywords: string[];
  speakers: string[];
};

export function getSearchIndex(): SearchEntry[] {
  const threads = loadThreads();
  const members = loadMembers();

  return threads.map((t) => {
    const speakerIds = [...new Set(t.speeches.map((s) => s.memberId))];
    const speakers = speakerIds.map((id) => members[id]?.name || "").filter(Boolean);
    const keywords = [...new Set(t.speeches.flatMap((s) => s.keywords))];

    return {
      threadId: t.id,
      topic: t.topic,
      committee: t.committee,
      house: t.house,
      date: t.date,
      summary: t.summary,
      topicTag: t.topicTag,
      keywords,
      speakers,
    };
  });
}

export type CalendarDay = {
  date: string; // YYYY.MM.DD
  committees: { house: string; name: string; threads: number }[];
  totalThreads: number;
};

export function getCalendarData(): CalendarDay[] {
  const threads = loadThreads();
  const byDate: Record<string, Record<string, { house: string; count: number }>> = {};

  for (const t of threads) {
    if (!byDate[t.date]) byDate[t.date] = {};
    const key = `${t.house}${t.committee}`;
    if (!byDate[t.date][key]) {
      byDate[t.date][key] = { house: t.house, count: 0 };
    }
    byDate[t.date][key].count++;
  }

  return Object.entries(byDate)
    .map(([date, comms]) => ({
      date,
      committees: Object.entries(comms).map(([key, v]) => ({
        house: v.house,
        name: key.replace(v.house, ""),
        threads: v.count,
      })),
      totalThreads: Object.values(comms).reduce((s, c) => s + c.count, 0),
    }))
    .sort((a, b) => b.date.localeCompare(a.date));
}

export function getProcessingStatus(): Record<string, unknown> | null {
  const statusPath = path.join(process.cwd(), "data", "status.json");
  if (fs.existsSync(statusPath)) {
    const raw = fs.readFileSync(statusPath, "utf-8");
    return JSON.parse(raw);
  }
  return null;
}

export type SessionInfo = {
  name: string;
  period: string;
  startDate: string;
  endDate: string;
};

export function getSessionInfo(): SessionInfo {
  const sessionPath = path.join(process.cwd(), "data", "session.json");
  const raw = fs.readFileSync(sessionPath, "utf-8");
  return JSON.parse(raw);
}
