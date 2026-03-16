import { test, expect } from "@playwright/test";

test.describe("Feed page", () => {
  test("displays thread cards", async ({ page }) => {
    await page.goto("/");

    const cards = page.locator('a[href^="/t/"]');
    await expect(cards.first()).toBeVisible();

    await expect(page.getByText("AI規制法案").first()).toBeVisible();
    await expect(page.getByText("少子化対策財源")).toBeVisible();
    await expect(page.getByText("緊急事態条項").first()).toBeVisible();
  });

  test("displays header with GIKAI branding", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("GIKAI").first()).toBeVisible();
  });

  test("switches between all and following tabs", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByText("📋 すべて")).toBeVisible();

    await page.getByText(/⭐ フォロー中/).click();

    await expect(
      page.getByText("議員をフォローすると")
    ).toBeVisible();

    await page.getByText("📋 すべて").click();
    await expect(page.getByText("AI規制法案").first()).toBeVisible();
  });

  test("shows trend panel on desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 768 });
    await page.goto("/");
    await expect(page.getByText("🔥 トレンド")).toBeVisible();
  });
});
