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
      <div className="mb-4 flex border-b border-slate-800">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setFeedFilter(id as "all" | "following")}
            className="-mb-px cursor-pointer border-none bg-transparent px-4 py-2.5 text-[13px]"
            style={{
              fontWeight: feedFilter === id ? 700 : 400,
              color: feedFilter === id ? "#f8fafc" : "#475569",
              borderBottom:
                feedFilter === id
                  ? "2px solid #3b82f6"
                  : "2px solid transparent",
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {feedFilter === "following" && follows.size === 0 ? (
        <div className="py-12 text-center text-sm text-slate-700">
          <div className="mb-3 text-[32px]">⭐</div>
          議員をフォローすると
          <br />
          その議員が出演するスレッドだけ表示されます
        </div>
      ) : (
        visibleThreads.map((t) => (
          <ThreadCard key={t.id} thread={t} members={members} />
        ))
      )}
    </>
  );
}
