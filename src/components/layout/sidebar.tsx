"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LevelBar } from "@/components/ui/level-bar";

const NAV_ITEMS = [
  { href: "/", label: "ホーム", icon: "🏠" },
  { href: "/search", label: "検索", icon: "🔍" },
  { href: "/members", label: "議員", icon: "👤" },
  { href: "/about", label: "About", icon: "ℹ️" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 hidden h-screen w-[68px] shrink-0 flex-col justify-between border-r border-x-border px-1 py-3 md:flex xl:w-[260px] xl:px-2">
      <div className="flex flex-col items-center xl:items-start">
        {/* Logo */}
        <Link
          href="/"
          className="mb-4 flex h-[52px] items-center justify-center rounded-full px-3 transition-colors hover:bg-x-hover"
        >
          <div className="flex items-center gap-2">
            <div className="h-2.5 w-2.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.5)]" />
            <span className="hidden text-xl font-extrabold tracking-wide xl:inline">
              OpenGIK<span className="text-emerald-400">AI</span>
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
                <span className="text-[24px]">{item.icon}</span>
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
      </div>

      {/* Bottom branding */}
      <div className="w-full px-1 xl:px-2">
        <div className="flex h-[60px] w-full items-center justify-center gap-3 rounded-full transition-colors hover:bg-x-hover xl:justify-start xl:px-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-emerald-400/20 text-sm font-bold text-emerald-400">
            G
          </div>
          <div className="hidden min-w-0 xl:block">
            <div className="truncate text-[14px] font-bold text-x-text">
              OpenGIK<span className="text-emerald-400">AI</span>
            </div>
            <div className="truncate text-[12px] text-x-secondary">国会をひらく</div>
          </div>
        </div>
      </div>
    </header>
  );
}
