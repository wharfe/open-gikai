#!/usr/bin/env node
/**
 * Generate split sitemaps with a sitemap index.
 * Produces: sitemap_index.xml + per-category sitemaps.
 *
 * Usage: node scripts/generate-sitemap.mjs
 */

import { readFileSync, readdirSync, writeFileSync } from "fs";
import { join } from "path";

const BASE_URL = "https://open-gikai.net";
const DATA_DIR = "data";
const OUT_DIR = "public";

/* ── helpers ─────────────────────────────────── */

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

function urlEntry({ loc, lastmod, changefreq, priority }) {
  const parts = [`  <url>`, `    <loc>${BASE_URL}${loc}</loc>`];
  if (lastmod) parts.push(`    <lastmod>${lastmod}</lastmod>`);
  if (changefreq) parts.push(`    <changefreq>${changefreq}</changefreq>`);
  if (priority) parts.push(`    <priority>${priority}</priority>`);
  parts.push(`  </url>`);
  return parts.join("\n");
}

function writeSitemap(filename, entries) {
  const xml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...entries,
    "</urlset>",
    "",
  ].join("\n");
  writeFileSync(join(OUT_DIR, filename), xml, "utf-8");
  return filename;
}

function writeSitemapIndex(sitemapFiles, now) {
  const entries = sitemapFiles.map(
    (f) =>
      `  <sitemap>\n    <loc>${BASE_URL}/${f}</loc>\n    <lastmod>${now}</lastmod>\n  </sitemap>`
  );
  const xml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...entries,
    "</sitemapindex>",
    "",
  ].join("\n");
  writeFileSync(join(OUT_DIR, "sitemap_index.xml"), xml, "utf-8");
}

/* ── data collectors ─────────────────────────── */

function collectThreadIds() {
  const threadsDir = join(DATA_DIR, "threads");
  const ids = [];
  for (const file of readdirSync(threadsDir)) {
    if (!file.endsWith(".json")) continue;
    const threads = JSON.parse(readFileSync(join(threadsDir, file), "utf-8"));
    for (const t of threads) {
      ids.push({ id: t.id, date: t.date?.replace(/\./g, "-") });
    }
  }
  return ids;
}

function collectMemberIds() {
  const membersPath = join(DATA_DIR, "members.json");
  const members = JSON.parse(readFileSync(membersPath, "utf-8"));
  return Object.keys(members);
}

function collectWeekIds() {
  const threadsDir = join(DATA_DIR, "threads");
  const ids = new Set();
  for (const file of readdirSync(threadsDir)) {
    if (!file.endsWith(".json")) continue;
    const threads = JSON.parse(readFileSync(join(threadsDir, file), "utf-8"));
    for (const t of threads) {
      const [y, m, d] = t.date.split(".").map(Number);
      const date = new Date(y, m - 1, d);
      ids.add(`${isoWeekYear(date)}-W${String(isoWeek(date)).padStart(2, "0")}`);
    }
  }
  return [...ids].sort();
}

const COUNCIL_SLUG_MAP = {
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

function collectCouncilSlugs() {
  const threadsDir = join(DATA_DIR, "threads");
  const labels = new Set();
  for (const file of readdirSync(threadsDir)) {
    if (!file.endsWith(".json")) continue;
    const threads = JSON.parse(readFileSync(join(threadsDir, file), "utf-8"));
    for (const t of threads) {
      if (t.source === "council") {
        labels.add(t.sourceLabel || t.committee);
      }
    }
  }
  return [...labels].map((l) => COUNCIL_SLUG_MAP[l] || l.replace(/[・\s]/g, "-")).filter(Boolean);
}

/* ── main ────────────────────────────────────── */

function buildSitemaps() {
  const now = new Date().toISOString().split("T")[0];
  const threads = collectThreadIds();
  const members = collectMemberIds();
  const weekIds = collectWeekIds();
  const councilSlugs = collectCouncilSlugs();

  const files = [];

  // 1. Static pages
  files.push(
    writeSitemap("sitemap-pages.xml", [
      urlEntry({ loc: "/", lastmod: now, changefreq: "daily", priority: "1.0" }),
      urlEntry({ loc: "/search", lastmod: now, changefreq: "daily", priority: "0.7" }),
      urlEntry({ loc: "/calendar", lastmod: now, changefreq: "daily", priority: "0.7" }),
      urlEntry({ loc: "/members", lastmod: now, changefreq: "weekly", priority: "0.7" }),
      urlEntry({ loc: "/about", lastmod: now, changefreq: "monthly", priority: "0.5" }),
      urlEntry({ loc: "/about/stats", lastmod: now, changefreq: "daily", priority: "0.4" }),
    ])
  );

  // 2. Thread pages
  files.push(
    writeSitemap(
      "sitemap-threads.xml",
      threads.map((t) =>
        urlEntry({ loc: `/t/${t.id}`, lastmod: now, changefreq: "monthly", priority: "0.8" })
      )
    )
  );

  // 3. Member pages
  files.push(
    writeSitemap(
      "sitemap-members.xml",
      members.map((id) =>
        urlEntry({ loc: `/m/${id}`, lastmod: now, changefreq: "weekly", priority: "0.6" })
      )
    )
  );

  // 4. Council pages
  files.push(
    writeSitemap("sitemap-councils.xml", [
      urlEntry({ loc: "/council", lastmod: now, changefreq: "weekly", priority: "0.7" }),
      ...councilSlugs.map((slug) =>
        urlEntry({ loc: `/council/${slug}`, lastmod: now, changefreq: "weekly", priority: "0.7" })
      ),
    ])
  );

  // 5. Digest pages
  files.push(
    writeSitemap("sitemap-digests.xml", [
      urlEntry({ loc: "/digest", lastmod: now, changefreq: "weekly", priority: "0.7" }),
      ...weekIds.map((wid) =>
        urlEntry({ loc: `/digest/weekly/${wid}`, lastmod: now, changefreq: "monthly", priority: "0.6" })
      ),
    ])
  );

  // Sitemap index
  writeSitemapIndex(files, now);

  // Keep sitemap.xml as a redirect target for any cached references
  writeSitemap("sitemap.xml", [
    urlEntry({ loc: "/", lastmod: now, changefreq: "daily", priority: "1.0" }),
  ]);

  console.log(
    `Sitemaps generated: ${threads.length} threads, ${members.length} members, ` +
      `${weekIds.length} digests, ${councilSlugs.length} councils → ${files.length} files + sitemap_index.xml`
  );
}

buildSitemaps();
