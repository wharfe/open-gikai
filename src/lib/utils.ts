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
  threads: Thread[]
): [string, number][] {
  const counts: Record<string, number> = {};
  threads.forEach((t) =>
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
    `📄 全スレッド → https://gikai.jp/t/${thread.id}`,
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
    `全${thread.speeches.length}発言 → https://gikai.jp/t/${thread.id}`,
    `#OpenGIKAI #国会 #${thread.topicTag}`,
  ].join("\n");
}
