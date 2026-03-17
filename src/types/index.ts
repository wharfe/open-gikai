export type Level = "easy" | "teen" | "adult";

export type TensionType = "追及" | "答弁" | "再追及" | "確認" | "割込み";

export type House = "衆議院" | "参議院" | "内閣" | string;

export type Rank = "pm" | "minister" | "viceminister" | "member";

export type MemberLink = {
  label: string;
  url: string;
};

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
  links?: MemberLink[];
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

export type ThreadOutcome = {
  result?: "可決" | "否決" | "継続審議" | "審議中";
  resolution?: string;
  commitments: string[];
  status: "resolved" | "pending" | "ongoing";
};

export type ThreadLink = {
  threadId: string;
  relationship: "同一法案" | "関連議論" | "続き";
  topic: string;
  committee: string;
  date: string;
};

export type ThreadContext = {
  description: string;
  links?: { label: string; url: string }[];
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
  outcome?: ThreadOutcome;
  relatedThreads?: ThreadLink[];
  context?: ThreadContext;
  source?: string;       // "ndl" | "kantei" | "council" etc.
  sourceLabel?: string;  // "国会会議録" | "首相記者会見" etc.
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
