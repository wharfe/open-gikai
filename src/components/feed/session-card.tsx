import type { ThreadSummary } from "@/lib/data";
import type { SessionInfo } from "@/lib/data";

type SessionCardProps = {
  threads: ThreadSummary[];
  session: SessionInfo;
};

export function SessionCard({ threads, session }: SessionCardProps) {
  const totalSpeeches = threads.reduce((s, t) => s + t.speechCount, 0);
  const uniqueMembers = new Set(threads.flatMap((t) => t.memberIds)).size;

  // Latest data date
  const dates = threads.map((t) => t.date).sort();
  const latestDate = dates.length > 0 ? dates[dates.length - 1] : null;

  // Source breakdown (use human-readable labels)
  const SOURCE_LABELS: Record<string, string> = {
    ndl: "国会会議録",
    kantei: "首相記者会見",
    council: "審議会",
  };
  const sources = threads.reduce<Record<string, number>>((acc, t) => {
    const label = t.sourceLabel || SOURCE_LABELS[t.source || "ndl"] || t.source || "国会会議録";
    acc[label] = (acc[label] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="overflow-hidden rounded-2xl bg-x-surface">
      {/* Session header */}
      <div className="flex items-start justify-between px-4 pt-4">
        <div>
          <div className="text-[15px] font-bold text-x-text">
            {session.name}
          </div>
          <div className="mt-0.5 text-[13px] text-x-secondary">
            {session.period}
          </div>
        </div>
        <span
          className="material-symbols-rounded text-x-brand"
          style={{ fontSize: 28 }}
        >
          account_balance
        </span>
      </div>

      {/* Stats */}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 px-4 text-[13px]">
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
      <div className="mt-2 flex flex-wrap gap-2 px-4">
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
        <div className="mt-3 flex items-center gap-1.5 px-4 pb-4 text-[12px] text-x-secondary">
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
  );
}
