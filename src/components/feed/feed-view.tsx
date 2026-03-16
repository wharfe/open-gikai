"use client";

import { useState } from "react";
import type { Member, Thread } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { ThreadCard } from "@/components/feed/thread-card";

type FeedViewProps = {
  threads: Thread[];
  members: Record<string, Member>;
};

export function FeedView({ threads, members }: FeedViewProps) {
  const { follows } = useAppContext();
  const [feedFilter, setFeedFilter] = useState<"all" | "following">("all");

  const visibleThreads =
    feedFilter === "following" && follows.size > 0
      ? threads.filter((t) =>
          t.speeches.some((s) => follows.has(s.memberId))
        )
      : threads;

  const tabs: [string, string][] = [
    ["all", "📋 すべて"],
    [
      "following",
      `⭐ フォロー中${follows.size > 0 ? ` (${follows.size})` : ""}`,
    ],
  ];

  return (
    <>
      {/* Sticky tab bar — X style */}
      <div className="sticky top-0 z-40 flex border-b border-x-border bg-x-bg/65 backdrop-blur-xl md:top-0">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setFeedFilter(id as "all" | "following")}
            className="relative flex-1 cursor-pointer border-none bg-transparent py-4 text-center text-[15px] transition-colors hover:bg-x-hover"
            style={{
              fontWeight: feedFilter === id ? 700 : 400,
              color: feedFilter === id ? "#e7e9ea" : "#71767b",
            }}
          >
            {label}
            {/* Active underline — X uses 4px rounded blue bar */}
            {feedFilter === id && (
              <div className="absolute bottom-0 left-1/2 h-1 w-14 -translate-x-1/2 rounded-full bg-x-accent" />
            )}
          </button>
        ))}
      </div>

      {feedFilter === "following" && follows.size === 0 ? (
        <div className="px-8 py-20 text-center">
          <div className="mb-4 text-4xl">⭐</div>
          <p className="text-[15px] text-x-secondary">
            議員をフォローすると
            <br />
            その議員が出演するスレッドだけ表示されます
          </p>
        </div>
      ) : (
        visibleThreads.map((t) => (
          <ThreadCard key={t.id} thread={t} members={members} />
        ))
      )}
    </>
  );
}
