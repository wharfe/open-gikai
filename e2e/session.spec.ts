import { test, expect } from "@playwright/test";

test.describe("Session summary page", () => {
  test("lists committees with links to threads", async ({ page }) => {
    await page.goto("/session/222");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("第222回国会");
    await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible();
    await expect(page.locator('a[href^="/t/"]').first()).toBeVisible();
  });

  test("lists enacted laws with links to their deliberation threads", async ({ page }) => {
    await page.goto("/session/222");
    const section = page.locator("section", {
      has: page.getByRole("heading", { level: 2, name: /成立した法律と審議スレッド/ }),
    });
    await expect(section).toBeVisible();
    await expect(section.locator('a[href^="/t/"]').first()).toBeVisible();
    await expect(section.locator('a[href^="https://www.shugiin.go.jp/"]').first()).toBeVisible();
  });
});
