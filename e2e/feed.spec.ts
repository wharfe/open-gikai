import { test, expect } from "@playwright/test";

test.describe("Feed page", () => {
  test("displays thread cards", async ({ page }) => {
    await page.goto("/");

    const cards = page.locator('a[href^="/t/"]');
    await expect(cards.first()).toBeVisible();

    // Real data: check for committee names from 2025-03-14
    await expect(page.getByText("予算委員会").first()).toBeVisible();
  });

  test("displays header with OpenGIKAI branding", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("OpenGIK").first()).toBeVisible();
  });

  test("switches between all and following tabs", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByText("📋 すべて")).toBeVisible();

    await page.getByText(/⭐ フォロー中/).click();

    await expect(
      page.getByText("議員をフォローすると")
    ).toBeVisible();

    await page.getByText("📋 すべて").click();
    const cards = page.locator('a[href^="/t/"]');
    await expect(cards.first()).toBeVisible();
  });

  test("shows trend panel on desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 768 });
    await page.goto("/");
    await expect(page.getByText("🔥 トレンド")).toBeVisible();
  });
});
