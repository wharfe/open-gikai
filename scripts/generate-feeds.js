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

function generateSitemap(threads) {
  const latestDate =
    threads.length > 0 ? toIsoDate(threads[0].date) : new Date().toISOString().slice(0, 10);

  function url(loc, changefreq, priority, lastmod) {
    const lm = lastmod ? `\n    <lastmod>${lastmod}</lastmod>` : "";
    return `  <url>\n    <loc>${loc}</loc>${lm}\n    <changefreq>${changefreq}</changefreq>\n    <priority>${priority}</priority>\n  </url>`;
  }

  const urls = [
    url(BASE, "daily", "1.0", latestDate),
    url(`${BASE}/search`, "daily", "0.7"),
    url(`${BASE}/calendar`, "daily", "0.7"),
    url(`${BASE}/members`, "weekly", "0.7", latestDate),
    url(`${BASE}/about`, "monthly", "0.5"),
    url(`${BASE}/about/stats`, "daily", "0.4", latestDate),
  ];

  for (const t of threads) {
    urls.push(url(`${BASE}/t/${t.id}`, "weekly", "0.8", toIsoDate(t.date)));
  }

  for (const id of loadMemberIds()) {
    urls.push(url(`${BASE}/m/${id}`, "weekly", "0.6"));
  }

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join("\n")}
</urlset>
`;
  fs.writeFileSync(path.join(PUBLIC, "sitemap.xml"), xml, "utf-8");
  console.log(`Sitemap generated: ${urls.length} URLs`);
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

  // Include up to 50 most recent threads
  const items = threads.slice(0, 50).map((t) => {
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
generateSitemap(threads);
generateRss(threads);
