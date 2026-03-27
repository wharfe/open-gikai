import Link from "next/link";
import type { ThreadSummary } from "@/lib/data";
import type { SessionInfo } from "@/lib/data";

type SessionCardProps = {
  threads: ThreadSummary[];
  session: SessionInfo;
};

export function SessionCard({ threads, session }: SessionCardProps) {
  // Filter to only Diet threads within the session period
  const sessionStart = session.startDate.replace(/-/g, ".");
  const dietThreads = threads.filter(
    (t) => t.source !== "council" && t.source !== "kantei" && t.date >= sessionStart,
  );
  const dietSpeeches = dietThreads.reduce((s, t) => s + t.speechCount, 0);
  const dietMembers = new Set(dietThreads.flatMap((t) => t.memberIds)).size;

  // Other sources (site-wide, not session-specific)
  const kanteiCount = threads.filter((t) => t.source === "kantei").length;
  const councilCount = threads.filter((t) => t.source === "council").length;

  // Latest data date
  const dates = threads.map((t) => t.date).sort();
  const latestDate = dates.length > 0 ? dates[dates.length - 1] : null;

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

      {/* Diet stats */}
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 px-4 text-[13px]">
        <span className="text-x-secondary">
          <span className="font-bold text-x-text">{dietThreads.length}</span> スレッド
        </span>
        <span className="text-x-secondary">
          <span className="font-bold text-x-text">{dietSpeeches}</span> 発言
        </span>
        <span className="text-x-secondary">
          <span className="font-bold text-x-text">{dietMembers}</span> 名
        </span>
      </div>

      {/* Other sources — linked, visually separated */}
      {(kanteiCount > 0 || councilCount > 0) && (
        <div className="mt-3 border-t border-x-border px-4 pt-3">
          <div className="text-[12px] text-x-secondary mb-1.5">その他のソース</div>
          <div className="flex flex-wrap gap-2">
            {kanteiCount > 0 && (
              <Link
                href="/search?q=首相記者会見"
                className="rounded-full bg-x-hover px-2.5 py-0.5 text-[12px] text-x-secondary transition-colors hover:text-x-text"
              >
                首相記者会見 {kanteiCount}
              </Link>
            )}
            {councilCount > 0 && (
              <Link
                href="/council"
                className="rounded-full bg-x-hover px-2.5 py-0.5 text-[12px] text-x-secondary transition-colors hover:text-x-text"
              >
                審議会 {councilCount}
              </Link>
            )}
          </div>
        </div>
      )}

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
