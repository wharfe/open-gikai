import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // This project is the dynamic MCP server. It is NOT a static export —
  // unlike the OpenGIKAI frontend at the repo root, which uses
  // output: "export". Deployed as a separate Vercel project.
  //
  // We previously set outputFileTracingRoot to the monorepo root so Vercel
  // would bundle the shared data/ directory, but that path-resolves
  // incorrectly inside Vercel's deployment environment (it gets double-
  // prefixed with the project root, producing paths like
  // /vercel/path0/vercel/path0/.next/...). Instead, a prebuild step
  // copies the needed JSON files into ./data and the runtime reads from
  // process.cwd() — see scripts/copy-data.mjs and src/lib/data.ts.

  // Pin Turbopack's workspace root to this project so it never tries to
  // infer the parent monorepo (which would re-introduce the lockfile-
  // detection warning and risk surprising behavior on Vercel).
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
