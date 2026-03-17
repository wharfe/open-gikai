"use client";

import { useState } from "react";
import Link from "next/link";
import type { SearchEntry } from "@/lib/data";

type SearchViewProps = {
  entries: SearchEntry[];
};

function searchEntries(entries: SearchEntry[], query: string): SearchEntry[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return [];

  return entries
    .map((entry) => {
      const searchable = [
        entry.topic,
        entry.summary,
        entry.committee,
        entry.topicTag,
        ...entry.keywords,
        ...entry.speakers,
      ]
        .join(" ")
        .toLowerCase();

      const score = terms.filter((t) => searchable.includes(t)).length;
      return { entry, score };
    })
    .filter(({ score }) => score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 20)
    .map(({ entry }) => entry);
}

export function SearchView({ entries }: SearchViewProps) {
  const [query, setQuery] = useState("");

  const results = searchEntries(entries, query);

  return (
    <div>
      {/* Search input */}
      <div className="border-b border-x-border px-4 py-3">
        <div className="flex items-center gap-3 rounded-full bg-x-search px-4">
          <span className="text-x-secondary">🔍</span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="スレッド・議員・キーワードを検索"
            className="h-[42px] w-full border-none bg-transparent text-[15px] text-x-text outline-none placeholder:text-x-secondary"
            autoFocus
          />
          {query && (
            <button
              onClick={() => setQuery("")}
              className="cursor-pointer border-none bg-transparent text-x-secondary hover:text-x-text"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Results */}
      {query.trim() === "" ? (
        <div className="px-8 py-16 text-center">
          <div className="mb-3 text-3xl">🔍</div>
          <p className="text-[15px] text-x-secondary">
            キーワードを入力してスレッドを検索
          </p>
          <p className="mt-2 text-[13px] text-x-secondary">
            テーマ名、議員名、委員会名、キーワードで検索できます
          </p>
        </div>
      ) : results.length === 0 ? (
        <div className="px-8 py-16 text-center">
          <p className="text-[15px] text-x-secondary">
            「{query}」に一致するスレッドが見つかりませんでした
          </p>
        </div>
      ) : (
        <div>
          <div className="px-4 py-2 text-[13px] text-x-secondary">
            {results.length}件のスレッドが見つかりました
          </div>
          {results.map((entry) => (
            <Link
              key={entry.threadId}
              href={`/t/${entry.threadId}`}
              className="block border-b border-x-border px-4 py-3 transition-colors hover:bg-x-hover"
            >
              <div className="flex items-center gap-1.5 text-[13px] text-x-secondary">
                <span>{entry.house}</span>
                <span>·</span>
                <span>{entry.committee}</span>
                <span>·</span>
                <span>{entry.date}</span>
              </div>
              <div className="mt-1 text-[15px] font-bold text-x-text">
                {entry.topic}
              </div>
              <div className="mt-1 text-[14px] leading-[22px] text-x-secondary">
                {entry.summary}
              </div>
              {entry.keywords.length > 0 && (
                <div className="mt-1.5">
                  {entry.keywords.slice(0, 3).map((k) => (
                    <span key={k} className="mr-2 text-[13px] text-x-accent">
                      #{k}
                    </span>
                  ))}
                </div>
              )}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
