import { test, expect } from "@playwright/test";
test("recorded routines fixture stays local and preserves exact-ID filters", async ({
  page,
}) => {
  const network: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) network.push(request.url());
  });
  await page.goto("/routines");
  await expect(page.locator(".sh-routine-card")).toHaveCount(3);
  await page.getByLabel("State", { exact: true }).selectOption("paused");
  await expect(page.locator(".sh-routine-card")).toHaveCount(1);
  expect(network).toEqual([]);
});
test("fixture empty and failure branches remain deliberate and retry through reads", async ({
  page,
}) => {
  await page.goto("/routines?scenario=routines-empty");
  await expect(
    page.getByRole("heading", { name: "No routines yet" }),
  ).toBeVisible();
  await page.goto("/routines?scenario=routines-failure");
  await expect(
    page.getByText("Routines are temporarily unavailable."),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Reload routines", exact: true })
    .click();
  await expect(page.locator(".sh-routine-card")).toHaveCount(3);
});
