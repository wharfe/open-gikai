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
  報告: { icon: "summarize", color: "#0ea5e9", bg: "rgba(14,165,233,0.1)" },
  要求: { icon: "gavel", color: "#dc2626", bg: "rgba(220,38,38,0.1)" },
  反対討論: { icon: "swap_horiz", color: "#f97316", bg: "rgba(249,115,22,0.1)" },
  説明: { icon: "description", color: "#6b7280", bg: "rgba(107,114,128,0.1)" },
  提案: { icon: "add", color: "#10b981", bg: "rgba(16,185,129,0.1)" },
};

// Neutral fallback for tension labels the summarizer may emit that aren't yet
// styled above. The summarizer writes `tension` as free text, so an unexpected
// value must degrade gracefully — a missing TENSION_STYLE entry would otherwise
// crash static prerender at TENSION_STYLE[x].color. validate-data.mjs still
// warns so we add a proper style later.
const DEFAULT_TENSION_STYLE: TensionStyle = {
  icon: "chat_bubble",
  color: "#6b7280",
  bg: "rgba(107,114,128,0.1)",
};

export function getTensionStyle(tension: string): TensionStyle {
  return TENSION_STYLE[tension] ?? DEFAULT_TENSION_STYLE;
}

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

// Pattern-based theme classification
// Each entry: [keywords to match in topicTag, theme ID]
const THEME_PATTERNS: [string[], LifeThemeId][] = [
  // Economy — tax, budget, finance, trade, industry
  [["税", "予算", "財政", "金融", "経済", "物価", "賃", "成長", "産業", "通商", "貿易", "規制改革", "中小企", "スタートアップ", "VTOL", "国産", "燃料", "ガソリン", "補助金", "交付金", "WG報告", "補正", "国債", "為替", "株", "投資", "競争力", "万博", "IR", "観光", "インバウンド", "知財", "特許", "土地"], "economy"],
  // Diplomacy & Security
  [["外交", "防衛", "安保", "日米", "米軍", "基地", "中国", "台湾", "北朝鮮", "韓国", "ロシア", "ウクライナ", "中東", "NATO", "国連", "PKO", "ミサイル", "核", "拉致", "サイバー", "情報機能", "海洋", "領土", "尖閣", "竹島", "地位協定", "邦人", "ODA", "ASEAN", "G7", "G20", "首脳"], "diplomacy"],
  // Demographics — population, regional, migration
  [["人口", "少子", "高齢", "過疎", "地方", "地域", "移住", "二地域", "創生", "一極", "東京集中", "国土", "広域", "中山間", "MaaS", "交通", "DX", "デジタル", "マイナ", "圏域", "担い手", "関係人口", "協力隊", "公共交通", "郵便", "離島", "過疎", "限界"], "demographics"],
  // Work — labor, employment
  [["雇用", "働", "労働", "人材", "賃上", "最賃", "年休", "シフト", "公務員", "官民", "テレワーク", "副業", "育休", "ハラスメント", "技能実習", "特定技能", "人身売買"], "work"],
  // Education & Science
  [["教育", "学校", "大学", "研究", "科学", "図書", "文科", "学術", "奨学", "いじめ", "不登校", "AI", "給食", "部活", "スポーツ", "文化"], "education"],
  // Constitution & Legal — politics, governance, law
  [["憲法", "法制", "国際法", "法の支配", "司法", "裁判", "刑法", "民法", "人権", "選挙", "政治改革", "政治資金", "政党", "国会運営", "議会", "行政", "公文書", "情報公開", "統計", "マイナンバ", "所信", "施政", "暫定"], "constitution"],
  // Energy & Disaster
  [["エネルギー", "原発", "再エネ", "脱炭素", "気候", "環境", "防災", "災害", "地震", "台風", "復興", "水害", "大雪", "噴火", "熊", "鳥獣", "PFAS", "汚染", "廃棄", "下水", "河川", "治水", "耐震"], "energy"],
  // Society — welfare, healthcare, housing, diversity
  [["医療", "介護", "福祉", "年金", "保険", "障害", "バリア", "住宅", "居住", "子育", "保育", "児童", "虐待", "外国人", "多文化", "入管", "難民", "更生", "刑務", "自殺", "孤独", "食品", "農", "水産", "漁業", "薬", "感染", "病床", "看護", "ワクチン", "健康", "生活保護", "貧困", "ひとり親", "DV"], "society"],
];

export function getLifeTheme(topicTag: string): LifeThemeId | null {
  for (const [keywords, theme] of THEME_PATTERNS) {
    if (keywords.some((kw) => topicTag.includes(kw))) return theme;
  }
  return null;
}

export function getLifeThemeConfig(id: LifeThemeId): LifeTheme | undefined {
  return LIFE_THEMES.find((t) => t.id === id);
}
