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
  const { follows, toggleFollow, level } = useAppContext();

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
      {/* Sticky header */}
      <div className="sticky top-[53px] z-40 flex h-[53px] items-center gap-5 bg-x-bg/65 px-4 backdrop-blur-xl md:top-0">
        <Link
          href="/"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-x-hover"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 20 }}>arrow_back</span>
        </Link>
        <div className="min-w-0">
          <div className="truncate text-[17px] font-bold leading-tight">
            {member.name}
          </div>
          <div className="text-[13px] text-x-secondary">
            {speeches.length}件の発言
          </div>
        </div>
      </div>

      {/* Banner */}
      <div
        className="h-[200px]"
        style={{
          background: `linear-gradient(135deg, ${ms.color}40, ${ms.color}15 40%, #16181c)`,
        }}
      />

      {/* Avatar + Follow */}
      <div className="relative px-4">
        <div className="-mt-[67px] flex items-end justify-between">
          <div className="rounded-full border-4 border-x-bg">
            <Avatar member={member} size={134} followed={isFollowed} />
          </div>
          <div className="pb-3">
            <button
              onClick={() => toggleFollow(member.id)}
              className="cursor-pointer rounded-full px-5 py-2 text-[15px] font-bold transition-colors"
              style={{
                background: isFollowed ? "transparent" : "#e7e9ea",
                border: isFollowed ? "1px solid #536471" : "none",
                color: isFollowed ? "#e7e9ea" : "#0f1419",
              }}
            >
              {isFollowed ? <><span className="material-symbols-rounded align-middle" style={{ fontSize: 16 }}>check</span> フォロー中</> : <><span className="material-symbols-rounded align-middle" style={{ fontSize: 16 }}>add</span> フォロー</>}
            </button>
          </div>
        </div>
      </div>

      {/* Profile info */}
      <div className="border-b border-x-border px-4 pb-5 pt-3">
        {/* Name */}
        <div className="flex flex-wrap items-center gap-2">
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
              style={{ color: ms.color, background: ms.bg }}
            >
              {PARTY_STYLE[member.party].short}
            </span>
          )}
        </div>

        {/* Role info */}
        <div className="mt-1.5 text-[15px] text-x-secondary">
          {member.role}
          {member.district ? ` · ${member.district}` : ""}
          {member.since ? ` · ${member.since}年〜` : ""}
        </div>
        <div className="mt-1 text-[12px] text-x-secondary/60">
          ※ 役職は国会会議録の記載に基づく（最新の状況と異なる場合があります）
        </div>
        {badge && (
          <div className="mt-1 text-[13px]" style={{ color: badge.color }}>
            {badge.icon} {badge.label}
          </div>
        )}

        {/* Bio */}
        {member.bio && (
          <p className="mt-4 text-[15px] leading-[24px] text-x-text">
            {member.bio}
          </p>
        )}

        {/* Stance tags */}
        {member.stance.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {member.stance.map((st) => (
              <span
                key={st}
                className="rounded-full px-3 py-1 text-[13px]"
                style={{ color: ms.color, background: ms.bg }}
              >
                {st}
              </span>
            ))}
          </div>
        )}

        {/* External links */}
        {member.links && member.links.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-3">
            {member.links.map((link) => (
              <a
                key={link.label}
                href={link.url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-full border border-x-border px-3 py-1 text-[13px] text-x-accent transition-colors hover:bg-x-accent/10"
              >
                {link.label} <span className="material-symbols-rounded align-middle" style={{ fontSize: 14 }}>open_in_new</span>
              </a>
            ))}
          </div>
        )}

        {/* Tension stats */}
        <div className="mt-4 flex flex-wrap gap-4">
          {Object.entries(tensionCount).map(([t, n]) => {
            const ts = TENSION_STYLE[t] || { icon: "•", color: "#6b7280", bg: "transparent" };
            return (
              <span key={t} className="text-[14px]">
                <span className="font-bold text-x-text">{n}</span>{" "}
                <span className="text-x-secondary"><span className="material-symbols-rounded" style={{ fontSize: 14 }}>{ts.icon}</span> {t}</span>
              </span>
            );
          })}
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-x-border">
        <div className="relative flex-1 py-4 text-center text-[15px] font-bold text-x-text">
          発言
          <div className="absolute bottom-0 left-1/2 h-1 w-16 -translate-x-1/2 rounded-full bg-x-accent" />
        </div>
      </div>

      {/* Speech list */}
      {speeches.map((sp, i) => {
        const ts = TENSION_STYLE[sp.tension];
        return (
          <Link
            key={i}
            href={`/t/${sp.thread.id}`}
            className="block border-b border-x-border px-4 py-4 transition-colors hover:bg-x-hover"
          >
            <div className="flex flex-wrap items-center gap-1.5 text-[15px]">
              <span className="font-bold text-x-text">
                {sp.thread.committee}
              </span>
              <span className="text-x-secondary">·</span>
              <span className="text-x-secondary">{sp.thread.date}</span>
            </div>

            <div className="mt-2 flex items-center gap-2">
              <span
                className="rounded-full px-2.5 py-0.5 text-[13px] font-semibold"
                style={{ color: ts.color, background: ts.bg }}
              >
                <span className="material-symbols-rounded" style={{ fontSize: 14 }}>{ts.icon}</span> {sp.tension}
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

            <p className="mt-3 text-[15px] leading-[24px] text-x-text">
              {sp.summaries[level]}
            </p>
          </Link>
        );
      })}

      {/* Disclaimer */}
      <div className="border-t border-x-border px-4 py-4 text-[13px] leading-[20px] text-x-secondary/60">
        <p>
          このプロフィール情報は国会会議録のメタデータから自動抽出しています。
          役職・所属は会議録記載時点のものであり、最新の状況と異なる場合があります。
          正確な情報は
          <a href="https://www.shugiin.go.jp/" target="_blank" rel="noopener noreferrer" className="text-x-accent hover:underline">衆議院</a>
          ・
          <a href="https://www.sangiin.go.jp/" target="_blank" rel="noopener noreferrer" className="text-x-accent hover:underline">参議院</a>
          の公式サイトをご確認ください。
        </p>
      </div>
    </>
  );
}
