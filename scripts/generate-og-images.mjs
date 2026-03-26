#!/usr/bin/env node
/**
 * Generate OGP images for thread pages at build time.
 * Uses satori (SVG) + @resvg/resvg-js (PNG).
 *
 * Usage: node scripts/generate-og-images.mjs
 */

import { readFileSync, readdirSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { join } from "path";
import satori from "satori";
import { Resvg } from "@resvg/resvg-js";

const DATA_DIR = "data";
const OUTPUT_DIR = "public/og";
const FONT_URL =
  "https://cdn.jsdelivr.net/fontsource/fonts/noto-sans-jp@latest/japanese-700-normal.woff";

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

function loadAllThreads() {
  const threadsDir = join(DATA_DIR, "threads");
  const threads = [];
  for (const f of readdirSync(threadsDir)) {
    if (!f.endsWith(".json") || f.endsWith(".progress.json")) continue;
    const data = JSON.parse(readFileSync(join(threadsDir, f), "utf-8"));
    if (Array.isArray(data)) threads.push(...data);
  }
  return threads;
}

function loadMembers() {
  const membersPath = join(DATA_DIR, "members.json");
  if (!existsSync(membersPath)) return {};
  return JSON.parse(readFileSync(membersPath, "utf-8"));
}

// ---------------------------------------------------------------------------
// Font loading
// ---------------------------------------------------------------------------

async function loadFont() {
  // Try local cache first
  const cacheDir = "node_modules/.cache";
  const cachePath = join(cacheDir, "noto-sans-jp-700.woff");
  if (existsSync(cachePath)) {
    return readFileSync(cachePath);
  }

  console.log("Downloading Noto Sans JP font...");
  const res = await fetch(FONT_URL);
  if (!res.ok) throw new Error(`Font download failed: ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());

  mkdirSync(cacheDir, { recursive: true });
  writeFileSync(cachePath, buf);
  return buf;
}

// ---------------------------------------------------------------------------
// OGP image rendering
// ---------------------------------------------------------------------------

/** Truncate text to fit within a character limit. */
function truncate(text, max) {
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

function buildOgElement(thread, actorNames) {
  const topic = truncate(thread.topic, 40);
  const committee = thread.committee;
  const date = thread.date;
  const actors = actorNames.slice(0, 4).join("  ");
  const source = thread.sourceLabel || "国会会議録";

  return {
    type: "div",
    props: {
      style: {
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100%",
        backgroundColor: "#15202b",
        padding: "48px 56px",
        fontFamily: "Noto Sans JP",
        color: "#e7e9ea",
      },
      children: [
        // Top bar: logo + source
        {
          type: "div",
          props: {
            style: {
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "32px",
            },
            children: [
              {
                type: "div",
                props: {
                  style: {
                    display: "flex",
                    alignItems: "center",
                    gap: "12px",
                  },
                  children: [
                    {
                      type: "div",
                      props: {
                        style: {
                          width: "40px",
                          height: "40px",
                          borderRadius: "50%",
                          backgroundColor: "rgba(52, 211, 153, 0.2)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontSize: "20px",
                          fontWeight: 700,
                          color: "#34d399",
                        },
                        children: "議",
                      },
                    },
                    {
                      type: "span",
                      props: {
                        style: { fontSize: "22px", fontWeight: 700 },
                        children: "OpenGIKAI",
                      },
                    },
                  ],
                },
              },
              {
                type: "span",
                props: {
                  style: {
                    fontSize: "16px",
                    color: "#8899a6",
                  },
                  children: source,
                },
              },
            ],
          },
        },
        // Committee + date
        {
          type: "div",
          props: {
            style: {
              display: "flex",
              alignItems: "center",
              gap: "16px",
              marginBottom: "16px",
            },
            children: [
              {
                type: "span",
                props: {
                  style: {
                    fontSize: "18px",
                    color: "#34d399",
                    backgroundColor: "rgba(52, 211, 153, 0.1)",
                    padding: "4px 16px",
                    borderRadius: "999px",
                  },
                  children: committee,
                },
              },
              {
                type: "span",
                props: {
                  style: { fontSize: "18px", color: "#8899a6" },
                  children: date,
                },
              },
            ],
          },
        },
        // Topic
        {
          type: "div",
          props: {
            style: {
              fontSize: "36px",
              fontWeight: 700,
              lineHeight: 1.3,
              flex: 1,
              display: "flex",
              alignItems: "center",
            },
            children: topic,
          },
        },
        // Actors
        actors
          ? {
              type: "div",
              props: {
                style: {
                  fontSize: "18px",
                  color: "#8899a6",
                  marginTop: "16px",
                },
                children: actors,
              },
            }
          : null,
      ].filter(Boolean),
    },
  };
}

async function renderOgImage(element, fontData) {
  const svg = await satori(element, {
    width: 1200,
    height: 630,
    fonts: [
      {
        name: "Noto Sans JP",
        data: fontData,
        weight: 700,
        style: "normal",
      },
    ],
  });

  const resvg = new Resvg(svg, {
    fitTo: { mode: "width", value: 1200 },
  });
  return resvg.render().asPng();
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  mkdirSync(OUTPUT_DIR, { recursive: true });

  const fontData = await loadFont();
  const threads = loadAllThreads();
  const members = loadMembers();

  console.log(`Generating OGP images for ${threads.length} threads...`);

  let count = 0;
  for (const t of threads) {
    const outPath = join(OUTPUT_DIR, `${t.id}.png`);

    // Skip if already generated
    if (existsSync(outPath)) continue;

    const actorIds = [...new Set(t.speeches.map((s) => s.memberId))];
    const actorNames = actorIds
      .map((id) => members[id]?.name || "")
      .filter(Boolean);

    const element = buildOgElement(t, actorNames);
    const png = await renderOgImage(element, fontData);
    writeFileSync(outPath, png);
    count++;
  }

  console.log(`OGP images: ${count} new, ${threads.length - count} cached → ${OUTPUT_DIR}/`);
}

main().catch((err) => {
  console.error("OGP generation failed:", err);
  process.exit(1);
});
