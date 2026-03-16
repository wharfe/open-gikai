"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LevelBar } from "@/components/ui/level-bar";

const NAV_ITEMS = [
  { href: "/", label: "ホーム", icon: "🏠" },
  { href: "/#trends", label: "トレンド", icon: "🔍" },
  { href: "/about", label: "このサイトについて", icon: "ℹ️" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 hidden h-screen w-[68px] flex-col justify-between border-r border-x-border py-3 md:flex xl:w-[275px] xl:px-3">
      <div className="flex flex-col items-center xl:items-start">
        {/* Logo */}
        <Link
          href="/"
          className="mb-2 flex h-[52px] w-[52px] items-center justify-center rounded-full transition-colors hover:bg-x-hover xl:px-3"
        >
          <div className="flex items-center gap-3">
            <div className="h-2.5 w-2.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.5)]" />
            <span className="hidden text-xl font-extrabold tracking-wide xl:inline">
              GIKAI
            </span>
          </div>
        </Link>

        {/* Navigation */}
        <nav className="flex w-full flex-col gap-0.5">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className="flex h-[50px] items-center justify-center rounded-full px-3 text-xl transition-colors hover:bg-x-hover xl:justify-start xl:gap-5 xl:px-5"
              >
                <span className="text-[26px]">{item.icon}</span>
                <span
                  className="hidden text-xl xl:inline"
                  style={{ fontWeight: isActive ? 700 : 400 }}
                >
                  {item.label}
                </span>
              </Link>
            );
          })}
        </nav>

        {/* Level selector */}
        <div className="mt-6 flex flex-col items-center gap-2 xl:items-start xl:px-2">
          <span className="hidden text-[13px] text-x-secondary xl:inline">
            読みやすさ
          </span>
          <LevelBar />
        </div>
      </div>

      {/* Bottom area — branding like X account widget */}
      <div className="flex flex-col items-center gap-3 xl:items-start">
        <div className="flex h-[52px] w-[52px] items-center justify-center rounded-full bg-x-surface text-lg font-bold text-emerald-400 xl:w-full xl:justify-start xl:gap-3 xl:px-4">
          <span>G</span>
          <div className="hidden xl:block">
            <div className="text-[15px] font-bold text-x-text">OpenGIKAI</div>
            <div className="text-[13px] text-x-secondary">国会をひらく</div>
          </div>
        </div>
      </div>
    </header>
  );
}
