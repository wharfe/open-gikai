"use client";

import Link from "next/link";
import type { Member, Thread } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { RANK_BADGE, TENSION_STYLE, PARTY_STYLE } from "@/lib/config";
import { getStyle } from "@/lib/utils";
import { Avatar } from "@/components/ui/avatar";

type MemberProfileViewProps = {
  member: Member;
  threads: Thread[];
  members: Record<string, Member>;
};

export function MemberProfileView({
  member,
  threads,
}: MemberProfileViewProps) {
  const { follows, toggleFollow } = useAppContext();

  const speeches = threads.flatMap((t) =>
    t.speeches
      .filter((s) => s.memberId === member.id)
      .map((s) => ({ ...s, thread: t }))
  );

  const tensionCount = speeches.reduce<Record<string, number>>((acc, s) => {
    acc[s.tension] = (acc[s.tension] || 0) + 1;
    return acc;
  }, {});

  const ms = getStyle(member);
  const isFollowed = follows.has(member.id);
  const badge = RANK_BADGE[member.rank];

  return (
    <>
      {/* Sticky header — X profile style */}
      <div className="sticky top-0 z-40 flex h-[53px] items-center gap-6 border-b border-x-border bg-x-bg/65 px-4 backdrop-blur-xl">
        <Link
          href="/"
          className="flex h-9 w-9 items-center justify-center rounded-full text-xl transition-colors hover:bg-x-hover"
        >
          ← 戻る
        </Link>
        <div>
          <div className="text-[17px] font-bold leading-tight">
            {member.name}
          </div>
          <div className="text-[13px] text-x-secondary">
            {speeches.length}件の発言
          </div>
        </div>
      </div>

      {/* Banner — X style gradient banner */}
      <div
        className="h-[133px] sm:h-[200px]"
        style={{
          background: `linear-gradient(135deg, ${ms.color}30, ${ms.color}10, #000)`,
        }}
      />

      {/* Avatar + Follow — X style overlapping avatar */}
      <div className="flex items-start justify-between px-4">
        <div className="-mt-[10%] rounded-full border-4 border-x-bg">
          <Avatar member={member} size={80} followed={isFollowed} />
        </div>
        <div className="mt-3">
          <button
            onClick={() => toggleFollow(member.id)}
            className="cursor-pointer rounded-full px-5 py-2 text-[15px] font-bold transition-colors"
            style={{
              background: isFollowed ? "transparent" : "#e7e9ea",
              border: isFollowed ? "1px solid #536471" : "none",
              color: isFollowed ? "#e7e9ea" : "#0f1419",
            }}
          >
            {isFollowed ? "フォロー中 ✓" : "+ フォロー"}
          </button>
        </div>
      </div>

      {/* Profile info */}
      <div className="border-b border-x-border px-4 pb-4">
        {/* Name */}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <h2 className="text-[20px] font-extrabold text-x-text">
            {member.name}
          </h2>
          {badge && (
            <span className="text-[18px]" title={badge.label}>
              {badge.icon}
            </span>
          )}
          {member.party && PARTY_STYLE[member.party] && (
            <span
              className="rounded px-2 py-0.5 text-[13px] font-bold"
              style={{
                color: ms.color,
                background: ms.bg,
              }}
            >
              {PARTY_STYLE[member.party].short}
            </span>
          )}
        </div>

        {/* Handle-like info */}
        <div className="mt-0.5 text-[15px] text-x-secondary">
          {member.role}
          {member.district ? ` · ${member.district}` : ""}
          {member.since ? ` · ${member.since}年〜` : ""}
        </div>
        {badge && (
          <div className="mt-1 text-[13px]" style={{ color: badge.color }}>
            {badge.icon} {badge.label}
          </div>
        )}

        {/* Bio */}
        <p className="mt-3 text-[15px] leading-[20px] text-x-text">
          {member.bio}
        </p>

        {/* Stance tags — like X profile labels */}
        <div className="mt-3 flex flex-wrap gap-2">
          {member.stance.map((st) => (
            <span
              key={st}
              className="rounded-full px-3 py-1 text-[13px]"
              style={{
                color: ms.color,
                background: ms.bg,
              }}
            >
              {st}
            </span>
          ))}
        </div>

        {/* Tension stats */}
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(tensionCount).map(([t, n]) => {
            const ts = TENSION_STYLE[t];
            return (
              <span key={t} className="text-[14px]">
                <span className="font-bold text-x-text">{n}</span>{" "}
                <span className="text-x-secondary">
                  {ts.icon} {t}
                </span>
              </span>
            );
          })}
        </div>
      </div>

      {/* Speech list — X style tweets */}
      {speeches.map((sp, i) => {
        const ts = TENSION_STYLE[sp.tension];
        return (
          <Link
            key={i}
            href={`/t/${sp.thread.id}`}
            className="block border-b border-x-border px-4 py-3 transition-colors hover:bg-x-hover"
          >
            <div className="mb-1 flex flex-wrap items-center gap-1 text-[15px]">
              <span className="font-bold text-x-text">
                {sp.thread.committee}
              </span>
              <span className="text-x-secondary">·</span>
              <span className="text-x-secondary">{sp.thread.date}</span>
            </div>
            <div className="mb-2 flex items-center gap-2">
              <span
                className="rounded-full px-2.5 py-0.5 text-[13px] font-semibold"
                style={{ color: ts.color, background: ts.bg }}
              >
                {ts.icon} {sp.tension}
              </span>
              <span
                className="rounded-full px-2.5 py-0.5 text-[13px]"
                style={{
                  color: sp.thread.topicColor,
                  background: `${sp.thread.topicColor}15`,
                }}
              >
                {sp.thread.topic}
              </span>
            </div>
            <p className="text-[15px] leading-[20px] text-x-text">
              {sp.summaries.adult}
            </p>
          </Link>
        );
      })}
    </>
  );
}
