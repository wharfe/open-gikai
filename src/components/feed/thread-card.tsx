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
    <div className="mb-2.5 rounded-[14px] border border-slate-800 bg-gikai-card p-4 transition-all hover:translate-y-[-1px] hover:border-slate-700">
      <Link href={`/t/${thread.id}`} className="block cursor-pointer no-underline">
        <div className="mb-2.5 flex flex-wrap justify-between gap-1.5">
          <div>
            <div className="mb-1.5 text-[11px] text-slate-600">
              {thread.house} {thread.committee} · {thread.date}
            </div>
            <span
              className="rounded px-2.5 py-0.5 text-[11px] font-bold"
              style={{
                color: thread.topicColor,
                background: `${thread.topicColor}18`,
                border: `1px solid ${thread.topicColor}40`,
              }}
            >
              {thread.topic}
            </span>
          </div>
          <span className="text-[11px] text-slate-700">
            {thread.speeches.length}発言 →
          </span>
        </div>
        <p className="mb-3 text-sm leading-relaxed text-slate-300">
          {thread.summary}
        </p>
      </Link>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="flex">
            {actors.map((id, i) => (
              <div
                key={id}
                style={{
                  marginLeft: i > 0 ? -8 : 0,
                  zIndex: actors.length - i,
                }}
              >
                <Avatar
                  member={members[id]}
                  size={26}
                  linkToProfile
                  followed={follows.has(id)}
                />
              </div>
            ))}
          </div>
          <span className="text-[11px] text-slate-600">
            {actors.map((id) => members[id].name.split(" ")[0]).join("、")}
          </span>
        </div>
        <ShareButton text={buildThreadShare(thread, members)} />
      </div>
    </div>
  );
}
