"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LevelBar } from "@/components/ui/level-bar";
import { Icon } from "@/components/ui/icon";
import { useAppContext } from "@/components/providers/app-provider";

const NAV_ITEMS = [
  { href: "/", label: "ホーム", icon: "home" },
  { href: "/search", label: "検索", icon: "search" },
  { href: "/calendar", label: "カレンダー", icon: "calendar_month" },
  { href: "/members", label: "発言者", icon: "group" },
  { href: "/about", label: "About", icon: "info" },
];

export function Sidebar() {
  const pathname = usePathname();
  const { theme, toggleTheme } = useAppContext();

  return (
    <header className="sticky top-0 hidden h-screen w-[68px] shrink-0 flex-col justify-between border-r border-x-border px-1 py-3 md:flex xl:w-[260px] xl:px-2">
      <div className="flex flex-col items-center xl:items-start">
        {/* Logo */}
        <Link
          href="/"
          className="mb-4 flex h-[52px] items-center justify-center rounded-full px-3 transition-colors hover:bg-x-hover"
        >
          <div className="flex items-center gap-2">
            <span className="shrink-0 text-[18px] font-bold text-emerald-400">議</span>
            <span className="hidden text-xl tracking-wide xl:inline">
              <span className="font-light">Open</span><span className="font-extrabold">GIK</span><span className="font-extrabold text-emerald-400">AI</span>
            </span>
          </div>
        </Link>

        {/* Navigation */}
        <nav className="flex w-full flex-col gap-1">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className="flex h-[50px] items-center justify-center rounded-full transition-colors hover:bg-x-hover xl:justify-start xl:gap-4 xl:px-4"
              >
                <Icon name={item.icon} size={24} className="text-x-text" />
                <span
                  className="hidden truncate text-lg xl:inline"
                  style={{ fontWeight: isActive ? 700 : 400 }}
                >
                  {item.label}
                </span>
              </Link>
            );
          })}
        </nav>

        {/* Level selector */}
        <div className="mt-6 w-full px-1 xl:px-2">
          <span className="mb-2 hidden text-[13px] text-x-secondary xl:block">
            読みやすさ
          </span>
          <LevelBar />
        </div>

        {/* Theme toggle */}
        <button
          onClick={toggleTheme}
          className="mt-4 flex h-[50px] w-full cursor-pointer items-center justify-center rounded-full border-none bg-transparent transition-colors hover:bg-x-hover xl:justify-start xl:gap-4 xl:px-4"
        >
          <Icon name={theme === "dark" ? "light_mode" : "dark_mode"} size={24} className="text-x-text" />
          <span className="hidden text-lg text-x-text xl:inline">
            {theme === "dark" ? "ライトモード" : "ダークモード"}
          </span>
        </button>
      </div>

      {/* Bottom branding */}
      <div className="w-full px-1 xl:px-2">
        <div className="flex h-[60px] w-full items-center justify-center gap-3 rounded-full transition-colors hover:bg-x-hover xl:justify-start xl:px-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-emerald-400/20 text-[18px] font-bold text-emerald-400">
            議
          </div>
          <div className="hidden min-w-0 xl:block">
            <div className="truncate text-[14px] text-x-text">
              <span className="font-light">Open</span><span className="font-bold">GIK</span><span className="font-bold text-emerald-400">AI</span>
            </div>
            <div className="truncate text-[12px] text-x-secondary">議会をひらく</div>
          </div>
        </div>
      </div>
    </header>
  );
}
