import { ImageResponse } from "next/og";
import { readFileSync } from "fs";
import { join } from "path";
import { getThread, getMembers, getAllThreadIds } from "@/lib/data";

// Static OGP image generation for thread pages.
//
// The site uses `output: "export"` (fully static), so all OGP images must
// be produced at build time. Next.js picks this file up automatically,
// and with dynamic = "force-static" + generateStaticParams it emits one
// PNG per thread alongside the page, then injects the correct <meta>
// tags automatically — no explicit image references in page.tsx.
//
// ImageResponse is backed by @vercel/og (satori + resvg-wasm), so it
// works without the native binaries that forced the earlier
// build-script rollback.

export const dynamic = "force-static";
export const alt = "OpenGIKAI — thread preview";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export function generateStaticParams() {
  return getAllThreadIds().map((threadId) => ({ threadId }));
}

// Font is bundled in-repo (assets/fonts/) rather than fetched from a CDN.
// A previous CDN + Next fetch-cache setup poisoned the build: a corrupted
// cached @latest woff made satori throw "Matched points out of range",
// failing every OGP prerender. Reading a pinned local file is deterministic
// and network-independent (consistent with the data layer reading from cwd).
const FONT_PATH = join(process.cwd(), "assets", "fonts", "noto-sans-jp-700.woff");

// Module-level font cache to avoid re-reading on every image render.
let _fontCache: ArrayBuffer | null = null;
function loadFont(): ArrayBuffer {
  if (_fontCache) return _fontCache;
  const buf = readFileSync(FONT_PATH);
  _fontCache = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  return _fontCache;
}

function truncate(text: string, max: number): string {
  return text.length > max ? text.slice(0, max - 1) + "…" : text;
}

type Props = { params: Promise<{ threadId: string }> };

export default async function Image({ params }: Props) {
  const { threadId } = await params;
  const thread = getThread(threadId);
  if (!thread) {
    // Parent route has dynamicParams = false, so this branch should not
    // be reachable in practice — but ImageResponse needs a valid return.
    return new ImageResponse(<div style={{ fontSize: 48 }}>OpenGIKAI</div>, size);
  }

  const members = getMembers();
  const actorIds = [...new Set(thread.speeches.map((s) => s.memberId))];
  const actorNames = actorIds
    .map((id) => members[id]?.name || "")
    .filter(Boolean);

  const topic = truncate(thread.topic, 40);
  const actorsLine = actorNames.slice(0, 4).join("  ");
  const source = thread.sourceLabel || "国会会議録";
  const fontData = loadFont();

  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          width: "100%",
          height: "100%",
          backgroundColor: "#15202b",
          padding: "48px 56px",
          color: "#e7e9ea",
          fontFamily: "Noto Sans JP",
        }}
      >
        {/* Top bar: brand mark + source */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 32,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 999,
                backgroundColor: "rgba(52, 211, 153, 0.2)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 20,
                fontWeight: 700,
                color: "#34d399",
              }}
            >
              議
            </div>
            <span style={{ fontSize: 22, fontWeight: 700 }}>OpenGIKAI</span>
          </div>
          <span style={{ fontSize: 16, color: "#8899a6" }}>{source}</span>
        </div>

        {/* Committee chip + date */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 16,
            marginBottom: 16,
          }}
        >
          <span
            style={{
              fontSize: 18,
              color: "#34d399",
              backgroundColor: "rgba(52, 211, 153, 0.1)",
              padding: "4px 16px",
              borderRadius: 999,
            }}
          >
            {thread.committee}
          </span>
          <span style={{ fontSize: 18, color: "#8899a6" }}>{thread.date}</span>
        </div>

        {/* Topic headline */}
        <div
          style={{
            fontSize: 36,
            fontWeight: 700,
            lineHeight: 1.3,
            flex: 1,
            display: "flex",
            alignItems: "center",
          }}
        >
          {topic}
        </div>

        {/* Speaker names */}
        {actorsLine && (
          <div
            style={{
              fontSize: 18,
              color: "#8899a6",
              marginTop: 16,
            }}
          >
            {actorsLine}
          </div>
        )}
      </div>
    ),
    {
      ...size,
      fonts: [
        {
          name: "Noto Sans JP",
          data: fontData,
          weight: 700,
          style: "normal",
        },
      ],
    }
  );
}
