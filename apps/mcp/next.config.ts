import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  // This project is the dynamic MCP server. It is NOT a static export —
  // unlike the OpenGIKAI frontend at the repo root, which uses
  // output: "export". Deployed as a separate Vercel project.

  // The MCP server reads thread/member JSON shipped at the repo root.
  // outputFileTracingRoot lets Vercel's traceability bundle pick those up
  // from outside the project root.
  outputFileTracingRoot: path.join(__dirname, "../.."),
};

export default nextConfig;
