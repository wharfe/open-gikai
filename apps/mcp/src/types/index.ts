/**
 * Minimal types for the MCP server. Mirrors the relevant subset of the
 * root project's src/types/index.ts. Kept separate so this Vercel project
 * can build independently without TS path crossings outside its root.
 */

export type Level = "easy" | "teen" | "adult";

export type Member = {
  id: string;
  name: string;
  party: string | null;
  role: string;
  district: string | null;
  since: number | null;
  bio: string;
  stance: string[];
  rank: string;
  ndlId?: string;
  links?: { label: string; url: string }[];
};

export type Speech = {
  memberId: string;
  tension: string;
  keywords: string[];
  quote: string;
  raw: string;
  sourceUrl?: string;
  summaries: Record<Level, string>;
};

export type ThreadOutcome = {
  result?: string;
  resolution?: string;
  commitments: string[];
  status: string;
};

export type Thread = {
  id: string;
  date: string;
  committee: string;
  house: string;
  topic: string;
  topicTag: string;
  topicColor: string;
  summary: string;
  speeches: Speech[];
  outcome?: ThreadOutcome;
  source?: string;
  sourceLabel?: string;
  context?: {
    description: string;
    links?: { label: string; url: string }[];
    news?: { title: string; url: string; source: string; pubDate: string; image?: string }[];
  };
};
