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
  size = 40,
  linkToProfile = false,
  followed = false,
}: AvatarProps) {
  const ms = getStyle(member);
  const badge = RANK_BADGE[member.rank];
  const initials = member.name.replace(" ", "").slice(0, 2);

  const avatarEl = (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <div
        className="flex items-center justify-center rounded-full font-bold transition-opacity hover:opacity-90"
        style={{
          width: size,
          height: size,
          background: ms.bg,
          border: followed
            ? `2px solid ${ms.color}`
            : `1px solid ${ms.border}`,
          fontSize: size * 0.3,
          color: ms.color,
          cursor: linkToProfile ? "pointer" : "default",
        }}
      >
        {initials}
      </div>
      {badge && (
        <div
          className="absolute -bottom-0.5 -right-0.5 leading-none"
          style={{
            fontSize: size * 0.35,
            filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.8))",
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
