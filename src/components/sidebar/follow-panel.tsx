"use client";

import Link from "next/link";
import type { Member } from "@/types";
import { useAppContext } from "@/components/providers/app-provider";
import { Avatar } from "@/components/ui/avatar";
import { getStyle } from "@/lib/utils";
import { PARTY_STYLE } from "@/lib/config";

type FollowPanelProps = {
  members: Record<string, Member>;
};

export function FollowPanel({ members }: FollowPanelProps) {
  const { follows, toggleFollow } = useAppContext();

  if (follows.size === 0) return null;

  return (
    <div className="overflow-hidden rounded-2xl bg-x-surface">
      <h2 className="px-4 py-3 text-[20px] font-extrabold text-x-text">
        ⭐ フォロー中
      </h2>
      {[...follows].map((id) => {
        const m = members[id];
        if (!m) return null;
        const ms = getStyle(m);
        return (
          <Link
            key={id}
            href={`/m/${id}`}
            className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-x-hover"
          >
            <Avatar member={m} size={40} followed />
            <div className="flex-1">
              <div className="text-[15px] font-bold text-x-text">
                {m.name}
              </div>
              <div className="text-[15px] text-x-secondary">
                {m.party && PARTY_STYLE[m.party]
                  ? PARTY_STYLE[m.party].short
                  : m.role}
              </div>
            </div>
            <button
              onClick={(e) => { e.preventDefault(); toggleFollow(id); }}
              className="cursor-pointer rounded-full border px-4 py-1.5 text-[13px] font-bold transition-colors hover:border-red-500/50 hover:text-red-400"
              style={{
                borderColor: ms.color,
                color: ms.color,
                background: "transparent",
              }}
            >
              フォロー中
            </button>
          </Link>
        );
      })}
    </div>
  );
}
