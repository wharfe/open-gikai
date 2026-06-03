#!/usr/bin/env node
/**
 * Generate split sitemaps with a sitemap index.
 * Produces: sitemap_index.xml + per-category sitemaps.
 *
 * Usage: node scripts/generate-sitemap.mjs
 */

import { readFileSync, readdirSync, writeFileSync } from "fs";
import { join } from "path";
import { getMemberMinistry } from "../src/lib/ministry.mjs";

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

// Single-pass collection over all thread files. Returns per-entity lastmod
// so sitemaps can use accurate dates instead of the build date.
function collectAll() {
  const threadsDir = join(DATA_DIR, "threads");
  const threads = []; // { id, lastmod }
  const weekLastmod = new Map(); // weekId -> latest isoDate
  const memberLastmod = new Map(); // memberId -> latest isoDate
  const councilLabels = new Set();
  let globalLatest = "";

  for (const file of readdirSync(threadsDir)) {
    if (!file.endsWith(".json")) continue;
    const parsed = JSON.parse(readFileSync(join(threadsDir, file), "utf-8"));
    for (const t of parsed) {
      const iso = t.date?.replace(/\./g, "-");
      if (!iso) continue;
      threads.push({ id: t.id, lastmod: iso });
      if (iso > globalLatest) globalLatest = iso;

      // Week bucket
      const [y, m, d] = t.date.split(".").map(Number);
      const dObj = new Date(y, m - 1, d);
      const wid = `${isoWeekYear(dObj)}-W${String(isoWeek(dObj)).padStart(2, "0")}`;
      const prevW = weekLastmod.get(wid);
      if (!prevW || iso > prevW) weekLastmod.set(wid, iso);

      // Member lastmod = latest thread date the member appeared in
      const speakerIds = new Set();
      for (const s of t.speeches || []) {
        if (s.memberId) speakerIds.add(s.memberId);
      }
      for (const id of speakerIds) {
        const prev = memberLastmod.get(id);
        if (!prev || iso > prev) memberLastmod.set(id, iso);
      }

      // Council labels
      if (t.source === "council") {
        councilLabels.add(t.sourceLabel || t.committee);
      }
    }
  }

  const councilSlugs = [...councilLabels]
    .map((l) => COUNCIL_SLUG_MAP[l] || l.replace(/[・\s]/g, "-"))
    .filter(Boolean);

  return { threads, weekLastmod, memberLastmod, councilSlugs, globalLatest };
}

function collectAllMemberIds() {
  const membersPath = join(DATA_DIR, "members.json");
  const members = JSON.parse(readFileSync(membersPath, "utf-8"));
  return Object.keys(members);
}

/* ── main ────────────────────────────────────── */

function buildSitemaps() {
  const now = new Date().toISOString().split("T")[0];
  const { threads, weekLastmod, memberLastmod, councilSlugs, globalLatest } =
    collectAll();
  const allMemberIds = collectAllMemberIds();

  // Use the latest known content date as the "site updated" signal for
  // listing pages. Falls back to today for fresh repos.
  const siteLastmod = globalLatest || now;

  const files = [];

  // 1. Static pages — listing pages track the latest content date,
  // truly static pages track today.
  files.push(
    writeSitemap("sitemap-pages.xml", [
      urlEntry({ loc: "/", lastmod: siteLastmod, changefreq: "daily", priority: "1.0" }),
      urlEntry({ loc: "/search", lastmod: siteLastmod, changefreq: "daily", priority: "0.7" }),
      urlEntry({ loc: "/calendar", lastmod: siteLastmod, changefreq: "daily", priority: "0.7" }),
      urlEntry({ loc: "/members", lastmod: siteLastmod, changefreq: "weekly", priority: "0.7" }),
      urlEntry({ loc: "/about", lastmod: now, changefreq: "monthly", priority: "0.5" }),
      urlEntry({ loc: "/about/stats", lastmod: siteLastmod, changefreq: "daily", priority: "0.4" }),
    ])
  );

  // 2. Thread pages — lastmod = the debate date itself.
  files.push(
    writeSitemap(
      "sitemap-threads.xml",
      threads.map((t) =>
        urlEntry({ loc: `/t/${t.id}`, lastmod: t.lastmod, changefreq: "monthly", priority: "0.8" })
      )
    )
  );

  // 3. Member pages — lastmod = latest debate the member appeared in.
  // Members without any recorded speeches fall back to siteLastmod.
  files.push(
    writeSitemap(
      "sitemap-members.xml",
      allMemberIds.map((id) =>
        urlEntry({
          loc: `/m/${id}`,
          lastmod: memberLastmod.get(id) || siteLastmod,
          changefreq: "weekly",
          priority: "0.6",
        })
      )
    )
  );

  // 4. Council pages
  files.push(
    writeSitemap("sitemap-councils.xml", [
      urlEntry({ loc: "/council", lastmod: siteLastmod, changefreq: "weekly", priority: "0.7" }),
      ...councilSlugs.map((slug) =>
        urlEntry({ loc: `/council/${slug}`, lastmod: siteLastmod, changefreq: "weekly", priority: "0.7" })
      ),
    ])
  );

  // 5. Digest pages — per-week lastmod = latest thread date in that week.
  const weekIds = [...weekLastmod.keys()].sort();
  files.push(
    writeSitemap("sitemap-digests.xml", [
      urlEntry({ loc: "/digest", lastmod: siteLastmod, changefreq: "weekly", priority: "0.7" }),
      ...weekIds.map((wid) =>
        urlEntry({
          loc: `/digest/weekly/${wid}`,
          lastmod: weekLastmod.get(wid),
          changefreq: "monthly",
          priority: "0.6",
        })
      ),
    ])
  );

  // 6. Gov (ministry hub) pages — lastmod = latest debate any of the
  // ministry's witnesses appeared in. Same membership rule as the pages
  // themselves: getMemberMinistry() + at least one recorded speech.
  const members = JSON.parse(
    readFileSync(join(DATA_DIR, "members.json"), "utf-8")
  );
  const govLastmod = new Map(); // slug -> latest isoDate
  for (const [id, m] of Object.entries(members)) {
    const ministry = getMemberMinistry(m);
    if (!ministry) continue;
    const lm = memberLastmod.get(id);
    if (!lm) continue; // skip members with no recorded speeches
    const prev = govLastmod.get(ministry.slug);
    if (!prev || lm > prev) govLastmod.set(ministry.slug, lm);
  }
  const govSlugs = [...govLastmod.keys()].sort();
  files.push(
    writeSitemap("sitemap-gov.xml", [
      urlEntry({ loc: "/gov", lastmod: siteLastmod, changefreq: "weekly", priority: "0.7" }),
      ...govSlugs.map((slug) =>
        urlEntry({
          loc: `/gov/${slug}`,
          lastmod: govLastmod.get(slug),
          changefreq: "weekly",
          priority: "0.7",
        })
      ),
    ])
  );

  // Sitemap index — use today (index itself was regenerated now).
  writeSitemapIndex(files, now);

  // Keep sitemap.xml as a redirect target for any cached references
  writeSitemap("sitemap.xml", [
    urlEntry({ loc: "/", lastmod: siteLastmod, changefreq: "daily", priority: "1.0" }),
  ]);

  console.log(
    `Sitemaps generated: ${threads.length} threads, ${allMemberIds.length} members, ` +
      `${weekIds.length} digests, ${councilSlugs.length} councils, ${govSlugs.length} gov pages → ` +
      `${files.length} files + sitemap_index.xml`
  );
}

buildSitemaps();
