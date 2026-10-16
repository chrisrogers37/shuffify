import { mkdir, writeFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import session from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import { workshopContext } from "../../src/fixtures/workshop";
import { createLibraryFixtures } from "../../src/fixtures/library";
import type { Operation } from "../../src/api/contracts";
const problems = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  problems.set(page, errors);
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (
      /content security policy|refused to (?:execute|apply|load)/iu.test(
        m.text(),
      )
    )
      errors.push(m.text());
  });
});
const logs = new WeakMap<
  Page,
  { path: string; key: string | null; body: unknown }[]
>();
async function backend(
  page: Page,
  mode: "happy" | "target-503" | "source-503",
) {
  const calls: { path: string; key: string | null; body: unknown }[] = [],
    library = createLibraryFixtures("happy");
  logs.set(page, calls);
  let target = 0,
    source = 0;
  const order = workshopContext().tracks.map((x) => x.uri);
  const op = (id: number, state: Operation["state"] = "queued"): Operation => ({
    id,
    kind: id === 801 ? "workshop_commit" : "workshop_finalize_raids",
    state,
    targets: id === 801 ? ["target"] : ["target", "collection"],
    started_at: null,
    completed_at: null,
    progress: {},
    result:
      state === "succeeded"
        ? id === 801
          ? {
              success: true,
              target_status: "saved",
              saved_track_uris: order,
              confirmed_batches: 1,
              total_batches: 1,
              message: "Target order verified.",
            }
          : {
              success: true,
              status: "complete",
              aggregate_status: "complete",
              resource: "collection",
              resource_name: "Holding room",
              source_status: "complete",
              database_status: "complete",
              affected_count: 1,
              promoted_count: 1,
              message: "Source work verified.",
            }
        : null,
    recovery: { available: false, action: null, reason: null },
  });
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = url.pathname;
    const send = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    if (req.method() === "POST") {
      expect(req.headers()["x-csrftoken"]).toBe(session.csrf_token);
      calls.push({
        path,
        key: req.headers()["idempotency-key"] ?? null,
        body: req.postDataJSON(),
      });
      if (path.endsWith("/commit")) {
        target++;
        if (mode === "target-503" && target === 1)
          return send(
            {
              success: false,
              error_code: "upstream",
              category: "error",
              message: "Target admission reply unavailable.",
            },
            503,
          );
        return send(
          { success: true, operation_id: 801, operation: op(801) },
          202,
        );
      }
      if (path.endsWith("/pending-raids/finalize")) {
        source++;
        if (mode === "source-503" && source === 1)
          return send(
            {
              success: false,
              error_code: "upstream",
              category: "error",
              message: "Source admission reply unavailable.",
            },
            503,
          );
        return send(
          { success: true, operation_id: 802, operation: op(802) },
          202,
        );
      }
    }
    if (path === "/api/session") return send(session);
    if (path === "/api/workshop/target") return send(workshopContext());
    if (path.endsWith("/source-context"))
      return send({
        success: true,
        target: { id: "target", name: "Evening Collection" },
        raid: { id: "collection", name: "Holding room" },
        archive: null,
      });
    if (path.startsWith("/api/operations/"))
      return send({
        success: true,
        operation: op(Number(path.split("/").pop()), "succeeded"),
      });
    const fixture = await library(path, req.method(), {});
    if (fixture)
      return route.fulfill({
        status: fixture.status,
        contentType: "application/json",
        body: (await fixture.text()).replaceAll("alice-mix", "target"),
      });
    return send(
      {
        success: false,
        error_code: "not_found",
        category: "error",
        message: "Unhandled fixture read",
      },
      404,
    );
  });
  return calls;
}
async function start(page: Page) {
  await page.goto(
    "/__foundation/workshop-sources/target?scenario=workshop-source-http",
  );
  await expect(
    page.getByRole("heading", { name: "Evening Collection", exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("source-child-context")).toHaveText(
    "Fixture child shares target target.",
  );
  await page.getByRole("button", { name: "Tools", exact: true }).click();
  await page
    .getByRole("button", { name: "Source fixture", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Stage pending fixture track" })
    .click();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Review & save" }).click();
  await expect(
    page.getByRole("heading", { name: "Source changes after target save" }),
  ).toBeVisible();
  await expect(page.getByText("1 selected track · Holding room")).toBeVisible();
  await page.getByRole("button", {name:"Save to Spotify", exact:true}).scrollIntoViewIfNeeded();
  await mkdir(test.info().outputDir, { recursive: true });
  await page.screenshot({ path: test.info().outputPath("source-review.png") });
}
async function leave(page: Page) {
  await page.keyboard.press("Escape");
  const menu = page.locator(".sh-app-menu");
  if (await menu.isVisible()) await menu.locator("summary").click();
  await page
    .getByRole("link", { name: "Library", exact: true })
    .first()
    .click();
  await page.getByRole("button", { name: "Leave page", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Your playlists", exact: true }),
  ).toBeVisible();
}
test.afterEach(async ({ page }, info) => {
  expect(problems.get(page)).toEqual([]);
  expect(
    await page
      .locator('script:not([src]),style,[style]:not([style=""])')
      .count(),
  ).toBe(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await mkdir(info.outputDir, { recursive: true });
  await page.screenshot({ path: info.outputPath("final-state.png") });
  await writeFile(
    info.outputPath("accessibility-tree.txt"),
    await page.locator("body").ariaSnapshot(),
  );
  await writeFile(
    info.outputPath("requests.json"),
    JSON.stringify(logs.get(page), null, 2),
  );
});
test("common keyboard Save names source work and continues only its original authorized chain after verified target", async ({
  page,
}) => {
  const calls = await backend(page, "happy");
  await start(page);
  await page
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Source work verified.")).toHaveCount(0);
  await expect.poll(() => calls.length).toBe(2);
  await expect(
    page.getByText("Target order verified.", { exact: true }),
  ).toBeVisible();
  expect(calls.map((x) => x.path)).toEqual([
    "/api/workshop/target/commit",
    "/api/playlist/target/pending-raids/finalize",
  ]);
  expect(calls[1]!.body).toEqual({
    source_playlist_id: "collection",
    target_track_uris: workshopContext().tracks.map((x) => x.uri),
    track_ids: [11],
  });
});
test("target503 retains all reviewed source claims and exact original target retry is the only continuation trigger", async ({
  page,
}) => {
  const calls = await backend(page, "target-503");
  await start(page);
  await page
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry this exact request", exact: true }),
  ).toBeVisible();
  expect(calls).toHaveLength(1);
  await page
    .getByRole("button", { name: "Retry this exact request", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Retry exact request", exact: true })
    .click();
  await expect.poll(() => calls.length).toBe(3);
  expect(calls[0]).toEqual(calls[1]);
  expect(calls[2]!.key).toBe("source-fixture-key-0");
});
test("unknown saved source work survives local discard/navigation and blocks Library until explicit exact source retry", async ({
  page,
}) => {
  const calls = await backend(page, "source-503");
  await start(page);
  await page
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await expect(
    page.getByText("Finish pending tracks · Holding room · unknown", {
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: test.info().outputPath("retained-unknown.png"),
  });
  await leave(page);
  const row = page.locator('[data-playlist-id="target"]');
  await row.locator("summary").first().click();
  await expect(
    row.getByRole("button", { name: /^Shuffle now/ }),
  ).toBeDisabled();
  expect(calls).toHaveLength(2);
  await page.goBack();
  await expect(
    page.getByRole("heading", { name: "Evening Collection", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Tools", exact: true }).click();
  await page
    .getByRole("button", { name: "Source fixture", exact: true })
    .click();
  await page
    .getByRole("button", {
      name: "Retry exact source fixture request",
      exact: true,
    })
    .click();
  await expect.poll(() => calls.length).toBe(3);
  expect(calls[1]).toEqual(calls[2]);
});
