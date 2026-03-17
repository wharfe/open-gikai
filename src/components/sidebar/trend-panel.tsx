"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import type { Thread } from "@/types";
import { TREND_PERIODS } from "@/lib/config";
import { extractTrends } from "@/lib/utils";

type TrendPanelProps = {
  threads: Thread[];
};

function findDefaultPeriod(threads: Thread[]): (typeof TREND_PERIODS)[number] {
  for (const p of TREND_PERIODS) {
    if (extractTrends(threads, p).length > 0) return p;
  }
  return TREND_PERIODS[0];
}

export function TrendPanel({ threads }: TrendPanelProps) {
  const defaultPeriod = useMemo(() => findDefaultPeriod(threads), [threads]);
  const [period, setPeriod] = useState<(typeof TREND_PERIODS)[number]>(defaultPeriod);
  const trends = extractTrends(threads, period);

  return (
    <div className="overflow-hidden rounded-2xl bg-x-surface">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-[20px] font-extrabold text-x-text">
          <span className="material-symbols-rounded align-middle text-orange-400" style={{ fontSize: 22 }}>trending_up</span> トレンド
        </h2>
        <div className="flex gap-0.5">
          {TREND_PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className="cursor-pointer rounded-full px-2.5 py-1 text-[13px] transition-colors"
              style={{
                background: period === p ? "rgba(29,155,240,0.1)" : "transparent",
                color: period === p ? "#1d9bf0" : "#71767b",
                border: "none",
              }}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Trend items — click to search */}
      {trends.length === 0 ? (
        <div className="px-4 py-6 text-center text-[13px] text-x-secondary">
          この期間のトレンドはありません
        </div>
      ) : (
        trends.map(([keyword, count], i) => (
          <Link
            key={keyword}
            href={`/search?q=${encodeURIComponent(keyword)}`}
            className="block cursor-pointer px-4 py-3 transition-colors hover:bg-x-hover"
          >
            <div className="text-[13px] text-x-secondary">
              {i + 1} · トレンド
            </div>
            <div className="text-[15px] font-bold text-x-text">
              #{keyword}
            </div>
            <div className="text-[13px] text-x-secondary">
              {count}件の発言
            </div>
          </Link>
        ))
      )}

      <Link
        href="/search"
        className="block cursor-pointer px-4 py-4 text-[15px] text-x-accent hover:bg-x-hover"
      >
        さらに検索
      </Link>
    </div>
  );
}
