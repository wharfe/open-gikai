import type { Metadata } from "next";
import { Suspense } from "react";
import { getThreadsSummary, getMembersForDisplay, getSessionInfo } from "@/lib/data";
import { FeedView } from "@/components/feed/feed-view";
import { SessionCard } from "@/components/feed/session-card";
import { RightSidebar } from "@/components/sidebar/right-sidebar";
import { MobileHeader } from "@/components/layout/header";

export const metadata: Metadata = {
  alternates: { canonical: "/" },
};

export default function Home() {
  const threadsSummary = getThreadsSummary();
  const members = getMembersForDisplay();
  const session = getSessionInfo();

  return (
    <>
      {/* Center column: feed */}
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />
        <h1 className="sr-only">OpenGIKAI — 国会・審議会の議事録スレッド</h1>
        {/* Session card — mobile only (desktop shows in right sidebar) */}
        <div className="border-b border-x-border px-4 py-4 lg:hidden">
          <SessionCard threads={threadsSummary} session={session} />
        </div>
        <Suspense>
          <FeedView threads={threadsSummary} members={members} />
        </Suspense>
      </main>

      {/* Right sidebar */}
      <RightSidebar threads={threadsSummary} members={members} session={session} />
    </>
  );
}
