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
  const max = trends[0] ? trends[0][1] : 1;

  return (
    <div className="rounded-[14px] border border-slate-800 bg-gikai-card p-4">
      <div className="mb-3.5 flex items-center justify-between">
        <span className="text-[13px] font-bold tracking-wider text-slate-400">
          🔥 トレンド
        </span>
        <div className="flex gap-0.5">
          {TREND_PERIODS.map((p) => (
            <button
              key={p}
              onClick={() => setPeriod(p)}
              className="cursor-pointer rounded-md px-2 py-0.5 text-[10px]"
              style={{
                background: period === p ? "#1e293b" : "none",
                border: `1px solid ${period === p ? "#334155" : "transparent"}`,
                color: period === p ? "#f8fafc" : "#475569",
              }}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
      {trends.map(([keyword, count], i) => (
        <div key={keyword} className="mb-2.5 flex items-center gap-2.5">
          <span className="w-3.5 text-right font-mono text-[10px] text-slate-700">
            {i + 1}
          </span>
          <div className="flex-1">
            <div className="mb-0.5 flex justify-between">
              <span
                className="text-xs"
                style={{
                  color: i < 3 ? "#f8fafc" : "#94a3b8",
                  fontWeight: i < 3 ? 600 : 400,
                }}
              >
                #{keyword}
              </span>
              <span className="text-[10px] text-slate-700">{count}</span>
            </div>
            <div className="h-0.5 overflow-hidden rounded-sm bg-slate-800">
              <div
                className="h-full rounded-sm"
                style={{
                  width: `${(count / max) * 100}%`,
                  background:
                    i < 3
                      ? "linear-gradient(90deg,#f97316,#ef4444)"
                      : i < 6
                        ? "linear-gradient(90deg,#3b82f6,#6366f1)"
                        : "#334155",
                }}
              />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
