import { test, expect } from "@playwright/test";

// Navigate to a thread dynamically by clicking the first thread link on the feed
async function goToFirstThread(page: import("@playwright/test").Page) {
  await page.goto("/");
  const firstCard = page.locator('a[href^="/t/"]').first();
  await expect(firstCard).toBeVisible();
  await firstCard.click();
  await page.waitForURL(/\/t\//);
}

test.describe("Thread detail page", () => {
  test("displays thread with speeches", async ({ page }) => {
    await goToFirstThread(page);

    // Should show a date in YYYY.MM.DD format
    await expect(page.locator("text=/\\d{4}\\.\\d{2}\\.\\d{2}/").first()).toBeVisible();
    await expect(page.locator("article").first()).toBeVisible();
  });

  test("shows tension badges", async ({ page }) => {
    await goToFirstThread(page);

    const anyTension = page.locator("text=/追及|答弁|再追及|確認|割込み/").first();
    await expect(anyTension).toBeVisible();
  });

  test("expands raw transcript on click", async ({ page }) => {
    await goToFirstThread(page);

    await page.getByText("原文", { exact: false }).first().click();

    await expect(
      page.getByText("原文（議事録より）").first()
    ).toBeVisible();
  });

  test("has back link to feed", async ({ page }) => {
    await goToFirstThread(page);

    await page.locator('a[href="/"]').first().click();
    await page.waitForURL("/");
    const cards = page.locator('a[href^="/t/"]');
    await expect(cards.first()).toBeVisible();
  });

  test("shows source attribution", async ({ page }) => {
    await goToFirstThread(page);

    await expect(
      page.getByText("出典：", { exact: false }).first()
    ).toBeVisible();
  });
});
