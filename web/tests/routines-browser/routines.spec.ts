import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createRoutinesFixtures } from "../../src/fixtures/routines";
import seed from "../../fixtures/routines/initial.json" with { type: "json" };
import session from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
async function setup(
  page: Page,
  path = "/routines",
  scenario = "routines-happy",
  lostRun = false,
) {
  const backend = createRoutinesFixtures(scenario),
    calls: {
      path: string;
      method: string;
      body: unknown;
      key: string | null;
    }[] = [],
    unexpected: string[] = [];
  let lost = lostRun;
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      method = request.method(),
      raw = request.postData(),
      path = url.pathname + url.search;
    if (url.pathname === "/api/session")
      return route.fulfill({ json: session });
    calls.push({
      path,
      method,
      body: raw ? JSON.parse(raw) : null,
      key: request.headers()["idempotency-key"] ?? null,
    });
    try {
      const response = await backend(path, method, {
        ...(raw ? { body: raw } : {}),
        headers: request.headers(),
      });
      if (!response) {
        unexpected.push(path);
        return route.abort();
      }
      if (lost && path.endsWith("/run")) {
        lost = false;
        return route.abort();
      }
      return route.fulfill({
        status: response.status,
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
        },
        body: await response.text(),
      });
    } catch {
      return route.abort();
    }
  });
  await page.goto(path);
  return { calls, unexpected };
}
const card = (page: Page, id: number) => page.locator(`#routine-${id}`);
test("exact cards, local filters, deep links and desired state retain identity", async ({
  page,
}) => {
  const state = await setup(page, "/routines#routine-2");
  await expect(page.locator("#routine-2-title")).toBeFocused();
  await expect(page.locator(".sh-routine-card")).toHaveCount(3);
  await page.getByLabel("State", { exact: true }).selectOption("paused");
  await expect(page.locator(".sh-routine-card")).toHaveCount(1);
  await expect(card(page, 2)).toBeVisible();
  await page.getByLabel("State", { exact: true }).selectOption("");
  await card(page, 1)
    .getByRole("button", { name: "Pause", exact: true })
    .click();
  await expect(card(page, 1).locator(".sh-badge")).toHaveText("Paused");
  await expect(card(page, 2).locator(".sh-badge")).toHaveText("Paused");
  const change = state.calls.find((value) => value.method === "PUT");
  expect(change?.path).toBe("/api/routines/1/state");
  expect(change?.body).toEqual({
    desired_enabled: false,
    expected_revision: "1".repeat(64),
  });
  await card(page, 2)
    .getByRole("button", { name: "History", exact: true })
    .click();
  await expect(card(page, 2).getByText("Run #1 · success")).toBeVisible();
  expect(state.unexpected).toEqual([]);
  expect(
    state.calls.filter((value) => value.path === "/api/routines"),
  ).toHaveLength(1);
});
test("run reviews first, submits once, keeps observing after close and leaves Pause available", async ({
  page,
}) => {
  const state = await setup(page);
  await card(page, 1)
    .getByRole("button", { name: "Run now", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Run routine #1", exact: true }),
  ).toBeFocused();
  const panel = page.locator(".sh-task-panel:visible");
  await panel
    .getByRole("button", { name: "Run reviewed routine", exact: true })
    .dblclick();
  await expect(
    panel.getByRole("heading", { name: /Operation #1001/ }),
  ).toBeVisible();
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    card(page, 1).getByRole("button", { name: "Run now", exact: true }),
  ).toBeDisabled();
  await expect(
    card(page, 1).getByRole("button", { name: "Pause", exact: true }),
  ).toBeEnabled();
  await expect(card(page, 1)).toContainText("succeeded", { timeout: 7000 });
  expect(
    state.calls.filter(
      (value) => value.method === "POST" && value.path.endsWith("/run"),
    ),
  ).toHaveLength(1);
  await card(page, 1)
    .getByRole("button", { name: "Run now", exact: true })
    .click();
  await expect(
    page
      .locator(".sh-task-panel:visible")
      .getByRole("button", { name: "Run reviewed routine", exact: true }),
  ).toBeVisible();
});
test("exact edit preserves timing and submits reviewed normalized body rather than rebuilding it", async ({
  page,
}) => {
  const state = await setup(
    page,
    "/playlist/target1/routines/new?schedule_id=2&origin=routines",
  );
  await expect(
    page.getByRole("heading", { name: "Edit routine #2", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Review routine", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Review and activate", exact: true }),
  ).toBeVisible();
  expect(
    state.calls.filter((value) => value.path.endsWith("/routines/review")),
  ).toHaveLength(1);
  await page
    .getByRole("button", { name: "Save routine", exact: true })
    .dblclick();
  await expect(
    page.getByRole("heading", { name: "Routine #2 saved", exact: true }),
  ).toBeVisible();
  const body = state.calls.find((value) => value.path.endsWith("/activate"))
      ?.body as Record<string, unknown>,
    review = state.calls.find((value) => value.path.endsWith("/review"))
      ?.body as Record<string, unknown>;
  expect(body.schedule_id).toBe(2);
  expect(body.expected_revision).toBe("2".repeat(64));
  expect(body.request_key).toBe(review.request_key);
  expect(body.intent).toEqual({ signature: "fixture-reviewed-intent" });
  expect((body.definition as Record<string, unknown>).schedule_value).toBe(
    "30 9 * * *",
  );
  expect((review.definition as Record<string, unknown>).timing).toBeUndefined();
  expect(
    state.calls.filter((value) => value.path.endsWith("/activate")),
  ).toHaveLength(1);
});
test("lost activation response has no automatic replay and explicit Check reuses exact body", async ({
  page,
}) => {
  const state = await setup(
    page,
    "/playlist/target1/routines/new?schedule_id=1&origin=routines",
    "routines-activation-unknown",
  );
  await page
    .getByRole("button", { name: "Review routine", exact: true })
    .click();
  await page.getByRole("button", { name: "Save routine", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Activation is not confirmed" }),
  ).toBeVisible();
  await page.waitForTimeout(300);
  expect(
    state.calls.filter((value) => value.path.endsWith("/activate")),
  ).toHaveLength(1);
  await page
    .getByRole("button", { name: "Check activation", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Routine #1 saved", exact: true }),
  ).toBeVisible();
  const calls = state.calls.filter((value) => value.path.endsWith("/activate"));
  expect(calls).toHaveLength(2);
  expect(calls[0]?.body).toEqual(calls[1]?.body);
});
test("setup toggles keep the checkbox and its text side by side", async ({
  page,
}) => {
  await setup(page, "/playlist/target1/setup?return_target=routines");
  const input = await page
    .getByLabel("Enable adding collected tracks", { exact: true })
    .boundingBox();
  const text = await page
    .locator("label.sh-check", { hasText: "Enable adding collected tracks" })
    .locator("span")
    .boundingBox();
  const centers = Math.abs(
    input!.y + input!.height / 2 - (text!.y + text!.height / 2),
  );
  expect(centers).toBeLessThan(12);
  const gap = text!.x - (input!.x + input!.width);
  expect(gap).toBeGreaterThanOrEqual(0);
  expect(gap).toBeLessThan(24);
});

test("setup stages changes, reviews shared impact, requires acknowledgment and saves exact reviewed changes", async ({
  page,
}) => {
  const state = await setup(
    page,
    "/playlist/target1/setup?return_target=routines",
  );
  await page
    .getByLabel("Add by search words", { exact: true })
    .fill("Instrumental evenings");
  await page
    .getByRole("button", { name: "Add search source", exact: true })
    .click();
  expect(state.calls.filter((value) => value.method !== "GET")).toHaveLength(0);
  await page
    .getByLabel("Enable adding collected tracks", { exact: true })
    .check();
  await page.getByLabel("Tracks added per run", { exact: true }).fill("4");
  await page
    .getByRole("button", { name: "Review playlist settings", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Save playlist settings", exact: true }),
  ).toBeDisabled();
  await page
    .getByLabel("I understand these settings affect existing routines", {
      exact: true,
    })
    .check();
  await page
    .getByRole("button", { name: "Save playlist settings", exact: true })
    .dblclick();
  await expect(
    page.getByText("Playlist settings saved.", { exact: false }),
  ).toBeVisible();
  const body = state.calls.find((value) => value.method === "PUT")
    ?.body as Record<string, unknown>;
  expect(body.changes).toEqual([
    { op: "add_search_source", search_query: "Instrumental evenings" },
    { op: "set_drip", drip_enabled: true, drip_count: 4 },
  ]);
  expect(body.acknowledgment).toEqual({ signature: "fixture-settings-review" });
  expect(state.calls.filter((value) => value.method === "PUT")).toHaveLength(1);
});
test("routine draft survives setup handoff without activation or an invented return URL", async ({
  page,
}) => {
  const state = await setup(
    page,
    "/playlist/target1/routines/new?schedule_id=1&origin=routines",
  );
  await page.getByLabel("keep first", { exact: true }).fill("2");
  await page
    .getByRole("button", { name: "Open playlist setup", exact: true })
    .click();
  await expect(page).toHaveURL(/\/setup\?return_target=routine$/);
  await page
    .getByRole("link", { name: "Return to routine draft", exact: true })
    .click();
  await expect(page.getByLabel("keep first", { exact: true })).toHaveValue("2");
  expect(
    state.calls.filter((value) => value.path.endsWith("/activate")),
  ).toHaveLength(0);
  const raw = await page.evaluate(() =>
    sessionStorage.getItem("shuffify:react-routine-continuation:v1"),
  );
  expect(raw).toBeNull();
});
test("known creation remains separate from staged association and saving", async ({
  page,
}) => {
  const state = await setup(
    page,
    "/playlist/target1/setup?return_target=routines",
    "routines-setup-empty",
  );
  await page
    .getByLabel("Name a new collection playlist", { exact: true })
    .fill("A new holding room");
  await page
    .getByRole("button", {
      name: "Create collection playlist in Spotify",
      exact: true,
    })
    .dblclick();
  await expect(
    page.getByText("Created: A new holding room", { exact: true }),
  ).toBeVisible({ timeout: 7000 });
  expect(
    state.calls.filter((value) => value.path.endsWith("/spotify-playlists")),
  ).toHaveLength(1);
  expect(state.calls.filter((value) => value.method === "PUT")).toHaveLength(0);
  await expect(page.locator("#setup-collection")).toContainText(
    "Saved: Not linked",
  );
  await page
    .getByRole("button", { name: "Use created collection", exact: true })
    .click();
  await expect(
    page.getByText("1 of 20 changes staged", { exact: true }),
  ).toBeVisible();
  expect(state.calls.filter((value) => value.method === "PUT")).toHaveLength(0);
});
test("unknown creation retains uncertainty, never adopts a name match, and requires explicit next creation", async ({
  page,
}) => {
  const state = await setup(
    page,
    "/playlist/target1/setup?return_target=routines",
    "routines-creation-unknown",
  );
  await page
    .getByLabel("Name a new collection playlist", { exact: true })
    .fill("Evening collection");
  await page
    .getByRole("button", {
      name: "Create collection playlist in Spotify",
      exact: true,
    })
    .click();
  await expect(
    page.getByText(
      "Creation could not be confirmed. The earlier request may have created a playlist.",
      { exact: false },
    ),
  ).toBeVisible({ timeout: 7000 });
  await page
    .getByRole("button", { name: "Refresh my playlists", exact: true })
    .click();
  await expect(
    page.getByText("No unsaved settings.", { exact: true }),
  ).toBeVisible();
  expect(
    state.calls.filter((value) => value.path.endsWith("/spotify-playlists")),
  ).toHaveLength(1);
  await page
    .getByLabel("Your playlists for collection", { exact: true })
    .selectOption("choose2");
  await page
    .getByRole("button", { name: "Use existing collection", exact: true })
    .click();
  await expect(
    page.getByText("Link collection · choose2", { exact: false }),
  ).toBeVisible();
  expect(
    state.calls.filter((value) => value.path.endsWith("/spotify-playlists")),
  ).toHaveLength(1);
});
test("dirty navigation is guarded and invalid deep links make no context calls", async ({
  page,
}) => {
  let state = await setup(
    page,
    "/playlist/target1/routines/new?schedule_id=1&origin=routines",
  );
  await page.getByLabel("keep first", { exact: true }).fill("3");
  await page
    .getByRole("link", { name: "Back to routines", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "Leave this page?" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Stay", exact: true }).click();
  await expect(page.getByLabel("keep first", { exact: true })).toHaveValue("3");
  await page.goto(
    "/playlist/target1/routines/new?schedule_id=%EF%BC%91&origin=routines",
  );
  await expect(
    page.getByRole("heading", { name: "This routine link is unavailable" }),
  ).toBeVisible();
  expect(
    state.calls.filter((value) => value.path.includes("schedule_id=%EF")),
  ).toHaveLength(0);
});
test("360/1440 layout, controls, focus, CSP and bundled assets meet shared shell contract", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  const state = await setup(page);
  await expect(
    page.getByRole("heading", { name: "Routines", exact: true }),
  ).toBeFocused();
  for (const control of await page
    .locator("main button,main select,main a")
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
  expect(await page.locator("[style],style,script:not([src])").count()).toBe(0);
  expect(errors).toEqual([]);
  expect(state.unexpected).toEqual([]);
});

test("Activity History deep link opens the exact routine and preserves observed origin", async ({
  page,
}) => {
  const state = await setup(page, "/routines?routine_id=2&view=history");
  await expect(page.locator("#routine-2-title")).toBeFocused();
  await expect(card(page, 2).getByText("Run #1 · success")).toBeVisible();
  expect(
    state.calls
      .filter((call) => call.path.endsWith("/history"))
      .every((call) => call.path === "/api/schedules/2/history"),
  ).toBe(true);
});
test("unconfirmed run has no automatic POST replay and explicit Check retains key, body and ID", async ({
  page,
}) => {
  const state = await setup(page, "/routines", "routines-happy", true);
  await card(page, 1)
    .getByRole("button", { name: "Run now", exact: true })
    .click();
  const panel = page.locator(".sh-task-panel:visible");
  await panel
    .getByRole("button", { name: "Run reviewed routine", exact: true })
    .click();
  await expect(
    panel.getByRole("button", {
      name: "Check the same run request",
      exact: true,
    }),
  ).toBeVisible();
  await page.waitForTimeout(250);
  expect(
    state.calls.filter(
      (call) => call.method === "POST" && call.path.endsWith("/run"),
    ),
  ).toHaveLength(1);
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    card(page, 1).getByRole("button", { name: "Delete routine", exact: true }),
  ).toBeDisabled();
  await card(page, 1)
    .getByRole("button", { name: "Check run request", exact: true })
    .click();
  await panel
    .getByRole("button", { name: "Check the same run request", exact: true })
    .click();
  await expect(
    panel.getByRole("heading", { name: /Operation #1001/ }),
  ).toBeVisible();
  const posts = state.calls.filter(
    (call) => call.method === "POST" && call.path.endsWith("/run"),
  );
  expect(posts).toHaveLength(2);
  expect(posts[0]?.key).toBe(posts[1]?.key);
  expect(posts[0]?.body).toEqual(posts[1]?.body);
});
test("unknown creation retries only its immutable original body and key", async ({
  page,
}) => {
  const state = await setup(
    page,
    "/playlist/target1/setup?return_target=routines",
    "routines-creation-admission-unknown",
  );
  await page
    .getByLabel("Name a new collection playlist", { exact: true })
    .fill("The holding room");
  await page
    .getByRole("button", {
      name: "Create collection playlist in Spotify",
      exact: true,
    })
    .click();
  await page
    .getByLabel("Name a new collection playlist", { exact: true })
    .fill("An unrelated edited name");
  await page
    .getByRole("button", {
      name: "Retry this exact creation request",
      exact: true,
    })
    .click();
  await expect(
    page
      .getByText("Creation could not be confirmed.", { exact: false })
      .first(),
  ).toBeVisible({ timeout: 7000 });
  const posts = state.calls.filter((call) =>
    call.path.endsWith("/spotify-playlists"),
  );
  expect(posts).toHaveLength(2);
  expect(posts[0]?.key).toBe(posts[1]?.key);
  expect(posts[1]?.body).toEqual(posts[0]?.body);
  expect(posts[1]?.body).toEqual({
    purpose: "collection",
    name: "The holding room",
  });
  expect(
    state.calls.filter((call) => call.path.endsWith("/spotify-playlists")),
  ).toHaveLength(2);
});

for (const [scenario, state, label] of [
  ["routines-partial", "failed", "Collect — Completed"],
  ["routines-attention", "needs attention", "Rotate — Restoration verified"],
] as const)
  test(`the ${scenario} run displays its authoritative terminal facts without replay`, async ({
    page,
  }) => {
    const calls = await setup(page, "/routines", scenario);
    await card(page, 1)
      .getByRole("button", { name: "Run now", exact: true })
      .click();
    const panel = page.locator(".sh-task-panel:visible");
    await panel
      .getByRole("button", { name: "Run reviewed routine", exact: true })
      .click();
    await expect(
      panel.getByRole("heading", { name: `Operation #1001 · ${state}` }),
    ).toBeVisible({ timeout: 7000 });
    await expect(panel.getByText(label, { exact: true })).toBeVisible();
    expect(
      calls.calls.filter(
        (call) => call.method === "POST" && call.path.endsWith("/run"),
      ),
    ).toHaveLength(1);
  });

test("known unlinked creation is named before leaving even after its staged link is undone", async ({ page }) => {
  const state = await setup(page, "/playlist/target1/setup?return_target=routines", "routines-setup-empty");
  await page.getByLabel("Name a new collection playlist", { exact: true }).fill("A new holding room");
  await page.getByRole("button", { name: "Create collection playlist in Spotify", exact: true }).click();
  await expect(page.getByText("Created: A new holding room", { exact: true })).toBeVisible({ timeout: 7000 });
  await page.getByRole("link", { name: "Back to routines", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Leave this page?", exact: true });
  await expect(dialog).toContainText("A new holding room stays in your Spotify library.");
  await dialog.getByRole("button", { name: "Stay", exact: true }).click();
  await page.getByRole("button", { name: "Use created collection", exact: true }).click();
  await page.getByRole("button", { name: "Undo Link collection", exact: true }).click();
  await expect(page.getByText("0 of 20 changes staged", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Back to routines", exact: true }).click();
  await expect(dialog).toContainText("A new holding room stays in your Spotify library.");
  await dialog.getByRole("button", { name: "Stay", exact: true }).click();
  expect(state.calls.filter((value) => value.path.endsWith("/spotify-playlists"))).toHaveLength(1);
  expect(state.calls.filter((value) => value.method === "PUT")).toHaveLength(0);
  await page.getByRole("button", { name: "Use created collection", exact: true }).click();
  await page.getByRole("button", { name: "Review playlist settings", exact: true }).click();
  await page.getByLabel("I understand these settings affect existing routines", { exact: true }).check();
  await page.getByRole("button", { name: "Save playlist settings", exact: true }).click();
  await expect(page.getByText(/Playlist settings saved\./).first()).toBeVisible();
  await page.getByRole("link", { name: "Back to routines", exact: true }).click();
  await expect(page).toHaveURL("/routines");
  await expect(dialog).not.toBeVisible();
});

test("unknown manual run asks before leaving and retains the exact original check after returning", async ({ page }) => {
  const state = await setup(page, "/routines", "routines-happy", true);
  await card(page, 1).getByRole("button", {name: "Run now", exact: true}).click();
  const panel = page.locator(".sh-task-panel:visible");
  await panel.getByRole("button", {name: "Run reviewed routine", exact: true}).click();
  await expect(panel.getByRole("button", {name: "Check the same run request", exact: true})).toBeVisible();
  await panel.getByRole("button", {name: "Close", exact: true}).click();
  await page.getByRole("link", {name: "Create routine", exact: true}).click();
  const leave = page.getByRole("dialog", {name: "Leave this page?", exact: true});
  await expect(leave).toContainText("unconfirmed");
  await leave.getByRole("button", {name: "Stay", exact: true}).click();
  await expect(page).toHaveURL("/routines");
  await page.getByRole("link", {name: "Create routine", exact: true}).click();
  await leave.getByRole("button", {name: "Leave page", exact: true}).click();
  await expect(page).toHaveURL(/\/routines\/new\?origin=routines$/);
  await page.goBack();
  await card(page, 1).getByRole("button", {name: "Check run request", exact: true}).click();
  expect(state.calls.filter((call) => call.method === "POST" && call.path.endsWith("/run"))).toHaveLength(1);
  await panel.getByRole("button", {name: "Check the same run request", exact: true}).click();
  await expect(panel.getByRole("heading", {name: /Operation #1001/})).toBeVisible();
  const posts = state.calls.filter((call) => call.method === "POST" && call.path.endsWith("/run"));
  expect(posts).toHaveLength(2);
  expect(posts[1]?.key).toBe(posts[0]?.key);
  expect(posts[1]?.body).toEqual(posts[0]?.body);
});

const path = "/api/playlist/target1/setup";
const adding = "Enable adding collected tracks";
const archiving = "Automatically archive tracks removed in Workshop";
async function open(page: Page, linked = false) {
  const backend = createRoutinesFixtures("routines-happy");
  const context = structuredClone(seed.setup);
  if (linked) {
    context.collection = { ...context.collection, drip_enabled: true, drip_count: 7 };
    context.archive = { ...context.archive, auto_archive_on_remove: false };
  }
  const initial = {
    ...context,
    collection: linked ? context.collection : null,
    archive: linked ? context.archive : null,
  };
  const calls: { path: string; method: string; body: Record<string, unknown> | null }[] = [];
  let saved = false;
  await page.route("**/api/**", async (route) => {
    const req = route.request(), url = new URL(req.url()), method = req.method();
    const target = url.pathname + url.search;
    const body = req.postData() ? JSON.parse(req.postData()!) : null;
    calls.push({ path: target, method, body });
    if (url.pathname === "/api/session") return route.fulfill({ json: session });
    if (url.pathname === path + "/context" && !saved)
      return route.fulfill({ json: { success: true, context: initial, return_target: "routines" } });
    const response = await backend(target, method, {
      headers: req.headers(),
      ...(req.postData() ? { body: req.postData()! } : {}),
    });
    expect(response, "Every request has an explicit HTTP fixture").not.toBeNull();
    if (url.pathname === path + "/settings" && method === "PUT") saved = true;
    await route.fulfill({ status: response!.status, body: await response!.text(), headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  });
  await page.goto("/playlist/target1/setup?return_target=routines");
  await expect(page.getByRole("heading", { name: "Playlist setup · Evening collection", exact: true })).toBeVisible();
  return { calls, initial };
}
async function choose(page: Page, purpose: "collection" | "archive", id: string) {
  await page.getByRole("button", { name: `Refresh my playlists for ${purpose}`, exact: true }).click();
  await page.getByLabel(`Your playlists for ${purpose}`, { exact: true }).selectOption(id);
  await page.getByRole("button", { name: `Use existing ${purpose}`, exact: true }).click();
}
test("fresh staged collection and archive configure defaults in one acknowledged save and retain them on reopen", async ({ page }, info) => {
  const { calls, initial } = await open(page);
  await expect(page.getByLabel(adding, { exact: true })).toHaveCount(0);
  await expect(page.getByLabel(archiving, { exact: true })).toHaveCount(0);
  await choose(page, "collection", "choose1");
  await expect(page.locator("#setup-collection")).toContainText("Saved: Not linked");
  await expect(page.getByLabel(adding, { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Tracks added per run", { exact: true })).toHaveValue("3");
  await page.getByLabel(adding, { exact: true }).check();
  await page.getByLabel("Tracks added per run", { exact: true }).fill("4");
  await choose(page, "archive", "choose2");
  await expect(page.locator("#setup-archive")).toContainText("Saved: Not linked");
  await expect(page.getByLabel(archiving, { exact: true })).toBeChecked();
  await page.getByLabel(archiving, { exact: true }).uncheck();
  expect(initial.collection).toBeNull();
  expect(initial.archive).toBeNull();
  expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
  const changes = [
    { op: "link_collection", playlist_id: "choose1" },
    { op: "set_drip", drip_enabled: true, drip_count: 4 },
    { op: "pair_archive", playlist_id: "choose2" },
    { op: "set_auto_archive", value: false },
  ];
  await expect(page.getByText("4 of 20 changes staged", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Review playlist settings", exact: true }).click();
  expect(calls.find((call) => call.path === path + "/impact")?.body?.changes).toEqual(changes);
  const save = page.getByRole("button", { name: "Save playlist settings", exact: true });
  await expect(save).toBeDisabled();
  await page.getByLabel("I understand these settings affect existing routines", { exact: true }).check();
  await save.click();
  await expect(page.getByText("0 of 20 changes staged", { exact: true })).toBeVisible();
  const writes = calls.filter((call) => call.method === "PUT");
  expect(writes).toHaveLength(1);
  expect(writes[0]?.path).toBe(path + "/settings");
  expect(writes[0]?.body).toEqual({
    configuration_revision: initial.configuration_revision,
    changes,
    section_digests: initial.section_digests,
    acknowledgment: { signature: "fixture-settings-review" },
  });
  await info.attach("reviewed-settings-body", {
    body: JSON.stringify(writes[0]?.body),
    contentType: "application/json",
  });
  await page.reload();
  await expect(page.getByLabel(adding, { exact: true })).toBeChecked();
  await expect(page.getByLabel("Tracks added per run", { exact: true })).toHaveValue("4");
  await expect(page.getByLabel(archiving, { exact: true })).not.toBeChecked();
  expect(calls.filter((call) => call.method === "PUT")).toHaveLength(1);
});
test("replacement keeps saved settings while staged unlink hides controls and undo restores them without a write", async ({ page }) => {
  const { calls } = await open(page, true);
  await choose(page, "collection", "choose1");
  await choose(page, "archive", "choose2");
  await expect(page.getByLabel(adding, { exact: true })).toBeChecked();
  await expect(page.getByLabel("Tracks added per run", { exact: true })).toHaveValue("7");
  await expect(page.getByLabel(archiving, { exact: true })).not.toBeChecked();
  await page.getByRole("button", { name: "Unlink collection", exact: true }).click();
  await expect(page.getByLabel(adding, { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Tracks added per run", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Undo Unlink collection", exact: true }).click();
  await expect(page.getByLabel(adding, { exact: true })).toBeChecked();
  await expect(page.getByLabel("Tracks added per run", { exact: true })).toHaveValue("7");
  await page.getByRole("button", { name: "Unlink archive", exact: true }).click();
  await expect(page.getByLabel(archiving, { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Undo Unpair archive", exact: true }).click();
  await expect(page.getByLabel(archiving, { exact: true })).not.toBeChecked();
  expect(calls.filter((call) => call.method !== "GET")).toEqual([]);
});
