"use client";

import Link from "next/link";
import { LevelBar } from "@/components/ui/level-bar";

export function MobileHeader() {
  return (
    <div className="sticky top-0 z-50 flex h-[53px] items-center justify-between border-b border-x-border bg-x-bg/80 px-4 backdrop-blur-xl md:hidden">
      <Link href="/" className="flex items-center gap-2">
        <div className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.5)]" />
        <span className="text-lg font-extrabold">GIKAI</span>
      </Link>
      <LevelBar />
    </div>
  );
}
