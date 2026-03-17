import type { Metadata } from "next";
import { AppProvider } from "@/components/providers/app-provider";
import { Sidebar } from "@/components/layout/sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "OpenGIKAI — 国会をひらく",
    template: "%s | OpenGIKAI",
  },
  description:
    "国会の審議内容を現代的なスレッド形式で再構築するオープンソースの公共メディア",
  metadataBase: new URL("https://open-gikai.net"),
  openGraph: {
    siteName: "OpenGIKAI",
    type: "website",
    locale: "ja_JP",
  },
  twitter: {
    card: "summary_large_image",
  },
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
            <div className="flex min-h-screen min-w-0 flex-1">
              {children}
            </div>
          </div>
        </AppProvider>
      </body>
    </html>
  );
}
