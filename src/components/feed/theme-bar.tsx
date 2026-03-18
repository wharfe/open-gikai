"use client";

import { LIFE_THEMES, type LifeThemeId } from "@/lib/config";

type ThemeBarProps = {
  selected: LifeThemeId | null;
  onSelect: (theme: LifeThemeId | null) => void;
  themeCounts: Record<string, number>;
};

export function ThemeBar({ selected, onSelect, themeCounts }: ThemeBarProps) {
  return (
    <div className="flex gap-2 overflow-x-auto border-b border-x-border px-4 py-3">
      {LIFE_THEMES.filter((t) => (themeCounts[t.id] ?? 0) > 0).map((theme) => {
        const isActive = selected === theme.id;
        const count = themeCounts[theme.id] ?? 0;
        return (
          <button
            key={theme.id}
            onClick={() => onSelect(isActive ? null : theme.id)}
            className="flex shrink-0 cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] font-bold transition-colors"
            style={{
              background: isActive ? `${theme.color}20` : "transparent",
              borderColor: isActive ? `${theme.color}60` : "var(--x-border)",
              color: isActive ? theme.color : "var(--x-secondary)",
            }}
          >
            <span className="material-symbols-rounded" style={{ fontSize: 16, color: theme.color }}>
              {theme.icon}
            </span>
            {theme.label}
            <span className="text-[11px] font-normal opacity-70">{count}</span>
          </button>
        );
      })}
    </div>
  );
}
