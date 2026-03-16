import { getThreads, getMembers } from "@/lib/data";
import { FeedView } from "@/components/feed/feed-view";
import { TrendPanel } from "@/components/sidebar/trend-panel";
import { FollowPanel } from "@/components/sidebar/follow-panel";
import { MobileHeader } from "@/components/layout/header";

export default function Home() {
  const threads = getThreads();
  const members = getMembers();

  return (
    <>
      {/* Center column: feed */}
      <main className="w-full max-w-[600px] border-r border-x-border">
        <MobileHeader />
        <FeedView threads={threads} members={members} />
      </main>

      {/* Right sidebar */}
      <aside className="sticky top-0 hidden h-screen w-[350px] shrink-0 overflow-y-auto px-6 py-3 lg:block">
        <div className="space-y-4">
          <TrendPanel threads={threads} />
          <FollowPanel members={members} />
        </div>
      </aside>
    </>
  );
}
