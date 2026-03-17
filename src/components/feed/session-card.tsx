import type { Thread } from "@/types";
import type { SessionInfo } from "@/lib/data";

type SessionCardProps = {
  threads: Thread[];
  session: SessionInfo;
};

export function SessionCard({ threads, session }: SessionCardProps) {
  const totalSpeeches = threads.reduce((s, t) => s + t.speeches.length, 0);
  const uniqueMembers = new Set(
    threads.flatMap((t) => t.speeches.map((s) => s.memberId))
  ).size;

  // Latest data date
  const dates = threads.map((t) => t.date).sort();
  const latestDate = dates.length > 0 ? dates[dates.length - 1] : null;

  // Source breakdown
  const sources = threads.reduce<Record<string, number>>((acc, t) => {
    const label = t.sourceLabel || "国会会議録";
    acc[label] = (acc[label] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="border-b border-x-border px-4 py-4">
      <div className="rounded-2xl border border-x-border bg-x-surface p-4">
        {/* Session header */}
        <div className="flex items-start justify-between">
          <div>
            <div className="text-[15px] font-bold text-x-text">
              {session.name}
            </div>
            <div className="mt-0.5 text-[13px] text-x-secondary">
              {session.period}
            </div>
          </div>
          <span
            className="material-symbols-rounded text-emerald-400"
            style={{ fontSize: 28 }}
          >
            account_balance
          </span>
        </div>

        {/* Stats */}
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
          <span className="text-x-secondary">
            <span className="font-bold text-x-text">{threads.length}</span> スレッド
          </span>
          <span className="text-x-secondary">
            <span className="font-bold text-x-text">{totalSpeeches}</span> 発言
          </span>
          <span className="text-x-secondary">
            <span className="font-bold text-x-text">{uniqueMembers}</span> 名
          </span>
        </div>

        {/* Source badges */}
        <div className="mt-2 flex flex-wrap gap-2">
          {Object.entries(sources).map(([label, count]) => (
            <span
              key={label}
              className="rounded-full bg-x-accent/10 px-2.5 py-0.5 text-[12px] text-x-accent"
            >
              {label} {count}
            </span>
          ))}
        </div>

        {/* Last updated */}
        {latestDate && (
          <div className="mt-3 flex items-center gap-1.5 text-[12px] text-x-secondary">
            <span
              className="material-symbols-rounded"
              style={{ fontSize: 14 }}
            >
              update
            </span>
            最終データ：{latestDate}
          </div>
        )}
      </div>
    </div>
  );
}
