import type { Level, Member, Speech, Thread } from "@/types";
import {
  PARTY_STYLE,
  MINISTER_STYLE,
  TENSION_STYLE,
} from "@/lib/config";
import type { PartyStyle } from "@/types";

export function getStyle(member: Member): PartyStyle {
  return member.party
    ? PARTY_STYLE[member.party] || MINISTER_STYLE
    : MINISTER_STYLE;
}

export function extractTrends(
  threads: Thread[],
  period?: "今週" | "今国会" | "今年",
): [string, number][] {
  // Filter threads by period based on date field (YYYY.MM.DD format)
  const now = new Date();
  const filtered = period
    ? threads.filter((t) => {
        const parts = t.date.split(".");
        if (parts.length !== 3) return true;
        const threadDate = new Date(
          parseInt(parts[0]),
          parseInt(parts[1]) - 1,
          parseInt(parts[2]),
        );
        if (period === "今週") {
          const weekAgo = new Date(now);
          weekAgo.setDate(weekAgo.getDate() - 7);
          return threadDate >= weekAgo;
        }
        if (period === "今年") {
          return threadDate.getFullYear() === now.getFullYear();
        }
        // "今国会" — current Diet session, approximate as last 6 months
        const sessionStart = new Date(now);
        sessionStart.setMonth(sessionStart.getMonth() - 6);
        return threadDate >= sessionStart;
      })
    : threads;

  const counts: Record<string, number> = {};
  filtered.forEach((t) =>
    t.speeches.forEach((s) =>
      s.keywords.forEach((k) => {
        counts[k] = (counts[k] || 0) + 1;
      })
    )
  );
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10);
}

export function buildSpeechShare(
  speech: Speech,
  member: Member,
  thread: Thread,
  level: Level
): string {
  const t = TENSION_STYLE[speech.tension];
  return [
    `${t.icon}【${thread.committee}・${thread.date}】`,
    `${member.name}（${member.party || member.role}）`,
    "",
    speech.summaries[level],
    "",
    `📄 全スレッド → https://open-gikai.net/t/${thread.id}`,
    `#OpenGIKAI #国会 #${thread.topicTag}`,
  ].join("\n");
}

export function buildThreadShare(thread: Thread, members: Record<string, Member>): string {
  const actors = [
    ...new Set(thread.speeches.map((s) => s.memberId)),
  ].map((id) => members[id].name.split(" ")[0]);
  return [
    `📋【${thread.committee}・${thread.date}】`,
    `テーマ：${thread.topic}`,
    "",
    thread.summary,
    "",
    `登場：${actors.join("、")}`,
    `全${thread.speeches.length}発言 → https://open-gikai.net/t/${thread.id}`,
    `#OpenGIKAI #国会 #${thread.topicTag}`,
  ].join("\n");
}
