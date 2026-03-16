"use client";

import Link from "next/link";
import type { Member, Thread } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { SpeechRow } from "@/components/ui/speech-row";
import { ShareButton } from "@/components/ui/share-button";
import { buildThreadShare } from "@/lib/utils";

type ThreadDetailViewProps = {
  thread: Thread;
  members: Record<string, Member>;
};

export function ThreadDetailView({
  thread,
  members,
}: ThreadDetailViewProps) {
  const { level, follows } = useAppContext();

  const depths = thread.speeches.map((s, i) => {
    if (i === 0) return 0;
    return s.memberId === thread.speeches[0].memberId
      ? Math.min(3, Math.floor(i * 0.7))
      : Math.min(2, Math.floor(i * 0.4));
  });

  return (
    <>
      {/* Sticky header */}
      <div className="sticky top-0 z-40 flex h-[53px] items-center gap-5 bg-x-bg/65 px-4 backdrop-blur-xl">
        <Link
          href="/"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-x-hover"
        >
          <span className="text-xl">←</span>
        </Link>
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
          <span>{thread.house}</span>
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

        <p className="mt-3 text-[15px] leading-[24px] text-x-secondary">
          {thread.summary}
        </p>
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

      {/* Source attribution */}
      <div className="border-t border-x-border px-4 py-4 text-[13px] text-x-secondary">
        📄 出典：国会会議録検索システム（NDL）　🤖 AI要約：Claude（事前生成）
      </div>
    </>
  );
}
