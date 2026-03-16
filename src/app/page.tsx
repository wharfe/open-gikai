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
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />
        <FeedView threads={threads} members={members} />
      </main>

      {/* Right sidebar */}
      <RightSidebar threads={threads} members={members} />
    </>
  );
}
