import { checks } from "./checks";
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createLibraryFixtures } from "../../src/fixtures/library";
import { createRoutinesFixtures } from "../../src/fixtures/routines";
import { createWorkshopFixtures } from "../../src/fixtures/workshop";
import { createWorkshopToolsFixtures } from "../../src/fixtures/workshop-tools";
import session from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import reviewed from "../../fixtures/valid/operation-restore-context.json" with { type: "json" };
import type { Operation } from "../../src/api/contracts";

checks();
const panel = (page: Page) =>
  page.locator(".sh-task-panel:not(.sh-panel-modal):not([hidden])");
async function setup(
  page: Page,
  feature: "library" | "routines" | "workshop",
  busy = false,
) {
  const library = createLibraryFixtures(
    busy ? "library-busy" : "library-recovery",
  );
  const routines = createRoutinesFixtures("routines-attention");
  const workshop = createWorkshopFixtures("workshop-history-failure");
  const tools = createWorkshopToolsFixtures("workshop-tools", workshop);
  let parent: Operation | null = null;
  let resolved = false;
  const posts: { path: string; body: unknown }[] = [];
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = url.pathname + url.search;
    const method = req.method(),
      headers = new Headers(req.headers());
    let body: BodyInit | undefined = req.postData() ?? undefined;
    if (body && headers.get("content-type")?.startsWith("multipart/form-data"))
      body = await new Request(req.url(), { method, headers, body }).formData();
    if (path === "/api/session") return route.fulfill({ json: session });
    if (parent && path === `/api/operations/${parent.id}/restore-context`) {
      const context = structuredClone(reviewed.context);
      context.operation_id = parent.id;
      context.resources = parent.targets.map((id) => ({
        ...context.resources[0]!,
        playlist_id: id,
        name: id,
      }));
      context.reviewed.reviewed_resources = parent.targets.map((id) => ({
        playlist_id: id,
        digest: "b".repeat(64),
      }));
      return route.fulfill({ json: { success: true, context } });
    }
    if (parent && path === `/api/operations/${parent.id}/restore`) {
      posts.push({ path, body: req.postDataJSON() });
      return route.fulfill({
        status: 202,
        json: {
          success: true,
          operation_id: 9000,
          operation: {
            id: 9000,
            kind: "operation_restore",
            state: "queued",
            targets: parent.targets,
            started_at: null,
            completed_at: null,
            progress: {},
            result: null,
            recovery: { available: false, action: null, reason: null },
          },
        },
        headers: { Location: "/api/operations/9000" },
      });
    }
    if (parent && path === "/api/operations/9000") {
      resolved = true;
      return route.fulfill({
        json: {
          success: true,
          operation: {
            id: 9000,
            kind: "operation_restore",
            state: "succeeded",
            targets: parent.targets,
            started_at: null,
            completed_at: null,
            progress: {},
            result: {
              success: true,
              parent_operation_id: parent.id,
              parent_resolution: "complete",
            },
            recovery: { available: false, action: null, reason: null },
          },
        },
      });
    }
    if (parent && resolved && path === `/api/operations/${parent.id}`)
      return route.fulfill({
        json: {
          success: true,
          operation: {
            ...parent,
            recovery: {
              available: false,
              action: null,
              reason: "operation_already_restored",
              resolved_operation_id: 9000,
            },
          },
        },
      });
    const init = { headers, ...(body === undefined ? {} : { body }) };
    const response =
      feature === "library"
        ? await library(path, method, init)
        : feature === "routines"
          ? await routines(path, method, init)
          : ((await tools(path, method, init)) ??
            (await workshop(path, method, init)));
    if (!response)
      return route.fulfill({
        status: 404,
        json: {
          success: false,
          category: "error",
          message: "Not found.",
          error_code: "not_found",
        },
      });
    const value = await response.json();
    if (value.operation?.state === "needs_attention") {
      value.operation.recovery = {
        available: true,
        action: "restore",
        reason: "external_edit",
      };
      parent = value.operation;
    }
    return route.fulfill({ status: response.status, json: value });
  });
  await page.goto(feature === "workshop" ? "/workshop/target" : `/${feature}`);
  return { posts };
}
async function restore(page: Page) {
  await panel(page)
    .getByRole("button", { name: "Review restoration", exact: true })
    .click();
  await expect(
    panel(page).getByRole("heading", {
      name: "Review restoration",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    panel(page).getByRole("button", {
      name: "Restore reviewed changes",
      exact: true,
    }),
  ).toHaveAttribute("aria-disabled", "true");
  await panel(page)
    .getByRole("checkbox", {
      name: "I have reviewed every playlist in this restoration.",
    })
    .check();
  await panel(page)
    .getByRole("button", { name: "Restore reviewed changes", exact: true })
    .click();
  await expect(panel(page)).toContainText(
    "The restoration and its original operation record are confirmed.",
  );
  await panel(page).getByRole("button", { name: "Close", exact: true }).click();
}

test("Library original result restores through its retained request and enables a fresh explicit review", async ({
  page,
}) => {
  const state = await setup(page, "library");
  const row = page.locator('[data-playlist-id="alice-mix"]');
  await row.locator(">details>summary").click();
  await row
    .getByRole("button", { name: "Shuffle now Evening rotation", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Run shuffle", exact: true })
    .click();
  await restore(page);
  await panel(page).getByRole("button", { name: "Close", exact: true }).click();
  await row
    .getByRole("button", { name: "Shuffle now Evening rotation", exact: true })
    .click();
  await expect(
    panel(page).getByRole("button", { name: "Run shuffle", exact: true }),
  ).toBeVisible();
  expect(state.posts).toHaveLength(1);
});

test("Library blocking ID stays inspection-only even when the blocked operation offers restoration", async ({
  page,
}) => {
  const state = await setup(page, "library", true);
  const row = page.locator('[data-playlist-id="alice-mix"]');
  await row.locator(">details>summary").click();
  await row
    .getByRole("button", { name: "Shuffle now Evening rotation", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Run shuffle", exact: true })
    .click();
  await expect(panel(page)).toContainText("Operation #99");
  await expect(
    panel(page).getByRole("button", {
      name: "Review restoration",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(state.posts).toHaveLength(0);
});

test("Routines restores the exact run receipt and leaves the saved definition available", async ({
  page,
}) => {
  const state = await setup(page, "routines");
  const card = page.locator("#routine-1");
  await card.getByRole("button", { name: "Run now", exact: true }).click();
  await panel(page)
    .getByRole("button", { name: "Run reviewed routine", exact: true })
    .click();
  await restore(page);
  await panel(page).getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    card.getByRole("button", { name: "Run now", exact: true }),
  ).toBeEnabled();
  await expect(
    card.getByRole("button", { name: "Pause", exact: true }),
  ).toBeEnabled();
  expect(state.posts).toHaveLength(1);
});

test("Workshop preserves its local duplicate draft while restoration uses the original save receipt", async ({
  page,
}) => {
  const state = await setup(page, "workshop");
  const first = page.locator("[data-occurrence]").first();
  await first.getByRole("button", { name: /^Actions for / }).click();
  await first
    .getByRole("button", { name: "Remove from draft", exact: true })
    .click();
  await expect(page.locator("[data-occurrence]")).toHaveCount(2);
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await restore(page);
  await panel(page).getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator("[data-occurrence]")).toHaveCount(2);
  const retained = page.locator("[data-occurrence]").first();
  await retained.getByRole("button", { name: /^Actions for / }).click();
  await retained
    .getByRole("button", { name: "Remove from draft", exact: true })
    .click();
  await expect(page.locator("[data-occurrence]")).toHaveCount(1);
  expect(state.posts).toHaveLength(1);
});
