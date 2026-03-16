import { test, expect } from "@playwright/test";

// Use mobile viewport so MobileHeader with LevelBar is visible
test.use({ viewport: { width: 375, height: 812 } });

test.describe("Level selector", () => {
  test("switches summary text when level changes", async ({ page }) => {
    await page.goto("/t/t1");

    // Default: adult level
    await expect(
      page.getByText("「高リスクAI」の包括的定義は")
    ).toBeVisible();

    // Switch to easy — use visible button (last one since sidebar is hidden on mobile)
    await page.getByRole("button", { name: "🌱" }).last().click();
    await expect(
      page.getByText("「危ないAI」の定義がざっくりすぎて")
    ).toBeVisible();

    // Switch to teen
    await page.getByRole("button", { name: "📖" }).last().click();
    await expect(
      page.getByText("AI規制法案の「高リスクAI」の定義が広すぎて")
    ).toBeVisible();
  });

  test("quote only shows in adult level", async ({ page }) => {
    await page.goto("/t/t1");

    // Adult level: quote visible
    await expect(
      page
        .getByText(
          "EUのAI Actでは用途別リスク分類を採用しているが、なぜ包括的な定義を選んだのか"
        )
        .first()
    ).toBeVisible();

    // Switch to easy: quote hidden
    await page.getByRole("button", { name: "🌱" }).last().click();
    await expect(
      page.getByText(
        "EUのAI Actでは用途別リスク分類を採用しているが、なぜ包括的な定義を選んだのか"
      )
    ).not.toBeVisible();
  });

  test("level persists across page navigation", async ({ page }) => {
    await page.goto("/t/t1");

    // Switch to easy
    await page.getByRole("button", { name: "🌱" }).last().click();
    await expect(
      page.getByText("「危ないAI」の定義がざっくりすぎて")
    ).toBeVisible();

    // Navigate to feed — use visible back link on mobile
    await page.locator('a[href="/"]').last().click();
    await page.getByText("AI規制法案").first().click();

    // Should still be easy level
    await expect(
      page.getByText("「危ないAI」の定義がざっくりすぎて")
    ).toBeVisible();
  });
});
