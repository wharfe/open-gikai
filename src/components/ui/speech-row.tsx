"use client";

import { useState } from "react";
import type { Level, Member, Speech, Thread } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { RANK_BADGE, TENSION_STYLE, PARTY_STYLE } from "@/lib/config";
import { getStyle, buildSpeechShare } from "@/lib/utils";
import { Avatar } from "@/components/ui/avatar";
import { ShareButton } from "@/components/ui/share-button";

type SpeechRowProps = {
  speech: Speech;
  member: Member;
  level: Level;
  thread: Thread;
  depth?: number;
  isLast?: boolean;
  followed?: boolean;
};

export function SpeechRow({
  speech,
  member,
  level,
  thread,
  depth = 0,
  isLast = false,
  followed = false,
}: SpeechRowProps) {
  const [expanded, setExpanded] = useState(false);
  const { toggleFollow } = useAppContext();
  const tension = TENSION_STYLE[speech.tension];
  const ms = getStyle(member);
  const badge = RANK_BADGE[member.rank];

  return (
    <article
      className={`relative flex gap-3 px-4 py-4 transition-colors hover:bg-x-hover ${isLast ? "border-b border-x-border" : ""}`}
      style={{ paddingLeft: 16 + depth * 12 }}
    >
      {/* Thread line */}
      {!isLast && (
        <div
          className="pointer-events-none absolute w-0.5 bg-x-border"
          style={{
            left: 16 + depth * 12 + 18,
            top: 58,
            bottom: -16,
          }}
        />
      )}

      {/* Avatar */}
      <div className="shrink-0 pt-0.5">
        <Avatar
          member={member}
          size={40}
          linkToProfile
          followed={followed}
        />
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        {/* Name row */}
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[15px] font-bold text-x-text">
            {member.name}
          </span>
          {badge && (
            <span className="text-[14px]" title={badge.label}>
              {badge.icon}
            </span>
          )}
          {member.party && PARTY_STYLE[member.party] && (
            <span
              className="rounded px-1.5 py-px text-[12px] font-bold"
              style={{ color: ms.color, background: ms.bg }}
            >
              {PARTY_STYLE[member.party].short}
            </span>
          )}
          <span className="text-[15px] text-x-secondary">·</span>
          <span className="text-[15px] text-x-secondary">{member.role}</span>
          <button
            onClick={(e) => { e.preventDefault(); toggleFollow(speech.memberId); }}
            className="cursor-pointer rounded-full border-none bg-transparent px-1.5 py-0.5 text-[11px] font-bold transition-colors hover:bg-x-accent/10"
            style={{ color: followed ? "#1d9bf0" : "#71767b" }}
          >
            {followed ? "フォロー中 ✓" : "+ フォロー"}
          </button>
        </div>

        {/* Tension badge */}
        <div className="mt-2">
          <span
            className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[13px] font-semibold"
            style={{ color: tension.color, background: tension.bg }}
          >
            {tension.icon} {speech.tension}
          </span>
        </div>

        {/* Summary text */}
        <div className="mt-3 text-[15px] leading-[24px] text-x-text">
          {speech.summaries[level]}
        </div>

        {/* Quote */}
        {speech.quote && level === "adult" && (
          <div className="mt-4 rounded-2xl border border-x-border px-4 py-3 text-[15px] leading-[24px] text-x-secondary">
            「{speech.quote}」
          </div>
        )}

        {/* Keywords */}
        <div className="mt-3">
          {speech.keywords.map((k) => (
            <span key={k} className="mr-3 text-[15px] text-x-accent">
              #{k}
            </span>
          ))}
        </div>

        {/* Action row */}
        <div className="-ml-2 mt-3 flex items-center gap-4 text-x-secondary">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex cursor-pointer items-center gap-1.5 rounded-full border-none bg-transparent px-2 py-1.5 text-[13px] text-x-secondary transition-colors hover:bg-x-accent/10 hover:text-x-accent"
          >
            <span className="material-symbols-rounded align-middle" style={{ fontSize: 16 }}>description</span> {expanded ? "閉じる" : "原文"}
          </button>
          <ShareButton
            text={buildSpeechShare(speech, member, thread, level)}
          />
        </div>

        {/* Raw transcript */}
        {expanded && (
          <div className="mt-4 rounded-2xl border border-x-border bg-x-bg p-4 text-[15px] leading-[24px] text-x-secondary">
            <div className="mb-3 text-[13px] text-x-secondary/60">
              <span className="material-symbols-rounded align-middle" style={{ fontSize: 14 }}>description</span> 原文（議事録より）
            </div>
            {speech.raw}
          </div>
        )}
      </div>
    </article>
  );
}
