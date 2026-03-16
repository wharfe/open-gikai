import { test, expect } from "@playwright/test";

test.describe("Member profile page", () => {
  test("displays member info", async ({ page }) => {
    await page.goto("/m/yamamoto");

    await expect(
      page.getByRole("heading", { name: "山本 和義" })
    ).toBeVisible();
    await expect(page.getByText("立憲")).toBeVisible();
    await expect(page.getByText("東京12区")).toBeVisible();
    await expect(page.getByText("2009年〜")).toBeVisible();
  });

  test("shows member bio and stance tags", async ({ page }) => {
    await page.goto("/m/yamamoto");

    await expect(
      page.getByText("元ITエンジニア出身")
    ).toBeVisible();
    await expect(page.getByText("デジタル規制")).toBeVisible();
    await expect(page.getByText("プライバシー保護")).toBeVisible();
  });

  test("displays speech history", async ({ page }) => {
    await page.goto("/m/yamamoto");
    await expect(page.getByText("件の発言")).toBeVisible();
  });

  test("follow button toggles", async ({ page }) => {
    await page.goto("/m/yamamoto");

    // Should show follow button
    const followBtn = page.getByRole("button", { name: "+ フォロー" });
    await expect(followBtn).toBeVisible();

    // Click to follow
    await followBtn.click();
    await expect(
      page.getByRole("button", { name: "フォロー中 ✓" })
    ).toBeVisible();

    // Click to unfollow
    await page.getByRole("button", { name: "フォロー中 ✓" }).click();
    await expect(
      page.getByRole("button", { name: "+ フォロー" })
    ).toBeVisible();
  });

  test("shows minister badge for cabinet members", async ({ page }) => {
    await page.goto("/m/tanaka");

    await expect(page.getByText("🔷").first()).toBeVisible();
    await expect(page.getByText("経済産業大臣")).toBeVisible();
  });

  test("has back link", async ({ page }) => {
    await page.goto("/m/yamamoto");
    await page.getByText("← 戻る").click();
    await expect(page).toHaveURL("/");
  });
});
