import type { Metadata } from "next";
import Link from "next/link";
import { getCouncils } from "@/lib/data";
import { MobileHeader } from "@/components/layout/header";

export const metadata: Metadata = {
  title: "審議会・検討会一覧",
  description:
    "政府の審議会・検討会の議事録をAI要約付きで閲覧。規制改革推進会議、国土審議会、地方創生2.0有識者会議など。",
  alternates: { canonical: "/council" },
};

export default function CouncilListPage() {
  const councils = getCouncils();

  return (
    <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
      <MobileHeader />

      <div className="sticky top-0 z-40 flex h-[53px] items-center gap-3 bg-x-bg/65 px-4 backdrop-blur-xl">
        <Link
          href="/"
          className="flex h-9 w-9 items-center justify-center rounded-full text-x-text transition-colors hover:bg-x-hover"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 20 }}>arrow_back</span>
        </Link>
        <h1 className="text-[17px] font-bold">審議会・検討会</h1>
      </div>

      <div className="px-4 py-4">
        <p className="text-[14px] leading-[22px] text-x-secondary">
          政府の審議会・検討会の議事録をAI要約付きのスレッド形式で公開しています。
          各会議の議事録PDFを取得し、発言を話者ごとに構造化しています。
        </p>

        <div className="mt-6 space-y-3">
          {councils.map((council) => (
            <Link
              key={council.slug}
              href={`/council/${council.slug}`}
              className="block rounded-2xl border border-x-border px-4 py-4 transition-colors hover:bg-x-hover"
            >
              <div className="flex items-center gap-2">
                <span
                  className="material-symbols-rounded text-green-500"
                  style={{ fontSize: 20 }}
                >
                  groups
                </span>
                <span className="text-[16px] font-bold text-x-text">
                  {council.name}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-3 text-[13px] text-x-secondary">
                <span>{council.meetings.length}回の会議</span>
                <span>{council.totalThreads}スレッド</span>
                <span>{council.totalSpeeches}件の発言</span>
              </div>
              <div className="mt-2 text-[13px] text-x-secondary">
                {council.meetings[0]?.date} — {council.meetings[council.meetings.length - 1]?.date}
              </div>
            </Link>
          ))}
        </div>
      </div>
    </main>
  );
}
