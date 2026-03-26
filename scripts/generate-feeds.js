#!/usr/bin/env node
// Generate sitemap.xml (with lastmod) and feed.xml (RSS 2.0) at build time

const fs = require("fs");
const path = require("path");

const BASE = "https://open-gikai.net";
const THREADS_DIR = path.join(__dirname, "..", "data", "threads");
const MEMBERS_PATH = path.join(__dirname, "..", "data", "members.json");
const PUBLIC = path.join(__dirname, "..", "public");

// ---------------------------------------------------------------------------
// Load data
// ---------------------------------------------------------------------------

function loadAllThreads() {
  if (!fs.existsSync(THREADS_DIR)) return [];
  const threads = [];
  for (const f of fs.readdirSync(THREADS_DIR)) {
    if (!f.endsWith(".json") || f.endsWith(".progress.json")) continue;
    const data = JSON.parse(
      fs.readFileSync(path.join(THREADS_DIR, f), "utf-8")
    );
    if (Array.isArray(data)) threads.push(...data);
  }
  // Sort newest first
  threads.sort((a, b) => b.date.localeCompare(a.date));
  return threads;
}

function loadMemberIds() {
  if (!fs.existsSync(MEMBERS_PATH)) return [];
  return Object.keys(JSON.parse(fs.readFileSync(MEMBERS_PATH, "utf-8")));
}

// Convert "YYYY.MM.DD" to "YYYY-MM-DD"
function toIsoDate(dotDate) {
  return dotDate.replace(/\./g, "-");
}

function escapeXml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// Sitemap (with lastmod)
// ---------------------------------------------------------------------------

// --- Week helpers for digest ---

function isoWeek(d) {
  const copy = new Date(d.getTime());
  copy.setHours(0, 0, 0, 0);
  copy.setDate(copy.getDate() + 3 - ((copy.getDay() + 6) % 7));
  const week1 = new Date(copy.getFullYear(), 0, 4);
  return 1 + Math.round(((copy.getTime() - week1.getTime()) / 86400000 - 3 + ((week1.getDay() + 6) % 7)) / 7);
}

function isoWeekYear(d) {
  const copy = new Date(d.getTime());
  copy.setDate(copy.getDate() + 3 - ((copy.getDay() + 6) % 7));
  return copy.getFullYear();
}

function threadWeekId(thread) {
  const [y, m, d] = thread.date.split(".").map(Number);
  const date = new Date(y, m - 1, d);
  return `${isoWeekYear(date)}-W${String(isoWeek(date)).padStart(2, "0")}`;
}

function collectWeekIds(threads) {
  const ids = new Set();
  for (const t of threads) {
    ids.add(threadWeekId(t));
  }
  return [...ids].sort();
}

// ---------------------------------------------------------------------------
// RSS Feed
// ---------------------------------------------------------------------------

function generateRss(threads) {
  const buildDate = new Date().toUTCString();
  const latestDate =
    threads.length > 0
      ? new Date(toIsoDate(threads[0].date)).toUTCString()
      : buildDate;

  // Weekly digest items
  const weekIds = collectWeekIds(threads);
  const digestItems = weekIds.slice(-4).reverse().map((wid) => {
    const weekThreads = threads.filter((t) => threadWeekId(t) === wid);
    const count = weekThreads.length;
    const comms = [...new Set(weekThreads.map((t) => t.committee))];
    const link = `${BASE}/digest/weekly/${wid}`;
    // Use the Sunday of that week as pubDate
    const sample = weekThreads[0];
    const pubDate = sample ? new Date(toIsoDate(sample.date)).toUTCString() : buildDate;
    return `    <item>
      <title>${escapeXml(`${wid} 国会まとめ — ${count}件の議論`)}</title>
      <link>${link}</link>
      <guid isPermaLink="true">${link}</guid>
      <pubDate>${pubDate}</pubDate>
      <description>${escapeXml(`${count}件のスレッド、${comms.length}委員会の議論をまとめました。${comms.slice(0, 3).join("、")}ほか。`)}</description>
      <category>週次ダイジェスト</category>
    </item>`;
  });

  // Include up to 50 most recent threads
  const threadItems = threads.slice(0, 50).map((t) => {
    const link = `${BASE}/t/${t.id}`;
    const pubDate = new Date(toIsoDate(t.date)).toUTCString();
    const source = t.sourceLabel || "国会会議録";
    return `    <item>
      <title>${escapeXml(t.topic)} — ${escapeXml(t.committee)}</title>
      <link>${link}</link>
      <guid isPermaLink="true">${link}</guid>
      <pubDate>${pubDate}</pubDate>
      <description>${escapeXml(t.summary)}（${escapeXml(source)}・${t.date}・${t.speeches.length}発言）</description>
      <category>${escapeXml(t.committee)}</category>
    </item>`;
  });

  const items = [...digestItems, ...threadItems];

  const rss = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>OpenGIKAI — 議会をひらく</title>
    <link>${BASE}</link>
    <description>議会の審議内容をAIで要約・構造化するオープンソースの公共メディア</description>
    <language>ja</language>
    <lastBuildDate>${buildDate}</lastBuildDate>
    <atom:link href="${BASE}/feed.xml" rel="self" type="application/rss+xml"/>
${items.join("\n")}
  </channel>
</rss>
`;
  fs.writeFileSync(path.join(PUBLIC, "feed.xml"), rss, "utf-8");
  console.log(`RSS feed generated: ${items.length} items`);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const threads = loadAllThreads();
generateRss(threads);
