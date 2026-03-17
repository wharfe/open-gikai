import { MobileHeader } from "@/components/layout/header";
import { MemberListView } from "@/components/member/member-list-view";
import { getMembers, getThreads } from "@/lib/data";

export default function MembersPage() {
  const members = getMembers();
  const threads = getThreads();

  return (
    <>
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />

        <div className="sticky top-[53px] z-40 flex h-[53px] items-center gap-5 bg-x-bg/65 px-4 backdrop-blur-xl md:top-0">
          <div className="text-[17px] font-bold">議員一覧</div>
        </div>

        <MemberListView members={members} threads={threads} />
      </main>
    </>
  );
}
