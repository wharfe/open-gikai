import type { Metadata } from "next";
import { AppProvider } from "@/components/providers/app-provider";
import { Sidebar } from "@/components/layout/sidebar";
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
      <body className="bg-x-bg text-x-text antialiased">
        <AppProvider>
          <div className="mx-auto flex max-w-[1280px]">
            {/* Left navigation */}
            <Sidebar />
            {/* Main content */}
            <div className="flex min-h-screen min-w-0 flex-1 overflow-x-hidden">
              {children}
            </div>
          </div>
        </AppProvider>
      </body>
    </html>
  );
}
