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
