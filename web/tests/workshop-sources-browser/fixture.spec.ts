import { test, expect } from "@playwright/test";
import { checks, open, close, panel } from "./helpers";
checks();
test("source fixture panels load through production routes with retained staging and task ancestry", async ({
  page,
}) => {
  await page.goto("/workshop/target?scenario=workshop-sources");
  await expect(
    page.getByRole("heading", { name: "Evening Collection", exact: true }),
  ).toBeVisible();
  await open(page, "Inbox");
  await panel(page)
    .locator('[data-pending-id="11"]')
    .getByRole("button", { name: "Add to draft", exact: true })
    .click();
  await close(page);
  await open(page, "Archive");
  await expect(panel(page).locator("[data-archive-row]")).toHaveCount(3);
  await panel(page)
    .getByRole("button", { name: "Archive setup", exact: true })
    .click();
  await expect(
    panel(page).getByRole("heading", {
      level: 2,
      name: "Archive setup",
      exact: true,
    }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    panel(page).getByRole("button", { name: "Archive setup", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(
    panel(page).getByRole("heading", { level: 2, name: "Workshop tools", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Tools", exact: true }),
  ).toBeFocused();
  await expect(page.locator("[data-occurrence]")).toHaveCount(4);
});
test("fixture source failure preserves saved target and explicit reviewed cleanup across panel closure", async ({
  page,
}) => {
  await page.goto("/workshop/target?scenario=workshop-sources-finalize-failed");
  await open(page, "Inbox");
  await panel(page)
    .locator('[data-pending-id="11"]')
    .getByRole("button", { name: "Add to draft", exact: true })
    .click();
  await close(page);
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await close(page);
  await open(page, "Inbox");
  await expect(
    panel(page).getByRole("button", {
      name: "Review failed cleanup again",
      exact: true,
    }),
  ).toBeVisible();
  await close(page);
  await open(page, "Inbox");
  await expect(
    panel(page).getByRole("button", {
      name: "Review failed cleanup again",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    panel(page)
      .getByText("Source refused the change; target save remains confirmed.", {
        exact: true,
      })
      .first(),
  ).toBeVisible();
});
test("fixture creator uncertainty never adopts a matching name from refresh", async ({
  page,
}) => {
  await page.goto(
    "/workshop/target?scenario=workshop-sources-create-unknown&setup=archive",
  );
  await expect(
    panel(page).getByRole("heading", {
      level: 2,
      name: "Archive setup",
      exact: true,
    }),
  ).toBeVisible();
  await panel(page)
    .getByRole("button", { name: "Create archive playlist now", exact: true })
    .click();
  await expect(
    panel(page).getByRole("button", {
      name: "Acknowledge unidentified creation",
      exact: true,
    }),
  ).toBeVisible();
  await panel(page)
    .getByRole("button", { name: "Refresh owned playlists", exact: true })
    .click();
  await expect(
    panel(page).getByText("No archive linked.", { exact: true }),
  ).toBeVisible();
  await expect(panel(page).getByRole("combobox").locator("option")).toHaveCount(
    3,
  );
  await expect(panel(page).getByRole("combobox")).toHaveValue("");
});
