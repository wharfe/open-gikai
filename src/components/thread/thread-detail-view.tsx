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
      {/* Sticky header — X style with back arrow */}
      <div className="sticky top-0 z-40 flex h-[53px] items-center gap-6 border-b border-x-border bg-x-bg/65 px-4 backdrop-blur-xl">
        <Link
          href="/"
          className="flex h-9 w-9 items-center justify-center rounded-full text-xl transition-colors hover:bg-x-hover"
        >
          ←
        </Link>
        <div>
          <div className="text-[17px] font-bold leading-tight">
            {thread.topic}
          </div>
          <div className="text-[13px] text-x-secondary">
            {thread.speeches.length}件の発言
          </div>
        </div>
      </div>

      {/* Thread header info */}
      <div className="border-b border-x-border px-4 py-3">
        <div className="mb-2 flex items-center gap-2 text-[15px] text-x-secondary">
          <span>{thread.house}</span>
          <span>·</span>
          <span>{thread.committee}</span>
          <span>·</span>
          <span>{thread.date}</span>
        </div>
        <div className="mb-2 flex items-center justify-between">
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
        <p className="text-[15px] leading-[20px] text-x-secondary">
          {thread.summary}
        </p>
      </div>

      {/* Speeches */}
      <div>
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

        {/* Source attribution */}
        <div className="border-t border-x-border px-4 py-3 text-[13px] text-x-secondary">
          📄 出典：国会会議録検索システム（NDL）　🤖 AI要約：Claude（事前生成）
        </div>
      </div>
    </>
  );
}
