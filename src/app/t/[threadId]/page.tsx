import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getThread, getThreadsSummary, getMembers, getAllThreadIds } from "@/lib/data";
import { ThreadDetailView } from "@/components/thread/thread-detail-view";
import { MobileHeader } from "@/components/layout/header";
import { RightSidebar } from "@/components/sidebar/right-sidebar";

export const dynamicParams = false;

export function generateStaticParams() {
  return getAllThreadIds().map((threadId) => ({ threadId }));
}

type Props = {
  params: Promise<{ threadId: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { threadId } = await params;
  const thread = getThread(threadId);
  if (!thread) return {};

  const members = getMembers();
  const actors = [...new Set(thread.speeches.map((s) => s.memberId))]
    .map((id) => members[id]?.name || "")
    .filter(Boolean);

  // Include top speaker names in title for SEO (politician name searches)
  const topActors = actors.slice(0, 3).join("・");
  const title = topActors
    ? `${thread.topic}（${topActors}）— ${thread.committee}`
    : `${thread.topic} — ${thread.committee}`;
  const description = `${thread.summary}（${actors.join("、")}）`;

  return {
    title,
    description,
    alternates: { canonical: `/t/${threadId}` },
    openGraph: {
      title,
      description,
      type: "article",
      url: `https://open-gikai.net/t/${threadId}`,
      siteName: "OpenGIKAI",
      images: [{ url: `/og/${threadId}.png`, width: 1200, height: 630 }],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [`/og/${threadId}.png`],
    },
  };
}

export default async function ThreadPage({ params }: Props) {
  const { threadId } = await params;
  const thread = getThread(threadId);
  if (!thread) notFound();

  const members = getMembers();
  const threadsSummary = getThreadsSummary();

  const isoDate = thread.date.replace(/\./g, "-");
  const actors = [...new Set(thread.speeches.map((s) => s.memberId))]
    .map((id) => members[id]?.name)
    .filter(Boolean);

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: thread.topic,
    description: thread.summary,
    datePublished: isoDate,
    author: actors.map((name) => ({ "@type": "Person", name })),
    publisher: {
      "@type": "Organization",
      name: "OpenGIKAI",
      url: "https://open-gikai.net",
    },
    mainEntityOfPage: `https://open-gikai.net/t/${threadId}`,
  };

  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "ホーム", item: "https://open-gikai.net" },
      { "@type": "ListItem", position: 2, name: thread.committee, item: `https://open-gikai.net/search?q=${encodeURIComponent(thread.committee)}` },
      { "@type": "ListItem", position: 3, name: thread.topic },
    ],
  };

  return (
    <>
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
        <ThreadDetailView thread={thread} members={members} />
      </main>
      <RightSidebar threads={threadsSummary} members={members} />
    </>
  );
}
