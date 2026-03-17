import { test, expect } from "@playwright/test";

// Use a known thread ID from the real data
const THREAD_URL = "/t/t_20250314_e1491c_02";

test.describe("Thread detail page", () => {
  test("displays thread with speeches", async ({ page }) => {
    await page.goto(THREAD_URL);

    await expect(page.getByText("2025.03.14").first()).toBeVisible();
    await expect(page.locator("article").first()).toBeVisible();
  });

  test("shows tension badges", async ({ page }) => {
    await page.goto(THREAD_URL);

    const anyTension = page.locator("text=/追及|答弁|再追及|確認|割込み/").first();
    await expect(anyTension).toBeVisible();
  });

  test("expands raw transcript on click", async ({ page }) => {
    await page.goto(THREAD_URL);

    await page.getByText("📄 原文").first().click();

    await expect(
      page.getByText("原文（国会会議録 NDL APIより）").first()
    ).toBeVisible();
  });

  test("has back link to feed", async ({ page }) => {
    await page.goto(THREAD_URL);

    await page.locator('a[href="/"]').first().click();
    await page.waitForURL("/");
    const cards = page.locator('a[href^="/t/"]');
    await expect(cards.first()).toBeVisible();
  });

  test("shows source attribution", async ({ page }) => {
    await page.goto(THREAD_URL);

    await expect(
      page.getByText("出典：国会会議録検索システム（NDL）")
    ).toBeVisible();
  });
});
