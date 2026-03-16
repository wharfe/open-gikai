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
    <div>
      <Link
        href="/"
        className="mb-4 flex items-center gap-1.5 text-[13px] text-slate-600 no-underline hover:text-slate-400"
      >
        ← 戻る
      </Link>

      {/* Profile card */}
      <div className="mb-4 overflow-hidden rounded-2xl border border-slate-800 bg-gikai-card">
        <div
          className="h-14 border-b"
          style={{
            background: `linear-gradient(135deg, ${ms.color}18, ${ms.color}05)`,
            borderColor: ms.border,
          }}
        />
        <div className="-mt-5 px-5 pb-5">
          <div className="flex items-end justify-between">
            <Avatar member={member} size={52} followed={isFollowed} />
            <button
              onClick={() => toggleFollow(member.id)}
              className="cursor-pointer rounded-[20px] px-4 py-1.5 text-[13px] font-bold transition-all"
              style={{
                background: isFollowed
                  ? "none"
                  : `linear-gradient(135deg, ${ms.color}30, ${ms.color}10)`,
                border: `1px solid ${isFollowed ? "#334155" : ms.border}`,
                color: isFollowed ? "#475569" : ms.color,
              }}
            >
              {isFollowed ? "フォロー中 ✓" : "+ フォロー"}
            </button>
          </div>

          <div className="mt-3">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-bold text-slate-50">
                {member.name}
              </h2>
              {badge && (
                <span className="text-lg" title={badge.label}>
                  {badge.icon}
                </span>
              )}
              {member.party && PARTY_STYLE[member.party] && (
                <span
                  className="rounded px-1.5 py-0.5 text-[10px] font-bold"
                  style={{
                    color: ms.color,
                    background: ms.bg,
                    border: `1px solid ${ms.border}`,
                  }}
                >
                  {PARTY_STYLE[member.party].short}
                </span>
              )}
            </div>
            {badge && (
              <div className="mb-1.5 text-[11px]" style={{ color: badge.color }}>
                {badge.icon} {badge.label}
              </div>
            )}
            <div className="mb-2.5 text-xs text-slate-600">
              {member.role}
              {member.district ? ` · ${member.district}` : ""}
              {member.since ? ` · ${member.since}年〜` : ""}
            </div>
            <p className="mb-3.5 text-[13px] leading-relaxed text-slate-400">
              {member.bio}
            </p>

            {/* Stance tags */}
            <div className="mb-4 flex flex-wrap gap-1.5">
              {member.stance.map((st) => (
                <span
                  key={st}
                  className="rounded-[20px] px-2.5 py-0.5 text-[11px]"
                  style={{
                    color: ms.color,
                    background: ms.bg,
                    border: `1px solid ${ms.border}`,
                  }}
                >
                  {st}
                </span>
              ))}
            </div>

            {/* Tension stats */}
            <div className="flex flex-wrap gap-2">
              {Object.entries(tensionCount).map(([t, n]) => {
                const ts = TENSION_STYLE[t];
                return (
                  <div
                    key={t}
                    className="flex items-center gap-1.5 rounded-lg px-2.5 py-1"
                    style={{
                      background: ts.bg,
                      border: `1px solid ${ts.color}30`,
                    }}
                  >
                    <span>{ts.icon}</span>
                    <span className="text-xs" style={{ color: ts.color }}>
                      {t} {n}回
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Speech list */}
      <div className="mb-2.5 text-xs text-slate-600">
        {speeches.length}件の発言
      </div>
      {speeches.map((sp, i) => {
        const ts = TENSION_STYLE[sp.tension];
        return (
          <Link
            key={i}
            href={`/t/${sp.thread.id}`}
            className="mb-2 block cursor-pointer rounded-xl border border-slate-800 bg-gikai-card p-3.5 no-underline transition-colors hover:border-slate-700"
          >
            <div className="mb-1.5 flex flex-wrap justify-between gap-1.5">
              <div className="flex items-center gap-2">
                <span
                  className="rounded px-1.5 py-0.5 text-[11px] font-semibold"
                  style={{ color: ts.color, background: ts.bg }}
                >
                  {ts.icon} {sp.tension}
                </span>
                <span className="text-[11px] text-slate-700">
                  {sp.thread.committee} · {sp.thread.date}
                </span>
              </div>
              <span
                className="rounded px-2 py-0.5 text-[11px]"
                style={{
                  color: sp.thread.topicColor,
                  background: `${sp.thread.topicColor}15`,
                }}
              >
                {sp.thread.topic}
              </span>
            </div>
            <p className="text-sm leading-relaxed text-slate-300">
              {sp.summaries.adult}
            </p>
          </Link>
        );
      })}
    </div>
  );
}
