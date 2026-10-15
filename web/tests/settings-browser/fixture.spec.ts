import { test, expect } from "@playwright/test";
test("deterministic signed-in settings fixture has every algorithm option and saves directly", async ({
  page,
}) => {
  const external: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) external.push(request.url());
  });
  await page.goto("/settings");
  await expect(
    page
      .getByRole("combobox", { name: "Default shuffle algorithm" })
      .locator("option"),
  ).toHaveCount(8);
  await page
    .getByRole("combobox", { name: "Default shuffle algorithm" })
    .selectOption("ArtistSpacingShuffle");
  await page
    .getByRole("spinbutton", { name: "Maximum snapshots per playlist" })
    .fill("20");
  await page
    .getByRole("button", { name: "Save settings", exact: true })
    .click();
  await expect(page.locator(".sh-toast")).toContainText(
    "Settings saved successfully.",
  );
  await expect(
    page.getByRole("combobox", { name: "Default shuffle algorithm" }),
  ).toHaveValue("ArtistSpacingShuffle");
  expect(external).toEqual([]);
});
test("fixture field error retains draft and succeeds only on manual retry", async ({
  page,
}) => {
  await page.goto("/settings?scenario=settings-field-error");
  await page
    .getByRole("spinbutton", { name: "Maximum snapshots per playlist" })
    .fill("30");
  await page
    .getByRole("button", { name: "Save settings", exact: true })
    .click();
  await expect(page.locator("#settings-error")).toBeFocused();
  await expect(
    page.getByText("Choose at most 20 snapshots.", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("spinbutton", { name: "Maximum snapshots per playlist" })
    .fill("20");
  await page
    .getByRole("button", { name: "Save settings", exact: true })
    .click();
  await expect(page.locator(".sh-toast")).toContainText(
    "Settings saved successfully.",
  );
});
test("fixture expired session retains draft across explicit connection check", async ({
  page,
}) => {
  await page.goto("/settings?scenario=settings-auth-loss");
  await page
    .getByRole("spinbutton", { name: "Maximum snapshots per playlist" })
    .fill("15");
  await page
    .getByRole("button", { name: "Save settings", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Connect to Spotify", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Check connection", exact: true })
    .click();
  await expect(
    page.getByRole("spinbutton", { name: "Maximum snapshots per playlist" }),
  ).toHaveValue("15");
  await expect(page.locator(".sh-toast")).toHaveCount(0);
});
test("fixture failure and load-error routes recover without success claims", async ({
  page,
}) => {
  await page.goto("/settings?scenario=settings-load-error");
  await expect(
    page.getByRole("heading", { name: "Settings could not be loaded" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(
    page.getByRole("spinbutton", { name: "Maximum snapshots per playlist" }),
  ).toHaveValue("10");
  await page.goto("/settings?scenario=settings-failure");
  await page
    .getByRole("button", { name: "Save settings", exact: true })
    .click();
  await expect(page.locator("#settings-error")).toContainText(
    "Settings could not be saved.",
  );
  await expect(page.locator(".sh-toast")).toHaveCount(0);
});
