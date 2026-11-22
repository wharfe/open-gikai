import Link from "next/link";
import type { EnactedLawEntry } from "@/lib/data";

const dot = (d: string) => d.replace(/-/g, ".");

export function EnactedLaws({ laws }: { laws: EnactedLawEntry[] }) {
  if (laws.length === 0) return null;
  return (
    <section className="border-b border-x-border" aria-labelledby="enacted-laws">
      <h2 id="enacted-laws" className="flex items-baseline justify-between gap-2 px-4 pt-4 pb-1">
        <span className="text-[15px] font-bold text-x-text">成立した法律と審議スレッド</span>
        <span className="shrink-0 text-[13px] font-normal text-x-secondary">{laws.length}件</span>
      </h2>
      <p className="px-4 pb-2 text-[12px] leading-[18px] text-x-secondary">
        成立日の順に並べています。成立した法律は衆議院の議案情報から転記し、審議スレッドは題名・要約・会議録の原文に法案名がそのまま出てくるものを機械的に並べています。
      </p>
      <ul className="pb-2">
        {laws.map((law) => (
          <li key={`${law.billKind}-${law.billSession}-${law.billNumber}`} className="px-4 py-2">
            <div className="text-[14px] font-bold text-x-text">{law.name}</div>
            <div className="mt-0.5 flex flex-wrap gap-x-3 text-[12px] text-x-secondary">
              <span>{dot(law.enactedDate)} 成立</span>
              {law.lawNumber && <span>{law.lawNumber}</span>}
              <a
                href={law.sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-x-brand hover:underline"
              >
                衆議院 議案情報
              </a>
              {law.lexdiff.map((l) => (
                <a
                  key={l.url}
                  href={l.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-x-brand hover:underline"
                >
                  {l.label}（lex-diff）
                </a>
              ))}
            </div>
            {law.threads.length > 0 ? (
              <ul className="mt-1">
                {law.threads.map((t) => (
                  <li key={t.id}>
                    <Link
                      href={`/t/${t.id}`}
                      className="-mx-2 block rounded px-2 py-1 transition-colors hover:bg-x-hover"
                    >
                      <div className="text-[14px] text-x-text">{t.topic}</div>
                      <div className="text-[12px] text-x-secondary">
                        {t.date} · {t.house} {t.committee}
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="mt-1 text-[12px] text-x-secondary">
                法律名が出てくる審議スレッドはまだありません
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
