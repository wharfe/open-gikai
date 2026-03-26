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

/** Extract top-N unique keywords from a thread's speeches. */
function topKeywords(thread: Thread, n: number): string[] {
  const counts: Record<string, number> = {};
  for (const s of thread.speeches) {
    for (const k of s.keywords) {
      counts[k] = (counts[k] || 0) + 1;
    }
  }
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([k]) => k);
}

/** Lightweight thread summaries for sidebar panels (no speeches/context). */
export type ThreadSummary = Pick<
  Thread,
  "id" | "date" | "committee" | "house" | "topic" | "topicTag" | "topicColor" | "source" | "procedural"
> & {
  speechCount: number;
  memberIds: string[];
  /** Top keywords for trend aggregation (deduplicated, max 8 per thread). */
  keywords: string[];
};

export function getThreadsSummary(): ThreadSummary[] {
  return loadThreads()
    .sort((a, b) => b.date.localeCompare(a.date))
    .map((t) => ({
      id: t.id,
      date: t.date,
      committee: t.committee,
      house: t.house,
      topic: t.topic,
      topicTag: t.topicTag,
      topicColor: t.topicColor,
      source: t.source,
      procedural: t.procedural,
      speechCount: t.speeches.length,
      memberIds: [...new Set(t.speeches.map((s) => s.memberId))],
      keywords: topKeywords(t, 8),
    }));
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

// --- Council data for /council pages ---

export type CouncilMeeting = {
  date: string;
  threadCount: number;
  speechCount: number;
  topics: string[];
};

export type CouncilInfo = {
  slug: string;
  name: string;
  meetings: CouncilMeeting[];
  totalThreads: number;
  totalSpeeches: number;
};

function councilSlug(name: string): string {
  // Stable slug from Japanese council name
  const map: Record<string, string> = {
    "規制改革推進会議": "kisei",
    "移住・二地域居住等促進専門委員会": "nichiiki",
    "関係人口懇談会": "kankeijinkou",
    "国土審議会推進部会": "suishin",
    "地域生活圏専門委員会": "chiikiseikatsu",
    "地方創生2.0有識者会議": "chihousousei",
    "デジタル田園都市構想": "digital-denen",
    "居住支援検討会": "kyojushien",
    "審議会": "council-general",
  };
  return map[name] || name.replace(/[・\s]/g, "-");
}

export function getCouncils(): CouncilInfo[] {
  const threads = loadThreads();
  const byLabel: Record<string, Thread[]> = {};

  for (const t of threads) {
    if (t.source !== "council") continue;
    const label = t.sourceLabel || t.committee;
    if (!byLabel[label]) byLabel[label] = [];
    byLabel[label].push(t);
  }

  return Object.entries(byLabel).map(([name, ts]) => {
    const byDate: Record<string, Thread[]> = {};
    for (const t of ts) {
      if (!byDate[t.date]) byDate[t.date] = [];
      byDate[t.date].push(t);
    }

    const meetings = Object.entries(byDate)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, dts]) => ({
        date,
        threadCount: dts.length,
        speechCount: dts.reduce((s, t) => s + t.speeches.length, 0),
        topics: dts.map((t) => t.topic),
      }));

    return {
      slug: councilSlug(name),
      name,
      meetings,
      totalThreads: ts.length,
      totalSpeeches: ts.reduce((s, t) => s + t.speeches.length, 0),
    };
  }).sort((a, b) => b.totalThreads - a.totalThreads);
}

export function getCouncilSlugs(): string[] {
  return getCouncils().map((c) => c.slug);
}

export function getAllMemberIds(): string[] {
  return Object.keys(loadMembers());
}

// --- Weekly digest data for /digest pages ---

export type WeeklyDigest = {
  weekId: string; // "2026-W12"
  startDate: string; // "2026.03.16"
  endDate: string; // "2026.03.22"
  threadCount: number;
  speechCount: number;
  committees: { name: string; threads: number }[];
  topKeywords: [string, number][];
  highlights: {
    id: string;
    topic: string;
    committee: string;
    summary: string;
    date: string;
  }[];
  sources: { source: string; count: number }[];
};

/** ISO week number from a Date object. */
function isoWeek(d: Date): number {
  const copy = new Date(d.getTime());
  copy.setHours(0, 0, 0, 0);
  copy.setDate(copy.getDate() + 3 - ((copy.getDay() + 6) % 7));
  const week1 = new Date(copy.getFullYear(), 0, 4);
  return (
    1 +
    Math.round(
      ((copy.getTime() - week1.getTime()) / 86400000 -
        3 +
        ((week1.getDay() + 6) % 7)) /
        7,
    )
  );
}

/** ISO week year (may differ from calendar year at year boundaries). */
function isoWeekYear(d: Date): number {
  const copy = new Date(d.getTime());
  copy.setDate(copy.getDate() + 3 - ((copy.getDay() + 6) % 7));
  return copy.getFullYear();
}

/** Format week number as "YYYY-WNN". */
function weekId(d: Date): string {
  return `${isoWeekYear(d)}-W${String(isoWeek(d)).padStart(2, "0")}`;
}

/** Parse "YYYY.MM.DD" to Date. */
function dotToDate(dot: string): Date {
  const [y, m, d] = dot.split(".").map(Number);
  return new Date(y, m - 1, d);
}

/** Monday of the ISO week containing d. */
function weekMonday(d: Date): Date {
  const copy = new Date(d.getTime());
  copy.setHours(0, 0, 0, 0);
  const day = copy.getDay();
  const diff = (day + 6) % 7; // 0=Mon, 6=Sun
  copy.setDate(copy.getDate() - diff);
  return copy;
}

/** Format Date as "YYYY.MM.DD". */
function toDotDate(d: Date): string {
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")}`;
}

export function getWeeklyDigests(): WeeklyDigest[] {
  const threads = loadThreads();
  const byWeek: Record<string, Thread[]> = {};

  for (const t of threads) {
    const d = dotToDate(t.date);
    const wid = weekId(d);
    if (!byWeek[wid]) byWeek[wid] = [];
    byWeek[wid].push(t);
  }

  return Object.entries(byWeek)
    .map(([wid, ts]) => {
      const monday = weekMonday(dotToDate(ts[0].date));
      const sunday = new Date(monday.getTime() + 6 * 86400000);

      // Committee breakdown
      const commCounts: Record<string, number> = {};
      for (const t of ts) {
        commCounts[t.committee] = (commCounts[t.committee] || 0) + 1;
      }
      const committees = Object.entries(commCounts)
        .sort((a, b) => b[1] - a[1])
        .map(([name, count]) => ({ name, threads: count }));

      // Keyword aggregation
      const kwCounts: Record<string, number> = {};
      for (const t of ts) {
        for (const s of t.speeches) {
          for (const k of s.keywords) {
            kwCounts[k] = (kwCounts[k] || 0) + 1;
          }
        }
      }
      const topKw = Object.entries(kwCounts)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10) as [string, number][];

      // Top highlights (most speeches = most substantive)
      const sorted = [...ts].sort(
        (a, b) => b.speeches.length - a.speeches.length,
      );
      const highlights = sorted
        .filter((t) => !t.procedural)
        .slice(0, 5)
        .map((t) => ({
          id: t.id,
          topic: t.topic,
          committee: t.committee,
          summary: t.summary,
          date: t.date,
        }));

      // Source breakdown
      const srcCounts: Record<string, number> = {};
      for (const t of ts) {
        const src = t.sourceLabel || "国会会議録";
        srcCounts[src] = (srcCounts[src] || 0) + 1;
      }
      const sources = Object.entries(srcCounts)
        .sort((a, b) => b[1] - a[1])
        .map(([source, count]) => ({ source, count }));

      return {
        weekId: wid,
        startDate: toDotDate(monday),
        endDate: toDotDate(sunday),
        threadCount: ts.length,
        speechCount: ts.reduce((s, t) => s + t.speeches.length, 0),
        committees,
        topKeywords: topKw,
        highlights,
        sources,
      };
    })
    .sort((a, b) => b.weekId.localeCompare(a.weekId));
}

export function getWeeklyDigest(wid: string): WeeklyDigest | undefined {
  return getWeeklyDigests().find((d) => d.weekId === wid);
}

export function getAllWeekIds(): string[] {
  return getWeeklyDigests().map((d) => d.weekId);
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
