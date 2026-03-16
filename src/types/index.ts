export type Level = "easy" | "teen" | "adult";

export type TensionType = "追及" | "答弁" | "再追及" | "確認" | "割込み";

export type House = "衆議院" | "参議院";

export type Rank = "pm" | "minister" | "viceminister" | "member";

export type Member = {
  id: string;
  name: string;
  party: string | null;
  role: string;
  district: string | null;
  since: number | null;
  bio: string;
  stance: string[];
  rank: Rank;
  ndlId?: string;
};

export type Speech = {
  memberId: string;
  tension: TensionType;
  keywords: string[];
  quote: string;
  raw: string;
  sourceUrl?: string;
  summaries: Record<Level, string>;
};

export type Thread = {
  id: string;
  date: string;
  committee: string;
  house: House;
  topic: string;
  topicTag: string;
  topicColor: string;
  summary: string;
  speeches: Speech[];
};

// UI config types

export type LevelConfig = {
  id: Level;
  label: string;
  icon: string;
  color: string;
  bg: string;
  border: string;
};

export type RankBadge = {
  icon: string;
  label: string;
  color: string;
};

export type PartyStyle = {
  color: string;
  bg: string;
  border: string;
  short: string;
};

export type TensionStyle = {
  icon: string;
  color: string;
  bg: string;
};
