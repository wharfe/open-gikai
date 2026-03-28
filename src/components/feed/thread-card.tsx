"use client";

import { useState } from "react";
import Link from "next/link";
import type { Member, Thread } from "@/types";
import type { ThreadSummary } from "@/lib/data";
import { useAppContext } from "@/components/providers/app-provider";
import { Avatar } from "@/components/ui/avatar";
import { ShareButton } from "@/components/ui/share-button";
import { buildThreadShare } from "@/lib/utils";
import { getLifeTheme, getLifeThemeConfig, SOURCE_STYLE } from "@/lib/config";

type ThreadCardProps = {
  thread: ThreadSummary | Thread;
  members: Record<string, Member>;
};

export function ThreadCard({ thread, members }: ThreadCardProps) {
  const { follows } = useAppContext();
  const [imgError, setImgError] = useState(false);
  const actors = "memberIds" in thread
    ? (thread as ThreadSummary).memberIds
    : [...new Set((thread as Thread).speeches?.map((s) => s.memberId) ?? [])];
  const themeId = getLifeTheme(thread.topicTag);
  const themeConfig = themeId ? getLifeThemeConfig(themeId) : null;
  const newsPreview = "newsPreview" in thread
    ? (thread as ThreadSummary).newsPreview
    : (thread as Thread).context?.news?.find((n) => n.image);

  return (
    <article className="border-b border-x-border px-4 py-4 transition-colors hover:bg-x-hover">
      <Link href={`/t/${thread.id}`} className="block">
        {/* Committee name */}
        <div className="text-[15px] font-bold text-x-text">{thread.committee}</div>

        {/* Meta: source · date */}
        <div className="mt-0.5 flex items-center gap-1.5 text-[13px] text-x-secondary">
          {SOURCE_STYLE[thread.source ?? ""] ? (
            <span
              className="material-symbols-rounded"
              style={{ fontSize: 14, color: SOURCE_STYLE[thread.source!].color }}
              title={SOURCE_STYLE[thread.source!].label}
            >
              {SOURCE_STYLE[thread.source!].icon}
            </span>
          ) : (
            <span>{thread.house}</span>
          )}
          <span>{thread.date}</span>
        </div>

        {/* Theme + Topic */}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {themeConfig && (
            <span
              className="inline-flex items-center gap-0.5 text-[12px]"
              style={{ color: themeConfig.color }}
            >
              <span className="material-symbols-rounded" style={{ fontSize: 13 }}>{themeConfig.icon}</span>
              {themeConfig.label}
              <span className="ml-0.5 text-x-secondary">›</span>
            </span>
          )}
          <span
            className="inline-block rounded-full px-2.5 py-0.5 text-[13px] font-bold"
            style={{
              color: thread.topicColor,
              background: `${thread.topicColor}18`,
            }}
          >
            {thread.topic}
          </span>
        </div>

        {/* Life impact */}
        {thread.impact && (
          <div className="mt-2 flex items-center gap-1.5 text-[13px] text-amber-400">
            <span className="material-symbols-rounded" style={{ fontSize: 15 }}>person</span>
            {thread.impact}
          </div>
        )}

        {/* Summary */}
        <p className="mt-3 text-[15px] leading-[24px] text-x-text">
          {thread.summary}
        </p>

        {/* Debate highlight */}
        {thread.debate && (
          <div className="mt-2 flex items-center gap-2 rounded-lg bg-x-surface px-3 py-2 text-[13px]">
            <span className="material-symbols-rounded shrink-0 text-orange-400" style={{ fontSize: 16 }}>swap_horiz</span>
            <span className="text-x-text">{thread.debate.position}</span>
            <span className="shrink-0 text-x-secondary">↔</span>
            <span className="text-x-text">{thread.debate.counterPosition}</span>
          </div>
        )}

        {/* Outcome badges */}
        {thread.outcome && (thread.outcome.result || (thread.outcome.commitments && thread.outcome.commitments.length > 0)) && (
          <div className="mt-2 flex items-center gap-2">
            {thread.outcome.result && (
              <span
                className={`inline-block rounded-full px-2 py-0.5 text-[12px] font-bold ${
                  thread.outcome.result === "可決"
                    ? "bg-green-500/10 text-green-500"
                    : thread.outcome.result === "否決"
                      ? "bg-red-500/10 text-red-500"
                      : "bg-yellow-500/10 text-yellow-500"
                }`}
              >
                {thread.outcome.result}
              </span>
            )}
            {thread.outcome.commitments && thread.outcome.commitments.length > 0 && (
              <span className="text-[12px] text-blue-400">
                &rarr; 約束{thread.outcome.commitments.length}件
              </span>
            )}
          </div>
        )}

        {/* News image preview (X-style link card) */}
        {newsPreview && !imgError && (
          <div className="mt-3 overflow-hidden rounded-2xl border border-x-border">
            <div className="relative aspect-[2/1] w-full overflow-hidden bg-x-surface">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={newsPreview.image}
                alt=""
                className="h-full w-full object-cover"
                loading="lazy"
                onError={() => setImgError(true)}
              />
              <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/70 to-transparent px-3 py-2">
                <div className="flex items-center gap-1 text-[12px] text-white/80">
                  <span className="material-symbols-rounded" style={{ fontSize: 12 }}>language</span>
                  {(() => { try { return new URL(newsPreview.url).hostname.replace(/^www\./, ""); } catch { return ""; } })()}
                </div>
                <div className="line-clamp-1 text-[13px] leading-tight text-white">
                  {newsPreview.title}
                </div>
              </div>
            </div>
          </div>
        )}
      </Link>

      {/* Footer */}
      <div className="mt-4 flex items-center justify-between">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <div className="flex shrink-0 -space-x-1.5">
            {actors.map((id) => (
              <div key={id} className="relative">
                <Avatar
                  member={members[id]}
                  size={24}
                  linkToProfile
                  followed={follows.has(id)}
                />
              </div>
            ))}
          </div>
          <span className="min-w-0 truncate text-[13px] text-x-secondary">
            {actors.map((id) => members[id].name.split(" ")[0]).join("、")}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-3 pl-3">
          <span className="text-[13px] text-x-secondary">
            <span className="material-symbols-rounded align-middle" style={{ fontSize: 16 }}>chat_bubble</span> {"speechCount" in thread ? (thread as ThreadSummary).speechCount : ((thread as Thread).speeches?.length ?? 0)}
          </span>
          <ShareButton text={buildThreadShare(thread, members)} />
        </div>
      </div>
    </article>
  );
}
