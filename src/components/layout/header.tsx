"use client";

import Link from "next/link";
import { LevelBar } from "@/components/ui/level-bar";

export function Header() {
  return (
    <div className="sticky top-0 z-[100] border-b border-slate-800 bg-gikai-bg/95 backdrop-blur-[12px]">
      <div className="mx-auto max-w-[920px] px-4">
        <div className="flex h-[52px] items-center justify-between gap-3">
          <Link
            href="/"
            className="flex items-center gap-2.5 no-underline"
          >
            <div className="h-1.5 w-1.5 rounded-full bg-green-500 shadow-[0_0_8px_#22c55e]" />
            <span className="text-[17px] font-bold tracking-wider text-slate-50">
              GIKAI
            </span>
            <span className="text-[10px] tracking-[2px] text-slate-700">
              国会をひらく
            </span>
          </Link>
          <LevelBar />
        </div>
      </div>
    </div>
  );
}
