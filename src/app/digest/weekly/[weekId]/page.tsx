import type { Metadata } from "next";
import Link from "next/link";
import { getAllWeekIds, getWeeklyDigest, getWeeklyDigests } from "@/lib/data";
import { MobileHeader } from "@/components/layout/header";
import { notFound } from "next/navigation";

export const dynamicParams = false;

export function generateStaticParams() {
  return getAllWeekIds().map((weekId) => ({ weekId }));
}

export function generateMetadata({
  params,
}: {
  params: Promise<{ weekId: string }>;
}): Promise<Metadata> {
  return params.then(({ weekId }) => {
    const digest = getWeeklyDigest(weekId);
    if (!digest) return { title: "ダイジェストが見つかりません" };

    const kw = digest.topKeywords
      .slice(0, 3)
      .map(([k]) => k)
      .join("・");
    return {
      title: `${weekId} 国会まとめ — ${kw}`,
      description: `${digest.startDate}〜${digest.endDate}の国会・審議会ダイジェスト。${digest.threadCount}件の議論、${digest.speechCount}件の発言。主要トピック：${kw}`,
      alternates: { canonical: `/digest/weekly/${weekId}` },
    };
  });
}

export default async function WeeklyDigestPage({
  params,
}: {
  params: Promise<{ weekId: string }>;
}) {
  const { weekId } = await params;
  const digest = getWeeklyDigest(weekId);
  if (!digest) notFound();

  // Prev/next navigation
  const allDigests = getWeeklyDigests();
  const idx = allDigests.findIndex((d) => d.weekId === weekId);
  const prev = idx < allDigests.length - 1 ? allDigests[idx + 1] : null;
  const next = idx > 0 ? allDigests[idx - 1] : null;

  return (
    <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
      <MobileHeader />

      {/* Header */}
      <div className="sticky top-0 z-40 flex h-[53px] items-center gap-3 bg-x-bg/65 px-4 backdrop-blur-xl">
        <Link
          href="/digest"
          className="flex h-9 w-9 items-center justify-center rounded-full text-x-text transition-colors hover:bg-x-hover"
        >
          <span
            className="material-symbols-rounded"
            style={{ fontSize: 20 }}
          >
            arrow_back
          </span>
        </Link>
        <div>
          <h1 className="text-[17px] font-bold">{weekId}</h1>
          <p className="text-[13px] text-x-secondary">
            {digest.startDate} — {digest.endDate}
          </p>
        </div>
      </div>

      <div className="px-4 py-4 space-y-6">
        {/* Stats banner */}
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: "スレッド", value: digest.threadCount },
            { label: "発言", value: digest.speechCount },
            { label: "委員会", value: digest.committees.length },
          ].map((s) => (
            <div
              key={s.label}
              className="rounded-xl border border-x-border px-3 py-3 text-center"
            >
              <div className="text-[20px] font-bold text-x-brand">
                {s.value}
              </div>
              <div className="text-[12px] text-x-secondary">{s.label}</div>
            </div>
          ))}
        </div>

        {/* Top keywords */}
        {digest.topKeywords.length > 0 && (
          <section>
            <h2 className="mb-3 flex items-center gap-2 text-[15px] font-bold">
              <span
                className="material-symbols-rounded text-x-brand"
                style={{ fontSize: 18 }}
              >
                trending_up
              </span>
              注目キーワード
            </h2>
            <div className="flex flex-wrap gap-2">
              {digest.topKeywords.map(([kw, count]) => (
                <span
                  key={kw}
                  className="inline-flex items-center gap-1 rounded-full border border-x-border px-3 py-1 text-[13px]"
                >
                  <span className="text-x-text">{kw}</span>
                  <span className="text-x-secondary">{count}</span>
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Highlights */}
        {digest.highlights.length > 0 && (
          <section>
            <h2 className="mb-3 flex items-center gap-2 text-[15px] font-bold">
              <span
                className="material-symbols-rounded text-amber-500"
                style={{ fontSize: 18 }}
              >
                local_fire_department
              </span>
              注目の議論
            </h2>
            <div className="space-y-3">
              {digest.highlights.map((h) => (
                <Link
                  key={h.id}
                  href={`/t/${h.id}`}
                  className="block rounded-xl border border-x-border px-4 py-3 transition-colors hover:bg-x-hover"
                >
                  <div className="flex items-center gap-2 text-[13px] text-x-secondary">
                    <span>{h.committee}</span>
                    <span>·</span>
                    <span>{h.date}</span>
                  </div>
                  <div className="mt-1 text-[15px] font-bold text-x-text">
                    {h.topic}
                  </div>
                  <p className="mt-1 text-[13px] leading-[20px] text-x-secondary line-clamp-2">
                    {h.summary}
                  </p>
                </Link>
              ))}
            </div>
          </section>
        )}

        {/* Committee breakdown */}
        <section>
          <h2 className="mb-3 flex items-center gap-2 text-[15px] font-bold">
            <span
              className="material-symbols-rounded text-blue-500"
              style={{ fontSize: 18 }}
            >
              account_balance
            </span>
            委員会別
          </h2>
          <div className="space-y-2">
            {digest.committees.map((c) => (
              <div
                key={c.name}
                className="flex items-center justify-between rounded-lg border border-x-border px-3 py-2"
              >
                <span className="text-[14px] text-x-text">{c.name}</span>
                <span className="text-[13px] text-x-secondary">
                  {c.threads}スレッド
                </span>
              </div>
            ))}
          </div>
        </section>

        {/* Source breakdown */}
        {digest.sources.length > 1 && (
          <section>
            <h2 className="mb-3 flex items-center gap-2 text-[15px] font-bold">
              <span
                className="material-symbols-rounded text-x-secondary"
                style={{ fontSize: 18 }}
              >
                description
              </span>
              データソース
            </h2>
            <div className="flex flex-wrap gap-3 text-[13px] text-x-secondary">
              {digest.sources.map((s) => (
                <span key={s.source}>
                  {s.source}: {s.count}件
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Prev/Next navigation */}
        <div className="flex items-center justify-between border-t border-x-border pt-4">
          {prev ? (
            <Link
              href={`/digest/weekly/${prev.weekId}`}
              className="flex items-center gap-1 text-[14px] text-x-brand hover:underline"
            >
              <span
                className="material-symbols-rounded"
                style={{ fontSize: 18 }}
              >
                chevron_left
              </span>
              {prev.weekId}
            </Link>
          ) : (
            <span />
          )}
          {next ? (
            <Link
              href={`/digest/weekly/${next.weekId}`}
              className="flex items-center gap-1 text-[14px] text-x-brand hover:underline"
            >
              {next.weekId}
              <span
                className="material-symbols-rounded"
                style={{ fontSize: 18 }}
              >
                chevron_right
              </span>
            </Link>
          ) : (
            <span />
          )}
        </div>
      </div>
    </main>
  );
}
