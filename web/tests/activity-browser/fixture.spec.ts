import { test, expect } from "@playwright/test";
test("recorded activity fixtures render exact bounded results with no API network fallback", async ({
  page,
}) => {
  const requests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) requests.push(request.url());
  });
  await page.goto("/activity");
  await expect(page.locator(".sh-activity-run")).toHaveCount(7);
  await page
    .getByRole("combobox", { name: "Show runs", exact: true })
    .selectOption("unknown");
  await expect(page.locator("#run-3")).toBeVisible();
  await page.locator("#run-3 summary").click();
  await expect(page.locator("#run-3")).toContainText(
    "Run history could not be verified.",
  );
  expect(requests).toEqual([]);
});
test("maximum-size fixture preserves loaded bounds while filters remain local and keyboard operated", async ({
  page,
}) => {
  await page.goto("/activity?scenario=activity-large");
  await expect(page.locator(".sh-activity-run")).toHaveCount(20);
  await expect(page.locator(".sh-activity-event")).toHaveCount(100);
  const events = page.getByRole("combobox", {
    name: "Show events",
    exact: true,
  });
  await events.focus();
  // Native select type-ahead works across the host Chromium platform.
  await page.keyboard.press("m");
  await page.keyboard.press("Enter");
  await expect(page.locator(".sh-activity-event:visible")).toHaveCount(25);
  await expect(page.locator("#activity-event-count")).toHaveText(
    "25 of 100 loaded events shown",
  );
});
test("empty fixture retains honest zero totals and empty history", async ({
  page,
}) => {
  await page.goto("/activity?scenario=activity-empty");
  await expect(
    page.getByText("No routine runs yet.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("No app activity yet.", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".sh-activity-run")).toHaveCount(0);
});
test("failure and rate-limit fixture scenarios recover through explicit retry", async ({
  page,
}) => {
  await page.goto("/activity?scenario=activity-failure");
  await expect(
    page.getByRole("heading", { name: "Activity could not be loaded" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.locator(".sh-activity-run")).toHaveCount(7);
  await page.goto("/activity?scenario=activity-rate-limit");
  await expect(
    page.getByText("Try again in 2 seconds.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Try again in/)).toHaveCount(0, {
    timeout: 4000,
  });
  await expect(page.locator(".sh-activity-run")).toHaveCount(0);
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.locator(".sh-activity-run")).toHaveCount(7);
});
