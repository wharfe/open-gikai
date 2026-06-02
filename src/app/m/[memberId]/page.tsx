import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getMember, getMembers, getThreads, getAllMemberIds, getMemberStats } from "@/lib/data";
import { getMemberMinistry } from "@/lib/ministry.mjs";
import { MemberProfileView } from "@/components/member/member-profile-view";
import { MobileHeader } from "@/components/layout/header";

export const dynamicParams = false;

export function generateStaticParams() {
  return getAllMemberIds().map((memberId) => ({ memberId }));
}

type Props = {
  params: Promise<{ memberId: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { memberId } = await params;
  const member = getMember(memberId);
  if (!member) return {};
  const desc = [member.party, member.role].filter(Boolean).join("・");
  const title = `${member.name}の発言一覧${desc ? `（${desc}）` : ""}`;
  const stats = getMemberStats().get(memberId);
  const namePart = member.role ? `${member.name}（${member.role}）` : member.name;
  const description = stats
    ? `${namePart}の国会・審議会での発言${stats.speechCount}件をAI要約付きで掲載。直近は${stats.latestDate}の${stats.latestCommittee}。`
    : `${member.name}の国会・審議会での発言をスレッド形式で閲覧。AI要約付きで審議の文脈がわかります。`;
  return {
    title,
    description,
    alternates: { canonical: `/m/${memberId}` },
    openGraph: {
      title,
      description,
      type: "profile",
      url: `https://open-gikai.net/m/${memberId}`,
      siteName: "OpenGIKAI",
    },
    twitter: {
      card: "summary",
      title,
      description,
    },
  };
}

export default async function MemberPage({ params }: Props) {
  const { memberId } = await params;
  const member = getMember(memberId);
  if (!member) notFound();

  const allThreads = getThreads();
  const members = getMembers();

  // Only pass threads where this member has speeches (avoids serializing all thread data)
  const threads = allThreads.filter((t) =>
    t.speeches.some((s) => s.memberId === memberId)
  );

  const desc = [member.party, member.role].filter(Boolean).join("・");
  const personJsonLd = {
    "@context": "https://schema.org",
    "@type": "Person",
    name: member.name,
    description: `${member.name}（${desc}）の国会発言一覧`,
    jobTitle: member.role || undefined,
    affiliation: member.party ? { "@type": "Organization", name: member.party } : undefined,
    url: `https://open-gikai.net/m/${memberId}`,
  };

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

  return (
    <>
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(personJsonLd) }}
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumb) }}
        />
        <MemberProfileView member={member} threads={threads} members={members} ministry={ministry} />
      </main>
    </>
  );
}
