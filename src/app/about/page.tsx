import type { Metadata } from "next";
import Link from "next/link";
import { MobileHeader } from "@/components/layout/header";
import { getProcessingStatus, getThreads } from "@/lib/data";
import { SOURCE_STYLE } from "@/lib/config";

export const metadata: Metadata = {
  title: "OpenGIKAIについて",
  description: "OpenGIKAIの仕組み、データソース、AI利用方針について。議会議事録をAIで要約・構造化するオープンソースプロジェクト。",
};

type Summary = {
  totalDates: number;
  totalThreads: number;
  totalSpeeches: number;
  totalCommittees: number;
  totalMembers: number;
  generatedAt: string;
};

export default function AboutPage() {
  const raw = getProcessingStatus() as Record<string, unknown> | null;
  const summary = raw?._summary as Summary | undefined;

  // Build committee/council list from thread data
  const threads = getThreads();
  const committeeMap = new Map<string, { source: string; house: string; count: number }>();
  for (const t of threads) {
    const key = `${t.source || "ndl"}::${t.committee}`;
    const existing = committeeMap.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      committeeMap.set(key, { source: t.source || "ndl", house: t.house, count: 1 });
    }
  }
  const committeeList = [...committeeMap.entries()]
    .map(([key, val]) => ({ name: key.split("::")[1], ...val }))
    .sort((a, b) => b.count - a.count);
  return (
    <>
      <main className="w-full min-w-0 md:border-r md:border-x-border md:max-w-[600px]">
        <MobileHeader />

        {/* Sticky header */}
        <div className="sticky top-0 z-40 flex h-[53px] items-center gap-5 bg-x-bg/65 px-4 backdrop-blur-xl">
          <div className="text-[17px] font-bold">OpenGIKAIについて</div>
        </div>

        <div className="space-y-10 px-4 py-6">
          {/* About */}
          <section>
            <h2 className="text-[20px] font-bold text-x-text">
              OpenGIK<span className="text-x-brand">AI</span>とは
            </h2>
            <p className="mt-3 text-[15px] leading-[26px] text-x-secondary">
              OpenGIKAIは、議会の審議内容を現代的なスレッド形式で再構築するオープンソースの公共メディアプロジェクトです。
              国立国会図書館（NDL）が公開する国会議事録をはじめ、公的機関の議事録を一次情報として使用し、AIによる要約・構造化を加えて提供しています。
            </p>
          </section>

          {/* How it works */}
          <section>
            <h2 className="text-[20px] font-bold text-x-text">仕組み</h2>
            <div className="mt-3 space-y-3 text-[15px] leading-[26px] text-x-secondary">
              <p>
                <span className="font-bold text-x-text">1. 議事録の取得</span>
                <br />
                NDL国会会議録検索システムAPIから、委員会ごとの全発言を取得します。取得ロジックはすべてオープンソースです。
              </p>
              <p>
                <span className="font-bold text-x-text">2. AIによる構造化</span>
                <br />
                Claude（Anthropic社）を使用して、発言をテーマ別に分類し、3段階（やさしく・標準・詳しく）の要約を生成します。
                要約プロンプトはすべて公開されています。
              </p>
              <p>
                <span className="font-bold text-x-text">3. 政治的中立性の担保</span>
                <br />
                全発言を同一のアルゴリズムで処理します。与党・野党の扱いに差はありません。
                取捨選択の基準、プロンプト全文、処理ロジックはGitHubで公開しています。
              </p>
            </div>
          </section>

          {/* AI Disclaimer */}
          <section>
            <h2 className="text-[20px] font-bold text-x-text">
              AI利用に関する注意事項
            </h2>
            <div className="mt-3 space-y-3 rounded-xl border border-yellow-500/20 bg-yellow-500/5 p-4 text-[14px] leading-[24px] text-x-secondary">
              <p>
                <span className="font-bold text-yellow-500">要約はAIが生成しています。</span>
                すべての要約はClaude AI（Anthropic社）によって事前に生成されたものです。
                原文の意図を正確に反映することを目指していますが、誤りや省略が含まれる可能性があります。
              </p>
              <p>
                <span className="font-bold text-yellow-500">発言者情報は自動抽出です。</span>
                議員の所属政党・役職等は国会会議録のメタデータから自動的に抽出しています。
                審議会の委員名は議事録PDFから抽出しており、姓のみの表記や同姓の別人が
                同一人物として扱われる場合があります。
                正確な情報は各機関の公式サイトをご参照ください。
              </p>
              <p>
                <span className="font-bold text-yellow-500">テンション分類はAIの判断です。</span>
                「追及」「答弁」「再追及」等の分類はAIが文脈から判定しています。
                政治的な評価ではなく、発言の構造的な役割を示すものです。
              </p>
              <p>
                正確な内容を確認するには、各スレッドに記載された出典リンクから
                国会会議録検索システム（NDL）の原文をご確認ください。
              </p>
              <p>
                <span className="font-bold text-yellow-500">フォロー機能はブラウザ内に保存されます。</span>
                フォロー中の発言者リストはお使いのブラウザのローカルストレージに保存されるため、
                別の端末やブラウザでは共有されません。ブラウザのデータを消去すると設定も失われます。
              </p>
            </div>
          </section>

          {/* Data Sources */}
          <section>
            <h2 className="text-[20px] font-bold text-x-text">データソース</h2>

            {/* Primary sources — actively ingested */}
            <h3 className="mt-4 text-[16px] font-bold text-x-text">対応済みソース</h3>
            <div className="mt-2 space-y-3">
              {[
                {
                  name: "国会会議録",
                  href: "https://kokkai.ndl.go.jp/",
                  org: "国立国会図書館（NDL）",
                  desc: "衆議院・参議院の全委員会議事録。著作権法第13条により保護対象外。",
                  status: "稼働中" as const,
                },
                {
                  name: "首相記者会見",
                  href: "https://www.kantei.go.jp/",
                  org: "首相官邸",
                  desc: "内閣総理大臣の定例・臨時記者会見。",
                  status: "稼働中" as const,
                },
                {
                  name: "規制改革推進会議",
                  href: "https://www8.cao.go.jp/kisei-kaikaku/kisei/meeting/meeting.html",
                  org: "内閣府",
                  desc: "本会議および各ワーキング・グループ（デジタル・AI、健康・医療・介護、地域活性化等）の議事録。",
                  status: "稼働中" as const,
                },
              ].map(({ name, href, org, desc, status }) => (
                <div key={name} className="rounded-xl border border-x-border p-3">
                  <div className="flex items-center justify-between">
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-[15px] font-bold text-x-accent hover:underline"
                    >
                      {name} ↗
                    </a>
                    <span className="rounded-full bg-green-500/10 px-2 py-0.5 text-[12px] font-medium text-green-600 dark:text-green-400">
                      {status}
                    </span>
                  </div>
                  <div className="mt-1 text-[13px] text-x-secondary">
                    {org} — {desc}
                  </div>
                </div>
              ))}
            </div>

            {/* Reference sources */}
            <h3 className="mt-5 text-[16px] font-bold text-x-text">参照データ</h3>
            <div className="mt-2 space-y-2 text-[15px] text-x-secondary">
              <div className="flex items-start gap-2">
                <span className="material-symbols-rounded shrink-0 text-x-brand" style={{ fontSize: 18 }}>arrow_forward</span>
                <div>
                  <a
                    href="https://laws.e-gov.go.jp/"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-x-accent hover:underline"
                  >
                    e-Gov法令検索
                  </a>
                  <span className="text-x-secondary"> — 法令の条文参照に使用。</span>
                </div>
              </div>
            </div>
          </section>

          {/* Committee & Council list */}
          {committeeList.length > 0 && (
            <section>
              <h2 className="text-[20px] font-bold text-x-text">対応委員会・審議会</h2>
              <p className="mt-2 text-[13px] text-x-secondary">
                現在データに含まれている委員会・審議会の一覧です。データの蓄積に応じて自動的に更新されます。
              </p>
              <div className="mt-3 space-y-1.5">
                {committeeList.map(({ name, source, house, count }) => {
                  const style = SOURCE_STYLE[source];
                  return (
                    <div key={`${source}-${name}`} className="flex items-center gap-2 text-[14px]">
                      {style ? (
                        <span
                          className="material-symbols-rounded shrink-0"
                          style={{ fontSize: 16, color: style.color }}
                          title={style.label}
                        >
                          {style.icon}
                        </span>
                      ) : (
                        <span className="w-4 shrink-0" />
                      )}
                      <span className="text-x-text">{name}</span>
                      <span className="text-[12px] text-x-secondary">
                        {count}スレッド
                      </span>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {/* FAQ */}
          <section>
            <h2 className="text-[20px] font-bold text-x-text">よくある質問</h2>
            <div className="mt-3 space-y-4">
              {[
                {
                  q: "要約は正確ですか？",
                  a: "AIによる要約のため、誤りや省略が含まれる可能性があります。重要な判断の根拠にする場合は、必ず原文（NDL議事録）をご確認ください。各発言には原文表示ボタンと出典リンクがあります。",
                },
                {
                  q: "政治的に中立ですか？",
                  a: "全発言を同一のアルゴリズム・プロンプトで処理しています。与党・野党による扱いの差はありません。処理ロジックとプロンプトはGitHubで全文公開しており、誰でも検証可能です。",
                },
                {
                  q: "特定の発言が省略されていませんか？",
                  a: "原則として、委員長の手続き的発言（開会宣言等）を除き、すべての発言を処理対象としています。ただし、AIのグルーピング処理で一部の発言がスレッドに含まれない場合があります。",
                },
                {
                  q: "議員のプロフィール情報はどこから取得していますか？",
                  a: "国会会議録のメタデータ（発言者名、所属会派、役職等）から自動抽出しています。経歴や政策スタンスなどの詳細情報は現時点では未実装です。",
                },
                {
                  q: "データの更新頻度は？",
                  a: "NDLの議事録公開後にバッチ処理で取得・要約します。議事録の公開は通常、審議の数日後です。",
                },
                {
                  q: "ソースコードはどこで見られますか？",
                  a: "GitHubで全ソースコードを公開しています。フロントエンド、データ取得スクリプト、AI要約プロンプトのすべてが含まれます。",
                },
              ].map(({ q, a }) => (
                <div key={q}>
                  <div className="text-[15px] font-bold text-x-text">{q}</div>
                  <p className="mt-1 text-[14px] leading-[22px] text-x-secondary">
                    {a}
                  </p>
                </div>
              ))}
            </div>
          </section>

          {/* OSS & Transparency */}
          <section>
            <h2 className="text-[20px] font-bold text-x-text">
              オープンソース・透明性
            </h2>
            <div className="mt-3 space-y-2 text-[15px] leading-[26px] text-x-secondary">
              <p>OpenGIKAIは公共財として運営されています。以下をすべてGitHubで公開しています：</p>
              <ul className="list-inside list-disc space-y-1 pl-2">
                <li>NDL APIからの取得ロジック</li>
                <li>テーマグルーピングのプロンプト全文</li>
                <li>要約生成のプロンプト全文</li>
                <li>テンション分類のプロンプト</li>
                <li>フロントエンドのソースコード</li>
              </ul>
              <p className="mt-2">
                <a
                  href="https://github.com/wharfe/open-gikai"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-x-accent hover:underline"
                >
                  GitHub: wharfe/open-gikai ↗
                </a>
              </p>
            </div>
          </section>

          {/* Processing Status — summary + link */}
          {summary && (
            <section>
              <h2 className="text-[20px] font-bold text-x-text">
                処理ステータス
              </h2>
              <p className="mt-2 text-[13px] text-x-secondary">
                各委員会の議事録処理状況をリアルタイムで公開しています。
              </p>

              <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  { label: "処理日数", value: summary.totalDates },
                  { label: "スレッド", value: summary.totalThreads },
                  { label: "発言数", value: summary.totalSpeeches.toLocaleString() },
                  { label: "議員数", value: summary.totalMembers },
                ].map(({ label, value }) => (
                  <div
                    key={label}
                    className="rounded-xl border border-x-border px-3 py-3 text-center"
                  >
                    <div className="text-[20px] font-bold text-x-brand">
                      {value}
                    </div>
                    <div className="text-[12px] text-x-secondary">
                      {label}
                    </div>
                  </div>
                ))}
              </div>

              <Link
                href="/about/stats"
                className="mt-4 flex items-center justify-between rounded-xl border border-x-border px-4 py-3 transition-colors hover:bg-x-hover"
              >
                <span className="text-[15px] text-x-text">日別の詳細ステータスを見る</span>
                <span className="material-symbols-rounded text-x-secondary" style={{ fontSize: 18 }}>chevron_right</span>
              </Link>
            </section>
          )}

          {/* Support */}
          <section>
            <h2 className="text-[20px] font-bold text-x-text">
              このプロジェクトを支援する
            </h2>
            <div className="mt-3 space-y-3 text-[15px] leading-[26px] text-x-secondary">
              <p>
                OpenGIKAIは広告なし・無料で運営していますが、
                AI要約の生成やサーバー維持にコストがかかっています。
                このプロジェクトの継続にご協力いただける方は、
                GitHub Sponsorsからご支援をお願いいたします。
              </p>
              <a
                href="https://github.com/sponsors/wharfe"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-full bg-x-accent px-5 py-2.5 text-[15px] font-bold text-white transition-colors hover:bg-x-accent-hover"
              >
                <span className="material-symbols-rounded" style={{ fontSize: 18 }}>favorite</span>
                GitHub Sponsors で支援する
              </a>
            </div>
          </section>

          {/* License */}
          <section className="pb-10">
            <h2 className="text-[20px] font-bold text-x-text">ライセンス</h2>
            <div className="mt-3 text-[15px] leading-[26px] text-x-secondary">
              <p>
                ソースコードはMITライセンスで公開しています。
                国会議事録は著作権法第13条により著作権の対象外です。
                AI生成要約は「Claude AIによる要約」として明示しています。
              </p>
            </div>
          </section>
        </div>
      </main>
    </>
  );
}
