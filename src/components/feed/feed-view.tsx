"use client";

import { useState, useMemo } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import type { Member, Thread } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { ThreadCard } from "@/components/feed/thread-card";

type FeedViewProps = {
  threads: Thread[];
  members: Record<string, Member>;
};

export function FeedView({ threads, members }: FeedViewProps) {
  const { follows } = useAppContext();
  const searchParams = useSearchParams();
  const router = useRouter();
  const [feedFilter, setFeedFilter] = useState<"all" | "following">("all");

  const dateParam = searchParams.get("date");
  const committeeParam = searchParams.get("committee");
  const hasFilter = dateParam || committeeParam;

  const filteredThreads = useMemo(() => {
    let result = threads;
    if (dateParam) result = result.filter((t) => t.date === dateParam);
    if (committeeParam) result = result.filter((t) => t.committee === committeeParam);
    return result;
  }, [threads, dateParam, committeeParam]);

  const visibleThreads =
    feedFilter === "following" && follows.size > 0
      ? filteredThreads.filter((t) =>
          t.speeches.some((s) => follows.has(s.memberId))
        )
      : filteredThreads;

  const tabs: [string, string][] = [
    ["all", "すべて"],
    [
      "following",
      `フォロー中${follows.size > 0 ? ` (${follows.size})` : ""}`,
    ],
  ];

  return (
    <>
      {/* Active filter banner */}
      {hasFilter && (
        <div className="flex items-center justify-between border-b border-x-border bg-x-surface px-4 py-2.5">
          <span className="text-[14px] text-x-text">
            {dateParam && <span className="mr-2 rounded bg-x-accent/20 px-2 py-0.5 text-x-accent">{dateParam}</span>}
            {committeeParam && <span className="rounded bg-x-accent/20 px-2 py-0.5 text-x-accent">{committeeParam}</span>}
            <span className="ml-2 text-x-secondary">{filteredThreads.length}件</span>
          </span>
          <button
            onClick={() => router.push("/")}
            className="cursor-pointer rounded-full border-none bg-transparent px-3 py-1 text-[13px] text-x-secondary transition-colors hover:bg-x-hover hover:text-x-text"
          >
            <span className="material-symbols-rounded align-middle" style={{ fontSize: 14 }}>close</span> 解除
          </button>
        </div>
      )}

      {/* Sticky tab bar — X style */}
      <div className="sticky top-[53px] z-40 flex border-b border-x-border bg-x-bg/65 backdrop-blur-xl md:top-0">
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
          <div className="mb-4"><span className="material-symbols-rounded text-amber-400" style={{ fontSize: 36 }}>star</span></div>
          <p className="text-[15px] text-x-secondary">
            発言者をフォローすると
            <br />
            その発言者が出演するスレッドだけ表示されます
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
