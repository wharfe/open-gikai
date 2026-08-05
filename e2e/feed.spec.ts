import { test, expect } from "@playwright/test";

test.describe("Feed page", () => {
  test("displays thread cards", async ({ page }) => {
    await page.goto("/");

    const cards = page.locator('a[href^="/t/"]');
    await expect(cards.first()).toBeVisible();

    // Assert the card's shape, not a specific meeting name. The feed renders
    // whatever is newest in data/threads/, and that is not always a 委員会: a
    // run of kantei press conferences pushed every committee off the first
    // screen and turned this into a red CI on an unrelated commit.
    //
    // Still content assertions, not just "an element exists": toHaveText(/\S/)
    // rather than not.toBeEmpty() so a card rendering only whitespace or nested
    // empty wrappers fails, and the summary check keeps the original test's
    // real point — that Japanese body text reaches the page at all.
    const firstCard = cards.first();
    await expect(firstCard.locator("div").first()).toHaveText(/\S/);
    await expect(
      firstCard.getByText(/^\d{4}[./-]\d{2}[./-]\d{2}$/).first()
    ).toBeVisible();
    await expect(firstCard.locator("p").first()).toHaveText(/[ぁ-んァ-ン一-龯]/);
  });

  test("displays header with OpenGIKAI branding", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("OpenGIK").first()).toBeVisible();
  });

  test("switches between all and following tabs", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByText("すべて")).toBeVisible();

    await page.getByText(/フォロー中/).click();

    await expect(
      page.getByText("発言者をフォローすると")
    ).toBeVisible();

    await page.getByText("すべて").click();
    const cards = page.locator('a[href^="/t/"]');
    await expect(cards.first()).toBeVisible();
  });

  test("shows trend panel on desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 768 });
    await page.goto("/");
    await expect(page.getByText("トレンド").first()).toBeVisible();
  });

  test("shows theme filter chips and filters threads", async ({ page }) => {
    await page.goto("/");

    // Theme bar should be visible with at least one chip
    const themeChip = page.locator("button").filter({ hasText: /税金|外交|少子|雇用|教育|憲法|防災|社会/ }).first();
    await expect(themeChip).toBeVisible();

    // Click theme chip to filter
    const chipText = await themeChip.textContent();
    await themeChip.click();

    // Filter banner should appear
    await expect(page.getByText("テーマで絞り込み中")).toBeVisible();

    // Click clear to remove filter
    await page.getByText("解除").first().click();
    await expect(page.getByText("テーマで絞り込み中")).not.toBeVisible();
  });

  test("theme panel links filter feed on desktop", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 768 });
    await page.goto("/");

    // Theme panel should be in sidebar
    const themePanel = page.getByText("テーマ").first();
    await expect(themePanel).toBeVisible();

    // Click a theme link in sidebar
    const themeLink = page.locator('a[href^="/?theme="]').first();
    if (await themeLink.isVisible()) {
      await themeLink.click();
      await expect(page.getByText("テーマで絞り込み中")).toBeVisible();
    }
  });

  test("debate highlights display on thread cards", async ({ page }) => {
    await page.goto("/");

    // At least one thread should have a debate highlight with ↔
    const debate = page.getByText("↔").first();
    // This is conditional - only if data has debates
    const count = await debate.count();
    if (count > 0) {
      await expect(debate).toBeVisible();
    }
  });

  test("invalid theme param is ignored", async ({ page }) => {
    await page.goto("/?theme=invalid");

    // Should show threads normally, no filter banner
    await expect(page.getByText("テーマで絞り込み中")).not.toBeVisible();
    const cards = page.locator('a[href^="/t/"]');
    await expect(cards.first()).toBeVisible();
  });
});
