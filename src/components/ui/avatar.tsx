"use client";

import Link from "next/link";
import type { Member } from "@/types";
import { RANK_BADGE } from "@/lib/config";
import { getStyle } from "@/lib/utils";

type AvatarProps = {
  member: Member;
  size?: number;
  linkToProfile?: boolean;
  followed?: boolean;
};

export function Avatar({
  member,
  size = 36,
  linkToProfile = false,
  followed = false,
}: AvatarProps) {
  const ms = getStyle(member);
  const badge = RANK_BADGE[member.rank];
  const initials = member.name.replace(" ", "").slice(0, 2);

  const avatarEl = (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <div
        className="flex items-center justify-center rounded-full font-bold transition-transform hover:scale-[1.08]"
        style={{
          width: size,
          height: size,
          background: ms.bg,
          border: `2px solid ${followed ? ms.color : ms.border}`,
          fontSize: size * 0.28,
          color: ms.color,
          cursor: linkToProfile ? "pointer" : "default",
          boxShadow: followed
            ? `0 0 8px ${ms.color}40`
            : badge
              ? `0 0 10px ${badge.color}30`
              : "none",
        }}
      >
        {initials}
      </div>
      {badge && (
        <div
          className="absolute -bottom-0.5 -right-0.5 leading-none"
          style={{
            fontSize: size * 0.38,
            filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.5))",
          }}
        >
          {badge.icon}
        </div>
      )}
    </div>
  );

  if (linkToProfile) {
    return <Link href={`/m/${member.id}`}>{avatarEl}</Link>;
  }
  return avatarEl;
}
