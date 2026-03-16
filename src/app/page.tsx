import { getThreads, getMembers } from "@/lib/data";
import { FeedView } from "@/components/feed/feed-view";
import { RightSidebar } from "@/components/sidebar/right-sidebar";
import { MobileHeader } from "@/components/layout/header";

export default function Home() {
  const threads = getThreads();
  const members = getMembers();

  return (
    <>
      {/* Center column: feed */}
      <main className="w-full max-w-[600px] overflow-hidden border-r border-x-border">
        <MobileHeader />
        <FeedView threads={threads} members={members} />
      </main>

      {/* Right sidebar */}
      <RightSidebar threads={threads} members={members} />
    </>
  );
}
