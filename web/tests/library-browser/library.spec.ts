import type { LibraryContext } from "../../src/api/contracts";
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createLibraryFixtures } from "../../src/fixtures/library";
import initial from "../../fixtures/library/initial.json" with { type: "json" };
import signedIn from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import activityInitial from "../../fixtures/activity/initial.json" with { type: "json" };
import signedOut from "../../fixtures/valid/session-signed-out.json" with { type: "json" };
const artworkRadius = (page: Page) =>
  page.evaluate(() => {
    const theme = getComputedStyle(document.documentElement);
    const wide = matchMedia(
      `(min-width: ${theme.getPropertyValue("--sh-breakpoint-workspace").trim()})`,
    ).matches;
    return theme
      .getPropertyValue(wide ? "--sh-radius-artwork-wide" : "--sh-radius-artwork")
      .trim();
  });
const fieldsOf = (form: FormData): Record<string, unknown> => {
  const fields: Record<string, unknown> = {};
  form.forEach((v, k) => {
    fields[k] = v;
  });
  return fields;
};
const headersOf = (headers: Headers): Record<string, string> => {
  const fields: Record<string, string> = {};
  headers.forEach((v, k) => {
    fields[k] = v;
  });
  return fields;
};
type Call = {
  path: string;
  method: string;
  fields: Record<string, unknown> | null;
  key: string | null;
  csrf: string | null;
};
async function setup(page: Page, scenario = "happy", context?: unknown) {
  const handler = createLibraryFixtures(scenario),
    calls: Call[] = [],
    errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().includes("Failed to load resource"))
      errors.push(m.text());
  });
  let actor = signedIn;
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      path = url.pathname + url.search,
      method = req.method(),
      headers = new Headers(req.headers());
    let body: BodyInit | undefined = req.postData() ?? undefined,
      fields: Record<string, unknown> | null = null;
    if (
      headers.get("content-type")?.startsWith("multipart/form-data") &&
      body
    ) {
      body = await new Request(req.url(), { method, headers, body }).formData();
      fields = fieldsOf(body);
    } else if (body) {
      try {
        fields = JSON.parse(String(body)) as Record<string, unknown>;
      } catch {
        fields = null;
      }
    }
    calls.push({
      path,
      method,
      fields,
      key: headers.get("Idempotency-Key"),
      csrf: headers.get("X-CSRFToken"),
    });
    if (path === "/api/session" && scenario !== "library-auth-loss")
      return route.fulfill({ json: actor });
    if (
      context &&
      (path === "/api/library" || path === "/api/library?refresh=1")
    )
      return route.fulfill({ json: context });
    const response = await handler(path, method, {
      headers,
      ...(body === undefined ? {} : { body }),
    }).catch(() => null);
    if (!response) return route.abort();
    return route.fulfill({
      status: response.status,
      headers: headersOf(response.headers),
      body: await response.text(),
    });
  });
  await page.goto("/library");
  return {
    calls,
    errors,
    setActor: (id: string) => {
      actor = { ...signedIn, user: { ...signedIn.user, id } };
    },
  };
}
const row = (page: Page, id = "alice-mix") =>
  page.locator(`[data-playlist-id="${id}"]`);
async function actions(page: Page, id = "alice-mix") {
  const r = row(page, id);
  await r.locator(">details>summary").click();
  return r;
}
async function review(page: Page, kind = "shuffle", id = "alice-mix") {
  const r = await actions(page, id);
  await r
    .getByRole("button", {
      name:
        kind === "undo"
          ? "Restore previous saved version now"
          : kind === "privacy"
            ? "Make private now"
            : `Shuffle now ${id === "alice-mix" ? "Evening rotation" : "Weekend discoveries"}`,
      exact: true,
    })
    .click();
  const panel = page.locator(".sh-task-panel:visible");
  await expect(
    panel.getByRole("heading", { name: "Playlist action", exact: true }),
  ).toBeFocused();
  await expect(panel.getByRole("button", { name: /^Run / })).toBeVisible();
  return panel;
}
const commands = (calls: Call[]) =>
  calls.filter(
    (c) =>
      c.method === "POST" &&
      (/^\/api\/(shuffle|undo)\//u.test(c.path) ||
        c.path.endsWith("/toggle-visibility")),
  );
test("ordered sections, returning greeting, Spotify links, responsive intact artwork, 44px controls and strict CSP", async ({
  page,
}) => {
  const state = await setup(page);
  await expect(
    page.getByRole("heading", { name: "Your playlists", exact: true }),
  ).toBeFocused();
  await expect(page).toHaveTitle("Your playlists · Shuffify");
  await expect(page.getByText("Welcome back, Alice!")).toBeVisible();
  await expect(
    page.getByText("1 thing happened since your last visit."),
  ).toBeVisible();
  await expect(
    page.locator('[data-section="favorites"]>.sh-library-row'),
  ).toHaveCount(1);
  await expect(
    page.locator('[data-section="playlists"]>.sh-library-row'),
  ).toHaveCount(3);
  await expect(row(page, "hidden-mix")).toBeHidden();
  for (const image of await page
    .locator(".sh-library-row:visible .sh-artwork-image")
    .all()) {
    expect(
      await image.evaluate((el) => getComputedStyle(el).borderRadius),
    ).toBe(await artworkRadius(page));
    const box = await image.boundingBox();
    expect(box?.width).toBe(64);
    expect(box?.height).toBe(64);
  }
  for (const link of await page
    .locator(".sh-library-row:visible figcaption a")
    .all()) {
    expect(await link.getAttribute("href")).toMatch(
      /^https:\/\/open\.spotify\.com\/playlist\//u,
    );
    expect(await link.getAttribute("rel")).toContain("noopener");
  }
  for (const control of await page
    .locator("main button,main summary,main a,main input,main select")
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
  expect(state.errors).toEqual([]);
  expect(state.calls.filter((c) => c.path === "/api/library")).toHaveLength(1);
});
test("no default opens method options without a review; cached options validate, Enter never dispatches, Escape returns focus", async ({
  page,
}) => {
  const state = await setup(page, "library-no-default");
  const r = await actions(page);
  await r
    .getByRole("button", {
      name: "Choose shuffle Evening rotation",
      exact: true,
    })
    .click();
  const select = r.getByRole("combobox", {
    name: "Shuffle method",
    exact: true,
  });
  await expect(select).toBeFocused();
  expect(
    state.calls.filter((c) => c.path.endsWith("/live-context")),
  ).toHaveLength(0);
  await select.selectOption("BasicShuffle");
  const keep = r.getByRole("spinbutton", {
    name: "Keep first tracks",
    exact: true,
  });
  await keep.fill("2");
  await keep.press("Enter");
  expect(commands(state.calls)).toHaveLength(0);
  await select.selectOption("PercentageShuffle");
  await select.selectOption("BasicShuffle");
  await expect(keep).toHaveValue("2");
  await keep.fill("13");
  await r
    .getByRole("button", { name: "Shuffle now Evening rotation", exact: true })
    .click();
  await expect(keep).toBeFocused();
  await expect(keep).toHaveAttribute("aria-invalid", "true");
  expect(
    state.calls.filter((c) => c.path.endsWith("/live-context")),
  ).toHaveLength(0);
  await keep.fill("1");
  await keep.press("Escape");
  await expect(r.locator("details>summary").last()).toBeFocused();
});
test("fresh review cancels with no write and restores trigger; duplicate Run dispatches one exact FormData with CSRF and stable key", async ({
  page,
}) => {
  const state = await setup(page);
  let panel = await review(page);
  await panel.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(
    row(page).getByRole("button", {
      name: "Shuffle now Evening rotation",
      exact: true,
    }),
  ).toBeFocused();
  expect(commands(state.calls)).toHaveLength(0);
  await row(page)
    .getByRole("button", { name: "Shuffle now Evening rotation", exact: true })
    .click();
  panel = page.locator(".sh-task-panel:visible");
  await panel
    .getByRole("button", { name: "Run shuffle", exact: true })
    .dblclick();
  await expect(
    panel.getByRole("heading", { name: "Operation #101", exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByRole("heading", { name: "Operation #101", exact: true }),
  ).toBeFocused();
  expect(commands(state.calls)).toHaveLength(1);
  const command = commands(state.calls)[0]!;
  expect(command.fields).toEqual({
    algorithm: "BasicShuffle",
    keep_first: "1",
    reviewed_scope: "a".repeat(64),
  });
  expect(command.csrf).toBe(signedIn.csrf_token);
  expect(command.key?.length).toBeGreaterThan(0);
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "View action results", exact: true }),
  ).toBeVisible();
  await expect
    .poll(
      () => state.calls.filter((c) => c.path === "/api/operations/101").length,
    )
    .toBeGreaterThanOrEqual(2);
  await page
    .getByRole("button", { name: "View action results", exact: true })
    .click();
  await expect(
    page.getByText("The playlist change was verified.", { exact: true }),
  ).toBeVisible();
  expect(commands(state.calls)).toHaveLength(1);
});
test("lost admission blocks random writes; explicit exact retry reuses frozen options/key and recovers one operation", async ({
  page,
}) => {
  const state = await setup(page, "library-unknown");
  const panel = await review(page);
  await panel.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await expect(
    panel.getByRole("button", {
      name: "Retry this exact request",
      exact: true,
    }),
  ).toBeVisible();
  expect(commands(state.calls)).toHaveLength(1);
  await panel
    .getByRole("button", { name: "Inspect current state", exact: true })
    .click();
  await expect(
    panel.getByText("Inspection result: unavailable", { exact: true }),
  ).toBeVisible();
  expect(
    state.calls.filter((call) => call.path.endsWith("/inspect-live")),
  ).toHaveLength(1);
  expect(
    state.calls.find((call) => call.path.endsWith("/inspect-live"))!.fields,
  ).toEqual({ kind: "tracks" });
  expect(commands(state.calls)).toHaveLength(1);
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByText(/New Spotify changes are paused/)).toBeVisible();
  await expect(
    row(page).getByRole("button", {
      name: "Shuffle now Evening rotation",
      exact: true,
    }),
  ).toHaveAttribute("aria-disabled", "true");
  await page
    .getByRole("button", { name: "View action results", exact: true })
    .click();
  await panel
    .getByRole("button", { name: "Retry this exact request", exact: true })
    .click();
  await expect(
    panel.getByRole("heading", { name: "Operation #101", exact: true }),
  ).toBeVisible();
  const writes = commands(state.calls);
  expect(writes).toHaveLength(2);
  expect(writes[1]!.key).toBe(writes[0]!.key);
  expect(writes[1]!.fields).toEqual(writes[0]!.fields);
  await expect(
    panel.getByText("The playlist change was verified.", { exact: true }),
  ).toBeVisible();
});
test("Undo binds saved history and ordered duplicate occurrences; visibility stays explicit JSON and updates only after terminal success", async ({
  page,
}) => {
  const state = await setup(page);
  let panel = await review(page, "undo");
  await expect(
    panel.getByText("0 added · 0 removed · order changes"),
  ).toBeVisible();
  await panel.getByRole("button", { name: "Run restore", exact: true }).click();
  await expect(
    panel.getByText("The playlist change was verified.", { exact: true }),
  ).toBeVisible();
  expect(commands(state.calls)[0]!.fields).toEqual({
    reviewed_scope: "a".repeat(64),
    reviewed_before_order: "b".repeat(64),
    reviewed_history: "c".repeat(64),
  });
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  await row(page)
    .getByRole("button", { name: "Make private now", exact: true })
    .click();
  panel = page.locator(".sh-task-panel:visible");
  await expect(panel.getByText("Change from public to private.")).toBeVisible();
  await panel
    .getByRole("button", { name: "Run visibility change", exact: true })
    .click();
  await expect(
    panel.getByText("The playlist change was verified.", { exact: true }),
  ).toBeVisible();
  expect(commands(state.calls)[1]!.fields).toEqual({
    public: false,
    reviewed_scope: "a".repeat(64),
  });
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    row(page).getByRole("button", { name: "Make public now", exact: true }),
  ).toBeVisible();
  await expect(
    row(page).getByRole("button", {
      name: "Restore previous saved version now",
      exact: true,
    }),
  ).toHaveCount(0);
});
test("preferences use completed DB writes, correct placement and returned focus, Hidden stays collapsed", async ({
  page,
}) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "Organize", exact: true }).click();
  const add = row(page, "bob-mix").getByRole("button", {
    name: "Add favorite Weekend discoveries",
    exact: true,
  });
  await add.focus();
  await add.press("Enter");
  await expect(
    page.locator('[data-section="favorites"]>.sh-library-row'),
  ).toHaveCount(2);
  await expect(
    row(page, "bob-mix").getByRole("button", {
      name: "Remove favorite Weekend discoveries",
      exact: true,
    }),
  ).toBeFocused();
  const hide = row(page, "bob-mix").getByRole("button", {
    name: "Hide playlist Weekend discoveries",
    exact: true,
  });
  await hide.focus();
  await hide.press("Enter");
  await expect(
    row(page).getByRole("button", {
      name: "Hide playlist Evening rotation",
      exact: true,
    }),
  ).toBeFocused();
  await expect(row(page, "bob-mix")).toBeHidden();
  await page.getByRole("button", { name: "Hidden 2", exact: true }).click();
  await expect(row(page, "bob-mix")).toBeVisible();
  await row(page, "bob-mix")
    .getByRole("button", {
      name: "Show playlist Weekend discoveries",
      exact: true,
    })
    .click();
  await expect(
    row(page, "bob-mix").getByRole("button", {
      name: "Hide playlist Weekend discoveries",
      exact: true,
    }),
  ).toBeFocused();
  await page
    .getByRole("button", { name: "Done organizing", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Hidden 1", exact: true }),
  ).toHaveAttribute("aria-expanded", "false");
  expect(commands(state.calls)).toHaveLength(0);
  expect(
    state.calls.filter((c) => c.path.includes("/playlist-preferences/")),
  ).toHaveLength(3);
});
test("keyboard ordering, clamped position, validation, native drag and clean resting DOM persist exact section order", async ({
  page,
}) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "Organize", exact: true }).click();
  const r = row(page, "bob-mix");
  await r
    .getByRole("button", { name: "Move down Weekend discoveries", exact: true })
    .click();
  await expect(
    page.locator('[data-section="playlists"]>.sh-library-row').first(),
  ).toHaveAttribute("data-playlist-id", "claire-mix");
  const position = r.getByRole("spinbutton", {
    name: "Move to position for Weekend discoveries",
    exact: true,
  });
  await position.fill("");
  await position.press("Enter");
  await expect(position).toBeFocused();
  await expect(position).toHaveAttribute("aria-invalid", "true");
  await position.fill("99");
  await position.press("Enter");
  await expect(
    page.locator('[data-section="playlists"]>.sh-library-row').last(),
  ).toHaveAttribute("data-playlist-id", "bob-mix");
  await expect
    .poll(() => state.calls.filter((c) => c.path.endsWith("/order")).length)
    .toBe(2);
  await expect(page.getByText("Order saved.", { exact: true })).toBeVisible();
  const source = row(page, "bob-mix").getByRole("button", {
      name: "Drag to reorder Weekend discoveries",
      exact: true,
    }),
    target = row(page, "claire-mix");
  await source.scrollIntoViewIfNeeded();
  const start = (await source.boundingBox())!;
  await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    start.x + start.width / 2 + 10,
    start.y + start.height / 2 + 10,
    { steps: 5 },
  );
  await target.evaluate((el) =>
    el.scrollIntoView({ block: "center", behavior: "instant" }),
  );
  const drop = (await target.boundingBox())!;
  await page.mouse.move(drop.x + 30, drop.y + 20, { steps: 20 });
  // Native Sortable suppresses another swap for 30ms. A human pause lets the
  // second crossed row receive dragover before release; no app state is mocked.
  await page.waitForTimeout(60);
  await page.mouse.move(drop.x + 31, drop.y + 20 + 1);
  await page.mouse.up();
  await expect
    .poll(() => state.calls.filter((c) => c.path.endsWith("/order")).length)
    .toBe(3);
  expect(
    state.calls.filter((c) => c.path.endsWith("/order")).at(-1)!.fields
      ?.playlist_ids,
  ).toEqual(["alice-mix", "bob-mix", "claire-mix", "empty-mix"]);
  await expect(page.locator("[style]")).toHaveCount(0);
  expect(state.errors).toEqual([]);
});
test("reset confirmation Cancel writes nothing; accepted reset removes preferences with refreshed GET", async ({
  page,
}) => {
  const state = await setup(page);
  await page.getByRole("button", { name: "Organize", exact: true }).click();
  await page
    .getByRole("button", { name: "Reset preferences", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("button", { name: "Cancel", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  expect(state.calls.filter((c) => c.path.endsWith("/reset"))).toHaveLength(0);
  await page
    .getByRole("button", { name: "Reset preferences", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "Reset preferences", exact: true })
    .click();
  await expect(
    page.locator('[data-section="playlists"]>.sh-library-row'),
  ).toHaveCount(5);
  await expect(page.locator('[data-section="favorites"]')).toHaveCount(0);
  expect(
    state.calls.filter((c) => c.path === "/api/library?refresh=1"),
  ).toHaveLength(1);
  expect(commands(state.calls)).toHaveLength(0);
});
test("read failure, empty, rate limit and refresh remain honest GET states", async ({
  page,
}) => {
  let state = await setup(page, "library-read-failure");
  await expect(page.getByText("Playlists could not be read.")).toBeVisible();
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(row(page)).toBeVisible();
  await page.unrouteAll({ behavior: "wait" });
  state = await setup(page, "library-empty");
  await expect(
    page.getByRole("heading", { name: "No playlists yet" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect
    .poll(
      () =>
        state.calls.filter((c) => c.path === "/api/library?refresh=1").length,
    )
    .toBe(1);
  await page.unrouteAll({ behavior: "wait" });
  state = await setup(page, "library-rate-limit");
  await expect(page.getByText("Try again in 2 seconds.")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Try again", exact: true }),
  ).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByText(/Try again in/)).toHaveCount(0, {
    timeout: 4000,
  });
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(row(page)).toBeVisible();
});
test("known preference failure preserves placement; unknown preference reconciles by fresh GET without replay", async ({
  page,
}) => {
  let state = await setup(page, "library-preference-failure");
  await page.getByRole("button", { name: "Organize", exact: true }).click();
  await row(page, "bob-mix")
    .getByRole("button", {
      name: "Add favorite Weekend discoveries",
      exact: true,
    })
    .click();
  await expect(page.getByText("Preferences were not saved.")).toBeVisible();
  await expect(
    page.locator('[data-section="playlists"]>[data-playlist-id="bob-mix"]'),
  ).toBeVisible();
  expect(
    state.calls.filter((c) => c.path.includes("toggle-pinned")),
  ).toHaveLength(1);
  await page.unrouteAll({ behavior: "wait" });
  state = await setup(page, "library-preference-unknown");
  await page.getByRole("button", { name: "Organize", exact: true }).click();
  await row(page, "bob-mix")
    .getByRole("button", {
      name: "Add favorite Weekend discoveries",
      exact: true,
    })
    .click();
  await expect(page.getByText(/This preference may have saved/)).toBeVisible();
  await expect(
    row(page, "bob-mix").getByRole("button", {
      name: "Add favorite Weekend discoveries",
      exact: true,
    }),
  ).toHaveAttribute("aria-disabled", "true");
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(
    page.locator('[data-section="favorites"]>[data-playlist-id="bob-mix"]'),
  ).toBeVisible();
  expect(
    state.calls.filter((c) => c.path.includes("toggle-pinned")),
  ).toHaveLength(1);
});
test("queue refusal and busy operation preserve details; recovery retains exact per-resource and failed history facts", async ({
  page,
}) => {
  let state = await setup(page, "library-busy");
  let panel = await review(page);
  await panel.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await expect(
    panel.getByRole("heading", { name: "Operation #99", exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByText(
      "This playlist is busy. This is the existing operation, not a new request.",
    ),
  ).toBeVisible();
  expect(commands(state.calls)).toHaveLength(1);
  await page.unrouteAll({ behavior: "wait" });
  state = await setup(page, "library-recovery");
  panel = await review(page);
  await panel.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await expect(
    panel.getByText("Saved playlist history: failed", { exact: true }),
  ).toBeVisible();
  await expect(
    panel.getByText(
      "Restore needs a fresh inspection before any further write.",
      { exact: true },
    ),
  ).toBeVisible();
  await panel
    .getByText("Exact retained operation details", { exact: true })
    .click();
  await expect(panel.locator("pre")).toContainText(
    '"state": "needs_attention"',
  );
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByText(/New Spotify changes are paused/)).toBeVisible();
  expect(commands(state.calls)).toHaveLength(1);
});
test("auth loss hides retained task and requires connection; reduced motion leaves no animation or overflow", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const state = await setup(page, "library-auth-loss");
  const panel = await review(page);
  await panel.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Connect to Spotify", exact: true }),
  ).toBeVisible();
  await expect(panel).toBeHidden();
  expect(commands(state.calls)).toHaveLength(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  expect(await page.locator("[style],script:not([src]),style").count()).toBe(0);
});
test("unavailable accepted status retains ID and GET-only explicit retry, with no command replay", async ({
  page,
}) => {
  const state = await setup(page, "library-status-failure");
  const panel = await review(page);
  await panel.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await expect(
    panel.getByRole("button", { name: "Retry status read", exact: true }),
  ).toBeVisible({ timeout: 12000 });
  expect(commands(state.calls)).toHaveLength(1);
  expect(
    state.calls.filter((c) => c.path === "/api/operations/101"),
  ).toHaveLength(4);
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  await page
    .getByRole("button", { name: "View action results", exact: true })
    .click();
  await panel
    .getByRole("button", { name: "Retry status read", exact: true })
    .click();
  await expect(
    panel.getByText("The playlist change was verified.", { exact: true }),
  ).toBeVisible();
  expect(
    state.calls.filter((c) => c.path === "/api/operations/101"),
  ).toHaveLength(5);
  expect(commands(state.calls)).toHaveLength(1);
});
test("HTTP503 retains ambiguity and normalized response facts without automatic resubmission", async ({
  page,
}) => {
  const state = await setup(page, "library-queue-failure");
  const panel = await review(page);
  await panel.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await expect(
    panel.getByText(/The request may have been accepted/),
  ).toBeVisible();
  await panel.getByText("Exact response details", { exact: true }).click();
  await expect(panel.locator("pre")).toContainText(
    '"error_code": "library_error"',
  );
  await expect(panel.locator("pre")).toContainText(
    "Queue admission was refused.",
  );
  await expect(
    panel.getByRole("button", {
      name: "Retry this exact request",
      exact: true,
    }),
  ).toBeVisible();
  expect(commands(state.calls)).toHaveLength(1);
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  await page
    .getByRole("button", { name: "View action results", exact: true })
    .click();
  expect(commands(state.calls)).toHaveLength(1);
});
test("all server methods render every parameter and edited options activate navigation confirmation", async ({
  page,
}) => {
  const state = await setup(page);
  const r = await actions(page);
  await r.locator("details>summary").last().click();
  const method = r.getByRole("combobox", {
    name: "Shuffle method",
    exact: true,
  });
  await expect(method.locator("option")).toHaveCount(
    initial.algorithms.length + 1,
  );
  for (const algorithm of initial.algorithms) {
    await method.selectOption(algorithm.class_name);
    for (const [name, info] of Object.entries(algorithm.parameters)) {
      const label =
        name === "keep_first" ? "Keep first tracks" : info.description;
      await expect(r.getByLabel(label, { exact: true })).toBeVisible();
    }
  }
  const menu = page.locator(".sh-app-menu>summary");
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("link", { name: "Activity", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "Stay", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL("/library");
  expect(commands(state.calls)).toHaveLength(0);
});
test("authoritative account change hides retained task details and disables exact-request resubmission", async ({
  page,
}) => {
  const state = await setup(page, "library-unknown");
  const panel = await review(page);
  await panel.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await expect(
    panel.getByRole("button", {
      name: "Retry this exact request",
      exact: true,
    }),
  ).toBeVisible();
  state.setActor("bob-account");
  await page.route("**/api/shuffle/alice-mix", (route) =>
    route.fulfill({
      status: 401,
      json: {
        success: false,
        error_code: "unauthorized",
        category: "error",
        message: "Session changed.",
      },
    }),
  );
  await panel
    .getByRole("button", { name: "Retry this exact request", exact: true })
    .click();
  await expect(
    panel.getByRole("heading", {
      name: "Confirm the original session",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    panel.getByText("Evening rotation", { exact: true }),
  ).toHaveCount(0);
  await expect(
    panel.getByRole("button", {
      name: "Retry this exact request",
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(panel.locator("pre")).toHaveCount(0);
});
test("real artwork is contained with official backlink and logo clear space; broken artwork falls back without layout change", async ({
  page,
}) => {
  const context = structuredClone(initial) as LibraryContext;
  context.sections.favorites[0]!.image_url =
    "https://i.scdn.co/image/synthetic-fixture";
  await page.route("https://i.scdn.co/image/synthetic-fixture", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="64" viewBox="0 0 128 64"><rect width="128" height="64" fill="#374045"/><circle cx="64" cy="32" r="24" fill="#c9ed75"/><path d="M0 0h16v16H0zM112 48h16v16h-16z" fill="#9cdfca"/></svg>',
    }),
  );
  await setup(page, "happy", context);
  const image = row(page).getByRole("img", {
    name: "Evening rotation",
    exact: true,
  });
  await expect(image).toBeVisible();
  expect(
    await image.evaluate((el) => ({
      fit: getComputedStyle(el).objectFit,
      radius: getComputedStyle(el).borderRadius,
      width: (el as HTMLImageElement).naturalWidth,
      height: (el as HTMLImageElement).naturalHeight,
    })),
  ).toEqual({ fit: "contain", radius: await artworkRadius(page), width: 128, height: 64 });
  const logo = row(page).getByRole("img", { name: "Spotify", exact: true });
  expect(
    await logo.evaluate((el) => ({
      height: el.getBoundingClientRect().height,
      top: getComputedStyle(el).marginTop,
      bottom: getComputedStyle(el).marginBottom,
    })),
  ).toEqual({ height: 24, top: "12px", bottom: "12px" });
  await page.route("https://i.scdn.co/image/synthetic-fixture", (route) =>
    route.fulfill({ status: 404, body: "" }),
  );
  await page.reload();
  await expect(
    row(page).getByRole("img", {
      name: "No artwork available for Evening rotation",
      exact: true,
    }),
  ).toBeVisible();
  await expect(row(page).locator(".sh-artwork-frame")).toHaveCSS(
    "width",
    "64px",
  );
});

test("accepted operation keeps polling across SPA page navigation and returns to the same retained Library receipt", async ({
  page,
}) => {
  const state = await setup(page);
  await page.route("**/api/activity", (route) =>
    route.fulfill({ json: activityInitial }),
  );
  const panel = await review(page);
  await panel.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await panel
    .getByRole("heading", { name: "Operation #101", exact: true })
    .waitFor();
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  let menu = page.locator(".sh-app-menu>summary");
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("link", { name: "Activity", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Activity", exact: true }),
  ).toBeVisible();
  await expect
    .poll(
      () => state.calls.filter((c) => c.path === "/api/operations/101").length,
    )
    .toBeGreaterThanOrEqual(2);
  menu = page.locator(".sh-app-menu>summary");
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("link", { name: "Library", exact: true }).click();
  await page
    .getByRole("button", { name: "View action results", exact: true })
    .click();
  await expect(
    page.getByText("The playlist change was verified.", { exact: true }),
  ).toBeVisible();
  expect(state.calls.filter((c) => c.path === "/api/library")).toHaveLength(1);
  expect(commands(state.calls)).toHaveLength(1);
});
