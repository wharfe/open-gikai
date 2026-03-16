import type { Metadata } from "next";
import { AppProvider } from "@/components/providers/app-provider";
import { Header } from "@/components/layout/header";
import "./globals.css";

export const metadata: Metadata = {
  title: "OpenGIKAI — 国会をひらく",
  description:
    "国会の審議内容を現代的なスレッド形式で再構築するオープンソースの公共メディア",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ja">
      <body className="min-h-screen bg-gikai-bg text-slate-50 antialiased">
        <AppProvider>
          <Header />
          <div className="mx-auto max-w-[920px] px-4 py-5">
            {children}
          </div>
        </AppProvider>
      </body>
    </html>
  );
}
