import { test, expect } from "@playwright/test";

test.describe("Thread detail page", () => {
  test("displays thread with speeches", async ({ page }) => {
    await page.goto("/t/t1");

    // Thread header — now in sticky bar
    await expect(page.getByText("AI規制法案").first()).toBeVisible();
    await expect(page.getByText("経済産業委員会")).toBeVisible();

    // Should show speakers
    await expect(page.getByText("山本 和義").first()).toBeVisible();
    await expect(page.getByText("田中 誠一").first()).toBeVisible();
  });

  test("shows tension badges", async ({ page }) => {
    await page.goto("/t/t1");

    await expect(page.getByText("⚡ 追及").first()).toBeVisible();
    await expect(page.getByText("💬 答弁").first()).toBeVisible();
  });

  test("expands raw transcript on click", async ({ page }) => {
    await page.goto("/t/t1");

    // Raw transcript should be hidden initially
    const rawText = "AI規制法案第三条における";
    await expect(page.getByText(rawText)).not.toBeVisible();

    // Click expand button (now "📄 原文")
    await page.getByText("📄 原文").first().click();

    // Raw transcript should now be visible
    await expect(page.getByText(rawText)).toBeVisible();
  });

  test("has back link to feed", async ({ page }) => {
    await page.goto("/t/t1");

    // Back button is now "←" in the sticky header
    await page.locator('a[href="/"]').first().click();
    await expect(page).toHaveURL("/");
  });

  test("shows source attribution", async ({ page }) => {
    await page.goto("/t/t1");
    await expect(
      page.getByText("出典：国会会議録検索システム（NDL）")
    ).toBeVisible();
  });
});
