#!/usr/bin/env node
/**
 * Generate sitemap.xml from thread and member data.
 * Run before `next build` to include all dynamic pages.
 *
 * Usage: node scripts/generate-sitemap.mjs
 */

import { readFileSync, readdirSync, writeFileSync } from "fs";
import { join } from "path";

const BASE_URL = "https://open-gikai.net";
const DATA_DIR = "data";
const OUTPUT = "public/sitemap.xml";

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

function collectCouncilSlugs(threadsDir) {
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

function buildSitemap() {
  const now = new Date().toISOString().split("T")[0];
  const threads = collectThreadIds();
  const members = collectMemberIds();

  const urls = [];

  // Static pages
  const staticPages = [
    { loc: "/", lastmod: now, changefreq: "daily", priority: "1.0" },
    { loc: "/search", changefreq: "daily", priority: "0.7" },
    { loc: "/calendar", changefreq: "daily", priority: "0.7" },
    { loc: "/members", lastmod: now, changefreq: "weekly", priority: "0.7" },
    { loc: "/about", changefreq: "monthly", priority: "0.5" },
    { loc: "/about/stats", lastmod: now, changefreq: "daily", priority: "0.4" },
  ];

  for (const p of staticPages) {
    urls.push(entry(p));
  }

  // Thread pages
  for (const t of threads) {
    urls.push(
      entry({ loc: `/t/${t.id}`, lastmod: t.date, changefreq: "weekly", priority: "0.8" })
    );
  }

  // Member pages
  for (const id of members) {
    urls.push(
      entry({ loc: `/m/${id}`, changefreq: "weekly", priority: "0.6" })
    );
  }

  // Council pages
  const councilSlugs = collectCouncilSlugs(join(DATA_DIR, "threads"));
  urls.push(
    entry({ loc: "/council", lastmod: now, changefreq: "weekly", priority: "0.7" })
  );
  for (const slug of councilSlugs) {
    urls.push(
      entry({ loc: `/council/${slug}`, lastmod: now, changefreq: "weekly", priority: "0.7" })
    );
  }

  const xml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...urls,
    "</urlset>",
    "",
  ].join("\n");

  writeFileSync(OUTPUT, xml, "utf-8");
  console.log(`Sitemap: ${threads.length} threads, ${members.length} members → ${OUTPUT}`);
}

function entry({ loc, lastmod, changefreq, priority }) {
  const parts = [`  <url>`, `    <loc>${BASE_URL}${loc}</loc>`];
  if (lastmod) parts.push(`    <lastmod>${lastmod}</lastmod>`);
  if (changefreq) parts.push(`    <changefreq>${changefreq}</changefreq>`);
  if (priority) parts.push(`    <priority>${priority}</priority>`);
  parts.push(`  </url>`);
  return parts.join("\n");
}

buildSitemap();
