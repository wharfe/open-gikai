"use client";

import Link from "next/link";
import type { Member } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { Avatar } from "@/components/ui/avatar";

type FollowPanelProps = {
  members: Record<string, Member>;
};

export function FollowPanel({ members }: FollowPanelProps) {
  const { follows } = useAppContext();

  if (follows.size === 0) return null;

  return (
    <div className="mt-4 rounded-[14px] border border-slate-800 bg-gikai-card p-4">
      <div className="mb-3 text-[13px] font-bold text-slate-400">
        ⭐ フォロー中
      </div>
      {[...follows].map((id) => {
        const m = members[id];
        if (!m) return null;
        return (
          <Link
            key={id}
            href={`/m/${id}`}
            className="mb-2.5 flex items-center gap-2 no-underline"
          >
            <Avatar member={m} size={28} followed />
            <div>
              <div className="text-[13px] text-slate-200">{m.name}</div>
              <div className="text-[10px] text-slate-600">
                {m.party || m.role}
              </div>
            </div>
          </Link>
        );
      })}
    </div>
  );
}
