"use client";

import { useState } from "react";
import Link from "next/link";
import { LevelBar } from "@/components/ui/level-bar";
import { Icon } from "@/components/ui/icon";
import { useAppContext } from "@/components/providers/app-provider";

export function MobileHeader() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { theme, toggleTheme } = useAppContext();

  return (
    <div className="sticky top-0 z-50 md:hidden">
      <div className="flex h-[53px] items-center justify-between border-b border-x-border bg-x-bg/80 px-4 backdrop-blur-xl">
        <Link href="/" className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.5)]" />
          <span className="text-lg">
            <span className="font-light">Open</span><span className="font-extrabold">GIK</span><span className="font-extrabold text-emerald-400">AI</span>
          </span>
        </Link>
        <div className="flex items-center gap-3">
          <LevelBar />
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-full border-none bg-transparent text-x-text transition-colors hover:bg-x-hover"
            aria-label="メニュー"
          >
            <Icon name={menuOpen ? "close" : "menu"} size={20} />
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
              <Icon name="home" size={20} /> ホーム
            </Link>
            <Link
              href="/search"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] transition-colors hover:bg-x-hover"
            >
              <Icon name="search" size={20} /> 検索
            </Link>
            <Link
              href="/calendar"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] transition-colors hover:bg-x-hover"
            >
              <Icon name="calendar_month" size={20} /> カレンダー
            </Link>
            <Link
              href="/members"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] transition-colors hover:bg-x-hover"
            >
              <Icon name="person" size={20} /> 議員
            </Link>
            <Link
              href="/about"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] transition-colors hover:bg-x-hover"
            >
              <Icon name="info" size={20} /> About
            </Link>
            <a
              href="https://github.com/wharfe/open-gikai"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 text-[15px] transition-colors hover:bg-x-hover"
            >
              <Icon name="code" size={20} /> GitHub
            </a>
            <button
              onClick={toggleTheme}
              className="flex w-full cursor-pointer items-center gap-3 rounded-lg border-none bg-transparent px-3 py-2.5 text-[15px] text-x-text transition-colors hover:bg-x-hover"
            >
              <Icon name={theme === "dark" ? "light_mode" : "dark_mode"} size={20} />
              {theme === "dark" ? "ライトモード" : "ダークモード"}
            </button>
          </nav>
        </div>
      )}
    </div>
  );
}
