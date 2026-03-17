import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { getMember, getMembers, getThreads, getAllMemberIds } from "@/lib/data";
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
  return {
    title: member.name,
    description: `${member.name}（${desc}）の国会発言一覧。審議スレッドをまとめて閲覧できます。`,
  };
}

export default async function MemberPage({ params }: Props) {
  const { memberId } = await params;
  const member = getMember(memberId);
  if (!member) notFound();

  const threads = getThreads();
  const members = getMembers();

  return (
    <>
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />
        <MemberProfileView member={member} threads={threads} members={members} />
      </main>
      <RightSidebar threads={threads} members={members} />
    </>
  );
}
