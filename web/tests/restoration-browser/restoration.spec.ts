import { checks } from "./checks";
import type {
  OperationRestoreContext,
  OperationRestoreResource,
} from "../../src/api/contracts";
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import signedIn from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import activity from "../../fixtures/activity/initial.json" with { type: "json" };
import library from "../../fixtures/library/initial.json" with { type: "json" };
import review from "../../fixtures/valid/operation-restore-context.json" with { type: "json" };

async function start(
  page: Page,
  lost = false,
  restorationReview: OperationRestoreContext = review as OperationRestoreContext,
) {
  const context = structuredClone(activity);
  const run = context.executions[0]!;
  run.id = 7;
  if (run.result_detail.detail_status === "available")
    run.result_detail.execution_id = 7;
  context.executions = [run];
  context.activities[0]!.id = 700;
  const parent = {
    id: 7,
    kind: "shuffle",
    state: "needs_attention",
    targets: restorationReview.context.resources
      .map((resource) => resource.playlist_id)
      .sort(),
    started_at: null,
    completed_at: null,
    progress: {},
    result: { success: false },
    recovery: { available: true, action: "restore", reason: "external_edit" },
  };
  const child = {
    ...parent,
    id: 8,
    kind: "operation_restore",
    state: "succeeded",
    result: {
      success: true,
      parent_operation_id: 7,
      parent_resolution: "complete",
      restored_resource_count: 1,
      changed_position_count: 3,
      message: "The reviewed changes were restored.",
    },
    recovery: { available: false, action: null, reason: null },
  };
  let resolved = false,
    submissions = 0;
  const calls: {
    path: string;
    method: string;
    body: unknown;
    key: string | null;
  }[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    calls.push({
      path,
      method: request.method(),
      body: request.postDataJSON(),
      key: request.headers()["idempotency-key"] ?? null,
    });
    if (path === "/api/session") return route.fulfill({ json: signedIn });
    if (path === "/api/activity") return route.fulfill({ json: context });
    if (path === "/api/library") return route.fulfill({ json: library });
    if (path === "/api/operations/7")
      return route.fulfill({
        json: {
          success: true,
          operation: {
            ...parent,
            recovery: resolved
              ? {
                  available: false,
                  action: null,
                  reason: "operation_already_restored",
                  resolved_operation_id: 8,
                }
              : parent.recovery,
          },
        },
      });
    if (path === "/api/operations/7/restore-context")
      return route.fulfill({ json: restorationReview });
    if (path === "/api/operations/7/restore") {
      submissions++;
      if (lost && submissions === 1) return route.abort("connectionreset");
      return route.fulfill({
        status: 202,
        json: {
          success: true,
          category: "info",
          message: "Accepted.",
          operation_id: 8,
          operation: { ...child, state: "queued", result: null },
        },
        headers: { Location: "/api/operations/8" },
      });
    }
    if (path === "/api/operations/8") {
      resolved = true;
      return route.fulfill({ json: { success: true, operation: child } });
    }
    return route.fulfill({
      status: 404,
      json: {
        success: false,
        category: "error",
        error_code: "not_found",
        message: "No owned operation exists.",
      },
    });
  });
  await page.goto("/activity");
  return calls;
}
checks();
const panel = (page: Page) =>
  page.locator(".sh-task-panel:not(.sh-panel-modal):not([hidden])");

test("an authored Activity run opens one reviewed restoration and confirms the exact parent-child result", async ({
  page,
}, testInfo) => {
  const calls = await start(page);
  await page
    .getByRole("button", { name: "View operation #7", exact: true })
    .click();
  await expect(
    panel(page).getByRole("heading", {
      name: "Review restoration",
      exact: true,
    }),
  ).toBeVisible();
  await expect(panel(page).getByText("Target", { exact: true })).toBeVisible();
  await expect(
    panel(page)
      .getByRole("list", { name: "Desired ordered preview" })
      .locator("li"),
  ).toHaveCount(3);
  await expect(
    panel(page)
      .getByRole("list", { name: "Desired ordered preview" })
      .locator("li"),
  ).toHaveText([
    "spotify:track:0000000000000000000001",
    "spotify:track:0000000000000000000001",
    "spotify:track:0000000000000000000002",
  ]);
  await page.screenshot({
    path: testInfo.outputPath("review.png"),
    fullPage: true,
  });
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
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
  await page.screenshot({
    path: testInfo.outputPath("confirmed.png"),
    fullPage: true,
  });
  await expect(
    panel(page).getByRole("heading", {
      name: "Restoration confirmed",
      exact: true,
    }),
  ).toBeInViewport();
  const posts = calls.filter((call) => call.method === "POST");
  expect(posts).toHaveLength(1);
  expect(posts[0]!.body).toEqual({ reviewed: review.context.reviewed });
  expect(posts[0]!.key).toBeTruthy();
  expect(calls.some((call) => call.path === "/api/operations/700")).toBe(false);
  await panel(page).getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "View operation #7", exact: true }),
  ).toBeFocused();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("lost admission survives panel closure and navigation; only explicit exact retry sends another POST", async ({
  page,
}) => {
  const calls = await start(page, true);
  await page
    .getByRole("button", { name: "View operation #7", exact: true })
    .click();
  await panel(page)
    .getByRole("checkbox", {
      name: "I have reviewed every playlist in this restoration.",
    })
    .check();
  await panel(page)
    .getByRole("button", { name: "Restore reviewed changes", exact: true })
    .click();
  await expect(panel(page)).toContainText(
    "The request could not be confirmed.",
  );
  await panel(page).getByRole("button", { name: "Close", exact: true }).click();
  const menu = page.locator(".sh-app-menu > summary");
  if (await menu.isVisible()) await menu.click();
  await page
    .getByRole("link", { name: "Library", exact: true })
    .filter({ visible: true })
    .click();
  await page
    .getByRole("button", {
      name: "View restoration for operation #7",
      exact: true,
    })
    .click();
  await expect(panel(page)).toContainText(
    "The request could not be confirmed.",
  );
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
  await panel(page)
    .getByRole("button", { name: "Retry this exact request", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Retry exact request", exact: true })
    .click();
  await expect(panel(page)).toContainText(
    "The restoration and its original operation record are confirmed.",
  );
  const posts = calls.filter((call) => call.method === "POST");
  expect(posts).toHaveLength(2);
  expect(posts[0]!.key).toEqual(posts[1]!.key);
  expect(posts[0]!.body).toEqual(posts[1]!.body);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("a historical run without an owned durable operation remains a read-only unavailable record", async ({
  page,
}) => {
  const calls = await start(page);
  await page.route("**/api/operations/7", (route) =>
    route.fulfill({
      status: 404,
      json: {
        success: false,
        category: "error",
        error_code: "not_found",
        message: "No owned operation exists.",
      },
    }),
  );
  await page
    .getByRole("button", { name: "View operation #7", exact: true })
    .click();
  await expect(panel(page)).toContainText("No owned operation exists.");
  await expect(
    panel(page).getByRole("button", {
      name: "Restore reviewed changes",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
});

test("the review distinguishes protected dependencies and metadata-only changes without sending desired lists", async ({
  page,
}) => {
  const context = structuredClone(review) as OperationRestoreContext;
  const target = context.context.resources[0]!;
  const guard: OperationRestoreResource = {
    ...target,
    playlist_id: "archive",
    name: "Protected archive",
    role: "guard",
    write_fields: [],
    desired_count: target.current_count,
    desired_preview: [...target.current_preview],
  };
  const metadata: OperationRestoreResource = {
    ...target,
    playlist_id: "privacy",
    name: "Visibility only",
    scope: "metadata",
    write_fields: ["public"],
    current_count: null,
    desired_count: null,
    current_preview: [],
    desired_preview: [],
    current_metadata: { public: true },
    desired_metadata: { public: false },
    metadata_truncated: false,
  };
  context.context.resources = [guard, metadata, target];
  context.context.reviewed.reviewed_resources = context.context.resources.map(
    (resource) => ({
      playlist_id: resource.playlist_id,
      digest: "b".repeat(64),
    }),
  );
  const calls = await start(page, false, context);
  await page
    .getByRole("button", { name: "View operation #7", exact: true })
    .click();
  const archive = panel(page)
    .getByRole("article")
    .filter({
      has: page.getByRole("heading", {
        name: "Protected archive",
        exact: true,
      }),
    });
  await expect(archive).toContainText(
    "Checked only · no changes will be made here.",
  );
  const privacy = panel(page)
    .getByRole("article")
    .filter({
      has: page.getByRole("heading", { name: "Visibility only", exact: true }),
    });
  await expect(privacy).toContainText("Current: Public");
  await expect(privacy).toContainText("Desired: Private");
  await expect(privacy.getByRole("list")).toHaveCount(0);
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
  expect(calls.filter((call) => call.method === "POST")[0]!.body).toEqual({
    reviewed: context.context.reviewed,
  });
});
