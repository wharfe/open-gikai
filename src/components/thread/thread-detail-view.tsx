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
    <div>
      <Link
        href="/"
        className="mb-4 flex items-center gap-1.5 text-[13px] text-slate-600 no-underline hover:text-slate-400"
      >
        ← フィードに戻る
      </Link>

      <div className="mb-4">
        <div className="mb-1.5 text-[11px] text-slate-600">
          {thread.house} {thread.committee} · {thread.date}
        </div>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-bold text-slate-50">
                {thread.topic}
              </h2>
              <span
                className="rounded px-2.5 py-0.5 text-[11px]"
                style={{
                  color: thread.topicColor,
                  background: `${thread.topicColor}18`,
                  border: `1px solid ${thread.topicColor}40`,
                }}
              >
                {thread.topicTag}
              </span>
            </div>
            <p className="mt-1.5 text-[13px] text-slate-500">
              {thread.summary}
            </p>
          </div>
          <ShareButton text={buildThreadShare(thread, members)} />
        </div>
      </div>

      <div className="rounded-[14px] border border-slate-800 bg-gikai-card-inner p-3.5 sm:p-4">
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
        <div className="border-t border-slate-800 pt-2.5 text-[11px] text-slate-700">
          📄 出典：国会会議録検索システム（NDL）　🤖 AI要約：Claude（事前生成）
        </div>
      </div>
    </div>
  );
}
