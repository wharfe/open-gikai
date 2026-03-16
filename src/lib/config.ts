import type {
  LevelConfig,
  RankBadge,
  PartyStyle,
  TensionStyle,
  Rank,
} from "@/types";

export const LEVELS: LevelConfig[] = [
  {
    id: "easy",
    label: "やさしく",
    icon: "🌱",
    color: "#fbbf24",
    bg: "rgba(251,191,36,0.1)",
    border: "rgba(251,191,36,0.3)",
  },
  {
    id: "teen",
    label: "標準",
    icon: "📖",
    color: "#34d399",
    bg: "rgba(52,211,153,0.1)",
    border: "rgba(52,211,153,0.3)",
  },
  {
    id: "adult",
    label: "詳しく",
    icon: "📰",
    color: "#7dd3fc",
    bg: "rgba(125,211,252,0.08)",
    border: "rgba(125,211,252,0.2)",
  },
];

export const RANK_BADGE: Partial<Record<Rank, RankBadge>> = {
  pm: { icon: "🔶", label: "首相", color: "#f59e0b" },
  minister: { icon: "🔷", label: "閣僚", color: "#60a5fa" },
  viceminister: { icon: "🔹", label: "副大臣", color: "#93c5fd" },
};

export const PARTY_STYLE: Record<string, PartyStyle> = {
  立憲民主党: {
    color: "#60a5fa",
    bg: "rgba(96,165,250,0.12)",
    border: "rgba(96,165,250,0.3)",
    short: "立憲",
  },
  日本維新の会: {
    color: "#c084fc",
    bg: "rgba(192,132,252,0.12)",
    border: "rgba(192,132,252,0.3)",
    short: "維新",
  },
  自由民主党: {
    color: "#fca5a5",
    bg: "rgba(252,165,165,0.12)",
    border: "rgba(252,165,165,0.3)",
    short: "自民",
  },
  日本共産党: {
    color: "#fb923c",
    bg: "rgba(251,146,60,0.12)",
    border: "rgba(251,146,60,0.3)",
    short: "共産",
  },
};

export const MINISTER_STYLE: PartyStyle = {
  color: "#94a3b8",
  bg: "rgba(148,163,184,0.1)",
  border: "rgba(148,163,184,0.2)",
  short: "大臣",
};

export const TENSION_STYLE: Record<string, TensionStyle> = {
  追及: { icon: "⚡", color: "#ef4444", bg: "rgba(239,68,68,0.1)" },
  再追及: { icon: "🔥", color: "#f97316", bg: "rgba(249,115,22,0.1)" },
  答弁: { icon: "💬", color: "#6b7280", bg: "rgba(107,114,128,0.1)" },
  確認: { icon: "❓", color: "#8b5cf6", bg: "rgba(139,92,246,0.1)" },
  割込み: { icon: "✋", color: "#eab308", bg: "rgba(234,179,8,0.1)" },
};

export const TREND_PERIODS = ["今週", "今国会", "今年"] as const;
