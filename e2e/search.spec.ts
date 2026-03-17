import { test, expect } from "@playwright/test";

test.describe("Search page", () => {
  test("shows search input", async ({ page }) => {
    await page.goto("/search");
    const input = page.locator('input[placeholder*="検索"]');
    await expect(input).toBeVisible();
  });

  test("returns results for valid query", async ({ page }) => {
    await page.goto("/search");
    const input = page.locator('input[placeholder*="検索"]');
    await input.fill("人材確保");

    // Should find results
    await expect(page.getByText("件のスレッドが見つかりました")).toBeVisible();
    // Result links should appear
    const results = page.locator('a[href^="/t/"]');
    await expect(results.first()).toBeVisible();
  });

  test("shows no results for nonsense query", async ({ page }) => {
    await page.goto("/search");
    const input = page.locator('input[placeholder*="検索"]');
    await input.fill("xyzzyzzyx");

    await expect(page.getByText("見つかりませんでした")).toBeVisible();
  });

  test("clear button works", async ({ page }) => {
    await page.goto("/search");
    const input = page.locator('input[placeholder*="検索"]');
    await input.fill("人材確保");
    await expect(page.getByText("件のスレッドが見つかりました")).toBeVisible();

    await page.getByText("✕").click();
    await expect(page.getByText("キーワードを入力してスレッドを検索")).toBeVisible();
  });

  test("navigates to search from sidebar", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 768 });
    await page.goto("/");
    await page.locator('a[href="/search"]').first().click();
    await page.waitForURL("/search");
    await expect(page.locator('input[placeholder*="検索"]')).toBeVisible();
  });
});
