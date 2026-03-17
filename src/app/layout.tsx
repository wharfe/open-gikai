import type { Metadata } from "next";
import Script from "next/script";
import { AppProvider } from "@/components/providers/app-provider";
import { Sidebar } from "@/components/layout/sidebar";
import "./globals.css";

const GA_ID = "G-XFC3R0M1MM";

export const metadata: Metadata = {
  title: {
    default: "OpenGIKAI — 国会をひらく",
    template: "%s | OpenGIKAI",
  },
  description:
    "国会の審議内容を現代的なスレッド形式で再構築するオープンソースの公共メディア",
  metadataBase: new URL("https://open-gikai.net"),
  icons: {
    icon: [{ url: "/favicon.svg", type: "image/svg+xml" }],
  },
  manifest: "/site.webmanifest",
  other: {
    "theme-color": "#34d399",
  },
  openGraph: {
    siteName: "OpenGIKAI",
    type: "website",
    locale: "ja_JP",
    images: [{ url: "/og-image.png", width: 1200, height: 630 }],
  },
  twitter: {
    card: "summary_large_image",
    images: ["/og-image.png"],
  },
};

const jsonLd = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  name: "OpenGIKAI",
  alternateName: "OpenGIKAI — 国会をひらく",
  url: "https://open-gikai.net",
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
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />
        <Script
          src={`https://www.googletagmanager.com/gtag/js?id=${GA_ID}`}
          strategy="afterInteractive"
        />
        <Script id="ga4" strategy="afterInteractive">
          {`window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag('js',new Date());gtag('config','${GA_ID}');`}
        </Script>
      </head>
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
