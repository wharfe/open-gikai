import Link from "next/link";
import type { Thread } from "@/types";
import { LIFE_THEMES, getLifeTheme } from "@/lib/config";

type ThemePanelProps = {
  threads: Thread[];
};

export function ThemePanel({ threads }: ThemePanelProps) {
  // Count non-procedural threads per theme
  const counts: Record<string, number> = {};
  for (const t of threads) {
    if (t.procedural) continue;
    const theme = getLifeTheme(t.topicTag);
    if (theme) counts[theme] = (counts[theme] ?? 0) + 1;
  }

  const activeThemes = LIFE_THEMES.filter((t) => (counts[t.id] ?? 0) > 0);
  if (activeThemes.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-2xl bg-x-surface">
      <div className="px-4 py-3">
        <h2 className="text-[20px] font-extrabold text-x-text">
          <span className="material-symbols-rounded align-middle text-emerald-400" style={{ fontSize: 22 }}>category</span> テーマ
        </h2>
      </div>

      {activeThemes.map((theme) => (
        <Link
          key={theme.id}
          href={`/?theme=${theme.id}`}
          className="flex cursor-pointer items-center gap-3 px-4 py-3 transition-colors hover:bg-x-hover"
        >
          <span
            className="flex h-8 w-8 items-center justify-center rounded-full"
            style={{ background: `${theme.color}20` }}
          >
            <span className="material-symbols-rounded" style={{ fontSize: 18, color: theme.color }}>
              {theme.icon}
            </span>
          </span>
          <div className="min-w-0 flex-1">
            <div className="text-[15px] font-bold text-x-text">{theme.label}</div>
            <div className="text-[13px] text-x-secondary">{theme.description}</div>
          </div>
          <span className="shrink-0 text-[13px] text-x-secondary">
            {counts[theme.id]}件
          </span>
        </Link>
      ))}
    </div>
  );
}
