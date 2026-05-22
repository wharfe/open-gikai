#!/usr/bin/env node
/**
 * Copy the subset of repo-root data/ that the MCP server actually needs
 * into apps/mcp/data/ so it gets bundled into the Vercel serverless function.
 *
 * Why this exists:
 *   Vercel resolves outputFileTracingRoot incorrectly when pointing above
 *   the project root (it double-prefixes the project base, producing paths
 *   like /vercel/path0/vercel/path0/.next/...). A simple selective copy at
 *   build time sidesteps the issue entirely and also drops ~340MB of
 *   intermediate pipeline data (data/raw/, data/batch/) that the MCP
 *   server never reads.
 *
 * Run via the prebuild npm script. Idempotent — clears the destination
 * before copying so stale files cannot survive between builds.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PROJECT_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(PROJECT_ROOT, "..", "..");
const SOURCE = path.join(REPO_ROOT, "data");
const DEST = path.join(PROJECT_ROOT, "data");

// Files / directories that the MCP server reads at request time.
// Keep this list narrow: every entry here ships in the function bundle.
const INCLUDE = ["threads", "members.json"];

function bytes(n) {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

function totalSize(p) {
  const stat = fs.statSync(p);
  if (stat.isFile()) return stat.size;
  let total = 0;
  for (const entry of fs.readdirSync(p)) {
    total += totalSize(path.join(p, entry));
  }
  return total;
}

// On Vercel the project is uploaded with apps/mcp as the root, so the
// repo-level data/ directory two levels up does not exist on the build
// machine — but the local prebuild already ran on the developer's machine
// and the resulting apps/mcp/data/ was included in the upload. So if the
// source is missing AND the destination already has every required entry,
// treat it as "Vercel rebuild, data already bundled" and exit successfully.
if (!fs.existsSync(SOURCE)) {
  const allBundled = INCLUDE.every((entry) =>
    fs.existsSync(path.join(DEST, entry))
  );
  if (allBundled) {
    console.log(
      `copy-data: source ${SOURCE} not present (expected on Vercel); ` +
      `data/ already bundled with project, skipping copy.`
    );
    process.exit(0);
  }
  console.error(
    `copy-data: source missing: ${SOURCE}\n` +
    `  (and apps/mcp/data/ is not pre-populated — cannot proceed)`
  );
  process.exit(1);
}

fs.rmSync(DEST, { recursive: true, force: true });
fs.mkdirSync(DEST, { recursive: true });

let copied = 0;
for (const entry of INCLUDE) {
  const src = path.join(SOURCE, entry);
  const dst = path.join(DEST, entry);
  if (!fs.existsSync(src)) {
    console.warn(`copy-data: skipping missing ${entry}`);
    continue;
  }
  fs.cpSync(src, dst, { recursive: true });
  const size = totalSize(dst);
  copied += size;
  console.log(`copy-data: + ${entry} (${bytes(size)})`);
}

console.log(`copy-data: done, ${bytes(copied)} total → ${DEST}`);
