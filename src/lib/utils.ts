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

// Procedural keywords that don't represent meaningful topics
const TREND_STOPWORDS = new Set([
  // Procedural
  "議事進行", "質疑終了", "質疑開始", "委員長", "感謝",
  "参考人紹介", "参考人意見", "参考人", "参考人招致", "参考人出頭", "参考人交代",
  "調査会進行", "議事運営", "開会宣言", "散会宣言", "審議進行",
  "採決", "動議", "異議なし", "賛成多数", "議長", "登壇",
  // Generic meeting tags (not topical)
  "議事", "議事説明", "会期", "開会", "閉会", "退任", "運営",
  "委員会", "専門委", "有識者", "座長指名", "意見交換", "意見表明",
  "事例発表", "報告説明", "状況報告", "質疑応答", "論点整理",
  "幹部挨拶", "役職挨拶", "任命手続", "議論", "日程",
  "会長選", "会長選出", "幹事選出", "委設立", "委員長選",
  "議長選", "副議長", "就任挨", "祝辞", "委員選", "理事配分",
  "小委設置", "会議設置", "小委員", "前委員長", "新委員長", "議長等",
  // Additional procedural terms from Diet proceedings
  "質疑時間", "委員会運営", "委員会案件", "要求大臣", "議事日程",
  "国務大臣演説", "記名投票", "緊急上程", "趣旨説明", "提案理由",
  "附帯決議", "請願", "陳情", "会派", "所信表明", "施政方針",
  "代表質問", "一般質疑", "総括質疑", "分科会", "公聴会",
  "特別委員会設置", "特別委設置", "調査承認", "閉会中審査",
  "継続調査", "継続審査", "委員派遣", "視察報告",
  "人事", "弔詞", "就任挨拶", "就任", "新体制", "会長選任",
  "本会議", "特別委", "補正予算",
]);

// Committee names should not appear as trending topics
const COMMITTEE_SUFFIX_RE = /委員会$/;

export function extractTrends(
  threads: { date: string; topicTag: string; keywords?: string[] }[],
  period?: "今週" | "今国会" | "今年",
  sessionStartDate?: string,
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
        // "今国会" — use session start date if available
        if (sessionStartDate) {
          const sessionStart = new Date(sessionStartDate);
          return threadDate >= sessionStart;
        }
        const sessionStart = new Date(now);
        sessionStart.setMonth(sessionStart.getMonth() - 6);
        return threadDate >= sessionStart;
      })
    : threads;

  const counts: Record<string, number> = {};
  for (const t of filtered) {
    const kws = t.keywords || [];
    for (const k of kws) {
      if (!TREND_STOPWORDS.has(k) && !COMMITTEE_SUFFIX_RE.test(k)) {
        counts[k] = (counts[k] || 0) + 1;
      }
    }
  }
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

export function buildThreadShare(
  thread: { id: string; committee: string; date: string; topic: string; topicTag: string; summary: string; memberIds?: string[]; speeches?: { memberId: string }[] },
  members: Record<string, Member>,
): string {
  const ids = thread.memberIds ?? [...new Set(thread.speeches?.map((s) => s.memberId) ?? [])];
  const actors = ids.map((id) => members[id]?.name?.split(" ")[0] ?? "").filter(Boolean);
  return [
    `📋【${thread.committee}・${thread.date}】`,
    `テーマ：${thread.topic}`,
    "",
    thread.summary,
    "",
    `登場：${actors.join("、")}`,
    `全${thread.speeches?.length ?? 0}発言 → https://open-gikai.net/t/${thread.id}`,
    `#OpenGIKAI #国会 #${thread.topicTag}`,
  ].join("\n");
}
