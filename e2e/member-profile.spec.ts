import { test, expect } from "@playwright/test";

// Navigate to a member profile dynamically via the member list page
async function goToFirstMember(page: import("@playwright/test").Page) {
  await page.goto("/members");
  const firstMemberLink = page.locator('a[href^="/m/"]').first();
  await expect(firstMemberLink).toBeVisible();
  await firstMemberLink.click();
  await page.waitForURL(/\/m\//);
}

test.describe("Member profile page", () => {
  test("displays member info", async ({ page }) => {
    await goToFirstMember(page);
    // The page should show a member name (any heading or bold text)
    await expect(page.locator("h1, h2, [class*='font-bold']").first()).toBeVisible();
  });

  test("follow button toggles", async ({ page }) => {
    await goToFirstMember(page);

    const followBtn = page.getByRole("button", { name: /フォロー/ }).first();
    await followBtn.click();
    await expect(page.getByText("フォロー中").first()).toBeVisible();

    await page.getByRole("button", { name: /フォロー中/ }).first().click();
    await expect(page.getByRole("button", { name: /フォロー/ }).first()).toBeVisible();
  });

  test("has back link", async ({ page }) => {
    await goToFirstMember(page);
    await page.locator('a[href="/"]').first().click();
    await page.waitForURL("/");
    await expect(page.locator('a[href^="/t/"]').first()).toBeVisible();
  });
});
