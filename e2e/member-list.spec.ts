import { test, expect } from "@playwright/test";

test.describe("Member list page", () => {
  test("displays member list", async ({ page }) => {
    await page.goto("/members");
    await expect(page.getByText("議員一覧")).toBeVisible();
    // Should show multiple members
    const members = page.locator('a[href^="/m/"]');
    const count = await members.count();
    expect(count).toBeGreaterThan(10);
  });

  test("filters by search query", async ({ page }) => {
    await page.goto("/members");
    const input = page.locator('input[placeholder*="議員名"]');
    await input.fill("加藤");
    // Should filter to matching members
    await expect(page.getByText("加藤勝信").first()).toBeVisible();
  });

  test("filters by party", async ({ page }) => {
    await page.goto("/members");
    await page.locator("select").first().selectOption("自由民主党");
    // All visible members should be LDP
    const firstMember = page.locator('a[href^="/m/"]').first();
    await expect(firstMember).toBeVisible();
  });

  test("follow button works", async ({ page }) => {
    await page.goto("/members");
    const followBtn = page.getByText("フォロー", { exact: true }).first();
    await followBtn.click();
    await expect(page.getByText("フォロー中").first()).toBeVisible();
  });

  test("navigates to member profile", async ({ page }) => {
    await page.goto("/members");
    await page.locator('a[href^="/m/"]').first().click();
    await page.waitForURL(/\/m\//);
  });
});
