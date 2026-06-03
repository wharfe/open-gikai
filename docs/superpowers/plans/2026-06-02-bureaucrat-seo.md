# 官僚ページSEO強化 + Googleインデックス拡大 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 省庁別ハブページ(`/gov`, `/gov/[slug]`)を新設し、メンバーページのdescription動的化・内部リンク強化・sitemap/IndexNow組み込みを行う。

**Architecture:** 省庁抽出は plain ESM の `src/lib/ministry.mjs` を単一実装とし、Next.js(TS)と node 実行スクリプト(sitemap/IndexNow)の両方から import する。発言者別統計は `src/lib/data.ts` の `getMemberStats()`(モジュールキャッシュ)に集約し、/gov ページとメンバーページ description の両方が使う。すべてビルド時の決定論的処理で、LLM は使わない。

**Tech Stack:** Next.js 16 (App Router, `output: "export"`), TypeScript strict, Tailwind CSS, Node built-in test runner (`node --test`)

**Spec:** `docs/superpowers/specs/2026-06-02-bureaucrat-seo-design.md`

**前提知識(このリポジトリ固有):**
- `data/members.json` は `Record<string, Member>`(辞書)。官僚は ID が `m_` で始まる(551人)。`Member` 型は `src/types/index.ts`
- `Thread.date` は `"YYYY.MM.DD"`(ドット区切り)。sitemap では `-` 区切りに変換している
- build は `node scripts/*.mjs && next build`。**tsx / ts-node は無い**ので scripts から TS は import 不可
- `tsconfig.json` は `allowJs: true`, `moduleResolution: "bundler"`, alias `@/* → ./src/*`
- データローダーは `src/lib/data.ts` のモジュールスコープ変数でキャッシュする(Vercel Hobby 1ワーカーでのビルド時間対策。既存コメント参照)
- ページの見た目は `src/app/council/[slug]/page.tsx` の X(Twitter)風パターンを踏襲(`MobileHeader`, sticky header, `border-x-border` 系クラス)
- コードコメントは英語、UIテキストは日本語

---

### Task 1: 省庁抽出モジュール `src/lib/ministry.mjs`

**Files:**
- Create: `src/lib/ministry.mjs`
- Create: `src/lib/ministry.d.mts`
- Test: `tests/unit/ministry.test.mjs`

- [ ] **Step 1: Write the failing test**

`tests/unit/ministry.test.mjs` を新規作成:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { getMemberMinistry, MINISTRIES } from "../../src/lib/ministry.mjs";

test("bureaucrat with ministry-prefixed role maps to that ministry", () => {
  assert.equal(
    getMemberMinistry({ id: "m_f107fb47", role: "総務省自治税務局長" })?.slug,
    "soumu",
  );
  assert.equal(
    getMemberMinistry({ id: "m_8f446114", role: "海上保安庁次長" })?.slug,
    "jcg",
  );
});

test("内閣官房 and 内閣府 are distinguished", () => {
  assert.equal(
    getMemberMinistry({ id: "m_x", role: "内閣官房内閣審議官" })?.slug,
    "cas",
  );
  assert.equal(
    getMemberMinistry({ id: "m_x", role: "内閣府大臣官房審議官" })?.slug,
    "cao",
  );
});

test("slug-ID politicians are excluded even when role matches a ministry", () => {
  // Real data: 林芳正(内閣官房長官) has a slug ID, not m_
  assert.equal(
    getMemberMinistry({ id: "hayashiyoshimasa", role: "内閣官房長官" }),
    null,
  );
});

test("political titles are excluded even with an m_ id (defense layer)", () => {
  assert.equal(
    getMemberMinistry({ id: "m_evil1", role: "内閣官房長官" }),
    null,
  );
  assert.equal(
    getMemberMinistry({
      id: "m_evil2",
      role: "内閣府特命担当大臣（経済財政政策）",
    }),
    null,
  );
});

test("agency chiefs map regardless of their (noisy) rank field", () => {
  // Real data: 気象庁長官 is rank:"minister" in members.json — rank must NOT
  // be used for filtering (it would drop genuine bureaucrats)
  assert.equal(
    getMemberMinistry({ id: "m_40667609", role: "気象庁長官" })?.slug,
    "jma",
  );
});

test("unmatched or empty roles return null", () => {
  assert.equal(getMemberMinistry({ id: "m_a", role: "中央大学文学部教授" }), null);
  assert.equal(getMemberMinistry({ id: "m_b", role: "" }), null);
  assert.equal(getMemberMinistry({ id: "m_c", role: "委員" }), null);
});

test("every ministry has a unique non-empty slug and name", () => {
  const slugs = new Set(MINISTRIES.map((m) => m.slug));
  const names = new Set(MINISTRIES.map((m) => m.name));
  assert.equal(slugs.size, MINISTRIES.length);
  assert.equal(names.size, MINISTRIES.length);
  for (const m of MINISTRIES) {
    assert.ok(m.slug.length > 0);
    assert.ok(m.name.length > 0);
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test "tests/unit/*.test.mjs"`
Expected: FAIL — `Cannot find module .../src/lib/ministry.mjs`

- [ ] **Step 3: Write the implementation**

`src/lib/ministry.mjs` を新規作成:

```js
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
  const role = member.role || "";
  if (!role) return null;
  if (POLITICAL_TITLE_PREFIXES.some((t) => role.startsWith(t))) return null;
  return BY_LENGTH.find((m) => role.startsWith(m.name)) ?? null;
}
```

`src/lib/ministry.d.mts` を新規作成:

```ts
export type Ministry = { slug: string; name: string };
export const MINISTRIES: ReadonlyArray<Ministry>;
export function getMemberMinistry(member: {
  id: string;
  role?: string | null;
}): Ministry | null;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test "tests/unit/*.test.mjs"`
Expected: PASS (7 tests)

- [ ] **Step 5: 実データでの分類結果を確認(スポットチェック)**

Run:
```bash
node -e "
import('./src/lib/ministry.mjs').then(async ({ getMemberMinistry }) => {
  const fs = await import('fs');
  const members = JSON.parse(fs.readFileSync('data/members.json', 'utf-8'));
  let matched = 0, politicians = 0;
  for (const m of Object.values(members)) {
    const min = getMemberMinistry(m);
    if (min) matched++;
    if (min && !m.id.startsWith('m_')) politicians++;
  }
  console.log('matched:', matched, 'politician leaks:', politicians);
});
"
```
Expected: `matched: 330` 前後(±10)、`politician leaks: 0`(0でなければバグ)

- [ ] **Step 6: Commit**

```bash
git add src/lib/ministry.mjs src/lib/ministry.d.mts tests/unit/ministry.test.mjs
git commit -m "feat: add deterministic ministry extraction for bureaucrat pages"
```

---

### Task 2: 発言者別統計 `getMemberStats()` と省庁ロスター `getMinistryRosters()`

**Files:**
- Modify: `src/lib/data.ts`(`getAllMemberIds` の直後、261行目付近に追記)

- [ ] **Step 1: data.ts に統計関数を追加**

`src/lib/data.ts` の import 群に追加:

```ts
import { getMemberMinistry } from "@/lib/ministry.mjs";
```

`getAllMemberIds()` の直後に追加:

```ts
// --- Per-member speech statistics (shared by /gov pages and /m metadata) ---

export type MemberStats = {
  speechCount: number;
  /** Same "YYYY.MM.DD" format as Thread.date. */
  latestDate: string;
  latestCommittee: string;
};

let _memberStatsCache: Map<string, MemberStats> | null = null;

/** Aggregate speech count / latest appearance per memberId over all threads. */
export function getMemberStats(): Map<string, MemberStats> {
  if (_memberStatsCache) return _memberStatsCache;
  const stats = new Map<string, MemberStats>();
  for (const t of loadThreads()) {
    const perThread = new Map<string, number>();
    for (const s of t.speeches) {
      if (!s.memberId) continue;
      perThread.set(s.memberId, (perThread.get(s.memberId) || 0) + 1);
    }
    for (const [id, count] of perThread) {
      const prev = stats.get(id);
      if (!prev) {
        stats.set(id, {
          speechCount: count,
          latestDate: t.date,
          latestCommittee: t.committee,
        });
      } else {
        prev.speechCount += count;
        if (t.date.localeCompare(prev.latestDate) > 0) {
          prev.latestDate = t.date;
          prev.latestCommittee = t.committee;
        }
      }
    }
  }
  _memberStatsCache = stats;
  return stats;
}

// --- Ministry rosters for /gov pages ---

export type MinistryRosterEntry = {
  member: Member;
  speechCount: number;
  latestDate: string;
  latestCommittee: string;
};

export type MinistryRoster = {
  slug: string;
  name: string;
  entries: MinistryRosterEntry[];
  totalSpeeches: number;
};

let _ministryRostersCache: MinistryRoster[] | null = null;

/**
 * Group government witnesses by ministry. Only members with at least one
 * recorded speech are listed (members.json contains a few entries never
 * referenced by threads). Entries are sorted by speech count.
 */
export function getMinistryRosters(): MinistryRoster[] {
  if (_ministryRostersCache) return _ministryRostersCache;
  const stats = getMemberStats();
  const bySlug = new Map<string, MinistryRoster>();
  for (const member of Object.values(loadMembers())) {
    const ministry = getMemberMinistry(member);
    if (!ministry) continue;
    const s = stats.get(member.id);
    if (!s) continue;
    let roster = bySlug.get(ministry.slug);
    if (!roster) {
      roster = {
        slug: ministry.slug,
        name: ministry.name,
        entries: [],
        totalSpeeches: 0,
      };
      bySlug.set(ministry.slug, roster);
    }
    roster.entries.push({
      member,
      speechCount: s.speechCount,
      latestDate: s.latestDate,
      latestCommittee: s.latestCommittee,
    });
    roster.totalSpeeches += s.speechCount;
  }
  for (const r of bySlug.values()) {
    r.entries.sort((a, b) => b.speechCount - a.speechCount);
  }
  _ministryRostersCache = [...bySlug.values()].sort(
    (a, b) => b.entries.length - a.entries.length,
  );
  return _ministryRostersCache;
}

export function getMinistrySlugs(): string[] {
  return getMinistryRosters().map((r) => r.slug);
}
```

- [ ] **Step 2: 型チェックとlintを通す**

Run: `npx tsc --noEmit && npm run lint`
Expected: エラーなし(`@/lib/ministry.mjs` の解決は `allowJs: true` + `ministry.d.mts` で通る。通らない場合は d.mts のファイル名・export を確認)

- [ ] **Step 3: Commit**

```bash
git add src/lib/data.ts
git commit -m "feat: add per-member speech stats and ministry rosters to data layer"
```

---

### Task 3: `/gov` 省庁一覧ページと `/gov/[slug]` 省庁別ページ

**Files:**
- Create: `src/app/gov/page.tsx`
- Create: `src/app/gov/[slug]/page.tsx`

- [ ] **Step 1: `/gov` 一覧ページを作成**

`src/app/gov/page.tsx` を新規作成:

```tsx
import type { Metadata } from "next";
import Link from "next/link";
import { getMinistryRosters } from "@/lib/data";
import { MobileHeader } from "@/components/layout/header";

const TITLE = "省庁別 発言者一覧";
const DESCRIPTION =
  "国会・審議会で答弁した省庁別の発言者(局長・審議官など政府参考人)一覧。すべての発言にAI要約と国会会議録の原文リンク付き。";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/gov" },
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: "website",
    url: "https://open-gikai.net/gov",
    siteName: "OpenGIKAI",
  },
};

export default function GovIndexPage() {
  const rosters = getMinistryRosters();

  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "ホーム", item: "https://open-gikai.net" },
      { "@type": "ListItem", position: 2, name: TITLE },
    ],
  };

  return (
    <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
      <MobileHeader />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumb) }}
      />

      <div className="sticky top-0 z-40 flex h-[53px] items-center bg-x-bg/65 px-4 backdrop-blur-xl">
        <h1 className="text-[17px] font-bold">{TITLE}</h1>
      </div>

      <p className="border-b border-x-border px-4 py-3 text-[13px] text-x-secondary">
        国会・審議会で答弁した各省庁の政府参考人(局長・審議官など)を、発言記録とあわせて一覧できます。
      </p>

      {rosters.map((r) => (
        <Link
          key={r.slug}
          href={`/gov/${r.slug}`}
          className="block border-b border-x-border px-4 py-3 transition-colors hover:bg-x-hover"
        >
          <div className="text-[15px] font-bold text-x-text">{r.name}</div>
          <div className="mt-0.5 text-[13px] text-x-secondary">
            発言者{r.entries.length}人 · {r.totalSpeeches}発言
          </div>
        </Link>
      ))}
    </main>
  );
}
```

- [ ] **Step 2: `/gov/[slug]` 省庁別ページを作成**

`src/app/gov/[slug]/page.tsx` を新規作成:

```tsx
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { getMinistryRosters, getMinistrySlugs } from "@/lib/data";
import { MobileHeader } from "@/components/layout/header";

export const dynamicParams = false;

export function generateStaticParams() {
  return getMinistrySlugs().map((slug) => ({ slug }));
}

type Props = {
  params: Promise<{ slug: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const roster = getMinistryRosters().find((r) => r.slug === slug);
  if (!roster) return {};

  const title = `${roster.name}の国会発言者一覧（局長・審議官など）`;
  const description = `${roster.name}の政府参考人${roster.entries.length}人の国会・審議会での発言${roster.totalSpeeches}件をAI要約付きで掲載。役職・発言数・直近の発言日から各発言者のページへ。`;

  return {
    title,
    description,
    alternates: { canonical: `/gov/${slug}` },
    openGraph: {
      title,
      description,
      type: "website",
      url: `https://open-gikai.net/gov/${slug}`,
      siteName: "OpenGIKAI",
    },
  };
}

export default async function GovMinistryPage({ params }: Props) {
  const { slug } = await params;
  const roster = getMinistryRosters().find((r) => r.slug === slug);
  if (!roster) notFound();

  const itemList = {
    "@context": "https://schema.org",
    "@type": "ItemList",
    name: `${roster.name}の国会発言者一覧`,
    itemListElement: roster.entries.map((e, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: e.member.name,
      url: `https://open-gikai.net/m/${e.member.id}`,
    })),
  };

  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "ホーム", item: "https://open-gikai.net" },
      { "@type": "ListItem", position: 2, name: "省庁別 発言者一覧", item: "https://open-gikai.net/gov" },
      { "@type": "ListItem", position: 3, name: roster.name },
    ],
  };

  return (
    <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
      <MobileHeader />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(itemList) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumb) }}
      />

      <div className="sticky top-0 z-40 flex h-[53px] items-center gap-3 bg-x-bg/65 px-4 backdrop-blur-xl">
        <Link
          href="/gov"
          className="flex h-9 w-9 items-center justify-center rounded-full text-x-text transition-colors hover:bg-x-hover"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 20 }}>arrow_back</span>
        </Link>
        <div className="min-w-0">
          <h1 className="truncate text-[17px] font-bold leading-tight">
            {roster.name}の国会発言者
          </h1>
          <div className="text-[13px] text-x-secondary">
            {roster.entries.length}人 · {roster.totalSpeeches}発言
          </div>
        </div>
      </div>

      {roster.entries.map((e) => (
        <Link
          key={e.member.id}
          href={`/m/${e.member.id}`}
          className="block border-b border-x-border px-4 py-3 transition-colors hover:bg-x-hover"
        >
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-[15px] font-bold text-x-text">{e.member.name}</span>
            <span className="shrink-0 text-[13px] text-x-secondary">{e.speechCount}発言</span>
          </div>
          <div className="mt-0.5 text-[13px] text-x-secondary">{e.member.role}</div>
          <div className="mt-0.5 text-[12px] text-x-secondary/60">
            直近の発言: {e.latestDate} {e.latestCommittee}
          </div>
        </Link>
      ))}
    </main>
  );
}
```

- [ ] **Step 3: ビルドして生成結果をスポットチェック**

Run: `npm run build`
Expected: ビルド成功。続けて:

```bash
ls out/gov/ | head
grep -o "五十嵐徹人" out/gov/mlit.html | head -1
grep -o "国土交通省鉄道局長" out/gov/mlit.html | head -1
grep -c "内閣官房長官" out/gov/cas.html || echo "0 — politicians correctly absent"
```
Expected: `out/gov/` に `mlit.html` 等の省庁ページが生成され、国交省ページに「五十嵐徹人」「国土交通省鉄道局長」が含まれる。`cas.html` に「内閣官房長官」が**含まれない**(政治家混入なし)

- [ ] **Step 4: Commit**

```bash
git add src/app/gov/
git commit -m "feat: add /gov ministry hub pages for government witnesses"
```

---

### Task 4: メンバーページの description 動的化・パンくず・役職リンク

**Files:**
- Modify: `src/app/m/[memberId]/page.tsx`
- Modify: `src/components/member/member-profile-view.tsx`(役職表示部、約109-114行目)

- [ ] **Step 1: `page.tsx` の generateMetadata を書き換え**

`src/app/m/[memberId]/page.tsx` の import に追加:

```ts
import { getMember, getMembers, getThreads, getAllMemberIds, getMemberStats } from "@/lib/data";
import { getMemberMinistry } from "@/lib/ministry.mjs";
```

(既存の `getMember, getMembers, getThreads, getAllMemberIds` の import 行を上記に置き換える)

`generateMetadata` 内の `description` 定義(23行目)を置き換え:

```ts
  const stats = getMemberStats().get(memberId);
  const namePart = member.role ? `${member.name}（${member.role}）` : member.name;
  const description = stats
    ? `${namePart}の国会・審議会での発言${stats.speechCount}件をAI要約付きで掲載。直近は${stats.latestDate}の${stats.latestCommittee}。`
    : `${member.name}の国会・審議会での発言をスレッド形式で閲覧。AI要約付きで審議の文脈がわかります。`;
```

- [ ] **Step 2: パンくず JSON-LD に省庁階層を挿入**

同ファイルの `MemberPage` コンポーネント内、`breadcrumb` 定義(67-75行目)を置き換え:

```ts
  const ministry = getMemberMinistry(member);
  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "ホーム", item: "https://open-gikai.net" },
      { "@type": "ListItem", position: 2, name: "発言者一覧", item: "https://open-gikai.net/members" },
      ...(ministry
        ? [{ "@type": "ListItem", position: 3, name: ministry.name, item: `https://open-gikai.net/gov/${ministry.slug}` }]
        : []),
      { "@type": "ListItem", position: ministry ? 4 : 3, name: member.name },
    ],
  };
```

`MemberProfileView` の呼び出し(89行目)に `ministry` prop を追加:

```tsx
        <MemberProfileView member={member} threads={threads} members={members} ministry={ministry} />
```

- [ ] **Step 3: `MemberProfileView` に役職リンクを追加**

`src/components/member/member-profile-view.tsx`(`Link` は import 済み):

1. Props 型(10-14行目)を置き換え:

```ts
type MemberProfileViewProps = {
  member: Member;
  threads: Thread[];
  members: Record<string, Member>;
  ministry?: { slug: string; name: string } | null;
};
```

2. 分割代入(16-19行目)を置き換え:

```ts
export function MemberProfileView({
  member,
  threads,
  ministry,
}: MemberProfileViewProps) {
```

3. 役職表示部(約109-114行目、`{/* Role info */}` ブロック)を置き換え:

```tsx
        {/* Role info */}
        <div className="mt-1.5 text-[15px] text-x-secondary">
          {ministry ? (
            <Link
              href={`/gov/${ministry.slug}`}
              className="text-x-accent hover:underline"
            >
              {member.role}
            </Link>
          ) : (
            member.role
          )}
          {member.district ? ` · ${member.district}` : ""}
          {member.since ? ` · ${member.since}年〜` : ""}
        </div>
```

- [ ] **Step 4: ビルドして確認**

Run: `npx tsc --noEmit && npm run build`
Expected: 成功。続けて:

```bash
grep -o "発言[0-9]*件をAI要約付きで掲載" out/m/m_f107fb47.html | head -1
grep -o "/gov/soumu" out/m/m_f107fb47.html | head -1
grep -o "の国会・審議会での発言をスレッド形式で閲覧" out/m/hayashiyoshimasa.html | head -1 && grep -c "/gov/" out/m/hayashiyoshimasa.html
```
Expected: 寺崎秀俊(m_f107fb47, 総務省)のページに件数入り description と `/gov/soumu` リンクがある。林芳正(政治家)のページは `/gov/` リンクが **0件**

- [ ] **Step 5: Commit**

```bash
git add src/app/m/ src/components/member/member-profile-view.tsx
git commit -m "feat: per-member descriptions, ministry breadcrumb and role links on member pages"
```

---

### Task 5: sitemap に `sitemap-gov.xml` を追加

**Files:**
- Modify: `scripts/generate-sitemap.mjs`

- [ ] **Step 1: 省庁ページの sitemap を生成する**

`scripts/generate-sitemap.mjs` の import に追加(10行目の後):

```js
import { getMemberMinistry } from "../src/lib/ministry.mjs";
```

`buildSitemaps()` 内、`// 5. Digest pages` ブロックの後・`writeSitemapIndex` の前に追加:

```js
  // 6. Gov (ministry hub) pages — lastmod = latest debate any of the
  // ministry's witnesses appeared in. Same membership rule as the pages
  // themselves: getMemberMinistry() + at least one recorded speech.
  const members = JSON.parse(
    readFileSync(join(DATA_DIR, "members.json"), "utf-8")
  );
  const govLastmod = new Map(); // slug -> latest isoDate
  for (const [id, m] of Object.entries(members)) {
    const ministry = getMemberMinistry(m);
    if (!ministry) continue;
    const lm = memberLastmod.get(id);
    if (!lm) continue; // skip members with no recorded speeches
    const prev = govLastmod.get(ministry.slug);
    if (!prev || lm > prev) govLastmod.set(ministry.slug, lm);
  }
  const govSlugs = [...govLastmod.keys()].sort();
  files.push(
    writeSitemap("sitemap-gov.xml", [
      urlEntry({ loc: "/gov", lastmod: siteLastmod, changefreq: "weekly", priority: "0.7" }),
      ...govSlugs.map((slug) =>
        urlEntry({
          loc: `/gov/${slug}`,
          lastmod: govLastmod.get(slug),
          changefreq: "weekly",
          priority: "0.7",
        })
      ),
    ])
  );
```

最後の `console.log`(225-228行目)の文言に gov を追加:

```js
  console.log(
    `Sitemaps generated: ${threads.length} threads, ${allMemberIds.length} members, ` +
      `${weekIds.length} digests, ${councilSlugs.length} councils, ${govSlugs.length} gov pages → ` +
      `${files.length} files + sitemap_index.xml`
  );
```

- [ ] **Step 2: 実行して出力を確認**

Run: `node scripts/generate-sitemap.mjs`
Expected: `... N gov pages → 6 files + sitemap_index.xml` と表示。続けて:

```bash
grep -c "<loc>" public/sitemap-gov.xml
grep "sitemap-gov.xml" public/sitemap_index.xml
grep "/gov/mlit" public/sitemap-gov.xml
```
Expected: sitemap-gov.xml に30件前後のURL、sitemap_index.xml から参照され、`/gov/mlit` が含まれる

- [ ] **Step 3: Commit**

```bash
git add scripts/generate-sitemap.mjs public/sitemap-gov.xml public/sitemap_index.xml
git commit -m "feat: include /gov ministry pages in sitemap index"
```

---

### Task 6: IndexNow に `/gov` URL を追加

**Files:**
- Modify: `scripts/notify-indexnow.mjs`(`collectNewUrls`、35-66行目)

- [ ] **Step 1: 当日発言者の省庁ページを送信対象に追加**

`scripts/notify-indexnow.mjs` の import に追加:

```js
import { getMemberMinistry } from "../src/lib/ministry.mjs";
```

`collectNewUrls()` 内、member URL 収集ブロック(`for (const id of memberIds) { urls.push(...) }`)の直後・`// Always include the home page` の前に追加:

```js
  // Gov (ministry hub) pages: any speech by a ministry's witness changes the
  // hub's speech counts / latest-date ordering, so notify for ALL of the
  // day's speakers, not only new ones.
  const membersPath = join(DATA_DIR, "members.json");
  if (existsSync(membersPath)) {
    const members = JSON.parse(readFileSync(membersPath, "utf-8"));
    const govSlugs = new Set();
    for (const id of memberIds) {
      const ministry = members[id] ? getMemberMinistry(members[id]) : null;
      if (ministry) govSlugs.add(ministry.slug);
    }
    if (govSlugs.size > 0) {
      urls.push(`${BASE_URL}/gov`);
      for (const slug of [...govSlugs].sort()) {
        urls.push(`${BASE_URL}/gov/${slug}`);
      }
    }
  }
```

※ `DATA_DIR` / `existsSync` / `readFileSync` / `join` は同ファイルで定義・import 済み。無い場合は既存の import 行(`fs` / `path`)に追加する。

- [ ] **Step 2: ドライランで確認**

直近にスレッドがある日付で実行(`ls data/threads/ | tail -3` で確認した日付を使う):

```bash
node scripts/notify-indexnow.mjs --date $(ls data/threads/ | grep -oP '\d{4}-\d{2}-\d{2}' | tail -1)
```
Expected: URL一覧に `/gov` と `/gov/{slug}` が含まれて送信される(IndexNow は副作用が軽い通知APIなので実送信して問題ない)

- [ ] **Step 3: Commit**

```bash
git add scripts/notify-indexnow.mjs
git commit -m "feat: notify IndexNow for /gov pages touched by the day's speakers"
```

---

### Task 7: 最終検証

**Files:** なし(検証のみ)

- [ ] **Step 1: 全チェックを順に実行**

```bash
node --test "tests/unit/*.test.mjs"
npm run lint
npm run build
```
Expected: すべて成功

- [ ] **Step 2: スペックの検証項目を通す**

```bash
# /gov pages generated
ls out/gov/*.html | wc -l
# bureaucrat listed with role on ministry page
grep -l "五十嵐徹人" out/gov/mlit.html
# unique per-member description
grep -o 'name="description" content="[^"]*"' out/m/m_8f8c090e.html
# sitemap wired
grep "sitemap-gov" public/sitemap_index.xml
```
Expected: 省庁ページ約30枚、国交省ページに五十嵐徹人、メンバーdescriptionに件数・直近委員会入り、sitemap_index参照あり

- [ ] **Step 3: 手動運用タスクの案内(コード外)**

実装完了後、ユーザーが GSC(sc-domain: open-gikai.net)で実施:
1. `https://www.open-gikai.net/sitemap_index.xml` を削除
2. 旧 `https://open-gikai.net/sitemap.xml` を削除
3. `https://open-gikai.net/sitemap_index.xml` を登録

- [ ] **Step 4: 効果測定の予約(コード外)**

2〜4週間後に GSC で再計測: 役職系クエリの表示回数 / インデックス数(基準: 約366/3,868)/ Google:Bing 流入比
