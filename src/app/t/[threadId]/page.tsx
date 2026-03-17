import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getThread, getThreads, getMembers, getAllThreadIds } from "@/lib/data";
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

  const title = `${thread.topic} — ${thread.committee}`;
  const description = `${thread.summary}（${actors.join("、")}）`;

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      type: "article",
      url: `https://open-gikai.net/t/${threadId}`,
      siteName: "OpenGIKAI",
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
    },
  };
}

export default async function ThreadPage({ params }: Props) {
  const { threadId } = await params;
  const thread = getThread(threadId);
  if (!thread) notFound();

  const threads = getThreads();
  const members = getMembers();

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

  return (
    <>
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />
        <ThreadDetailView thread={thread} members={members} />
      </main>
      <RightSidebar threads={threads} members={members} />
    </>
  );
}
