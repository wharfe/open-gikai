import { ImageResponse } from "next/og";
import { readFile } from "fs/promises";
import { join } from "path";
import { getThread, getMembers, getAllThreadIds } from "@/lib/data";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export function generateStaticParams() {
  return getAllThreadIds().map((threadId) => ({ threadId }));
}

async function loadFont() {
  const fontPath = join(process.cwd(), "src/app/fonts/NotoSansJP-Bold.ttf");
  return await readFile(fontPath);
}

export default async function OgImage({
  params,
}: {
  params: Promise<{ threadId: string }>;
}) {
  const { threadId } = await params;
  const thread = getThread(threadId);
  const fontData = await loadFont();

  if (!thread) {
    return new ImageResponse(
      <div style={{ display: "flex", width: "100%", height: "100%", backgroundColor: "#000" }} />,
      { ...size },
    );
  }

  const members = getMembers();
  const actors = [...new Set(thread.speeches.map((s) => s.memberId))]
    .map((id) => members[id]?.name || "")
    .filter(Boolean)
    .slice(0, 4);

  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          width: "100%",
          height: "100%",
          backgroundColor: "#000000",
          color: "#e7e9ea",
          fontFamily: "NotoSansJP",
          padding: "48px 56px",
        }}
      >
        {/* Top: committee & date */}
        <div style={{ display: "flex", alignItems: "center", fontSize: "24px", color: "#71767b" }}>
          {thread.house} · {thread.committee} · {thread.date}
        </div>

        {/* Middle: topic & summary */}
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
            <span
              style={{
                display: "flex",
                color: thread.topicColor,
                backgroundColor: `${thread.topicColor}20`,
                padding: "6px 16px",
                borderRadius: "24px",
                fontSize: "20px",
                fontWeight: 700,
              }}
            >
              {thread.topicTag}
            </span>
            {thread.outcome?.result && (
              <span
                style={{
                  display: "flex",
                  color: thread.outcome.result === "可決" ? "#22c55e" : "#ef4444",
                  backgroundColor:
                    thread.outcome.result === "可決"
                      ? "rgba(34,197,94,0.1)"
                      : "rgba(239,68,68,0.1)",
                  padding: "6px 16px",
                  borderRadius: "24px",
                  fontSize: "20px",
                  fontWeight: 700,
                }}
              >
                {thread.outcome.result}
              </span>
            )}
          </div>

          <div
            style={{
              display: "flex",
              fontSize: "40px",
              fontWeight: 700,
              lineHeight: 1.3,
              overflow: "hidden",
            }}
          >
            {thread.topic}
          </div>

          <div
            style={{
              display: "flex",
              fontSize: "24px",
              color: "#71767b",
              lineHeight: 1.5,
              overflow: "hidden",
            }}
          >
            {thread.summary}
          </div>
        </div>

        {/* Bottom: actors & branding */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-end",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            <div style={{ display: "flex", fontSize: "20px", color: "#71767b" }}>
              {actors.join("、")}
              {thread.speeches.length > 4 ? ` 他${thread.speeches.length - 4}名` : ""}
            </div>
            <div style={{ display: "flex", fontSize: "18px", color: "#71767b" }}>
              {thread.speeches.length}件の発言
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <div
              style={{
                display: "flex",
                width: "16px",
                height: "16px",
                borderRadius: "50%",
                backgroundColor: "#34d399",
                boxShadow: "0 0 12px rgba(52,211,153,0.5)",
              }}
            />
            <span style={{ display: "flex", fontSize: "28px", fontWeight: 700 }}>
              OpenGIKAI
            </span>
          </div>
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        {
          name: "NotoSansJP",
          data: fontData,
          weight: 700,
          style: "normal",
        },
      ],
    },
  );
}
