"use client";

import { useAppContext } from "@/components/providers/app-provider";
import { LEVELS } from "@/lib/config";

export function LevelBar() {
  const { level, setLevel } = useAppContext();

  return (
    <div className="flex items-center gap-1 rounded-full border border-slate-800 bg-gikai-card px-1.5 py-0.5">
      <span className="whitespace-nowrap pl-1.5 text-[10px] text-slate-700">
        読み方
      </span>
      {LEVELS.map((l) => {
        const isActive = level === l.id;
        return (
          <button
            key={l.id}
            onClick={() => setLevel(l.id)}
            className="whitespace-nowrap rounded-full px-2.5 py-1 text-xs transition-all"
            style={{
              background: isActive ? l.bg : "none",
              border: `1px solid ${isActive ? l.border : "transparent"}`,
              color: isActive ? l.color : "#475569",
              fontWeight: isActive ? 700 : 400,
            }}
          >
            {l.icon} {l.label}
          </button>
        );
      })}
    </div>
  );
}
