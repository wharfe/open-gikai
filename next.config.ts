import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // ISR: pages are generated on-demand and cached
  // No output: "export" — Vercel handles SSR/ISR natively
};

export default nextConfig;
