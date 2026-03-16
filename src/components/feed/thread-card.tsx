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
    <article className="border-b border-x-border px-4 py-3 transition-colors hover:bg-x-hover">
      <Link href={`/t/${thread.id}`} className="block">
        {/* Committee & date — like X username row */}
        <div className="mb-1 flex items-center gap-1 text-[15px]">
          <span className="font-bold text-x-text">
            {thread.committee}
          </span>
          <span className="text-x-secondary">·</span>
          <span className="text-x-secondary">{thread.date}</span>
          <span className="text-x-secondary">·</span>
          <span className="text-x-secondary">{thread.house}</span>
        </div>

        {/* Topic tag */}
        <div className="mb-2">
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

        {/* Summary — like tweet text */}
        <p className="mb-3 text-[15px] leading-[20px] text-x-text">
          {thread.summary}
        </p>
      </Link>

      {/* Footer — like X action row */}
      <div className="flex items-center justify-between">
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
        <div className="flex items-center gap-4">
          <span className="text-[13px] text-x-secondary">
            💬 {thread.speeches.length}
          </span>
          <ShareButton text={buildThreadShare(thread, members)} />
        </div>
      </div>
    </article>
  );
}
