import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getMember, getMembers, getThreads, getThreadsSummary, getAllMemberIds } from "@/lib/data";
import { MemberProfileView } from "@/components/member/member-profile-view";
import { MobileHeader } from "@/components/layout/header";
import { RightSidebar } from "@/components/sidebar/right-sidebar";

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
  const title = member.name;
  const description = `${member.name}（${desc}）の国会発言一覧。審議スレッドをまとめて閲覧できます。`;
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

  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "ホーム", item: "https://open-gikai.net" },
      { "@type": "ListItem", position: 2, name: "発言者一覧", item: "https://open-gikai.net/members" },
      { "@type": "ListItem", position: 3, name: member.name },
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
        <MemberProfileView member={member} threads={threads} members={members} />
      </main>
      <RightSidebar threads={getThreadsSummary()} members={members} />
    </>
  );
}
