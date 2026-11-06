import { defineConfig } from "@playwright/test";

// E2E_BASE_URL points the same specs at a deployed site (e.g. the Cloudflare
// workers.dev deployment before the DNS cutover, #102) instead of `serve out`.
const remote = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  timeout: 60000,
  use: {
    baseURL: remote ?? "http://localhost:3200",
    trace: "on-first-retry",
    navigationTimeout: 30000,
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
  webServer: remote
    ? undefined
    : {
        command: "npx serve out -l 3200 --no-clipboard",
        url: "http://localhost:3200",
        reuseExistingServer: !process.env.CI,
      },
});
