import type { Metadata } from "next";
import { MobileHeader } from "@/components/layout/header";
import { MemberListView } from "@/components/member/member-list-view";
import { getMembers, getThreads } from "@/lib/data";

export const metadata: Metadata = {
  title: "発言者一覧",
  description: "議員・大臣・参考人など発言者のプロフィールと発言履歴。所属・役職・出演スレッド数で検索できます。",
};

export default function MembersPage() {
  const members = getMembers();
  const threads = getThreads();

  return (
    <>
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />

        <div className="sticky top-[53px] z-40 flex h-[53px] items-center gap-5 bg-x-bg/65 px-4 backdrop-blur-xl md:top-0">
          <div className="text-[17px] font-bold">発言者一覧</div>
        </div>

        <MemberListView members={members} threads={threads} />
      </main>
    </>
  );
}
