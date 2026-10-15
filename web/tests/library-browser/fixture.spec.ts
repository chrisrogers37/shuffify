import { test, expect } from "@playwright/test";
test("fixture transport covers Library navigation and preference saves without API network fallback", async ({
  page,
}) => {
  const calls: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/")) calls.push(r.url());
  });
  await page.goto("/library");
  await expect(
    page.locator('[data-section="playlists"]>.sh-library-row'),
  ).toHaveCount(3);
  await page.getByRole("button", { name: "Organize", exact: true }).click();
  await page
    .locator('[data-playlist-id="bob-mix"]')
    .getByRole("button", {
      name: "Add favorite Weekend discoveries",
      exact: true,
    })
    .click();
  await expect(
    page.locator('[data-section="favorites"]>.sh-library-row'),
  ).toHaveCount(2);
  expect(calls).toEqual([]);
});
test("unknown admission scenario preserves exact request and recovers its one operation explicitly", async ({
  page,
}) => {
  await page.goto("/library?scenario=library-unknown");
  const row = page.locator('[data-playlist-id="alice-mix"]');
  await row.locator(">details>summary").click();
  await row
    .getByRole("button", { name: "Shuffle now Evening rotation", exact: true })
    .click();
  const panel = page.locator(".sh-task-panel:visible");
  await panel.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await panel
    .getByRole("button", { name: "Retry this exact request", exact: true })
    .click();
  await expect(
    panel.getByRole("heading", { name: "Operation #101", exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByText("The playlist change was verified.", { exact: true }),
  ).toBeVisible();
});

test("loading, failed read and empty fixture states stay deterministic without network fallback", async ({
  page,
}) => {
  await page.goto("/library?scenario=library-loading");
  await expect(
    page.getByRole("heading", { name: "Loading your playlists…", exact: true }),
  ).toBeVisible();
  await expect(page.locator('[data-playlist-id="alice-mix"]')).toBeVisible();
  await page.goto("/library?scenario=library-read-failure");
  await expect(
    page.getByText("Playlists could not be read.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.locator('[data-playlist-id="alice-mix"]')).toBeVisible();
  await page.goto("/library?scenario=library-empty");
  await expect(
    page.getByRole("heading", { name: "No playlists yet", exact: true }),
  ).toBeVisible();
});
