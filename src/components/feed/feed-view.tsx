"use client";

import { useState, useMemo } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import type { Member, Thread } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { ThreadCard } from "@/components/feed/thread-card";
import { ThemeBar } from "@/components/feed/theme-bar";
import { getLifeTheme, getLifeThemeConfig, type LifeThemeId } from "@/lib/config";

type FeedViewProps = {
  threads: Thread[];
  members: Record<string, Member>;
};

export function FeedView({ threads, members }: FeedViewProps) {
  const { follows } = useAppContext();
  // Only count follows that still exist in members data
  const activeFollowCount = useMemo(
    () => [...follows].filter((id) => id in members).length,
    [follows, members],
  );
  const searchParams = useSearchParams();
  const router = useRouter();
  const [feedFilter, setFeedFilter] = useState<"all" | "following">("all");
  const [showProcedural, setShowProcedural] = useState(false);
  const themeRaw = searchParams.get("theme");
  const themeParam: LifeThemeId | null =
    themeRaw && getLifeThemeConfig(themeRaw as LifeThemeId) ? (themeRaw as LifeThemeId) : null;
  const [localTheme, setLocalTheme] = useState<LifeThemeId | null>(null);
  const selectedTheme = themeParam || localTheme;

  const setSelectedTheme = (theme: LifeThemeId | null) => {
    // If there's a URL theme param, clear it by navigating
    if (themeParam) {
      router.push("/");
    }
    setLocalTheme(theme);
  };

  const dateParam = searchParams.get("date");
  const committeeParam = searchParams.get("committee");
  const hasFilter = dateParam || committeeParam;

  // Base threads after URL param filters (but before procedural filter)
  const baseThreads = useMemo(() => {
    let result = threads;
    if (dateParam) result = result.filter((t) => t.date === dateParam);
    if (committeeParam) result = result.filter((t) => t.committee === committeeParam);
    return result;
  }, [threads, dateParam, committeeParam]);

  // Apply following filter
  const followFiltered =
    feedFilter === "following" && activeFollowCount > 0
      ? baseThreads.filter((t) =>
          t.speeches.some((s) => follows.has(s.memberId) && s.memberId in members)
        )
      : baseThreads;

  // Count procedural threads in the current view
  const proceduralCount = useMemo(
    () => followFiltered.filter((t) => t.procedural).length,
    [followFiltered],
  );

  // Apply procedural filter
  const nonProceduralThreads = showProcedural
    ? followFiltered
    : followFiltered.filter((t) => !t.procedural);

  // Compute theme counts (from non-procedural threads for relevance)
  const themeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const t of nonProceduralThreads) {
      const theme = getLifeTheme(t.topicTag);
      if (theme) counts[theme] = (counts[theme] ?? 0) + 1;
    }
    return counts;
  }, [nonProceduralThreads]);

  // Apply theme filter last
  const visibleThreads = selectedTheme
    ? nonProceduralThreads.filter((t) => getLifeTheme(t.topicTag) === selectedTheme)
    : nonProceduralThreads;

  const tabs: [string, string][] = [
    ["all", "すべて"],
    [
      "following",
      `フォロー中${activeFollowCount > 0 ? ` (${activeFollowCount})` : ""}`,
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
            <span className="ml-2 text-x-secondary">{visibleThreads.length}件</span>
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

      {/* Life-theme filter chips */}
      {feedFilter === "all" && !hasFilter && Object.keys(themeCounts).length > 0 && (
        <ThemeBar selected={selectedTheme} onSelect={setSelectedTheme} themeCounts={themeCounts} />
      )}

      {/* Active theme filter banner */}
      {selectedTheme && (
        <div className="flex items-center justify-between border-b border-x-border bg-x-surface px-4 py-2">
          <span className="text-[13px] text-x-secondary">
            テーマで絞り込み中 · {visibleThreads.length}件
          </span>
          <button
            onClick={() => setSelectedTheme(null)}
            className="cursor-pointer rounded-full border-none bg-transparent px-2 py-1 text-[13px] text-x-secondary transition-colors hover:bg-x-hover hover:text-x-text"
          >
            <span className="material-symbols-rounded align-middle" style={{ fontSize: 14 }}>close</span> 解除
          </button>
        </div>
      )}

      {feedFilter === "following" && activeFollowCount === 0 ? (
        <div className="px-8 py-20 text-center">
          <div className="mb-4"><span className="material-symbols-rounded text-amber-400" style={{ fontSize: 36 }}>star</span></div>
          <p className="text-[15px] text-x-secondary">
            発言者をフォローすると
            <br />
            その発言者が出演するスレッドだけ表示されます
          </p>
        </div>
      ) : (
        <>
          {visibleThreads.map((t) => (
            <ThreadCard key={t.id} thread={t} members={members} />
          ))}
          {!showProcedural && proceduralCount > 0 && !hasFilter && (
            <button
              onClick={() => setShowProcedural(true)}
              className="flex w-full cursor-pointer items-center justify-center gap-2 border-none bg-transparent py-6 text-[14px] text-x-secondary transition-colors hover:bg-x-hover hover:text-x-text"
            >
              <span className="material-symbols-rounded" style={{ fontSize: 16 }}>expand_more</span>
              手続き・組織編成スレッドを表示（{proceduralCount}件）
            </button>
          )}
        </>
      )}
    </>
  );
}
