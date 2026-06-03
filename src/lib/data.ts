import type { Member, Thread } from "@/types";
import fs from "fs";
import path from "path";
import { COMMITTEE_SUFFIX_RE, TREND_ALIASES, TREND_STOPWORDS } from "@/lib/utils";
import { getMemberMinistry } from "@/lib/ministry.mjs";

const THREADS_DIR = path.join(process.cwd(), "data", "threads");
const MEMBERS_PATH = path.join(process.cwd(), "data", "members.json");

// Module-level caches — safe because data files are read-only during a
// build and the Node.js process is reused across static generation calls
// within the same worker. Without this cache, every getThread/getMembers
// call re-reads all JSON files from disk, which becomes catastrophic when
// generating 2,400+ OGP images on a single Vercel worker.
let _threadsCache: Thread[] | null = null;
let _membersCache: Record<string, Member> | null = null;

function loadThreads(): Thread[] {
  if (_threadsCache) return _threadsCache;
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
  _threadsCache = threads;
  return threads;
}

function loadMembers(): Record<string, Member> {
  if (_membersCache) return _membersCache;
  if (!fs.existsSync(MEMBERS_PATH)) return {};

  const raw = fs.readFileSync(MEMBERS_PATH, "utf-8");
  const data = JSON.parse(raw);
  if (data && typeof data === "object" && !Array.isArray(data)) {
    _membersCache = data;
    return data;
  }
  return {};
}

export function getThreads(): Thread[] {
  return loadThreads().sort((a, b) => b.date.localeCompare(a.date));
}

// Existing ministries and institutions that are "actors" rather than
// trending topics. Proposed/debated bodies (e.g. 防災庁) are intentionally
// NOT listed — they can legitimately be the subject of debate.
const TREND_MINISTRY_BLOCKLIST: ReadonlySet<string> = new Set([
  // Cabinet and ministries
  "内閣府", "内閣官房", "総務省", "法務省", "外務省", "財務省",
  "文部科学省", "厚生労働省", "農林水産省", "経済産業省",
  "国土交通省", "環境省", "防衛省", "国交省",
  // Agencies
  "復興庁", "デジタル庁", "こども家庭庁", "消費者庁", "金融庁",
  "警察庁", "文化庁", "スポーツ庁", "観光庁", "気象庁",
  "会計検査院", "人事院",
  // Other institutional actors
  "国立国会図書館", "関係省庁", "事務局",
]);

let _trendBlocklistCache: Set<string> | null = null;

/** Blocklist of keywords that should never appear in trend aggregation. */
function getTrendBlocklist(): Set<string> {
  if (_trendBlocklistCache) return _trendBlocklistCache;
  const blocklist = new Set<string>(TREND_MINISTRY_BLOCKLIST);
  // Politicians are handled by dedicated profile pages — they should not
  // surface as trend keywords even when an AI extractor names them.
  for (const m of Object.values(loadMembers())) {
    if (m.name) blocklist.add(m.name);
  }
  _trendBlocklistCache = blocklist;
  return blocklist;
}

/**
 * Count, for each keyword in a thread, how many distinct speeches mention it.
 * Returns the top-N most-mentioned keywords as a map.
 *
 * Pipeline per keyword:
 *   1. Alias normalization (variant → canonical form) — merges variant counts
 *      BEFORE any filtering, so "令和8年度予算成立" joins "令和8年度予算".
 *   2. Drop politician names and institutional actors (member + ministry list).
 *   3. Drop procedural stopwords and bare committee names.
 *   4. Dedupe within each speech so one repeated keyword counts once.
 *   5. Take the top-N by speech frequency.
 *
 * Filters are applied BEFORE the top-N cap so noise can't evict legitimate
 * topics.
 */
function threadKeywordCounts(thread: Thread, n: number): Record<string, number> {
  const blocklist = getTrendBlocklist();
  const counts: Record<string, number> = {};
  for (const s of thread.speeches) {
    const seenInSpeech = new Set<string>();
    for (const raw of s.keywords) {
      const k = TREND_ALIASES[raw] ?? raw;
      if (seenInSpeech.has(k)) continue;
      seenInSpeech.add(k);
      if (blocklist.has(k)) continue;
      if (TREND_STOPWORDS.has(k)) continue;
      if (COMMITTEE_SUFFIX_RE.test(k)) continue;
      counts[k] = (counts[k] || 0) + 1;
    }
  }
  const top = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, n);
  return Object.fromEntries(top);
}

/** Lightweight thread summaries for feed and sidebar (no raw speeches). */
export type ThreadSummary = Pick<
  Thread,
  | "id" | "date" | "committee" | "house" | "topic" | "topicTag" | "topicColor"
  | "source" | "sourceLabel" | "procedural" | "summary" | "impact" | "debate" | "outcome"
> & {
  speechCount: number;
  memberIds: string[];
  /**
   * Top keywords for trend aggregation: keyword → number of distinct speeches
   * in this thread that mention it. Capped at top-10 unique keywords.
   */
  keywordCounts: Record<string, number>;
  /** First news article with an image, for link-card preview. */
  newsPreview?: { title: string; url: string; image: string };
};

export function getThreadsSummary(): ThreadSummary[] {
  return loadThreads()
    .sort((a, b) => b.date.localeCompare(a.date))
    .map((t) => {
      const newsWithImage = t.context?.news?.find((n) => n.image);
      return {
        id: t.id,
        date: t.date,
        committee: t.committee,
        house: t.house,
        topic: t.topic,
        topicTag: t.topicTag,
        topicColor: t.topicColor,
        source: t.source,
        sourceLabel: t.sourceLabel,
        procedural: t.procedural,
        summary: t.summary,
        impact: t.impact,
        debate: t.debate,
        outcome: t.outcome,
        speechCount: t.speeches.length,
        memberIds: [...new Set(t.speeches.map((s) => s.memberId))],
        keywordCounts: threadKeywordCounts(t, 10),
        newsPreview: newsWithImage
          ? { title: newsWithImage.title, url: newsWithImage.url, image: newsWithImage.image! }
          : undefined,
      };
    });
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

// --- Per-member speech statistics (shared by /gov pages and /m metadata) ---

export type MemberStats = {
  speechCount: number;
  /** Same "YYYY.MM.DD" format as Thread.date. */
  latestDate: string;
  latestCommittee: string;
};

let _memberStatsCache: Map<string, MemberStats> | null = null;

/** Aggregate speech count / latest appearance per memberId over all threads. */
export function getMemberStats(): Map<string, MemberStats> {
  if (_memberStatsCache) return _memberStatsCache;
  const stats = new Map<string, MemberStats>();
  for (const t of loadThreads()) {
    const perThread = new Map<string, number>();
    for (const s of t.speeches) {
      if (!s.memberId) continue;
      perThread.set(s.memberId, (perThread.get(s.memberId) || 0) + 1);
    }
    for (const [id, count] of perThread) {
      const prev = stats.get(id);
      if (!prev) {
        stats.set(id, {
          speechCount: count,
          latestDate: t.date,
          latestCommittee: t.committee,
        });
      } else {
        prev.speechCount += count;
        if (t.date.localeCompare(prev.latestDate) > 0) {
          prev.latestDate = t.date;
          prev.latestCommittee = t.committee;
        }
      }
    }
  }
  _memberStatsCache = stats;
  return stats;
}

// --- Ministry rosters for /gov pages ---

export type MinistryRosterEntry = {
  member: Member;
  speechCount: number;
  latestDate: string;
  latestCommittee: string;
};

export type MinistryRoster = {
  slug: string;
  name: string;
  entries: MinistryRosterEntry[];
  totalSpeeches: number;
};

let _ministryRostersCache: MinistryRoster[] | null = null;

/**
 * Group government witnesses by ministry. Only members with at least one
 * recorded speech are listed (members.json contains a few entries never
 * referenced by threads). Entries are sorted by speech count.
 */
export function getMinistryRosters(): MinistryRoster[] {
  if (_ministryRostersCache) return _ministryRostersCache;
  const stats = getMemberStats();
  const bySlug = new Map<string, MinistryRoster>();
  for (const member of Object.values(loadMembers())) {
    const ministry = getMemberMinistry(member);
    if (!ministry) continue;
    const s = stats.get(member.id);
    if (!s) continue;
    let roster = bySlug.get(ministry.slug);
    if (!roster) {
      roster = {
        slug: ministry.slug,
        name: ministry.name,
        entries: [],
        totalSpeeches: 0,
      };
      bySlug.set(ministry.slug, roster);
    }
    roster.entries.push({
      member,
      speechCount: s.speechCount,
      latestDate: s.latestDate,
      latestCommittee: s.latestCommittee,
    });
    roster.totalSpeeches += s.speechCount;
  }
  for (const r of bySlug.values()) {
    r.entries.sort((a, b) => b.speechCount - a.speechCount);
  }
  _ministryRostersCache = [...bySlug.values()].sort(
    (a, b) => b.entries.length - a.entries.length,
  );
  return _ministryRostersCache;
}

export function getMinistrySlugs(): string[] {
  return getMinistryRosters().map((r) => r.slug);
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
