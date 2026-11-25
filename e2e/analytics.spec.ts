import { test, expect, type Page } from "@playwright/test";

// #118: the two value-signal interactions must reach GA4 as gtag events.
// A local build has no GA_ID, so gtag.js is never loaded; the events are read
// back from window.dataLayer, which is the queue gtag() writes to in production.
const THREAD = "/t/t_20251211_f393e8_56";

// Against a deployed site gtag.js is live, so these clicks would land in the
// production GA4 property as fake signals — the very metric #118 adds.
test.skip(!!process.env.E2E_BASE_URL, "would send real events to production GA4");

// gtag() pushes `arguments` objects, and gtag.js ignores plain arrays, so the
// entry type is part of the contract. Normalise to arrays after checking it.
async function gtagEvents(page: Page): Promise<unknown[][]> {
  const entries = await page.evaluate(() => {
    const dl = (window as unknown as { dataLayer?: unknown[] }).dataLayer ?? [];
    return dl.map((entry) => ({
      type: Object.prototype.toString.call(entry),
      args: Array.from(entry as ArrayLike<unknown>),
    }));
  });
  const events = entries.filter((e) => e.args[0] === "event");
  for (const e of events) expect(e.type).toBe("[object Arguments]");
  return events.map((e) => e.args);
}

const levelButton = (page: Page, icon: string) => page.getByText(icon, { exact: true }).first();

test.describe("Analytics events", () => {
  test("opening the raw transcript sends view_raw_transcript", async ({ page }) => {
    await page.goto(THREAD);
    const article = page.locator("article").first();
    await expect(article).toBeVisible();

    await article.getByRole("button", { name: /原文/ }).click();
    await expect(article.getByText("原文（議事録より）")).toBeVisible();

    const opened = (await gtagEvents(page)).filter((e) => e[1] === "view_raw_transcript");
    expect(opened).toHaveLength(1);
    expect(opened[0][2]).toEqual({ thread_id: "t_20251211_f393e8_56", level: "adult" });

    // Closing it again is not a second view.
    await article.getByRole("button", { name: /閉じる/ }).click();
    expect((await gtagEvents(page)).filter((e) => e[1] === "view_raw_transcript")).toHaveLength(1);
  });

  test("switching the reading level sends change_reading_level", async ({ page }) => {
    await page.goto(THREAD);
    await expect(page.locator("article").first()).toBeVisible();

    await levelButton(page, "eco").click();
    // Clicking the level that is already active is not a change.
    await levelButton(page, "eco").click();

    const changes = (await gtagEvents(page)).filter((e) => e[1] === "change_reading_level");
    expect(changes).toHaveLength(1);
    expect(changes[0][2]).toEqual({ level: "easy", previous_level: "adult" });
  });

  test("when gtag is defined, events go through gtag itself", async ({ page }) => {
    // Stand in for the production snippet: a gtag that records its calls.
    await page.addInitScript(() => {
      const w = window as unknown as { gtagCalls: unknown[][]; gtag: (...a: unknown[]) => void };
      w.gtagCalls = [];
      w.gtag = (...a: unknown[]) => {
        w.gtagCalls.push(a);
      };
    });
    await page.goto(THREAD);
    const article = page.locator("article").first();
    await expect(article).toBeVisible();

    await article.getByRole("button", { name: /原文/ }).click();
    await expect(article.getByText("原文（議事録より）")).toBeVisible();
    await levelButton(page, "eco").click();

    const calls = await page.evaluate(() => (window as unknown as { gtagCalls: unknown[][] }).gtagCalls);
    expect(calls).toEqual([
      ["event", "view_raw_transcript", { thread_id: "t_20251211_f393e8_56", level: "adult" }],
      ["event", "change_reading_level", { level: "easy", previous_level: "adult" }],
    ]);
    // Nothing bypassed gtag into the queue.
    expect(await gtagEvents(page)).toEqual([]);
  });
});
