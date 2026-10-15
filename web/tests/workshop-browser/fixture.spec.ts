import { test, expect } from "@playwright/test";
for (const [scenario, title] of [
  ["workshop-happy", "Evening Collection"],
  ["workshop-empty", "Evening Collection"],
  ["workshop-long", "Evening Collection"],
  ["workshop-not-found", "Playlist unavailable"],
] as const)
  test(`deterministic ${scenario} scenario`, async ({ page }) => {
    await page.goto(`/workshop/target?scenario=${scenario}`);
    await expect(
      page.getByRole("heading", { name: title, exact: true }),
    ).toBeVisible();
    expect(
      await page
        .locator('script:not([src]),style,[style]:not([style=""])')
        .count(),
    ).toBe(0);
  });
test("one Tools chooser stacks named panels and restores exact keyboard focus", async ({
  page,
}) => {
  await page.goto(
    "/__foundation/workshop-tools/target?scenario=workshop-happy",
  );
  await expect(
    page.getByRole("heading", { name: "Evening Collection", exact: true }),
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const initialActions = await page
    .locator("[data-occurrence]")
    .first()
    .getByRole("button", { name: /^Actions/ })
    .boundingBox();
  const initialFooter = await page
    .locator(".sh-workshop-draft-footer")
    .boundingBox();
  expect(initialActions!.y + initialActions!.height).toBeLessThanOrEqual(
    initialFooter!.y,
  );
  const tools = page.getByRole("button", { name: "Tools", exact: true });
  await tools.focus();
  await tools.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Workshop tools", exact: true }),
  ).toBeVisible();
  const chooser = page.getByRole("button", {
    name: "Algorithm preview",
    exact: true,
  });
  await chooser.focus();
  await chooser.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Algorithm preview", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Algorithm preview", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(chooser).toBeFocused();
  await expect(
    page.getByRole("heading", { name: "Workshop tools", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(tools).toBeFocused();
  expect(
    await page
      .locator('script:not([src]),style,[style]:not([style=""])')
      .count(),
  ).toBe(0);
});
