import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { getCouncils, getCouncilSlugs, getThreads, getMembers } from "@/lib/data";
import { MobileHeader } from "@/components/layout/header";
import { ThreadCard } from "@/components/feed/thread-card";

export const dynamicParams = false;

export function generateStaticParams() {
  return getCouncilSlugs().map((slug) => ({ slug }));
}

type Props = {
  params: Promise<{ slug: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const council = getCouncils().find((c) => c.slug === slug);
  if (!council) return {};

  const title = `${council.name} 議事録要約`;
  const description = `${council.name}の全${council.meetings.length}回の議事録をAI要約付きスレッド形式で閲覧。${council.totalThreads}件の議論テーマ、${council.totalSpeeches}件の発言を収録。`;

  return {
    title,
    description,
    alternates: { canonical: `/council/${slug}` },
    openGraph: {
      title,
      description,
      type: "website",
      url: `https://open-gikai.net/council/${slug}`,
      siteName: "OpenGIKAI",
    },
  };
}

export default async function CouncilPage({ params }: Props) {
  const { slug } = await params;
  const council = getCouncils().find((c) => c.slug === slug);
  if (!council) notFound();

  const allThreads = getThreads();
  const members = getMembers();

  // Get threads for this council, grouped by date
  const councilThreads = allThreads.filter(
    (t) => t.source === "council" && (t.sourceLabel === council.name || t.committee === council.name)
  );

  const byDate: Record<string, typeof councilThreads> = {};
  for (const t of councilThreads) {
    if (!byDate[t.date]) byDate[t.date] = [];
    byDate[t.date].push(t);
  }
  const sortedDates = Object.keys(byDate).sort((a, b) => b.localeCompare(a));

  // Meeting number (reverse chronological → ascending number)
  const meetingNumbers = new Map<string, number>();
  const datesAsc = [...sortedDates].reverse();
  datesAsc.forEach((d, i) => meetingNumbers.set(d, i + 1));

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "GovernmentOrganization",
    name: council.name,
    url: `https://open-gikai.net/council/${slug}`,
    description: `${council.name}の議事録要約`,
  };

  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "ホーム", item: "https://open-gikai.net" },
      { "@type": "ListItem", position: 2, name: "審議会", item: "https://open-gikai.net/council" },
      { "@type": "ListItem", position: 3, name: council.name },
    ],
  };

  return (
    <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
      <MobileHeader />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumb) }}
      />

      {/* Header */}
      <div className="sticky top-0 z-40 flex h-[53px] items-center gap-3 bg-x-bg/65 px-4 backdrop-blur-xl">
        <Link
          href="/council"
          className="flex h-9 w-9 items-center justify-center rounded-full text-x-text transition-colors hover:bg-x-hover"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 20 }}>arrow_back</span>
        </Link>
        <div className="min-w-0">
          <h1 className="truncate text-[17px] font-bold leading-tight">{council.name}</h1>
          <div className="text-[13px] text-x-secondary">
            {council.meetings.length}回 · {council.totalThreads}スレッド
          </div>
        </div>
      </div>

      {/* Meeting list */}
      {sortedDates.map((date) => {
        const threads = byDate[date];
        const num = meetingNumbers.get(date)!;

        return (
          <div key={date}>
            {/* Meeting header */}
            <div className="sticky top-[53px] z-30 flex items-center gap-2 border-b border-x-border bg-x-bg/80 px-4 py-2 backdrop-blur-xl">
              <span className="text-[14px] font-bold text-x-accent">
                第{num}回
              </span>
              <span className="text-[14px] text-x-secondary">{date}</span>
              <span className="text-[12px] text-x-secondary">
                {threads.length}スレッド
              </span>
            </div>

            {/* Threads */}
            {threads.map((thread) => (
              <ThreadCard key={thread.id} thread={thread} members={members} />
            ))}
          </div>
        );
      })}
    </main>
  );
}
