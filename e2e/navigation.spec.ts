import { test, expect } from "@playwright/test";

test.describe("Navigation", () => {
  test("navigates from feed to thread detail", async ({ page }) => {
    await page.goto("/");

    // Click on a thread
    await page.getByText("AI規制法案").first().click();
    await expect(page).toHaveURL("/t/t1");
    await expect(
      page.getByRole("heading", { name: "AI規制法案" })
    ).toBeVisible();
  });

  test("navigates from thread to member profile", async ({ page }) => {
    await page.goto("/t/t1");

    // Click on member avatar link
    await page.locator('a[href="/m/yamamoto"]').first().click();
    await expect(page).toHaveURL("/m/yamamoto");
  });

  test("logo navigates back to feed", async ({ page }) => {
    await page.goto("/t/t1");
    await page.getByText("GIKAI").click();
    await expect(page).toHaveURL("/");
  });

  test("handles 404 for invalid thread", async ({ page }) => {
    const response = await page.goto("/t/nonexistent");
    expect(response?.status()).toBe(404);
  });

  test("handles 404 for invalid member", async ({ page }) => {
    const response = await page.goto("/m/nonexistent");
    expect(response?.status()).toBe(404);
  });
});
