import { test, expect } from "@playwright/test";

test.describe("Feed page", () => {
  test("displays thread cards", async ({ page }) => {
    await page.goto("/");

    // Should show 3 thread cards
    const cards = page.locator('a[href^="/t/"]');
    await expect(cards.first()).toBeVisible();

    // Should show thread topics
    await expect(page.getByText("AI規制法案")).toBeVisible();
    await expect(page.getByText("少子化対策財源")).toBeVisible();
    await expect(page.getByText("緊急事態条項").first()).toBeVisible();
  });

  test("displays header with GIKAI branding", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("GIKAI")).toBeVisible();
    await expect(page.getByText("国会をひらく")).toBeVisible();
  });

  test("shows level selector bar", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("やさしく")).toBeVisible();
    await expect(page.getByText("標準")).toBeVisible();
    await expect(page.getByText("詳しく")).toBeVisible();
  });

  test("switches between all and following tabs", async ({ page }) => {
    await page.goto("/");

    // Default: all tab
    await expect(page.getByText("📋 すべて")).toBeVisible();

    // Click following tab
    await page.getByText(/⭐ フォロー中/).click();

    // Should show empty state message
    await expect(
      page.getByText("議員をフォローすると")
    ).toBeVisible();

    // Click back to all
    await page.getByText("📋 すべて").click();
    await expect(page.getByText("AI規制法案")).toBeVisible();
  });

  test("shows trend panel on desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.goto("/");
    await expect(page.getByText("🔥 トレンド")).toBeVisible();
  });
});
