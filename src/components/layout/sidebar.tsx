"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LevelBar } from "@/components/ui/level-bar";

const NAV_ITEMS = [
  { href: "/", label: "ホーム", icon: "🏠" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 hidden h-screen w-[68px] flex-col items-center justify-between border-r border-x-border py-3 md:flex xl:w-[275px] xl:items-start xl:px-3">
      {/* Logo */}
      <div className="flex flex-col items-center gap-1 xl:items-start">
        <Link
          href="/"
          className="mb-4 flex h-[50px] w-[50px] items-center justify-center rounded-full transition-colors hover:bg-x-hover xl:px-3"
        >
          <div className="flex items-center gap-3">
            <div className="h-2.5 w-2.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.5)]" />
            <span className="hidden text-xl font-extrabold tracking-wide xl:inline">
              GIKAI
            </span>
          </div>
        </Link>

        {/* Navigation */}
        <nav className="flex flex-col gap-1">
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
        <div className="mt-4">
          <LevelBar />
        </div>
      </div>

      {/* Bottom branding */}
      <div className="hidden text-xs text-x-secondary xl:block">
        国会をひらく
      </div>
    </header>
  );
}
