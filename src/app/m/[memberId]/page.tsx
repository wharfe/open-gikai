import { notFound } from "next/navigation";
import { getMember, getMembers, getThreads, getAllMemberIds } from "@/lib/data";
import { MemberProfileView } from "@/components/member/member-profile-view";

export function generateStaticParams() {
  return getAllMemberIds().map((memberId) => ({ memberId }));
}

type Props = {
  params: Promise<{ memberId: string }>;
};

export default async function MemberPage({ params }: Props) {
  const { memberId } = await params;
  const member = getMember(memberId);
  if (!member) notFound();

  const threads = getThreads();
  const members = getMembers();

  return (
    <MemberProfileView member={member} threads={threads} members={members} />
  );
}
