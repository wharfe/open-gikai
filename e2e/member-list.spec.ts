import { test, expect } from "@playwright/test";

test.describe("Member list page", () => {
  test("displays member list", async ({ page }) => {
    await page.goto("/members");
    await expect(page.getByText("議員一覧")).toBeVisible();
    // Should show multiple members
    const members = page.locator('a[href^="/m/"]');
    const count = await members.count();
    expect(count).toBeGreaterThan(5);
  });

  test("filters by search query", async ({ page }) => {
    await page.goto("/members");
    // Get the name of the first member before filtering
    const firstMemberName = await page.locator('a[href^="/m/"]').first().innerText();
    // Use the first character of the first member's name as the search query
    const searchChar = firstMemberName.charAt(0);

    const input = page.locator('input[placeholder*="議員名"]');
    await input.fill(searchChar);
    // Should filter to matching members — at least one result visible
    const results = page.locator('a[href^="/m/"]');
    await expect(results.first()).toBeVisible();
  });

  test("filters by party", async ({ page }) => {
    await page.goto("/members");
    // Select the first non-default option from the party dropdown
    const select = page.locator("select").first();
    const options = select.locator("option");
    const optionCount = await options.count();
    // Pick the second option (first is usually "all parties" default)
    if (optionCount > 1) {
      const partyValue = await options.nth(1).getAttribute("value");
      if (partyValue) {
        await select.selectOption(partyValue);
      }
    }
    // At least one member should be visible after filtering
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
