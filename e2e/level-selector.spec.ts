import { test, expect } from "@playwright/test";

test.describe("Level selector", () => {
  test("level selector is visible", async ({ page }) => {
    await page.goto("/");
    // Level icons should be visible (Material Symbols text content)
    await expect(page.getByText("eco").first()).toBeVisible();
    await expect(page.getByText("menu_book").first()).toBeVisible();
    await expect(page.getByText("newspaper").first()).toBeVisible();
  });

  test("switching levels does not break the page", async ({ page }) => {
    await page.goto("/t/t_20260303_d537a4_07");

    // Get initial article count
    const articles = page.locator("article");
    const initialCount = await articles.count();
    expect(initialCount).toBeGreaterThan(0);

    // Switch to easy
    await page.getByText("eco").first().click();
    const easyCount = await articles.count();
    expect(easyCount).toBe(initialCount);

    // Switch to teen
    await page.getByText("menu_book").first().click();
    const teenCount = await articles.count();
    expect(teenCount).toBe(initialCount);
  });
});
