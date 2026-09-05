/**
 * Ministry extraction for government-witness (政府参考人) speaker pages.
 *
 * Single source of truth shared by Next.js pages (import "@/lib/ministry.mjs")
 * and node-run scripts (generate-sitemap.mjs / notify-indexnow.mjs), which
 * cannot import TypeScript — hence plain ESM with JSDoc types. Type
 * declarations live in ministry.d.mts.
 *
 * Adding a new ministry/agency (e.g. after a government reorganization):
 * append one entry to MINISTRIES below. Slugs are short romaji identifiers
 * used in /gov/{slug} URLs and must stay stable once published.
 */

/** @typedef {{ slug: string, name: string }} Ministry */

/** @type {ReadonlyArray<Ministry>} */
export const MINISTRIES = [
  { slug: "cas", name: "内閣官房" },
  { slug: "cao", name: "内閣府" },
  { slug: "digital", name: "デジタル庁" },
  { slug: "reconstruction", name: "復興庁" },
  { slug: "soumu", name: "総務省" },
  { slug: "moj", name: "法務省" },
  { slug: "mofa", name: "外務省" },
  { slug: "mof", name: "財務省" },
  { slug: "mext", name: "文部科学省" },
  { slug: "mhlw", name: "厚生労働省" },
  { slug: "maff", name: "農林水産省" },
  { slug: "meti", name: "経済産業省" },
  { slug: "mlit", name: "国土交通省" },
  { slug: "env", name: "環境省" },
  { slug: "mod", name: "防衛省" },
  { slug: "npa", name: "警察庁" },
  { slug: "fsa", name: "金融庁" },
  { slug: "caa", name: "消費者庁" },
  { slug: "cfa", name: "こども家庭庁" },
  { slug: "jcg", name: "海上保安庁" },
  { slug: "jta", name: "観光庁" },
  { slug: "bunka", name: "文化庁" },
  { slug: "fdma", name: "消防庁" },
  { slug: "rinya", name: "林野庁" },
  { slug: "jfa", name: "水産庁" },
  { slug: "chusho", name: "中小企業庁" },
  { slug: "jpo", name: "特許庁" },
  { slug: "enecho", name: "資源エネルギー庁" },
  { slug: "sports", name: "スポーツ庁" },
  { slug: "jma", name: "気象庁" },
  { slug: "nta", name: "国税庁" },
  { slug: "isa", name: "出入国在留管理庁" },
  { slug: "psia", name: "公安調査庁" },
  { slug: "atla", name: "防衛装備庁" },
  { slug: "jinjiin", name: "人事院" },
  { slug: "jbaudit", name: "会計検査院" },
  { slug: "jftc", name: "公正取引委員会" },
  { slug: "kunaicho", name: "宮内庁" },
];

// Political appointee titles that begin with a ministry name. Slug-ID
// politicians are already excluded by the m_ check, but this guards against
// politicians ever receiving an m_ id from the pipeline.
const POLITICAL_TITLE_PREFIXES = [
  "内閣官房長官",
  "内閣官房副長官",
  "内閣府特命担当大臣",
  "内閣府副大臣",
  "内閣府大臣政務官",
];

// Longest name first so the most specific prefix wins.
const BY_LENGTH = [...MINISTRIES].sort((a, b) => b.name.length - a.name.length);

/**
 * Resolve the ministry a government-witness member belongs to, or null.
 *
 * Enforced inside this API (callers must NOT re-implement):
 * - Only m_-prefixed IDs (政府参考人系). Politicians with ministry-prefixed
 *   roles (内閣官房長官 etc.) all have slug IDs in real data.
 * - Political-title blocklist as a second defense layer.
 * - NO rank-based filtering: members.json rank misclassifies bureaucrats
 *   (e.g. 気象庁長官 is rank "minister").
 *
 * @param {{ id: string, role?: string | null }} member
 * @returns {Ministry | null}
 */
export function getMemberMinistry(member) {
  if (!member || typeof member.id !== "string" || !member.id.startsWith("m_")) {
    return null;
  }
  if (typeof member.role !== "string") return null;
  const role = member.role;
  if (!role) return null;
  if (POLITICAL_TITLE_PREFIXES.some((t) => role.startsWith(t))) return null;
  return BY_LENGTH.find((m) => role.startsWith(m.name)) ?? null;
}
