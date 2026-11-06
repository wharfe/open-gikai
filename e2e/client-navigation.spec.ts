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
  const rsc: string[] = [];
  page.on("response", (res) => {
    const url = new URL(res.url());
    if (!res.url().startsWith(origin) || !url.pathname.endsWith(".txt")) return;
    // Cloudflare static assets answer a path containing `$` (Next 16 names
    // segment payloads `__next.t.$d$threadId...`) with a 307 to the same path
    // percent-encoded; the browser follows it to a 200. Only that redirect is
    // tolerated — any other non-200 is a failure.
    const loc = res.headers()["location"];
    if (res.status() === 307 && loc) {
      const target = new URL(loc, url);
      if (decodeURIComponent(target.pathname) === decodeURIComponent(url.pathname)
          && target.search === url.search) return;
    }
    rsc.push(`${res.status()} ${url.pathname}`);
  });

  await page.goto("/");
  // A marker on window survives client navigation and dies with a full page
  // load. Without it, a host serving HTML for the RSC payloads would make Next
  // fall back to hard navigation and this test would still pass.
  await page.evaluate(() => ((window as unknown as { __spa: number }).__spa = 1));
  const stillSpa = () =>
    page.evaluate(() => (window as unknown as { __spa?: number }).__spa === 1);
  const thread = page.locator('a[href^="/t/"]').first();
  await expect(thread).toBeVisible();
  await thread.click();
  await page.waitForURL(/\/t\//);
  await expect(page.getByText("件の発言").first()).toBeVisible();

  const member = page.locator('a[href^="/m/"]').first();
  await member.click();
  await page.waitForURL(/\/m\//);
  expect(await stillSpa(), "thread → member was a full page load").toBe(true);

  await page.goBack();
  await page.waitForURL(/\/t\//);
  await page.goForward();
  await page.waitForURL(/\/m\//);

  await page.locator('a[href="/"]').first().click();
  await page.waitForURL((u) => u.pathname === "/");

  expect(await stillSpa(), "navigation fell back to full page loads").toBe(true);
  expect(rsc.length, "client navigation should have fetched RSC payloads").toBeGreaterThan(0);
  expect(rsc.filter((r) => !r.startsWith("200 ")), "every RSC payload should answer 200").toEqual([]);
  expect(problems).toEqual([]);
});
