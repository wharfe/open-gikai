import { test, expect } from "@playwright/test";

// Navigate to a known member from the real data
const MEMBER_URL = "/m/yamashitayuuhei";

test.describe("Member profile page", () => {
  test("displays member info", async ({ page }) => {
    await page.goto(MEMBER_URL);
    await expect(page.getByText("山下雄平").first()).toBeVisible();
  });

  test("follow button toggles", async ({ page }) => {
    await page.goto(MEMBER_URL);

    const followBtn = page.getByRole("button", { name: /フォロー/ }).first();
    await followBtn.click();
    await expect(page.getByText("フォロー中 ✓").first()).toBeVisible();

    await page.getByRole("button", { name: "フォロー中 ✓" }).first().click();
    await expect(page.getByRole("button", { name: /\+ フォロー/ }).first()).toBeVisible();
  });

  test("has back link", async ({ page }) => {
    await page.goto(MEMBER_URL);
    await page.locator('a[href="/"]').first().click();
    await page.waitForURL("/");
    await expect(page.locator('a[href^="/t/"]').first()).toBeVisible();
  });
});
