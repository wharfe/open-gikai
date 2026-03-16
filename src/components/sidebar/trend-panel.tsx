"use client";

import { useState } from "react";
import type { Thread } from "@/types";
import { TREND_PERIODS } from "@/lib/config";
import { extractTrends } from "@/lib/utils";

type TrendPanelProps = {
  threads: Thread[];
};

export function TrendPanel({ threads }: TrendPanelProps) {
  const [period, setPeriod] = useState<(typeof TREND_PERIODS)[number]>(TREND_PERIODS[0]);
  const trends = extractTrends(threads);

  return (
    <div className="overflow-hidden rounded-2xl bg-x-surface">
      {/* Header — X "What's happening" style */}
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-[20px] font-extrabold text-x-text">
          🔥 トレンド
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

      {/* Trend items — X "Trending" card style */}
      {trends.map(([keyword, count], i) => (
        <div
          key={keyword}
          className="cursor-pointer px-4 py-3 transition-colors hover:bg-x-hover"
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
        </div>
      ))}

      <div className="cursor-pointer px-4 py-4 text-[15px] text-x-accent hover:bg-x-hover">
        さらに表示
      </div>
    </div>
  );
}
