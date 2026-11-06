import { test, expect, type Page } from "@playwright/test";

// #102: the site keeps its RSC payloads (`*.txt`) so client-side navigation
// works. Moving hosts can break that without breaking any page load, so this
// walks the site the way a reader does and fails on a same-origin request that
// fails or answers non-2xx. Aborted requests (a prefetch cut short by the next
// navigation) are normal and ignored; third-party requests (analytics, fonts)
// are not this site's contract.
function watch(page: Page, origin: string) {
  const problems: string[] = [];
  page.on("requestfailed", (req) => {
    if (!req.url().startsWith(origin)) return;
    const err = req.failure()?.errorText ?? "";
    if (err.includes("ERR_ABORTED") || err.includes("NS_BINDING_ABORTED")) return;
    problems.push(`failed ${req.url()} ${err}`);
  });
  page.on("response", (res) => {
    const url = res.url();
    if (!url.startsWith(origin)) return;
    if (res.status() >= 400) problems.push(`${res.status()} ${url}`);
  });
  return problems;
}

test("client navigation fetches its RSC payloads without errors", async ({ page, baseURL }) => {
  const origin = new URL(baseURL!).origin;
  const problems = watch(page, origin);
  const rsc: number[] = [];
  page.on("response", (res) => {
    if (res.url().startsWith(origin) && new URL(res.url()).pathname.endsWith(".txt")) {
      rsc.push(res.status());
    }
  });

  await page.goto("/");
  const thread = page.locator('a[href^="/t/"]').first();
  await expect(thread).toBeVisible();
  await thread.click();
  await page.waitForURL(/\/t\//);
  await expect(page.getByText("件の発言").first()).toBeVisible();

  const member = page.locator('a[href^="/m/"]').first();
  await member.click();
  await page.waitForURL(/\/m\//);

  await page.goBack();
  await page.waitForURL(/\/t\//);
  await page.goForward();
  await page.waitForURL(/\/m\//);

  await page.locator('a[href="/"]').first().click();
  await page.waitForURL((u) => u.pathname === "/");

  expect(rsc.length, "client navigation should have fetched RSC payloads").toBeGreaterThan(0);
  expect(problems).toEqual([]);
});
