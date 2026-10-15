import fs from "node:fs";
import path from "node:path";
import { test, expect, type Page } from "@playwright/test";

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

// --- Speech list, checked against data/ (#104) ---
//
// The profile page is built from data/threads/, so these tests read the same
// files and derive what one member's page must show. The member is picked
// from the data rather than hard-coded, because the data changes every day —
// which also means out/ must be built from this checkout's data/ (CI builds
// both from one checkout; a stale local out/ fails these on counts).

type Level = "easy" | "teen" | "adult";
type DataSpeech = { memberId: string; tension: string; quote: string; raw: string; summaries: Record<string, string> };
type DataThread = {
  id: string;
  date: string;
  committee: string;
  topic: string;
  summary: string;
  context?: { description?: string };
  speeches: DataSpeech[];
};
type Row = { thread: DataThread; speech: DataSpeech };

const LEVELS = ["easy", "teen", "adult"] as const;
const LEVEL_ICON: Record<Level, string> = { easy: "eco", teen: "menu_book", adult: "newspaper" };

// Same order as data.ts getThreads(): files in name order, then newest date
// first (Array.prototype.sort is stable, so same-date threads keep file order).
function loadThreads(): DataThread[] {
  const dir = path.join(process.cwd(), "data", "threads");
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".json") && !f.endsWith(".progress.json"))
    .sort()
    .flatMap((f) => {
      const d: unknown = JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8"));
      return Array.isArray(d) ? (d as DataThread[]) : [];
    })
    .sort((a, b) => b.date.localeCompare(a.date));
}

const hasAllLevels = (s: DataSpeech) => LEVELS.every((l) => typeof s.summaries[l] === "string" && s.summaries[l] !== "");
const levelsDiffer = ({ summaries: m }: DataSpeech) => m.easy !== m.teen && m.teen !== m.adult && m.easy !== m.adult;

// The smallest member whose page exercises everything the list does: two or
// more threads, a thread with two of their own speeches, two tension types,
// other speakers in their threads, and a speech whose three levels differ.
let picked: { id: string; rows: Row[]; threads: DataThread[] } | undefined;
function pickMember() {
  if (picked) return picked;
  const members = JSON.parse(fs.readFileSync(path.join(process.cwd(), "data", "members.json"), "utf-8"));
  const threads = loadThreads();
  const rowsBy = new Map<string, Row[]>();
  for (const thread of threads) {
    for (const speech of thread.speeches) {
      const rows = rowsBy.get(speech.memberId) ?? [];
      rows.push({ thread, speech });
      rowsBy.set(speech.memberId, rows);
    }
  }
  const candidates = [...rowsBy].filter(([id, rows]) => {
    if (members[id]?.id !== id) return false;
    const ids = rows.map((r) => r.thread.id);
    return (
      new Set(ids).size >= 2 &&
      ids.some((t, i) => ids.indexOf(t) !== i) &&
      new Set(rows.map((r) => r.speech.tension)).size >= 2 &&
      rows.some((r) => r.thread.speeches.some((s) => s.memberId !== id)) &&
      rows.every((r) => hasAllLevels(r.speech)) &&
      rows.some((r) => levelsDiffer(r.speech))
    );
  });
  candidates.sort((a, b) => a[1].length - b[1].length || a[0].localeCompare(b[0]));
  if (candidates.length === 0) throw new Error("no member in data/ exercises the speech list");
  const [id, rows] = candidates[0];
  picked = { id, rows, threads: [...new Set(rows.map((r) => r.thread))] };
  return picked;
}

// A level button is a no-op until the page hydrates, so retry the click.
// Selecting "easy" (not the server-rendered default) therefore also serves as
// the wait for hydration before any other click.
async function selectLevel(page: Page, level: Level) {
  const button = page.locator("button:visible").filter({ hasText: new RegExp(`^${LEVEL_ICON[level]}$`) }).first();
  await expect(async () => {
    await button.click();
    expect(await button.evaluate((el) => getComputedStyle(el).fontWeight)).toBe("700");
  }).toPass();
}

// Uncaught errors and React hydration complaints, collected for the whole
// test. out/ is a production build, where React reports a hydration mismatch
// only as a minified error code (#418, #423, #425 …), not by name.
function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && /hydrat|Minified React error|react\.dev\/errors\//i.test(m.text())) errors.push(m.text());
  });
  return errors;
}

async function expectSpeechList(page: Page, rows: Row[]) {
  await expect(page.getByText(`${rows.length}件の発言`, { exact: true })).toBeVisible();
  const items = page.locator('main a[href^="/t/"]');
  await expect(items).toHaveCount(rows.length);
  expect(await items.evaluateAll((els) => els.map((el) => el.getAttribute("href")))).toEqual(
    rows.map((r) => `/t/${r.thread.id}`),
  );
  // Whole-text matches: "追及" must not pass for "再追及", nor 3 for 13.
  for (const [i, r] of rows.entries()) {
    const chips = items.nth(i).locator("span.rounded-full");
    await expect(items.nth(i).locator("div").first()).toHaveText(`${r.thread.committee}·${r.thread.date}`);
    await expect(chips.nth(0)).toHaveText(new RegExp(`^\\S+ ${r.speech.tension}$`));
    await expect(chips.nth(1)).toHaveText(r.thread.topic);
  }
  for (const level of LEVELS) {
    await selectLevel(page, level);
    await expect(items.locator("p")).toHaveText(rows.map((r) => r.speech.summaries[level]));
  }
}

test.describe("Member profile speech list", () => {
  test("lists the member's own speeches in order at every level on a direct load", async ({ page }) => {
    const { id, rows } = pickMember();
    const errors = watchErrors(page);
    await page.goto(`/m/${id}`);
    await expectSpeechList(page, rows);

    await page.locator('main a[href^="/t/"]').first().click();
    await page.waitForURL(`**/t/${rows[0].thread.id}`);
    expect(errors).toEqual([]);
  });

  // Client-side navigation renders the page from its RSC payload files, not
  // from the HTML, so it is a separate path to the same list.
  test("lists the same speeches after client-side navigation from a thread", async ({ page }) => {
    const { id, rows } = pickMember();
    const errors = watchErrors(page);
    await page.goto(`/t/${rows[0].thread.id}`);
    // Before hydration the link is a plain <a> and the click a full load.
    await selectLevel(page, "easy");
    await page.evaluate(() => Object.assign(window, { __noFullReload: true }));
    await page.locator(`main a[href="/m/${id}"]`).first().click();
    await page.waitForURL(`**/m/${id}`);
    expect(await page.evaluate(() => "__noFullReload" in window)).toBe(true);
    await expectSpeechList(page, rows);
    expect(errors).toEqual([]);
  });

  test("follows this member and remembers it", async ({ page }) => {
    const { id } = pickMember();
    await page.goto(`/m/${id}`);
    const main = page.locator("main");
    const follow = main.getByRole("button", { name: /^add フォロー$/ });
    const following = main.getByRole("button", { name: /^check フォロー中$/ });
    const stored = () => page.evaluate(() => JSON.parse(localStorage.getItem("gikai-follows") ?? "[]"));
    await selectLevel(page, "easy");
    await follow.click();
    await expect(following).toBeVisible();
    // Persisted by an effect after the re-render, so poll rather than read once.
    await expect.poll(stored).toEqual([id]);

    // Restored from storage by an effect, i.e. only once the page is hydrated.
    await page.reload();
    await expect(following).toBeVisible();
    await following.click();
    await expect(follow).toBeVisible();
    await expect.poll(stored).toEqual([]);
  });

  // The page used to carry every thread it appears in whole — every other
  // speaker's speeches, the full transcripts, the thread summaries — though it
  // shows only this member's summaries and the threads' headings (#104).
  // Probe the delivered HTML for text that must and must not be there. The
  // RSC payload is inlined as self.__next_f.push([1,"…"]) string chunks that
  // can split anywhere, so the chunks are decoded and joined before probing.
  // Probes are runs of characters neither HTML nor JSON escapes, so finding or
  // not finding one is meaningful.
  test("delivers only this member's own speeches", async ({ request }) => {
    const { id, rows, threads } = pickMember();
    const html = await (await request.get(`/m/${id}`)).text();
    const flight = [...html.matchAll(/self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)/g)]
      .map((m) => JSON.parse(m[1]) as string)
      .join("");
    expect(flight.length).toBeGreaterThan(0);
    const delivered = `${html}\n${flight}`;

    const runs = (s: string | undefined) =>
      (s?.match(/[^\s"'\\<>&/]{16,}/g) ?? []).sort((a, b) => b.length - a.length).map((r) => r.slice(0, 24));
    const own = JSON.stringify(rows.map((r) => [r.speech.summaries, r.thread.topic, r.thread.committee]));

    // Positive control: every level of the member's own summaries is there.
    const present = rows.flatMap((r) => LEVELS.map((l) => runs(r.speech.summaries[l])[0])).filter((p): p is string => !!p);
    expect(present.length).toBeGreaterThan(0);
    expect(present.filter((p) => !delivered.includes(p)), "the member's own summaries").toEqual([]);

    // Each kind of text must yield probes of its own (a probe that also occurs
    // in the member's own text proves nothing, so the next run is tried), and
    // each kind is reported separately.
    const mustBeAbsent = {
      "another speaker's summary": threads.flatMap((t) =>
        t.speeches.filter((s) => s.memberId !== id).flatMap((s) => Object.values(s.summaries)),
      ),
      "a thread summary or context": threads.flatMap((t) => [t.summary, t.context?.description]),
      "this member's raw transcript or quote": rows.flatMap((r) => [r.speech.raw, r.speech.quote]),
    };
    for (const [kind, texts] of Object.entries(mustBeAbsent)) {
      const probes = texts.map((t) => runs(t).find((p) => !own.includes(p))).filter((p): p is string => !!p);
      expect.soft(probes.length, kind).toBeGreaterThan(0);
      expect.soft(probes.filter((p) => delivered.includes(p)), kind).toEqual([]);
    }
  });
});
