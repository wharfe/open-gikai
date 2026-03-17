import { test, expect } from "@playwright/test";

const THREAD_URL = "/t/t_20250314_e1491c_02";

test.describe("Navigation", () => {
  test("navigates from feed to thread detail", async ({ page }) => {
    await page.goto("/");
    const firstCard = page.locator('a[href^="/t/"]').first();
    await firstCard.click();
    await expect(page.getByText("件の発言").first()).toBeVisible();
  });

  test("navigates from thread to member profile", async ({ page }) => {
    await page.goto(THREAD_URL);
    const memberLink = page.locator('a[href^="/m/"]').first();
    await memberLink.click();
    await page.waitForURL(/\/m\//);
    await expect(page.locator("article, [class*='font-bold']").first()).toBeVisible();
  });

  test("logo navigates back to feed", async ({ page }) => {
    await page.goto("/about");
    await page.locator('a[href="/"]').first().click();
    await page.waitForURL("/");
    const cards = page.locator('a[href^="/t/"]');
    await expect(cards.first()).toBeVisible();
  });

  test("about page is accessible", async ({ page }) => {
    await page.goto("/about");
    await expect(page.getByText("OpenGIKAIとは")).toBeVisible();
    await expect(page.getByText("AI利用に関する注意事項")).toBeVisible();
  });
});
