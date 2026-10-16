import { test, expect } from "@playwright/test";
test("opt-in panel position survives close/reopen and stays separate for another actor", async ({
  page,
}) => {
  await page.goto("/__foundation/task-scroll");
  await page.getByRole("button", { name: "Open alice history" }).click();
  const body = page.locator(".sh-workspace > .sh-task-panel:not([hidden]) > .sh-panel-body");
  const narrow = (page.viewportSize()?.width ?? 1440) < 1024;
  const position = () =>
    narrow
      ? page.evaluate(() => window.scrollY)
      : body.evaluate((el) => el.scrollTop);
  if (narrow)
    await page.evaluate(() =>
      window.scrollTo({ top: 440, behavior: "instant" }),
    );
  else
    await body.evaluate((el) => {
      el.scrollTop = 440;
      el.dispatchEvent(new Event("scroll", { bubbles: true }));
    });
  await expect.poll(position).toBe(440);
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Open alice history" }),
  ).toBeFocused();
  await page.getByRole("button", { name: "Open alice history" }).click();
  await expect.poll(position).toBe(440);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Open bob history" }).click();
  await expect.poll(position).toBe(0);
});
