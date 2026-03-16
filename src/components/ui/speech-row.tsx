"use client";

import { useState } from "react";
import type { Level, Member, Speech, Thread } from "@/types";
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
  const tension = TENSION_STYLE[speech.tension];
  const ms = getStyle(member);
  const badge = RANK_BADGE[member.rank];

  return (
    <article
      className="relative flex gap-3 border-b border-x-border px-4 py-3 transition-colors hover:bg-x-hover"
      style={{ paddingLeft: 16 + depth * 12 }}
    >
      {/* Thread line */}
      {!isLast && (
        <div
          className="pointer-events-none absolute w-0.5 bg-x-border"
          style={{
            left: 16 + depth * 12 + 18,
            top: 56,
            bottom: 0,
          }}
        />
      )}

      {/* Avatar */}
      <Avatar
        member={member}
        size={40}
        linkToProfile
        followed={followed}
      />

      {/* Content */}
      <div className="min-w-0 flex-1">
        {/* Name row — X style */}
        <div className="mb-0.5 flex flex-wrap items-center gap-1">
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
          {followed && (
            <span className="text-[11px] text-x-accent">フォロー中</span>
          )}
        </div>

        {/* Tension badge */}
        <div className="mb-2">
          <span
            className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[13px] font-semibold"
            style={{ color: tension.color, background: tension.bg }}
          >
            {tension.icon} {speech.tension}
          </span>
        </div>

        {/* Summary text — like tweet body */}
        <div className="mb-3 text-[15px] leading-[20px] text-x-text">
          {speech.summaries[level]}
        </div>

        {/* Quote — X style quoted tweet */}
        {speech.quote && level === "adult" && (
          <div className="mb-3 rounded-2xl border border-x-border px-4 py-3 text-[15px] leading-[20px] text-x-secondary">
            「{speech.quote}」
          </div>
        )}

        {/* Keywords — like hashtags */}
        <div className="mb-3">
          {speech.keywords.map((k) => (
            <span key={k} className="mr-2 text-[15px] text-x-accent">
              #{k}
            </span>
          ))}
        </div>

        {/* Action row — X style icons with spacing */}
        <div className="-ml-2 flex items-center justify-between text-x-secondary">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex cursor-pointer items-center gap-1.5 rounded-full border-none bg-transparent px-2 py-1.5 text-[13px] text-x-secondary transition-colors hover:bg-x-accent/10 hover:text-x-accent"
          >
            📄 {expanded ? "閉じる" : "原文"}
          </button>
          <span className="text-[13px]">
            💬 {speech.keywords.length}
          </span>
          <ShareButton
            text={buildSpeechShare(speech, member, thread, level)}
          />
          <span className="w-8" />
        </div>

        {/* Raw transcript — expandable */}
        {expanded && (
          <div className="mt-2 rounded-2xl border border-x-border bg-x-bg p-4 text-[15px] leading-[20px] text-x-secondary">
            <div className="mb-2 text-[13px] text-x-secondary/60">
              📄 原文（国会会議録 NDL APIより）
            </div>
            {speech.raw}
          </div>
        )}
      </div>
    </article>
  );
}
