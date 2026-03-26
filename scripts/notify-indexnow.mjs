#!/usr/bin/env node
/**
 * Notify search engines of new/updated URLs via IndexNow protocol.
 * Supports Bing (powers Yahoo! JAPAN) and Yandex.
 *
 * Usage: node scripts/notify-indexnow.mjs --date 2026-03-19
 *        node scripts/notify-indexnow.mjs  (defaults to yesterday)
 */

import { readFileSync, existsSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASE_URL = "https://open-gikai.net";
const KEY = "263c8aace67646c3b69bfe4fc5cf1c08";
const KEY_LOCATION = `${BASE_URL}/${KEY}.txt`;
const DATA_DIR = join(__dirname, "..", "data");

// IndexNow endpoints — submitting to one propagates to all participating engines
const INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow";

function parseArgs() {
  const args = process.argv.slice(2);
  const dateIdx = args.indexOf("--date");
  if (dateIdx !== -1 && args[dateIdx + 1]) {
    return args[dateIdx + 1];
  }
  // Default to yesterday
  const d = new Date();
  d.setDate(d.getDate() - 1);
  return d.toISOString().split("T")[0];
}

function collectNewUrls(date) {
  const urls = [];
  const threadsFile = join(DATA_DIR, "threads", `${date}.json`);

  if (!existsSync(threadsFile)) {
    console.log(`No thread file for ${date}, skipping IndexNow`);
    return urls;
  }

  const threads = JSON.parse(readFileSync(threadsFile, "utf-8"));

  // Collect thread URLs
  for (const t of threads) {
    urls.push(`${BASE_URL}/t/${t.id}`);
  }

  // Collect member URLs from speeches (new members may appear)
  const memberIds = new Set();
  for (const t of threads) {
    for (const s of t.speeches || []) {
      if (s.memberId) memberIds.add(s.memberId);
    }
  }
  for (const id of memberIds) {
    urls.push(`${BASE_URL}/m/${id}`);
  }

  // Always include the home page and sitemap (updated content)
  urls.push(BASE_URL);

  return urls;
}

async function submitIndexNow(urls) {
  if (urls.length === 0) return;

  const payload = {
    host: "open-gikai.net",
    key: KEY,
    keyLocation: KEY_LOCATION,
    urlList: urls,
  };

  console.log(`IndexNow: submitting ${urls.length} URLs...`);

  try {
    const res = await fetch(INDEXNOW_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify(payload),
    });

    // IndexNow returns 200 (OK) or 202 (Accepted)
    if (res.ok || res.status === 202) {
      console.log(`IndexNow: accepted (HTTP ${res.status})`);
    } else {
      const body = await res.text();
      console.error(`IndexNow: HTTP ${res.status} — ${body}`);
    }
  } catch (err) {
    // Non-fatal: don't break the batch pipeline
    console.error(`IndexNow: request failed — ${err.message}`);
  }
}

const date = parseArgs();
const urls = collectNewUrls(date);

if (urls.length > 0) {
  console.log(`IndexNow: ${urls.length} URLs for ${date}`);
  for (const u of urls) console.log(`  ${u}`);
  await submitIndexNow(urls);
} else {
  console.log(`IndexNow: no URLs to submit for ${date}`);
}
