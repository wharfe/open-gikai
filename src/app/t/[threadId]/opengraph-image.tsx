import { ImageResponse } from "next/og";
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

const FONT_URL =
  "https://cdn.jsdelivr.net/fontsource/fonts/noto-sans-jp@latest/japanese-700-normal.woff";

async function loadFont(): Promise<ArrayBuffer> {
  // Next.js dedupes identical fetches within a worker, so the font is
  // downloaded at most once per build worker rather than per image.
  const res = await fetch(FONT_URL, { cache: "force-cache" });
  if (!res.ok) throw new Error(`Font fetch failed: ${res.status}`);
  return res.arrayBuffer();
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
  const fontData = await loadFont();

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
