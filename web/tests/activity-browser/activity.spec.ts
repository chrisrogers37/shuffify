import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import initial from "../../fixtures/activity/initial.json" with { type: "json" };
import signedIn from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import signedOut from "../../fixtures/valid/session-signed-out.json" with { type: "json" };
const error = (message: string, extra: object = {}) => ({
  success: false,
  error_code: "activity_error",
  category: "error",
  message,
  ...extra,
});
async function setup(page: Page, value: unknown = initial, hash = "") {
  let reads = 0;
  const unexpected: string[] = [];
  await page.route("**/api/**", (route) => {
    unexpected.push(route.request().url());
    return route.abort();
  });
  await page.route("**/api/session", (route) =>
    route.fulfill({ json: signedIn }),
  );
  await page.route("**/api/activity", (route) => {
    reads++;
    return route.fulfill({ json: value });
  });
  await page.goto(`/activity${hash}`);
  return {
    get reads() {
      return reads;
    },
    unexpected,
  };
}
test("stat tiles, bounded lists, title, labels and responsive targets match authoritative data", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  const state = await setup(page);
  await expect(
    page.getByRole("heading", { name: "Activity", exact: true }),
  ).toBeFocused();
  await expect(page).toHaveTitle("Activity · Shuffify");
  await expect(
    page.getByRole("group", { name: "Activity totals" }),
  ).toContainText("4Shuffles");
  await expect(page.locator(".sh-activity-run")).toHaveCount(7);
  await expect(page.locator(".sh-activity-event")).toHaveCount(4);
  for (const control of await page
    .locator("main select,main summary,main a")
    .all()) {
    const box = await control.boundingBox();
    if (box) {
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.width).toBeGreaterThanOrEqual(44);
    }
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  expect(await page.locator("[style],script:not([src]),style").count()).toBe(0);
  expect(errors).toEqual([]);
  expect(state.reads).toBe(1);
  expect(state.unexpected).toEqual([]);
});
test("operation and outcome filters use only loaded exact results and announce counts", async ({
  page,
}) => {
  const state = await setup(page);
  const outcome = page.getByRole("combobox", {
    name: "Show runs",
    exact: true,
  });
  await outcome.selectOption("partial");
  await expect(page.locator(".sh-activity-run:visible")).toHaveCount(1);
  await expect(page.locator("#activity-run-count")).toHaveText(
    "1 of 7 loaded runs shown",
  );
  await page
    .getByRole("combobox", { name: "Operation", exact: true })
    .selectOption("rotate");
  await expect(
    page.getByText("No loaded runs match this filter.", { exact: true }),
  ).toBeVisible();
  await expect(outcome).toBeEnabled();
  await outcome.selectOption("unknown");
  await expect(page.locator(".sh-activity-run:visible")).toHaveCount(1);
  await expect(page.locator("#run-3")).toBeVisible();
  expect(state.reads).toBe(1);
  expect(state.unexpected).toEqual([]);
  await expect(page.locator('[aria-live="polite"]')).toContainText(
    "1 loaded runs shown.",
  );
});
test("keyboard detail expand/collapse preserves focus and shows exact facts without invented counts", async ({
  page,
}) => {
  const state = await setup(page);
  const run = page.locator("#run-2"),
    summary = run.locator("summary");
  await summary.focus();
  await page.keyboard.press("Enter");
  await expect(run.locator("details")).toHaveAttribute("open", "");
  await expect(
    run.getByText("3 collected for review", { exact: true }),
  ).toBeVisible();
  await expect(
    run.getByText("Reorder — Failed", { exact: true }),
  ).toBeVisible();
  await expect(run.getByText(/999 positions/)).toHaveCount(0);
  await expect(
    run.getByText("Target playlist: Changes could not be verified", {
      exact: true,
    }),
  ).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(run.locator("details")).not.toHaveAttribute("open", "");
  await expect(summary).toBeFocused();
  expect(state.reads).toBe(1);
});
test("unknown/restored/archive and in-progress detail retain their precise semantics", async ({
  page,
}) => {
  await setup(page);
  const archive = page.locator("#run-3");
  await archive.locator("summary").click();
  await expect(
    archive.getByText("Rotate — Restoration verified", { exact: true }),
  ).toBeVisible();
  await expect(
    archive.getByText("Archive: Changes could not be verified", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    archive.getByText("Archive cleanup: Failed before a confirmed change", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    archive.getByText(
      "Review inbox restoration: Restoration could not be verified",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    archive.getByText("Run history: Save failed", { exact: true }),
  ).toBeVisible();
  await expect(
    archive.getByText("Run history could not be verified.", { exact: true }),
  ).toBeVisible();
  const running = page.locator("#run-4");
  await running.locator("summary").click();
  await expect(
    running.getByText("No new tracks collected", { exact: true }),
  ).toBeVisible();
  await expect(
    running.getByText("Add to playlist — Not started yet", { exact: true }),
  ).toBeVisible();
  await expect(running.getByText(/888 added/)).toHaveCount(0);
  const unchanged = page.locator("#run-7");
  await unchanged.locator("summary").click();
  await expect(
    unchanged.getByText("Order unchanged", { exact: true }),
  ).toBeVisible();
  await expect(
    unchanged.getByText("Target playlist: No change needed", { exact: true }),
  ).toBeVisible();
  await expect(
    unchanged.getByText("Run history: Save could not be verified", {
      exact: true,
    }),
  ).toBeVisible();
});
test("legacy and identity-mismatched detail stay unavailable without raw error or false precise counts", async ({
  page,
}) => {
  await setup(page);
  for (const id of [5, 6]) {
    const run = page.locator(`#run-${id}`);
    await expect(
      run.getByText("Step details unavailable", { exact: true }),
    ).toBeVisible();
    await expect(run.locator("details")).toHaveCount(0);
  }
  await expect(page.locator("#run-5")).toContainText(
    "Run failed; rollback reported",
  );
  await expect(page.locator("#run-5")).toContainText("Time unavailable");
  await expect(page.locator("#run-6")).toContainText("Origin unavailable");
});
test("top-level event origin drives manual filter and safe operation labels; null never guessed from metadata", async ({
  page,
}) => {
  const state = await setup(page);
  const filter = page.getByRole("combobox", {
    name: "Show events",
    exact: true,
  });
  await filter.selectOption("manual");
  await expect(page.locator(".sh-activity-event:visible")).toHaveCount(1);
  await expect(page.locator("#event-1")).toContainText(
    "Manual Collect then add to playlist: Alice mix",
  );
  await expect(page.locator("#event-3")).not.toBeVisible();
  const playlist = await page
    .locator("#event-1")
    .getByRole("link", { name: "Alice mix", exact: true })
    .boundingBox();
  const origin = await page
    .locator("#event-1")
    .getByText("Manual action", { exact: true })
    .boundingBox();
  expect(playlist).not.toBeNull();
  expect(origin).not.toBeNull();
  expect(origin!.y).toBeGreaterThanOrEqual(playlist!.y + playlist!.height);
  await filter.selectOption("type:manual");
  await expect(page.locator("#event-3")).toBeVisible();
  await expect(page.locator("#event-3")).toContainText("Origin unavailable");
  await expect(
    page.locator("#event-3").getByText("Manual action", { exact: true }),
  ).toHaveCount(0);
  expect(state.reads).toBe(1);
});
test("direct run fragment opens precise details, focuses exact run and keeps safe destination links", async ({
  page,
}) => {
  await setup(page, initial, "#run-3");
  await expect(page.locator("#run-3")).toBeFocused();
  await expect(page.locator("#run-3 details")).toHaveAttribute("open", "");
  await expect(
    page
      .locator("#run-3")
      .getByRole("link", { name: "Bob archive", exact: true }),
  ).toHaveAttribute("href", "/routines?routine_id=3&view=history");
  await expect(
    page
      .locator("#event-2")
      .getByRole("link", { name: "Bob mix", exact: true }),
  ).toHaveAttribute("href", "/workshop/bob-mix");
  await expect(
    page.locator("#run-5").getByRole("link", { name: /routine history/i }),
  ).toHaveCount(0);
});
test("empty histories show genuine empty states with zero stat tiles and usable filters", async ({
  page,
}) => {
  await setup(page, {
    ...initial,
    stats: {
      total_shuffles: 0,
      total_scheduled_runs: 0,
      total_snapshots: 0,
      active_schedule_count: 0,
    },
    executions: [],
    activities: [],
  });
  await expect(
    page.getByText("No routine runs yet.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("No app activity yet.", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/No loaded.*match/)).toHaveCount(0);
  await expect(
    page.getByRole("combobox", { name: "Show runs", exact: true }),
  ).toBeEnabled();
});
test("read failure retries explicitly, keeps honest state, and never submits writes", async ({
  page,
}) => {
  await page.route("**/api/session", (route) =>
    route.fulfill({ json: signedIn }),
  );
  let reads = 0;
  const methods: string[] = [];
  await page.route("**/api/activity", (route) => {
    methods.push(route.request().method());
    return route.fulfill(
      ++reads === 1
        ? { status: 503, json: error("Activity is temporarily unavailable.") }
        : { json: initial },
    );
  });
  await page.goto("/activity");
  await expect(
    page.getByRole("heading", { name: "Activity could not be loaded" }),
  ).toBeVisible();
  await expect(page.locator(".sh-activity-run")).toHaveCount(0);
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.locator(".sh-activity-run")).toHaveCount(7);
  expect(methods).toEqual(["GET", "GET"]);
});
test("rate limit preserves full read hint and requires manual retry", async ({
  page,
}) => {
  await page.route("**/api/session", (route) =>
    route.fulfill({ json: signedIn }),
  );
  let reads = 0;
  await page.route("**/api/activity", (route) =>
    route.fulfill(
      ++reads === 1
        ? {
            status: 429,
            headers: { "Retry-After": "1" },
            json: error("Wait before loading activity.", { retry_after: 2 }),
          }
        : { json: initial },
    ),
  );
  await page.goto("/activity");
  await expect(
    page.getByText("Try again in 2 seconds.", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Try again", exact: true })
    .click({ force: true });
  expect(reads).toBe(1);
  await expect(page.getByText(/Try again in/)).toHaveCount(0, {
    timeout: 4000,
  });
  expect(reads).toBe(1);
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(page.locator(".sh-activity-run")).toHaveCount(7);
});
test("authentication loss hides all protected records and renders native reconnect consent", async ({
  page,
}) => {
  let signed = true;
  await page.route("**/api/session", (route) =>
    route.fulfill({ json: signed ? signedIn : signedOut }),
  );
  await page.route("**/api/activity", (route) => {
    signed = false;
    return route.fulfill({ status: 401, json: error("Your session ended.") });
  });
  await page.goto("/activity");
  await expect(
    page.getByRole("heading", { name: "Connect to Spotify", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Activity", exact: true }),
  ).not.toBeVisible();
  await expect(page.locator('form[action="/login"]')).toHaveAttribute(
    "method",
    "get",
  );
});
test("malformed bounded context fails visibly instead of silently showing a repaired history", async ({
  page,
}) => {
  await setup(page, {
    ...initial,
    executions: Array(21).fill(initial.executions[0]),
  });
  await expect(
    page.getByRole("heading", { name: "Activity could not be loaded" }),
  ).toBeVisible();
  await expect(page.locator(".sh-activity-run")).toHaveCount(0);
});

test("filtering out a focused run returns focus to its filter and retains expansion when shown again", async ({
  page,
}) => {
  await setup(page);
  const summary = page.locator("#run-2 summary");
  await summary.focus();
  await page.keyboard.press("Enter");
  await page
    .getByRole("combobox", { name: "Show runs", exact: true })
    .selectOption("complete");
  await expect(
    page.getByRole("combobox", { name: "Show runs", exact: true }),
  ).toBeFocused();
  await expect(page.locator("#run-2")).not.toBeVisible();
  await page
    .getByRole("combobox", { name: "Show runs", exact: true })
    .selectOption("all");
  await expect(page.locator("#run-2 details")).toHaveAttribute("open", "");
});
test("same exact-run link opens collapsed details again and keeps a stable share target", async ({
  page,
}) => {
  await setup(page, initial, "#run-3");
  await page.locator("#run-3 summary").click();
  await expect(page.locator("#run-3 details")).not.toHaveAttribute("open", "");
  await page
    .locator("#run-3")
    .getByRole("link", { name: "Link to run #3", exact: true })
    .click();
  await expect(page.locator("#run-3 details")).toHaveAttribute("open", "");
  await expect(page.locator("#run-3")).toBeFocused();
});
