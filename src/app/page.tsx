import { Suspense } from "react";
import { getThreads, getMembers, getSessionInfo } from "@/lib/data";
import { FeedView } from "@/components/feed/feed-view";
import { SessionCard } from "@/components/feed/session-card";
import { RightSidebar } from "@/components/sidebar/right-sidebar";
import { MobileHeader } from "@/components/layout/header";

export default function Home() {
  const threads = getThreads();
  const members = getMembers();
  const session = getSessionInfo();

  return (
    <>
      {/* Center column: feed */}
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />
        <SessionCard threads={threads} session={session} />
        <Suspense>
          <FeedView threads={threads} members={members} />
        </Suspense>
      </main>

      {/* Right sidebar */}
      <RightSidebar threads={threads} members={members} />
    </>
  );
}
