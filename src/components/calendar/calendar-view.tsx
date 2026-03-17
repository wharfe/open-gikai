"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import type { CalendarDay } from "@/lib/data";

type CalendarViewProps = {
  days: CalendarDay[];
};

function parseDateStr(d: string): Date {
  const [y, m, day] = d.split(".").map(Number);
  return new Date(y, m - 1, day);
}

function formatMonth(year: number, month: number): string {
  return `${year}年${month + 1}月`;
}

export function CalendarView({ days }: CalendarViewProps) {
  // Build lookup: "YYYY.MM.DD" -> CalendarDay
  const dayMap = useMemo(() => {
    const m = new Map<string, CalendarDay>();
    days.forEach((d) => m.set(d.date, d));
    return m;
  }, [days]);

  // Determine initial month from most recent data
  const latestDate = days.length > 0 ? parseDateStr(days[0].date) : new Date();
  const [viewYear, setViewYear] = useState(latestDate.getFullYear());
  const [viewMonth, setViewMonth] = useState(latestDate.getMonth());

  // Selected day
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const selectedDay = selectedDate ? dayMap.get(selectedDate) : null;

  // Calendar grid
  const firstDay = new Date(viewYear, viewMonth, 1).getDay(); // 0=Sun
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  const weeks: (number | null)[][] = [];
  let week: (number | null)[] = Array(firstDay).fill(null);

  for (let d = 1; d <= daysInMonth; d++) {
    week.push(d);
    if (week.length === 7) {
      weeks.push(week);
      week = [];
    }
  }
  if (week.length > 0) {
    while (week.length < 7) week.push(null);
    weeks.push(week);
  }

  const prevMonth = () => {
    if (viewMonth === 0) {
      setViewYear(viewYear - 1);
      setViewMonth(11);
    } else {
      setViewMonth(viewMonth - 1);
    }
    setSelectedDate(null);
  };

  const nextMonth = () => {
    if (viewMonth === 11) {
      setViewYear(viewYear + 1);
      setViewMonth(0);
    } else {
      setViewMonth(viewMonth + 1);
    }
    setSelectedDate(null);
  };

  const dayNames = ["日", "月", "火", "水", "木", "金", "土"];

  return (
    <div>
      {/* Month navigation */}
      <div className="flex items-center justify-between border-b border-x-border px-4 py-3">
        <button
          onClick={prevMonth}
          className="flex h-9 w-9 cursor-pointer items-center justify-center rounded-full border-none bg-transparent text-x-text transition-colors hover:bg-x-hover"
        >
          ←
        </button>
        <span className="text-[17px] font-bold">
          {formatMonth(viewYear, viewMonth)}
        </span>
        <button
          onClick={nextMonth}
          className="flex h-9 w-9 cursor-pointer items-center justify-center rounded-full border-none bg-transparent text-x-text transition-colors hover:bg-x-hover"
        >
          →
        </button>
      </div>

      {/* Day names */}
      <div className="grid grid-cols-7 border-b border-x-border">
        {dayNames.map((name) => (
          <div
            key={name}
            className="py-2 text-center text-[13px] text-x-secondary"
          >
            {name}
          </div>
        ))}
      </div>

      {/* Calendar grid */}
      <div className="border-b border-x-border">
        {weeks.map((week, wi) => (
          <div key={wi} className="grid grid-cols-7">
            {week.map((day, di) => {
              if (day === null) {
                return <div key={di} className="h-16" />;
              }

              const dateStr = `${viewYear}.${String(viewMonth + 1).padStart(2, "0")}.${String(day).padStart(2, "0")}`;
              const data = dayMap.get(dateStr);
              const isSelected = selectedDate === dateStr;
              const isToday =
                new Date().getFullYear() === viewYear &&
                new Date().getMonth() === viewMonth &&
                new Date().getDate() === day;

              return (
                <button
                  key={di}
                  onClick={() => data && setSelectedDate(isSelected ? null : dateStr)}
                  className={`flex h-16 cursor-pointer flex-col items-center justify-start border-none pt-1.5 transition-colors ${
                    isSelected
                      ? "bg-x-accent/10"
                      : data
                        ? "bg-transparent hover:bg-x-hover"
                        : "bg-transparent opacity-40"
                  }`}
                  disabled={!data}
                  style={{ cursor: data ? "pointer" : "default" }}
                >
                  <span
                    className={`flex h-7 w-7 items-center justify-center rounded-full text-[14px] ${
                      isToday
                        ? "bg-x-accent font-bold text-white"
                        : isSelected
                          ? "font-bold text-x-accent"
                          : "text-x-text"
                    }`}
                  >
                    {day}
                  </span>
                  {data && (
                    <span className="mt-0.5 text-[10px] text-emerald-400">
                      {data.totalThreads}件
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      {/* Selected day details */}
      {selectedDay && (
        <div className="px-4 py-4">
          <div className="text-[15px] font-bold text-x-text">
            {selectedDay.date} の委員会
          </div>
          <div className="mt-3 space-y-2">
            {selectedDay.committees.map((c) => (
              <Link
                key={`${c.house}${c.name}`}
                href={`/?date=${selectedDay.date}&committee=${encodeURIComponent(c.name)}`}
                className="flex items-center justify-between rounded-xl border border-x-border px-4 py-3 transition-colors hover:bg-x-hover"
              >
                <div>
                  <div className="text-[15px] font-bold text-x-text">
                    {c.name}
                  </div>
                  <div className="text-[13px] text-x-secondary">
                    {c.house}
                  </div>
                </div>
                <span className="text-[13px] text-x-secondary">
                  {c.threads}スレッド →
                </span>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* No data selected */}
      {!selectedDay && (
        <div className="px-8 py-12 text-center text-[14px] text-x-secondary">
          日付をタップすると、その日の委員会一覧を表示します
        </div>
      )}
    </div>
  );
}
