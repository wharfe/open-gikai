"use client";

import { useState } from "react";
import Link from "next/link";
import { LevelBar } from "@/components/ui/level-bar";

export function MobileHeader() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className="sticky top-0 z-50 md:hidden">
      <div className="flex h-[53px] items-center justify-between border-b border-x-border bg-x-bg/80 px-4 backdrop-blur-xl">
        <Link href="/" className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.5)]" />
          <span className="text-lg font-extrabold">
            OpenGIK<span className="text-emerald-400">AI</span>
          </span>
        </Link>
        <div className="flex items-center gap-3">
          <LevelBar />
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-full border-none bg-transparent text-x-text transition-colors hover:bg-x-hover"
            aria-label="メニュー"
          >
            <span className="text-[20px]">{menuOpen ? "✕" : "☰"}</span>
          </button>
        </div>
      </div>

      {/* Mobile menu dropdown */}
      {menuOpen && (
        <div className="border-b border-x-border bg-x-bg px-4 py-3">
          <nav className="space-y-1">
            <Link
              href="/"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] transition-colors hover:bg-x-hover"
            >
              <span>🏠</span> ホーム
            </Link>
            <Link
              href="/search"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] transition-colors hover:bg-x-hover"
            >
              <span>🔍</span> 検索
            </Link>
            <Link
              href="/members"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] transition-colors hover:bg-x-hover"
            >
              <span>👤</span> 議員
            </Link>
            <Link
              href="/about"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] transition-colors hover:bg-x-hover"
            >
              <span>ℹ️</span> About
            </Link>
            <a
              href="https://github.com/wharfe/open-gikai"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] transition-colors hover:bg-x-hover"
            >
              <span>📦</span> GitHub
            </a>
          </nav>
        </div>
      )}
    </div>
  );
}
