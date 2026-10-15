import { test, expect } from "@playwright/test";
test("a retained draft handoff does not show an empty stale confirmation", async ({
  page,
}) => {
  await page.goto("/__foundation/guard-handoff");
  await page
    .getByRole("button", { name: "Retain draft and continue", exact: true })
    .click();
  await expect(page).toHaveURL(/\/__foundation\/public$/u);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
test("a real unsaved draft still asks before leaving and Stay retains it", async ({
  page,
}) => {
  await page.goto("/__foundation/guard-handoff");
  await page.getByRole("link", { name: "Leave unsaved", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Leave this page?" });
  await expect(dialog).toContainText("Keep this fixture draft.");
  await dialog.getByRole("button", { name: "Stay", exact: true }).click();
  await expect(page).toHaveURL(/\/__foundation\/guard-handoff$/u);
});
