"use client";

import { useAppContext } from "@/components/providers/app-provider";
import { LEVELS } from "@/lib/config";

export function LevelBar() {
  const { level, setLevel } = useAppContext();

  return (
    <div className="inline-flex items-center gap-0.5 rounded-full border border-x-border bg-x-bg p-0.5">
      {LEVELS.map((l) => {
        const isActive = level === l.id;
        return (
          <button
            key={l.id}
            onClick={() => setLevel(l.id)}
            className="cursor-pointer whitespace-nowrap rounded-full px-2 py-1 text-[13px] transition-colors"
            style={{
              background: isActive ? l.bg : "transparent",
              border: `1px solid ${isActive ? l.border : "transparent"}`,
              color: isActive ? l.color : "#71767b",
              fontWeight: isActive ? 700 : 400,
            }}
          >
            <span className="material-symbols-rounded" style={{ fontSize: 16 }}>{l.icon}</span>
          </button>
        );
      })}
    </div>
  );
}
