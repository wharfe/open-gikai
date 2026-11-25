import { test, expect, type Page } from "@playwright/test";

// #118: the two value-signal interactions must reach GA4 as gtag events.
// A local build has no GA_ID, so gtag.js is never loaded; the events are read
// back from window.dataLayer, which is the queue gtag() writes to in production.
const THREAD = "/t/t_20251211_f393e8_56";

// gtag() pushes `arguments` objects; normalise each entry to a plain array.
async function gtagEvents(page: Page): Promise<unknown[][]> {
  return page.evaluate(() => {
    const dl = (window as unknown as { dataLayer?: ArrayLike<unknown>[] }).dataLayer ?? [];
    return dl
      .map((entry) => Array.from(entry as ArrayLike<unknown>))
      .filter((args) => args[0] === "event");
  });
}

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

    await page.getByText("eco").first().click();
    // Clicking the level that is already active is not a change.
    await page.getByText("eco").first().click();

    const changes = (await gtagEvents(page)).filter((e) => e[1] === "change_reading_level");
    expect(changes).toHaveLength(1);
    expect(changes[0][2]).toEqual({ level: "easy", previous_level: "adult" });
  });
});
