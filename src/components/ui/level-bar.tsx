"use client";

import { useAppContext } from "@/components/providers/app-provider";
import { LEVELS } from "@/lib/config";

export function LevelBar() {
  const { level, setLevel } = useAppContext();

  return (
    <div className="flex items-center gap-0.5 rounded-full border border-x-border bg-x-bg p-0.5 xl:gap-1 xl:p-1">
      {LEVELS.map((l) => {
        const isActive = level === l.id;
        return (
          <button
            key={l.id}
            onClick={() => setLevel(l.id)}
            className="cursor-pointer whitespace-nowrap rounded-full px-2.5 py-1.5 text-[13px] transition-colors xl:px-3"
            style={{
              background: isActive ? l.bg : "transparent",
              border: `1px solid ${isActive ? l.border : "transparent"}`,
              color: isActive ? l.color : "#71767b",
              fontWeight: isActive ? 700 : 400,
            }}
          >
            <span className="xl:hidden">{l.icon}</span>
            <span className="hidden xl:inline">{l.icon} {l.label}</span>
          </button>
        );
      })}
    </div>
  );
}
