"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import type { Member, Thread } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { Avatar } from "@/components/ui/avatar";
import { getStyle } from "@/lib/utils";
import { PARTY_STYLE, RANK_BADGE } from "@/lib/config";

type MemberListViewProps = {
  members: Record<string, Member>;
  threads: Thread[];
};

type FilterState = {
  query: string;
  party: string;
  rank: string;
};

export function MemberListView({ members, threads }: MemberListViewProps) {
  const { follows, toggleFollow } = useAppContext();
  const [filter, setFilter] = useState<FilterState>({
    query: "",
    party: "",
    rank: "",
  });

  // Count speeches per member
  const speechCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    threads.forEach((t) =>
      t.speeches.forEach((s) => {
        counts[s.memberId] = (counts[s.memberId] || 0) + 1;
      }),
    );
    return counts;
  }, [threads]);

  // Get unique parties for filter
  const parties = useMemo(() => {
    const set = new Set<string>();
    Object.values(members).forEach((m) => {
      if (m.party) set.add(m.party);
    });
    return [...set].sort();
  }, [members]);

  // Filter and sort members
  const filtered = useMemo(() => {
    return Object.values(members)
      .filter((m) => {
        if (filter.query) {
          const q = filter.query.toLowerCase();
          const searchable = `${m.name} ${m.role} ${m.party || ""}`.toLowerCase();
          if (!searchable.includes(q)) return false;
        }
        if (filter.party && m.party !== filter.party) return false;
        if (filter.rank && m.rank !== filter.rank) return false;
        return true;
      })
      .sort((a, b) => (speechCounts[b.id] || 0) - (speechCounts[a.id] || 0));
  }, [members, filter, speechCounts]);

  return (
    <>
      {/* Filters */}
      <div className="border-b border-x-border px-4 py-3">
        {/* Search */}
        <div className="flex items-center gap-3 rounded-full bg-x-search px-4">
          <span className="text-x-secondary">🔍</span>
          <input
            type="text"
            value={filter.query}
            onChange={(e) => setFilter((f) => ({ ...f, query: e.target.value }))}
            placeholder="議員名・役職で検索"
            className="h-[42px] w-full border-none bg-transparent text-[15px] text-x-text outline-none placeholder:text-x-secondary"
          />
        </div>

        {/* Filter chips */}
        <div className="mt-3 flex flex-wrap gap-2">
          {/* Party filter */}
          <select
            value={filter.party}
            onChange={(e) => setFilter((f) => ({ ...f, party: e.target.value }))}
            className="cursor-pointer rounded-full border border-x-border bg-transparent px-3 py-1.5 text-[13px] text-x-text outline-none"
          >
            <option value="">全政党</option>
            {parties.map((p) => (
              <option key={p} value={p}>
                {PARTY_STYLE[p]?.short || p}
              </option>
            ))}
          </select>

          {/* Rank filter */}
          <select
            value={filter.rank}
            onChange={(e) => setFilter((f) => ({ ...f, rank: e.target.value }))}
            className="cursor-pointer rounded-full border border-x-border bg-transparent px-3 py-1.5 text-[13px] text-x-text outline-none"
          >
            <option value="">全役職</option>
            <option value="pm">首相</option>
            <option value="minister">閣僚</option>
            <option value="viceminister">副大臣・政務官</option>
            <option value="member">議員・参考人</option>
          </select>

          {/* Result count */}
          <span className="flex items-center px-2 text-[13px] text-x-secondary">
            {filtered.length}名
          </span>
        </div>

        {/* Badge legend */}
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[12px] text-x-secondary">
          {Object.values(RANK_BADGE).map((b) => (
            <span key={b.label} className="flex items-center gap-1">
              <span>{b.icon}</span>{b.label}
            </span>
          ))}
        </div>
      </div>

      {/* Member list */}
      {filtered.map((m) => {
        const ms = getStyle(m);
        const badge = RANK_BADGE[m.rank];
        const count = speechCounts[m.id] || 0;
        const isFollowed = follows.has(m.id);

        return (
          <div
            key={m.id}
            className="flex items-center gap-3 border-b border-x-border px-4 py-3 transition-colors hover:bg-x-hover"
          >
            <Link href={`/m/${m.id}`} className="shrink-0">
              <Avatar member={m} size={48} followed={isFollowed} />
            </Link>

            <Link href={`/m/${m.id}`} className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1">
                <span className="text-[15px] font-bold text-x-text">
                  {m.name}
                </span>
                {badge && (
                  <span className="text-[14px]" title={badge.label}>
                    {badge.icon}
                  </span>
                )}
                {m.party && PARTY_STYLE[m.party] && (
                  <span
                    className="rounded px-1.5 py-px text-[12px] font-bold"
                    style={{ color: ms.color, background: ms.bg }}
                  >
                    {PARTY_STYLE[m.party].short}
                  </span>
                )}
              </div>
              <div className="text-[13px] text-x-secondary">
                {m.role}
              </div>
              {count > 0 && (
                <div className="text-[13px] text-x-secondary">
                  💬 {count}件の発言
                </div>
              )}
            </Link>

            <button
              onClick={() => toggleFollow(m.id)}
              className="shrink-0 cursor-pointer rounded-full border px-3 py-1.5 text-[13px] font-bold transition-colors"
              style={{
                background: isFollowed ? "transparent" : "#e7e9ea",
                borderColor: isFollowed ? "#536471" : "transparent",
                color: isFollowed ? "#e7e9ea" : "#0f1419",
              }}
            >
              {isFollowed ? "フォロー中" : "フォロー"}
            </button>
          </div>
        );
      })}

      {filtered.length === 0 && (
        <div className="px-8 py-16 text-center text-[15px] text-x-secondary">
          条件に一致する議員がいません
        </div>
      )}
    </>
  );
}
