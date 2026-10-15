import { test, expect } from "@playwright/test";
import { checks, open, close, panel } from "./helpers";
checks();
test("fixture tools preserve all seven algorithms, retained state and exact source IDs", async ({
  page,
}) => {
  await page.goto("/workshop/target?scenario=workshop-tools");
  await expect(
    page.getByRole("heading", { name: "Evening Collection", exact: true }),
  ).toBeVisible();
  await open(page, "Shuffle options");
  await expect(
    page.getByRole("combobox", { name: "Shuffle algorithm" }).locator("option"),
  ).toHaveCount(7);
  await page
    .getByRole("combobox", { name: "Shuffle algorithm" })
    .selectOption("BalancedShuffle");
  await close(page);
  await open(page, "Shuffle options");
  await expect(
    page.getByRole("combobox", { name: "Shuffle algorithm" }),
  ).toHaveValue("BalancedShuffle");
  await close(page);
  await open(page, "Import playlist");
  await panel(page)
    .getByRole("button", { name: "Load Night Signals", exact: true })
    .last()
    .click();
  await expect(
    panel(page).locator('[data-loaded-source="sourceB"]'),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Add missing tracks to draft", exact: true })
    .click();
  await close(page);
  await expect(page.locator("[data-occurrence]")).toHaveCount(4);
});
test("fixture snapshot read error retries same ID then exact restore uses retained queued result", async ({
  page,
}) => {
  await page.goto("/workshop/target?scenario=workshop-tools-snapshot-error");
  await open(page, "Snapshots");
  const card = panel(page).locator('[data-snapshot-id="31"]');
  await card.getByRole("button", { name: "View tracks", exact: true }).click();
  await expect(card.locator(".sh-tools-error")).toContainText(
    "Snapshot tracks could not be loaded.",
  );
  await expect(page.locator('.sr-only[aria-live="assertive"]')).toContainText(
    "Snapshot tracks could not be loaded.",
  );
  await card.getByRole("button", { name: "Hide tracks", exact: true }).click();
  await card
    .getByRole("button", { name: "Retry viewing tracks", exact: true })
    .click();
  await expect(card.locator(".sh-tools-snapshot-tracks li")).toHaveCount(3);
  await card.getByRole("button", { name: "Restore now", exact: true }).click();
  await page
    .getByRole("button", { name: "Restore snapshot now", exact: true })
    .click();
  await expect(
    panel(page)
      .getByRole("region", { name: "Retained operation", exact: true })
      .getByRole("status")
      .first(),
  ).toContainText("Snapshot restored. Undo history confirmed.");
  await close(page);
  await expect(page.locator("[data-occurrence] h3")).toHaveText([
    "First Light",
    "First Light",
    "Night Drive",
  ]);
});
test("fixture unconfirmed restore preserves known operation and unknown provider facts", async ({
  page,
}) => {
  await page.goto("/workshop/target?scenario=workshop-tools-restore-partial");
  await open(page, "Snapshots");
  await panel(page)
    .locator('[data-snapshot-id="31"]')
    .getByRole("button", { name: "Restore now", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Restore snapshot now", exact: true })
    .click();
  await expect(
    panel(page)
      .getByRole("region", { name: "Retained operation", exact: true })
      .getByRole("status")
      .first(),
  ).toContainText("The restore could not be confirmed.");
  await expect(panel(page)).toContainText("3001 · needs attention");
  await expect(
    panel(page).getByText("Confirmed batches", { exact: true }),
  ).toHaveCount(0);
  await close(page);
  await expect(page.locator("[data-occurrence] h3")).toHaveText([
    "First Light",
    "Night Drive",
    "First Light",
  ]);
  await expect(
    page.getByRole("button", { name: "Review & save", exact: true }),
  ).toBeDisabled();
});
