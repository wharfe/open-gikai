"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { Member, Thread } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { SpeechRow } from "@/components/ui/speech-row";
import { ShareButton } from "@/components/ui/share-button";
import { buildThreadShare } from "@/lib/utils";
import { SOURCE_STYLE } from "@/lib/config";

type ThreadDetailViewProps = {
  thread: Thread;
  members: Record<string, Member>;
};

export function ThreadDetailView({
  thread,
  members,
}: ThreadDetailViewProps) {
  const router = useRouter();
  const { level, follows } = useAppContext();
  const [contextOpen, setContextOpen] = useState(false);

  const depths = thread.speeches.map((s, i) => {
    if (i === 0) return 0;
    return s.memberId === thread.speeches[0].memberId
      ? Math.min(3, Math.floor(i * 0.7))
      : Math.min(2, Math.floor(i * 0.4));
  });

  return (
    <>
      {/* Sticky header — offset by mobile header height on small screens */}
      <div className="sticky top-[53px] z-40 flex h-[53px] items-center gap-5 bg-x-bg/65 px-4 backdrop-blur-xl md:top-0">
        <button
          onClick={() => router.back()}
          className="flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-full border-none bg-transparent text-x-text transition-colors hover:bg-x-hover"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 20 }}>arrow_back</span>
        </button>
        <div className="min-w-0">
          <div className="truncate text-[17px] font-bold leading-tight">
            {thread.topic}
          </div>
          <div className="text-[13px] text-x-secondary">
            {thread.speeches.length}件の発言
          </div>
        </div>
      </div>

      {/* Thread header info */}
      <div className="border-b border-x-border px-4 py-4">
        <div className="flex items-center gap-2 text-[15px] text-x-secondary">
          {SOURCE_STYLE[thread.source ?? ""] ? (
            <span
              className="inline-flex items-center gap-0.5 font-medium"
              style={{ color: SOURCE_STYLE[thread.source!].color }}
            >
              <span className="material-symbols-rounded" style={{ fontSize: 16 }}>{SOURCE_STYLE[thread.source!].icon}</span>
              {SOURCE_STYLE[thread.source!].label}
            </span>
          ) : (
            <span>{thread.house}</span>
          )}
          <span>·</span>
          <span>{thread.committee}</span>
          <span>·</span>
          <span>{thread.date}</span>
        </div>

        <div className="mt-3 flex items-center justify-between">
          <span
            className="rounded-full px-3 py-1 text-[13px] font-bold"
            style={{
              color: thread.topicColor,
              background: `${thread.topicColor}18`,
            }}
          >
            {thread.topicTag}
          </span>
          <ShareButton text={buildThreadShare(thread, members)} />
        </div>

        {/* Life impact */}
        {thread.impact && (
          <div className="mt-3 flex items-center gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-[14px] text-amber-400">
            <span className="material-symbols-rounded" style={{ fontSize: 18 }}>person</span>
            {thread.impact}
          </div>
        )}

        <p className="mt-3 text-[15px] leading-[24px] text-x-secondary">
          {thread.summary}
        </p>

        {/* Debate highlight */}
        {thread.debate && (
          <div className="mt-3 rounded-xl border border-x-border bg-x-surface px-4 py-3">
            <div className="mb-2 flex items-center gap-1.5 text-[13px] font-bold text-orange-400">
              <span className="material-symbols-rounded" style={{ fontSize: 16 }}>swap_horiz</span>
              争点
            </div>
            <div className="flex items-start gap-3 text-[14px] leading-[22px]">
              <div className="flex-1 text-x-text">{thread.debate.position}</div>
              <span className="mt-1 shrink-0 text-x-secondary">↔</span>
              <div className="flex-1 text-x-text">{thread.debate.counterPosition}</div>
            </div>
          </div>
        )}

        {/* Context: background info */}
        {thread.context && (
          <div className="mt-3">
            <button
              onClick={() => setContextOpen(!contextOpen)}
              className="flex cursor-pointer items-center gap-1.5 rounded-full border-none bg-transparent px-0 py-1 text-[13px] text-x-accent transition-colors hover:underline"
            >
              <span>{contextOpen ? "▾" : "▸"}</span>
              この議題について
            </button>
            {contextOpen && (
              <div className="mt-2 rounded-xl border border-x-border bg-x-surface px-4 py-3">
                <p className="text-[14px] leading-[22px] text-x-secondary">
                  {thread.context.description}
                </p>
                {thread.context.links && thread.context.links.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-3">
                    {thread.context.links.map((link, i) => (
                      <a
                        key={i}
                        href={link.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[13px] text-x-accent hover:underline"
                      >
                        {link.label} <span className="material-symbols-rounded align-middle" style={{ fontSize: 14 }}>open_in_new</span>
                      </a>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Speeches */}
      {thread.speeches.map((speech, i) => (
        <SpeechRow
          key={i}
          speech={speech}
          member={members[speech.memberId]}
          level={level}
          thread={thread}
          depth={depths[i]}
          isLast={i === thread.speeches.length - 1}
          followed={follows.has(speech.memberId)}
        />
      ))}

      {/* Outcome section */}
      {thread.outcome &&
        (thread.outcome.result ||
          (thread.outcome.commitments &&
            thread.outcome.commitments.length > 0)) && (
          <div className="border-t border-x-border px-4 py-4">
            <div className="text-[13px] font-bold uppercase tracking-wider text-x-secondary">
              結論
            </div>

            {thread.outcome.result && (
              <div className="mt-2 flex items-center gap-2">
                <span
                  className={`inline-block rounded-full px-2.5 py-0.5 text-[13px] font-bold ${
                    thread.outcome.result === "可決"
                      ? "bg-green-500/10 text-green-500"
                      : thread.outcome.result === "否決"
                        ? "bg-red-500/10 text-red-500"
                        : "bg-yellow-500/10 text-yellow-500"
                  }`}
                >
                  {thread.outcome.result}
                </span>
                {thread.outcome.resolution && (
                  <span className="text-[13px] text-x-secondary">
                    {thread.outcome.resolution}
                  </span>
                )}
              </div>
            )}

            {thread.outcome.commitments &&
              thread.outcome.commitments.length > 0 && (
                <div className="mt-3 space-y-1.5">
                  <div className="text-[13px] font-bold text-x-secondary">
                    答弁での約束
                  </div>
                  {thread.outcome.commitments.map((c, i) => (
                    <div
                      key={i}
                      className="flex items-start gap-2 text-[14px] leading-[22px] text-x-text"
                    >
                      <span className="mt-0.5 shrink-0 text-[12px] text-blue-400">
                        &rarr;
                      </span>
                      <span>{c}</span>
                    </div>
                  ))}
                </div>
              )}

            {!thread.outcome.result &&
              thread.outcome.status === "ongoing" &&
              thread.outcome.commitments &&
              thread.outcome.commitments.length === 0 && (
                <div className="mt-2 text-[13px] text-x-secondary">
                  この質疑では採決は行われていません
                </div>
              )}
          </div>
        )}

      {/* Related threads */}
      {thread.relatedThreads && thread.relatedThreads.length > 0 && (
        <div className="border-t border-x-border px-4 py-4">
          <div className="text-[13px] font-bold uppercase tracking-wider text-x-secondary">
            関連スレッド
          </div>
          <div className="mt-2 space-y-2">
            {thread.relatedThreads.map((link) => (
              <Link
                key={link.threadId}
                href={`/t/${link.threadId}`}
                className="block rounded-xl border border-x-border px-3 py-2.5 transition-colors hover:bg-x-hover"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ${
                      link.relationship === "同一法案"
                        ? "bg-blue-500/10 text-blue-400"
                        : link.relationship === "続き"
                          ? "bg-green-500/10 text-green-400"
                          : "bg-x-hover text-x-secondary"
                    }`}
                  >
                    {link.relationship}
                  </span>
                  <span className="text-[13px] text-x-secondary">
                    {link.committee} · {link.date}
                  </span>
                </div>
                <div className="mt-1 text-[14px] leading-tight text-x-text">
                  {link.topic}
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* Source attribution */}
      <div className="border-t border-x-border px-4 py-4 text-[13px] text-x-secondary">
        <span className="material-symbols-rounded align-middle" style={{ fontSize: 14 }}>description</span> 出典：{thread.sourceLabel || "国会会議録検索システム（NDL）"}　<span className="material-symbols-rounded align-middle" style={{ fontSize: 14 }}>smart_toy</span> AI要約：Claude（事前生成）
      </div>
    </>
  );
}
