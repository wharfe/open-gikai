"use client";

import Link from "next/link";
import type { Member, Thread } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { Avatar } from "@/components/ui/avatar";
import { ShareButton } from "@/components/ui/share-button";
import { buildThreadShare } from "@/lib/utils";

type ThreadCardProps = {
  thread: Thread;
  members: Record<string, Member>;
};

export function ThreadCard({ thread, members }: ThreadCardProps) {
  const { follows } = useAppContext();
  const actors = [...new Set(thread.speeches.map((s) => s.memberId))];

  return (
    <article className="border-b border-x-border px-4 py-4 transition-colors hover:bg-x-hover">
      <Link href={`/t/${thread.id}`} className="block">
        {/* Committee & date */}
        <div className="flex items-center gap-1.5 text-[15px]">
          <span className="font-bold text-x-text">{thread.committee}</span>
          <span className="text-x-secondary">·</span>
          <span className="text-x-secondary">{thread.date}</span>
          <span className="text-x-secondary">·</span>
          <span className="text-x-secondary">{thread.house}</span>
        </div>

        {/* Topic tag */}
        <div className="mt-3">
          <span
            className="inline-block rounded-full px-3 py-1 text-[13px] font-bold"
            style={{
              color: thread.topicColor,
              background: `${thread.topicColor}18`,
            }}
          >
            {thread.topic}
          </span>
        </div>

        {/* Summary */}
        <p className="mt-3 text-[15px] leading-[24px] text-x-text">
          {thread.summary}
        </p>

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
      </Link>

      {/* Footer */}
      <div className="mt-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex -space-x-1.5">
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
          <span className="text-[13px] text-x-secondary">
            {actors.map((id) => members[id].name.split(" ")[0]).join("、")}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-4">
          <span className="text-[13px] text-x-secondary">
            💬 {thread.speeches.length}
          </span>
          <ShareButton text={buildThreadShare(thread, members)} />
        </div>
      </div>
    </article>
  );
}
