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
    icon: "eco",
    color: "#fbbf24",
    bg: "rgba(251,191,36,0.1)",
    border: "rgba(251,191,36,0.3)",
  },
  {
    id: "teen",
    label: "標準",
    icon: "menu_book",
    color: "#34d399",
    bg: "rgba(52,211,153,0.1)",
    border: "rgba(52,211,153,0.3)",
  },
  {
    id: "adult",
    label: "詳しく",
    icon: "newspaper",
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
  追及: { icon: "bolt", color: "#ef4444", bg: "rgba(239,68,68,0.1)" },
  再追及: { icon: "local_fire_department", color: "#f97316", bg: "rgba(249,115,22,0.1)" },
  答弁: { icon: "chat_bubble", color: "#6b7280", bg: "rgba(107,114,128,0.1)" },
  確認: { icon: "help", color: "#8b5cf6", bg: "rgba(139,92,246,0.1)" },
  割込み: { icon: "front_hand", color: "#eab308", bg: "rgba(234,179,8,0.1)" },
  議事: { icon: "assignment", color: "#94a3b8", bg: "rgba(148,163,184,0.1)" },
};

// Source styling — visual hints to distinguish data origins in the feed
export const SOURCE_STYLE: Record<string, { icon: string; label: string; color: string }> = {
  ndl:     { icon: "account_balance", label: "国会",   color: "#6366f1" },
  kantei:  { icon: "podium",          label: "官邸",   color: "#f59e0b" },
  council: { icon: "groups",          label: "審議会", color: "#22c55e" },
};

export const TREND_PERIODS = ["今週", "今国会", "今年"] as const;

// Life themes for content discovery
export type LifeThemeId =
  | "economy"
  | "diplomacy"
  | "demographics"
  | "work"
  | "education"
  | "constitution"
  | "energy"
  | "society";

export type LifeTheme = {
  id: LifeThemeId;
  label: string;
  icon: string;
  color: string;
  description: string;
};

export const LIFE_THEMES: LifeTheme[] = [
  { id: "economy", label: "税金・家計", icon: "account_balance_wallet", color: "#fbbf24", description: "予算・消費税・金融政策など" },
  { id: "diplomacy", label: "外交・安全保障", icon: "public", color: "#60a5fa", description: "日米関係・防衛・国際情勢" },
  { id: "demographics", label: "少子高齢化", icon: "family_restroom", color: "#f472b6", description: "人口減少・子育て・高齢者支援" },
  { id: "work", label: "雇用・働き方", icon: "work", color: "#34d399", description: "労働改革・人材確保・公務員制度" },
  { id: "education", label: "教育・科学", icon: "school", color: "#a78bfa", description: "文部科学・図書館・研究" },
  { id: "constitution", label: "憲法・法制度", icon: "gavel", color: "#fb923c", description: "憲法改正・国際法・法の支配" },
  { id: "energy", label: "防災・エネルギー", icon: "bolt", color: "#f87171", description: "災害対策・燃料価格・環境" },
  { id: "society", label: "社会・多文化", icon: "diversity_3", color: "#38bdf8", description: "外国人政策・バリアフリー・地域格差" },
];

// Mapping from topicTag to life theme
// Tags not listed here are either procedural (議事, 委員会, etc.) or unmapped
export const TOPIC_TAG_TO_THEME: Record<string, LifeThemeId> = {
  // Economy
  消費税: "economy", 金融政策: "economy", 積極財政: "economy", 予算概要: "economy",
  予算案: "economy", 予算審議: "economy", R8予算: "economy", 予算: "economy",
  経済格差: "economy", 燃料対策: "economy",
  // Diplomacy & Security
  トランプ外交: "diplomacy", 日米協力: "diplomacy", 中国外交: "diplomacy",
  台湾海峡: "diplomacy", 防衛装備: "diplomacy", 米軍基地: "diplomacy",
  中東情勢: "diplomacy", イラン: "diplomacy", 米国秩序: "diplomacy",
  日朝関係: "diplomacy", 情報機能: "diplomacy",
  // Demographics
  人口動態: "demographics", 少子化: "demographics", 高齢化: "demographics",
  東京集中: "demographics", 地域格差: "demographics", 地方雇用: "demographics",
  // Work
  働き方: "work", 働方改革: "work", 人材確保: "work",
  官民交流: "work", 院独立性: "work",
  // Education
  図書: "education", 図書館: "education", 図書館予算: "education",
  // Constitution & Legal
  憲法改正: "constitution", 国際法: "constitution", 法の支配: "constitution",
  国際法意義: "constitution",
  // Energy & Disaster
  防災所信: "energy", 大雪追悼: "energy",
  // Society
  外国人: "society", バリア: "society", 価値観: "society",
  政権基盤: "society",
  // Council: regulatory reform (規制改革推進会議)
  規制改革: "economy", 基本方針: "economy", 分野別提案: "economy",
  総理決意: "economy", 総理総括: "economy", WG報告: "economy",
  年休算出: "work", シフト年休: "work",
  // Council: drone/startup WG
  VTOL免許: "economy", "レベル3.5": "economy", 国産強化: "economy",
  VTOL: "economy", 国産化: "economy",
  ホット処理: "economy", ホット: "economy",
  // Council: 二地域居住
  移住促進: "demographics", 二拠点事例: "demographics",
  交通実践: "demographics", 高知実践: "demographics",
  MaaS実践: "demographics", 各省施策: "demographics",
  施策方向: "demographics", 中間取: "demographics",
};

export function getLifeTheme(topicTag: string): LifeThemeId | null {
  return TOPIC_TAG_TO_THEME[topicTag] ?? null;
}

export function getLifeThemeConfig(id: LifeThemeId): LifeTheme | undefined {
  return LIFE_THEMES.find((t) => t.id === id);
}
