import { notFound } from "next/navigation";
import { getThread, getMembers, getAllThreadIds } from "@/lib/data";
import { ThreadDetailView } from "@/components/thread/thread-detail-view";

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

  const members = getMembers();

  return <ThreadDetailView thread={thread} members={members} />;
}
