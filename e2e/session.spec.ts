import { test, expect } from "@playwright/test";

test.describe("Session summary page", () => {
  test("lists committees with links to threads", async ({ page }) => {
    await page.goto("/session/222");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("第222回国会");
    await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible();
    await expect(page.locator('a[href^="/t/"]').first()).toBeVisible();
  });
});
