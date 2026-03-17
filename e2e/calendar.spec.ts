import { test, expect } from "@playwright/test";

test.describe("Calendar page", () => {
  test("displays calendar grid", async ({ page }) => {
    await page.goto("/calendar");
    await expect(page.getByText("カレンダー").first()).toBeVisible();
    // Month/year header
    await expect(page.locator("text=/\\d{4}年\\d{1,2}月/")).toBeVisible();
    // Navigation arrows
    await expect(page.locator("button:has-text('←')").first()).toBeVisible();
    await expect(page.locator("button:has-text('→')").first()).toBeVisible();
  });

  test("shows thread count on dates with data", async ({ page }) => {
    await page.goto("/calendar");
    // Navigate to March 2025 which has data
    // Click prev until we reach it, or just check current view has some "件" labels
    await expect(page.getByText("件").first()).toBeVisible();
  });

  test("clicking a date shows committee list", async ({ page }) => {
    await page.goto("/calendar");
    // Click on a date that has data (look for a "件" badge and click its parent)
    const dayWithData = page.locator("button:has-text('件')").first();
    await dayWithData.click();
    // Should show committee details
    await expect(page.getByText("の委員会").first()).toBeVisible();
    await expect(page.getByText("スレッド →").first()).toBeVisible();
  });

  test("month navigation works", async ({ page }) => {
    await page.goto("/calendar");
    const monthLabel = page.locator("text=/\\d{4}年\\d{1,2}月/");
    const initialMonth = await monthLabel.textContent();
    // Click prev
    await page.locator("button:has-text('←')").first().click();
    const prevMonth = await monthLabel.textContent();
    expect(prevMonth).not.toBe(initialMonth);
    // Click next twice to go forward
    await page.locator("button:has-text('→')").first().click();
    await page.locator("button:has-text('→')").first().click();
    const nextMonth = await monthLabel.textContent();
    expect(nextMonth).not.toBe(initialMonth);
  });
});
