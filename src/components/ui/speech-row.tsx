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
    <div
      className="relative flex gap-2.5"
      style={{ paddingLeft: depth * 20, marginBottom: 2 }}
    >
      {/* Thread line */}
      {!isLast && (
        <div
          className="pointer-events-none absolute w-0.5"
          style={{
            left: depth * 20 + 17,
            top: 42,
            bottom: -2,
            background:
              "linear-gradient(to bottom, #1e293b88, transparent)",
          }}
        />
      )}

      <Avatar
        member={member}
        size={34}
        linkToProfile
        followed={followed}
      />

      <div className="flex-1 pb-5">
        {/* Header */}
        <div className="mb-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="flex items-center gap-1 text-sm font-bold text-slate-100">
              {member.name}
              {badge && (
                <span className="text-[13px]" title={badge.label}>
                  {badge.icon}
                </span>
              )}
              {followed && (
                <span className="text-[9px]" style={{ color: ms.color }}>
                  フォロー中
                </span>
              )}
            </span>
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
            <span className="text-[11px] text-slate-700">{member.role}</span>
          </div>
          <div className="mt-1">
            <span
              className="rounded px-1.5 py-0.5 text-[11px] font-semibold"
              style={{
                color: tension.color,
                background: tension.bg,
              }}
            >
              {tension.icon} {speech.tension}
            </span>
          </div>
        </div>

        {/* Summary */}
        <div className="mb-2.5 text-[15px] leading-[1.75] text-slate-200">
          {speech.summaries[level]}
        </div>

        {/* Quote (adult level only) */}
        {speech.quote && level === "adult" && (
          <div
            className="mb-2.5 pl-2.5 text-[13px] italic text-slate-500"
            style={{ borderLeft: `3px solid ${ms.color}50` }}
          >
            「{speech.quote}」
          </div>
        )}

        {/* Keywords */}
        <div className="mb-2 flex flex-wrap gap-1.5">
          {speech.keywords.map((k) => (
            <span
              key={k}
              className="rounded-xl border border-sky-300/20 bg-sky-300/[0.07] px-2 py-0.5 text-[11px] text-sky-300"
            >
              #{k}
            </span>
          ))}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2.5">
          <button
            onClick={() => setExpanded(!expanded)}
            className="cursor-pointer border-none bg-transparent p-0 text-[11px] text-slate-700"
          >
            {expanded ? "▲ 閉じる" : "▼ 議事録の原文"}
          </button>
          <ShareButton
            text={buildSpeechShare(speech, member, thread, level)}
          />
        </div>

        {/* Raw transcript */}
        {expanded && (
          <div className="mt-2 rounded-lg border border-slate-800 bg-slate-900/70 p-3 text-[13px] leading-[1.8] text-slate-600">
            <div className="mb-1.5 text-[10px] text-slate-800">
              📄 原文（国会会議録 NDL APIより）
            </div>
            {speech.raw}
          </div>
        )}
      </div>
    </div>
  );
}
