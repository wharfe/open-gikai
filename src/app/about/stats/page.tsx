import type { Metadata } from "next";
import Link from "next/link";
import { MobileHeader } from "@/components/layout/header";
import { getProcessingStatus } from "@/lib/data";

export const metadata: Metadata = {
  title: "処理ステータス",
  description: "国会議事録の日別処理状況。各委員会の議事録取得・AI要約の進捗を公開しています。",
};

type CommitteeStatus = {
  name: string;
  house: string;
  status: string;
  threads: number;
  error?: string;
};

type DateStats = {
  threads: number;
  speeches: number;
  committees: number;
};

type DateStatus = {
  updatedAt: string;
  phase: string;
  committees: CommitteeStatus[];
  stats?: DateStats;
};

type Summary = {
  totalDates: number;
  totalThreads: number;
  totalSpeeches: number;
  totalCommittees: number;
  totalMembers: number;
  generatedAt: string;
};

export default function StatsPage() {
  const raw = getProcessingStatus() as Record<string, unknown> | null;
  const summary = raw?._summary as Summary | undefined;

  const dateEntries: [string, DateStatus][] = raw
    ? Object.entries(raw)
        .filter(([key]) => key !== "_summary")
        .map(([key, val]) => [key, val as DateStatus])
    : [];

  return (
    <>
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />

        {/* Sticky header */}
        <div className="sticky top-0 z-40 flex h-[53px] items-center gap-3 bg-x-bg/65 px-4 backdrop-blur-xl">
          <Link
            href="/about"
            className="flex h-9 w-9 items-center justify-center rounded-full text-x-text transition-colors hover:bg-x-hover"
          >
            <span className="material-symbols-rounded" style={{ fontSize: 20 }}>arrow_back</span>
          </Link>
          <div className="text-[17px] font-bold">処理ステータス</div>
        </div>

        <div className="px-4 py-6">
          {/* Summary stats */}
          {summary && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                { label: "処理日数", value: summary.totalDates },
                { label: "スレッド", value: summary.totalThreads },
                { label: "発言数", value: summary.totalSpeeches.toLocaleString() },
                { label: "議員数", value: summary.totalMembers },
              ].map(({ label, value }) => (
                <div
                  key={label}
                  className="rounded-xl border border-x-border px-3 py-3 text-center"
                >
                  <div className="text-[20px] font-bold text-x-brand">
                    {value}
                  </div>
                  <div className="text-[12px] text-x-secondary">
                    {label}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Date entries */}
          <div className="mt-6 space-y-4">
            {dateEntries
              .sort(([a], [b]) => b.localeCompare(a))
              .map(([dateKey, dayStatus]) => (
                <div key={dateKey}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[15px] font-bold text-x-text">
                      {dateKey}
                    </span>
                    {dayStatus.stats && (
                      <span className="text-[12px] text-x-secondary">
                        {dayStatus.stats.threads}スレッド · {dayStatus.stats.speeches}発言 · {dayStatus.stats.committees}委員会
                      </span>
                    )}
                    <span
                      className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${
                        dayStatus.phase === "completed"
                          ? "bg-green-500/10 text-green-500"
                          : dayStatus.phase === "failed"
                            ? "bg-red-500/10 text-red-500"
                            : "bg-yellow-500/10 text-yellow-500"
                      }`}
                    >
                      {dayStatus.phase}
                    </span>
                  </div>
                  <div className="mt-2 space-y-1">
                    {dayStatus.committees.map((c) => (
                      <div
                        key={`${c.house}${c.name}`}
                        className="flex items-center gap-2 text-[13px]"
                      >
                        <span>
                          {c.status === "completed"
                            ? "✅"
                            : c.status === "pending"
                              ? "⏳"
                              : "❌"}
                        </span>
                        <span className="text-x-secondary">
                          {c.house}{c.name}
                        </span>
                        {c.threads > 0 && (
                          <span className="text-x-secondary">
                            — {c.threads}スレッド
                          </span>
                        )}
                        {c.error && (
                          <span className="text-red-400">({c.error})</span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
          </div>

          {dateEntries.length === 0 && (
            <div className="py-12 text-center text-[14px] text-x-secondary">
              処理ステータスデータがありません
            </div>
          )}
        </div>
      </main>
    </>
  );
}
