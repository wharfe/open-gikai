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

  // JSON-LD structured data — all values are server-generated static objects, no user input
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

  // JSON-LD structured data — all values are server-generated static objects, no user input
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
