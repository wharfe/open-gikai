import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getMember, getThreads, getAllMemberIds, getMemberStats, getMinistrySlugs } from "@/lib/data";
import { getMemberMinistry } from "@/lib/ministry.mjs";
import { MemberProfileView, type MemberThread } from "@/components/member/member-profile-view";
import { MobileHeader } from "@/components/layout/header";

export const dynamicParams = false;

const TOPICS_MAX_CHARS = 50;

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
  const stats = getMemberStats().get(memberId);
  // Name and role first: person queries are by name, and for government
  // witnesses the role is what tells a searcher it is the right person.
  const title = stats
    ? `${member.name}${desc ? `（${desc}）` : ""}— ${stats.dietOnly ? "国会での発言" : "発言"}${stats.speechCount}件`
    : `${member.name}の発言一覧${desc ? `（${desc}）` : ""}`;
  const namePart = member.role ? `${member.name}（${member.role}）` : member.name;
  // Topics lead the description because snippets are cut at ~120 chars, and
  // the topic is the one thing a person query does not already say. Topics
  // after the first are dropped once the prefix would pass TOPICS_MAX_CHARS,
  // so the name and role that follow still fit in the snippet.
  const topics = (stats?.topTopics ?? []).reduce<string[]>(
    (kept, t) =>
      kept.length === 0 || [...kept, t].join("、").length <= TOPICS_MAX_CHARS ? [...kept, t] : kept,
    [],
  );
  const topicsPart = topics.length ? `主なテーマ: ${topics.join("、")}。` : "";
  const description = stats
    ? `${topicsPart}${namePart}の${stats.dietOnly ? "国会" : "国会・審議会など"}での発言${stats.speechCount}件をAI要約付きで掲載。直近は${stats.latestDate}の${stats.latestCommittee}。`
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

  // Only the threads this member speaks in, cut to what the list renders: the
  // heading fields and this member's own speeches. Props are serialized into
  // the HTML and every RSC payload file of the page, and a whole Thread carries
  // every other speaker's speeches and transcripts (#104).
  //
  // Threads are picked by the route id and speeches by member.id, exactly as
  // the page and the view did before. They differ only for the entries that
  // validate-data --fix adds, which carry no id, so their pages list no
  // speeches. Changing that would change what those pages show; this only
  // cuts size.
  const threads: MemberThread[] = getThreads()
    .filter((t) => t.speeches.some((s) => s.memberId === memberId))
    .map((t) => ({
      id: t.id,
      date: t.date,
      committee: t.committee,
      topic: t.topic,
      topicColor: t.topicColor,
      speeches: t.speeches
        .filter((s) => s.memberId === member.id)
        .map((s) => ({ tension: s.tension, summaries: s.summaries })),
    }));

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

  // Only surface the ministry link when its /gov/{slug} page is actually
  // built. A ministry-matched member with no recorded speeches (and no
  // speaking colleagues) yields no roster page; linking there would 404
  // because /gov/[slug] has dynamicParams = false.
  const ministryMatch = getMemberMinistry(member);
  const ministry =
    ministryMatch && getMinistrySlugs().includes(ministryMatch.slug)
      ? ministryMatch
      : null;
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
        <MemberProfileView member={member} threads={threads} ministry={ministry} />
      </main>
    </>
  );
}
