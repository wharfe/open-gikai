/**
 * Minimal layout for the MCP-server-only Next.js app.
 *
 * Required because Next's App Router needs a root layout, even though this
 * project exposes no human-facing pages — only the JSON-RPC endpoint at
 * /api/mcp.
 */

export const metadata = {
  title: "OpenGIKAI MCP server",
  description: "Read-only MCP access to Japanese Diet transcript data.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
