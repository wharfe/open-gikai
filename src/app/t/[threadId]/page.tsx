import { notFound } from "next/navigation";
import { getThread, getThreads, getMembers, getAllThreadIds } from "@/lib/data";
import { ThreadDetailView } from "@/components/thread/thread-detail-view";
import { MobileHeader } from "@/components/layout/header";
import { RightSidebar } from "@/components/sidebar/right-sidebar";

export function generateStaticParams() {
  return getAllThreadIds().map((threadId) => ({ threadId }));
}

type Props = {
  params: Promise<{ threadId: string }>;
};

export default async function ThreadPage({ params }: Props) {
  const { threadId } = await params;
  const thread = getThread(threadId);
  if (!thread) notFound();

  const threads = getThreads();
  const members = getMembers();

  return (
    <>
      <main className="w-full max-w-[600px] overflow-hidden border-r border-x-border">
        <MobileHeader />
        <ThreadDetailView thread={thread} members={members} />
      </main>
      <RightSidebar threads={threads} members={members} />
    </>
  );
}
