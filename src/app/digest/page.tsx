import type { Metadata } from "next";
import Link from "next/link";
import { getWeeklyDigests } from "@/lib/data";
import { MobileHeader } from "@/components/layout/header";

export const metadata: Metadata = {
  title: "週次ダイジェスト",
  description:
    "国会・審議会の週次まとめ。今週の主要トピック、注目発言、委員会活動を一覧で確認できます。",
  alternates: { canonical: "/digest" },
};

export default function DigestListPage() {
  const digests = getWeeklyDigests();

  return (
    <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
      <MobileHeader />

      <div className="sticky top-0 z-40 flex h-[53px] items-center gap-3 bg-x-bg/65 px-4 backdrop-blur-xl">
        <Link
          href="/"
          className="flex h-9 w-9 items-center justify-center rounded-full text-x-text transition-colors hover:bg-x-hover"
        >
          <span
            className="material-symbols-rounded"
            style={{ fontSize: 20 }}
          >
            arrow_back
          </span>
        </Link>
        <h1 className="text-[17px] font-bold">週次ダイジェスト</h1>
      </div>

      <div className="px-4 py-4">
        <p className="text-[14px] leading-[22px] text-x-secondary">
          毎週の国会・審議会の動きをまとめています。主要トピック、注目の議論、委員会別の活動状況を確認できます。
        </p>

        <div className="mt-6 space-y-3">
          {digests.map((d) => (
            <Link
              key={d.weekId}
              href={`/digest/weekly/${d.weekId}`}
              className="block rounded-2xl border border-x-border px-4 py-4 transition-colors hover:bg-x-hover"
            >
              <div className="flex items-center gap-2">
                <span
                  className="material-symbols-rounded text-x-brand"
                  style={{ fontSize: 20 }}
                >
                  summarize
                </span>
                <span className="text-[16px] font-bold text-x-text">
                  {d.weekId}
                </span>
                <span className="text-[13px] text-x-secondary">
                  {d.startDate} — {d.endDate}
                </span>
              </div>

              <div className="mt-2 flex flex-wrap gap-3 text-[13px] text-x-secondary">
                <span>{d.threadCount}スレッド</span>
                <span>{d.speechCount}件の発言</span>
                <span>{d.committees.length}委員会</span>
              </div>

              {d.topKeywords.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {d.topKeywords.slice(0, 5).map(([kw]) => (
                    <span
                      key={kw}
                      className="rounded-full bg-x-hover px-2 py-0.5 text-[12px] text-x-secondary"
                    >
                      {kw}
                    </span>
                  ))}
                </div>
              )}
            </Link>
          ))}

          {digests.length === 0 && (
            <p className="py-8 text-center text-[14px] text-x-secondary">
              ダイジェストはまだありません
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
