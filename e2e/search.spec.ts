import { test, expect } from "@playwright/test";

test.describe("Search page", () => {
  test("shows search input", async ({ page }) => {
    await page.goto("/search");
    const input = page.locator('input[placeholder*="検索"]');
    await expect(input).toBeVisible();
  });

  test("returns results or no-results for a query", async ({ page }) => {
    await page.goto("/search");
    const input = page.locator('input[placeholder*="検索"]');
    // Use a generic Japanese political term likely to exist in any Diet data
    await input.fill("予算");

    // Should show either results or a no-results message
    const resultsOrEmpty = page.locator(
      'text=/件のスレッドが見つかりました|見つかりませんでした/'
    );
    await expect(resultsOrEmpty.first()).toBeVisible();
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
    // Use a generic query
    await input.fill("予算");

    // Wait for search to complete (results or no-results)
    const resultsOrEmpty = page.locator(
      'text=/件のスレッドが見つかりました|見つかりませんでした/'
    );
    await expect(resultsOrEmpty.first()).toBeVisible();

    await page.getByText("close").click();
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
