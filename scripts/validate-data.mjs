#!/usr/bin/env node
/**
 * Pre-build data validation & auto-fix.
 *
 * Checks:
 *   1. members.json has all memberIds referenced in threads (auto-fix)
 *   2. All tension values in threads have a TENSION_STYLE entry (warn)
 *   3. status.json is up-to-date (auto-regenerate)
 *
 * Usage: node scripts/validate-data.mjs [--fix]
 *   --fix  Auto-fix issues where possible (default in build pipeline)
 */

import { readFileSync, writeFileSync, readdirSync, existsSync } from "fs";
import { join } from "path";
import { execSync } from "child_process";

const FIX = process.argv.includes("--fix");
const DATA_DIR = "data";
const THREADS_DIR = join(DATA_DIR, "threads");
const MEMBERS_PATH = join(DATA_DIR, "members.json");
const CONFIG_PATH = "src/lib/config.ts";

let errors = 0;
let warnings = 0;
let fixes = 0;

function warn(msg) {
  console.warn(`  ⚠  ${msg}`);
  warnings++;
}

function error(msg) {
  console.error(`  ✗  ${msg}`);
  errors++;
}

function ok(msg) {
  console.log(`  ✓  ${msg}`);
}

function fix(msg) {
  console.log(`  🔧 ${msg}`);
  fixes++;
}

// --- Load all threads ---
function loadAllThreads() {
  const threads = [];
  for (const file of readdirSync(THREADS_DIR)) {
    if (!file.endsWith(".json")) continue;
    const data = JSON.parse(readFileSync(join(THREADS_DIR, file), "utf-8"));
    threads.push(...data);
  }
  return threads;
}

// --- 1. Member integrity ---
function checkMembers(threads) {
  console.log("\n[1/3] Member integrity");
  const members = JSON.parse(readFileSync(MEMBERS_PATH, "utf-8"));
  const referenced = new Map(); // memberId -> {speaker, group, role, house}

  for (const t of threads) {
    for (const s of t.speeches || []) {
      if (s.memberId && !members[s.memberId]) {
        referenced.set(s.memberId, {
          speaker: s.speaker || s.memberId,
          group: s.group || "",
          role: s.role || "",
          house: t.house || "",
        });
      }
    }
  }

  if (referenced.size === 0) {
    ok(`All memberIds found in members.json (${Object.keys(members).length} members)`);
    return;
  }

  if (FIX) {
    const defaults = {
      nameReading: "", group: "", role: "", house: "",
      party: "", since: null, bio: "", stance: [],
      rank: { score: 0, tier: "newcomer", speechCount: 0 },
    };

    for (const [id, info] of referenced) {
      members[id] = {
        name: info.speaker,
        ...defaults,
        group: info.group,
        role: info.role,
        house: info.house,
      };
    }

    // Ensure all existing members have required fields
    let patched = 0;
    for (const m of Object.values(members)) {
      for (const [key, val] of Object.entries(defaults)) {
        if (!(key in m)) {
          m[key] = val;
          patched++;
        }
      }
    }

    writeFileSync(MEMBERS_PATH, JSON.stringify(members, null, 2) + "\n", "utf-8");
    fix(`Added ${referenced.size} missing members, patched ${patched} incomplete entries`);
  } else {
    error(`${referenced.size} memberIds referenced in threads but missing from members.json`);
    for (const [id] of [...referenced].slice(0, 5)) {
      console.error(`        ${id}`);
    }
    if (referenced.size > 5) console.error(`        ... and ${referenced.size - 5} more`);
  }
}

// --- 2. Tension styles ---
function checkTensions(threads) {
  console.log("\n[2/3] Tension styles");
  const configSrc = readFileSync(CONFIG_PATH, "utf-8");

  // Extract defined tensions from TENSION_STYLE
  const defined = new Set();
  const block = configSrc.match(/TENSION_STYLE[^{]*\{([\s\S]*?)\n\};/);
  if (block) {
    // Match Japanese keys like: 追及: { or "追及": {
    const keyRe = /^\s*"?([^\s":]+)"?\s*:/gm;
    let m;
    while ((m = keyRe.exec(block[1])) !== null) {
      defined.add(m[1]);
    }
  }

  const usedTensions = new Set();
  for (const t of threads) {
    for (const s of t.speeches || []) {
      if (s.tension) usedTensions.add(s.tension);
    }
  }

  const missing = [...usedTensions].filter((t) => !defined.has(t));
  if (missing.length === 0) {
    ok(`All ${usedTensions.size} tension types have styles defined`);
  } else {
    error(`${missing.length} tension type(s) missing from TENSION_STYLE in ${CONFIG_PATH}:`);
    for (const t of missing) {
      console.error(`        "${t}"`);
    }
    console.error(`        → Add entries to TENSION_STYLE to prevent build failures`);
  }
}

// --- 3. Material Symbols icons ---
function checkIcons() {
  console.log("\n[3/4] Material Symbols icons");

  const LAYOUT_PATH = "src/app/layout.tsx";
  const layoutSrc = readFileSync(LAYOUT_PATH, "utf-8");

  // Extract icon_names from the font URL
  const urlMatch = layoutSrc.match(/icon_names=([^&"]+)/);
  if (!urlMatch) {
    warn("Could not find icon_names parameter in layout.tsx font URL");
    return;
  }
  const registered = new Set(urlMatch[1].split(","));

  // Collect all icon names used in source code
  const used = new Set();

  // 1. Icons rendered directly: >icon_name<
  const srcDir = "src";
  const scanFiles = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        scanFiles(full);
      } else if (entry.name.endsWith(".tsx") || entry.name.endsWith(".ts")) {
        const content = readFileSync(full, "utf-8");
        // Match material-symbols-rounded ... >icon_name< (literal only, skip JSX expressions)
        for (const m of content.matchAll(/material-symbols-rounded[^>]*>\s*([a-z_]+)\s*</g)) {
          used.add(m[1]);
        }
        // Match icon: "icon_name" in config objects
        for (const m of content.matchAll(/icon:\s*"([a-z_]+)"/g)) {
          used.add(m[1]);
        }
      }
    }
  };
  scanFiles(srcDir);

  const missing = [...used].filter((i) => !registered.has(i)).sort();
  if (missing.length === 0) {
    ok(`All ${used.size} icons registered in font URL`);
  } else {
    error(`${missing.length} icon(s) used in code but missing from font URL in ${LAYOUT_PATH}:`);
    for (const i of missing) {
      console.error(`        "${i}"`);
    }
    console.error(`        → Add to icon_names parameter in the Material Symbols font URL`);
  }
}

// --- 4. status.json ---
function checkStatus() {
  console.log("\n[4/4] Status data");

  if (FIX) {
    try {
      const output = execSync("python3 scripts/gen_status.py", { encoding: "utf-8" });
      fix(output.trim().split("\n").pop());
    } catch (e) {
      error(`Failed to regenerate status.json: ${e.message}`);
    }
  } else {
    // Check if status.json thread count matches actual threads
    const status = existsSync("data/status.json")
      ? JSON.parse(readFileSync("data/status.json", "utf-8"))
      : {};
    const summary = status._summary || {};
    const threadFiles = readdirSync(THREADS_DIR).filter((f) => f.endsWith(".json"));
    const statusDates = Object.keys(status).filter((k) => k !== "_summary").length;

    if (statusDates < threadFiles.length) {
      error(`status.json has ${statusDates} dates but ${threadFiles.length} thread files exist`);
    } else {
      ok(`status.json covers ${statusDates} dates (${summary.totalThreads || "?"} threads)`);
    }
  }
}

// --- Run ---
console.log("Validating data integrity...");

const threads = loadAllThreads();
console.log(`Loaded ${threads.length} threads`);

checkMembers(threads);
checkTensions(threads);
checkIcons();
checkStatus();

console.log(`\n${fixes ? `${fixes} fix(es) applied. ` : ""}${warnings} warning(s), ${errors} error(s)`);

if (errors > 0 && !FIX) {
  console.error("\nRun with --fix to auto-repair, or fix manually.");
  process.exit(1);
}
