import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { getSessionDigest, getSessions } from "@/lib/data";
import { MobileHeader } from "@/components/layout/header";

export const dynamicParams = false;

export function generateStaticParams() {
  return getSessions().map((s) => ({ number: String(s.number) }));
}

type Props = {
  params: Promise<{ number: string }>;
};

async function load(params: Props["params"]) {
  const { number } = await params;
  return getSessionDigest(Number(number));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const digest = await load(params);
  if (!digest) return {};
  const { session } = digest;

  const title = `${session.name}まとめ — 委員会別の審議スレッド`;
  const description = `${session.name}（${session.period}）の審議を委員会別に一覧。${digest.committees.length}委員会・${digest.threadCount}スレッド・${digest.speechCount}発言。各スレッドでAI要約と国会会議録の原文を確認できます。`;
  const url = `/session/${session.number}`;

  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: {
      title,
      description,
      type: "website",
      url: `https://open-gikai.net${url}`,
      siteName: "OpenGIKAI",
    },
  };
}

export default async function SessionPage({ params }: Props) {
  const digest = await load(params);
  if (!digest) notFound();
  const { session } = digest;
  // Cross-links keep past session pages reachable after the next session starts.
  const otherSessions = getSessions()
    .filter((s) => s.number !== session.number)
    .sort((a, b) => b.number - a.number);

  // JSON-LD structured data — all values are server-generated static objects, no user input
  const breadcrumb = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: "ホーム", item: "https://open-gikai.net" },
      { "@type": "ListItem", position: 2, name: `${session.name}まとめ` },
    ],
  };

  return (
    <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
      <MobileHeader />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumb) }}
      />

      <div className="sticky top-0 z-40 flex h-[53px] items-center gap-3 bg-x-bg/65 px-4 backdrop-blur-xl">
        <Link
          href="/"
          className="flex h-9 w-9 items-center justify-center rounded-full text-x-text transition-colors hover:bg-x-hover"
        >
          <span className="material-symbols-rounded" style={{ fontSize: 20 }}>arrow_back</span>
        </Link>
        <div className="min-w-0">
          <h1 className="truncate text-[17px] font-bold leading-tight">
            {session.name}まとめ
          </h1>
          <div className="text-[13px] text-x-secondary">{session.period}</div>
        </div>
      </div>

      <div className="border-b border-x-border px-4 py-4">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-x-secondary">
          <span>
            <span className="font-bold text-x-text">{digest.committees.length}</span> 委員会
          </span>
          <span>
            <span className="font-bold text-x-text">{digest.threadCount}</span> スレッド
          </span>
          <span>
            <span className="font-bold text-x-text">{digest.speechCount}</span> 発言
          </span>
          <span>
            <span className="font-bold text-x-text">{digest.memberCount}</span> 名
          </span>
        </div>
        <p className="mt-2 text-[13px] leading-[20px] text-x-secondary">
          会期中に開かれた委員会・本会議の審議を、スレッド数の多い委員会から順に並べています。スレッド名は Claude AI による要約です。会議録は国立国会図書館の公開から数日遅れて反映されます。
        </p>
      </div>

      {digest.committees.map((g) => (
        <section key={`${g.house}-${g.committee}`} className="border-b border-x-border">
          <h2 className="flex items-baseline justify-between gap-2 px-4 pt-4 pb-1">
            <span className="text-[15px] font-bold text-x-text">
              {g.house} {g.committee}
            </span>
            <span className="shrink-0 text-[13px] font-normal text-x-secondary">
              {g.threads.length}スレッド
            </span>
          </h2>
          <ul className="pb-2">
            {g.threads.map((t) => (
              <li key={t.id}>
                <Link
                  href={`/t/${t.id}`}
                  className="block px-4 py-2 transition-colors hover:bg-x-hover"
                >
                  <div className="text-[14px] text-x-text">{t.topic}</div>
                  <div className="mt-0.5 text-[12px] text-x-secondary">
                    {t.date} · {t.speechCount}発言
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {otherSessions.length > 0 && (
        <nav className="px-4 py-4" aria-label="ほかの会期">
          <h2 className="text-[15px] font-bold text-x-text">ほかの会期</h2>
          <ul className="mt-2 space-y-1">
            {otherSessions.map((s) => (
              <li key={s.number}>
                <Link href={`/session/${s.number}`} className="text-[14px] text-x-brand hover:underline">
                  {s.name}まとめ
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      )}
    </main>
  );
}
