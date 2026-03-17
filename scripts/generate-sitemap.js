#!/usr/bin/env node
// Generate sitemap.xml from thread and member data at build time

const fs = require("fs");
const path = require("path");

const BASE = "https://open-gikai.net";
const THREADS_DIR = path.join(__dirname, "..", "data", "threads");
const MEMBERS_PATH = path.join(__dirname, "..", "data", "members.json");
const OUT = path.join(__dirname, "..", "public", "sitemap.xml");

function loadThreadIds() {
  if (!fs.existsSync(THREADS_DIR)) return [];
  const ids = [];
  for (const f of fs.readdirSync(THREADS_DIR)) {
    if (!f.endsWith(".json") || f.endsWith(".progress.json")) continue;
    const data = JSON.parse(fs.readFileSync(path.join(THREADS_DIR, f), "utf-8"));
    if (Array.isArray(data)) {
      for (const t of data) ids.push(t.id);
    }
  }
  return ids;
}

function loadMemberIds() {
  if (!fs.existsSync(MEMBERS_PATH)) return [];
  const data = JSON.parse(fs.readFileSync(MEMBERS_PATH, "utf-8"));
  return Object.keys(data);
}

function url(loc, changefreq, priority) {
  return `  <url>\n    <loc>${loc}</loc>\n    <changefreq>${changefreq}</changefreq>\n    <priority>${priority}</priority>\n  </url>`;
}

const urls = [
  url(BASE, "daily", "1.0"),
  url(`${BASE}/search`, "daily", "0.7"),
  url(`${BASE}/calendar`, "daily", "0.7"),
  url(`${BASE}/members`, "weekly", "0.7"),
  url(`${BASE}/about`, "monthly", "0.5"),
  url(`${BASE}/about/stats`, "daily", "0.4"),
];

for (const id of loadThreadIds()) {
  urls.push(url(`${BASE}/t/${id}`, "weekly", "0.8"));
}

for (const id of loadMemberIds()) {
  urls.push(url(`${BASE}/m/${id}`, "weekly", "0.6"));
}

const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join("\n")}
</urlset>
`;

fs.writeFileSync(OUT, xml, "utf-8");
console.log(`Sitemap generated: ${urls.length} URLs`);
