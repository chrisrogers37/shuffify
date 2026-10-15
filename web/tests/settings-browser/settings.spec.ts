import { test, expect } from "@playwright/test";
import type { Page, Route } from "@playwright/test";
import initial from "../../fixtures/valid/settings-custom-params.json" with { type: "json" };
import signedIn from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import signedOut from "../../fixtures/valid/session-signed-out.json" with { type: "json" };
const error = (message: string, extra: object = {}) => ({
  success: false,
  error_code: "settings_error",
  category: "error",
  message,
  ...extra,
});
async function setup(
  page: Page,
  post?: (route: Route, index: number) => Promise<void>,
) {
  const posts: { body: Record<string, unknown>; csrf: string | undefined }[] =
    [];
  let settings = structuredClone(initial),
    signed = true,
    token = signedIn.csrf_token,
    gets = 0;
  await page.route("**/api/session", (route) =>
    route.fulfill({
      json: signed ? { ...signedIn, csrf_token: token } : signedOut,
    }),
  );
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "GET") {
      gets++;
      await route.fulfill({ json: settings });
      return;
    }
    posts.push({
      body: route.request().postDataJSON(),
      csrf: route.request().headers()["x-csrftoken"],
    });
    if (post) {
      await post(route, posts.length);
      return;
    }
    settings = {
      ...settings,
      message: "Settings saved successfully.",
      settings: { ...settings.settings, ...route.request().postDataJSON() },
    };
    await route.fulfill({ json: settings });
  });
  await page.goto("/settings");
  return {
    posts,
    get gets() {
      return gets;
    },
    setSigned(value: boolean) {
      signed = value;
    },
    setToken(value: string) {
      token = value;
    },
  };
}
const limit = (page: Page) =>
  page.getByRole("spinbutton", { name: "Maximum snapshots per playlist" });
const save = (page: Page) =>
  page.getByRole("button", { name: "Save settings", exact: true });
test("complete settings groups use typed saved values, current identity, local fonts and keyboard focus", async ({
  page,
}) => {
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") violations.push(msg.text());
  });
  await setup(page);
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeFocused();
  await expect(page).toHaveTitle("Settings · Shuffify");
  for (const name of [
    "Shuffle defaults",
    "Snapshots & History",
    "Library",
    "Account",
  ])
    await expect(
      page.getByRole("heading", { name, exact: true }),
    ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Default shuffle algorithm" }),
  ).toHaveValue("BasicShuffle");
  await expect(limit(page)).toHaveValue("10");
  await expect(
    page.getByRole("checkbox", {
      name: "Save a snapshot before shuffling",
      exact: true,
    }),
  ).toBeChecked();
  await expect(
    page.getByText("Connected as Alice.", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(
      "Theme selection is not available yet. Your saved preference is preserved.",
    ),
  ).toBeVisible();
  const controls = page.locator(
    "#settings-form select,#settings-form input:not([type=hidden]),#settings-form button,#settings-logout-form button",
  );
  for (const control of await controls.all()) {
    const box = await control.evaluate((el) =>
      (el instanceof HTMLInputElement && el.type === "checkbox"
        ? el.closest("label")!
        : el
      )
        .getBoundingClientRect()
        .toJSON(),
    );
    expect(box.height).toBeGreaterThanOrEqual(44);
    expect(box.width).toBeGreaterThanOrEqual(44);
  }
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("combobox", { name: "Default shuffle algorithm" }),
  ).toBeFocused();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > innerWidth,
  );
  expect(overflow).toBe(false);
  expect(await page.locator("[style],script:not([src]),style").count()).toBe(0);
  expect(violations).toEqual([]);
});
test("keyboard save submits real booleans and numeric limit once with CSRF then clears draft guard", async ({
  page,
}) => {
  const state = await setup(page);
  await page
    .getByRole("combobox", { name: "Default shuffle algorithm" })
    .selectOption("");
  await page
    .getByRole("checkbox", {
      name: "Save a snapshot before shuffling",
      exact: true,
    })
    .uncheck();
  await page
    .getByRole("checkbox", { name: "Show recent activity", exact: true })
    .uncheck();
  await limit(page).fill("25");
  await save(page).focus();
  await page.keyboard.press("Enter");
  await expect(
    page
      .locator(".sh-toast")
      .getByText("Settings saved successfully.", { exact: true }),
  ).toBeVisible();
  expect(state.posts).toEqual([
    {
      body: {
        default_algorithm: null,
        auto_snapshot_enabled: false,
        max_snapshots_per_playlist: 25,
        dashboard_show_recent_activity: false,
      },
      csrf: signedIn.csrf_token,
    },
  ]);
  await expect(save(page)).toBeFocused();
  await page.getByRole("link", { name: "Privacy Policy", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Privacy Policy", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
test("pending save stays focusable, disables edits, and blocks duplicate click and Enter submits", async ({
  page,
}) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const state = await setup(page, async (route) => {
    await gate;
    await route.fulfill({
      json: {
        ...initial,
        message: "Settings saved successfully.",
        settings: { ...initial.settings, max_snapshots_per_playlist: 22 },
      },
    });
  });
  await limit(page).fill("22");
  await save(page).click();
  const pending = page.getByRole("button", { name: "Saving…", exact: true });
  await expect(pending).toBeFocused();
  await expect(limit(page)).toBeDisabled();
  await pending.click({ force: true });
  await page.keyboard.press("Enter");
  expect(state.posts).toHaveLength(1);
  release();
  await expect(save(page)).toBeFocused();
  await expect(limit(page)).toBeEnabled();
  expect(state.posts).toHaveLength(1);
});
test("whole number validation focuses the field without POST", async ({
  page,
}) => {
  const state = await setup(page);
  for (const value of ["", "0", "51", "1.5"]) {
    await limit(page).fill(value);
    await save(page).click();
    await expect(limit(page)).toBeFocused();
    await expect(limit(page)).toHaveAttribute("aria-invalid", "true");
    await expect(
      page.getByText("Choose a whole number from 1 to 50.", { exact: true }),
    ).toBeVisible();
  }
  expect(state.posts).toHaveLength(0);
});
test("server field errors retain entered values and focus an actionable summary", async ({
  page,
}) => {
  const state = await setup(page, async (route) => {
    await route.fulfill({
      status: 400,
      json: error("Check your settings.", {
        field_errors: [
          {
            path: "max_snapshots_per_playlist",
            message: "Choose at most 20 snapshots.",
          },
        ],
      }),
    });
  });
  await limit(page).fill("30");
  await save(page).click();
  await expect(page.locator("#settings-error")).toBeFocused();
  await expect(limit(page)).toHaveValue("30");
  await page
    .locator("#settings-error")
    .getByRole("link", { name: "Maximum snapshots per playlist" })
    .click();
  await expect(limit(page)).toBeFocused();
  await expect(
    page.getByText("Choose at most 20 snapshots.", { exact: true }),
  ).toBeVisible();
  expect(state.posts).toHaveLength(1);
});
test("failed save retains draft and retries only on explicit activation", async ({
  page,
}) => {
  const state = await setup(page, async (route, index) => {
    await route.fulfill(
      index === 1
        ? { status: 500, json: error("Settings could not be saved.") }
        : {
            json: {
              ...initial,
              message: "Settings saved successfully.",
              settings: { ...initial.settings, max_snapshots_per_playlist: 18 },
            },
          },
    );
  });
  await limit(page).fill("18");
  await save(page).click();
  await expect(page.locator("#settings-error")).toBeFocused();
  await expect(limit(page)).toHaveValue("18");
  await expect(
    page
      .locator(".sh-toast")
      .getByText("Settings saved successfully.", { exact: true }),
  ).toHaveCount(0);
  expect(state.posts).toHaveLength(1);
  await save(page).click();
  await expect(
    page
      .locator(".sh-toast")
      .getByText("Settings saved successfully.", { exact: true }),
  ).toBeVisible();
  expect(state.posts).toHaveLength(2);
});
test("rate limit honors full hint without automatic retry or duplicate POST", async ({
  page,
}) => {
  const state = await setup(page, async (route, index) => {
    await route.fulfill(
      index === 1
        ? {
            status: 429,
            headers: { "Retry-After": "1" },
            json: error("Wait before saving again.", { retry_after: 2 }),
          }
        : { json: { ...initial, message: "Settings saved successfully." } },
    );
  });
  await limit(page).fill("11");
  await save(page).click();
  await expect(page.getByText(/Try again in 2 seconds/)).toBeVisible();
  await save(page).click({ force: true });
  expect(state.posts).toHaveLength(1);
  await expect(page.getByText(/Try again in/)).toHaveCount(0, {
    timeout: 4000,
  });
  expect(state.posts).toHaveLength(1);
  await save(page).click();
  await expect(
    page
      .locator(".sh-toast")
      .getByText("Settings saved successfully.", { exact: true }),
  ).toBeVisible();
  expect(state.posts).toHaveLength(2);
});
test("conflict is authored failure with no queued-operation polling", async ({
  page,
}) => {
  const unexpected: string[] = [];
  await page.route("**/api/operations/**", (route) => {
    unexpected.push(route.request().url());
    return route.abort();
  });
  await setup(page, async (route) => {
    await route.fulfill({
      status: 409,
      json: error("Your preferences changed. Review them before saving again."),
    });
  });
  await limit(page).fill("13");
  await save(page).click();
  await expect(page.locator("#settings-error")).toContainText(
    "Your preferences changed.",
  );
  await expect(limit(page)).toHaveValue("13");
  expect(unexpected).toEqual([]);
});
test("interrupted response remains unconfirmed with draft retained", async ({
  page,
}) => {
  const state = await setup(page, async (route) => {
    await route.abort("connectionreset");
  });
  await limit(page).fill("14");
  await save(page).click();
  await expect(page.locator("#settings-error")).toContainText(
    "Could not confirm your settings were saved.",
  );
  await expect(limit(page)).toHaveValue("14");
  expect(state.posts).toHaveLength(1);
});
test("dirty navigation cancellation restores clicked link focus and leaving is explicit", async ({
  page,
}) => {
  await setup(page);
  await limit(page).fill("12");
  const privacy = page.getByRole("link", {
    name: "Privacy Policy",
    exact: true,
  });
  await privacy.click();
  await expect(
    page.getByRole("dialog", { name: "Leave this page?" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Stay", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(privacy).toBeFocused();
  await expect(limit(page)).toHaveValue("12");
  await privacy.click();
  await page.getByRole("button", { name: "Leave page", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Privacy Policy", exact: true }),
  ).toBeVisible();
});
test("native signout posts only root logout and CSRF; dirty draft can cancel first", async ({
  page,
}) => {
  const state = await setup(page);
  const logouts: { method: string; body: string | null }[] = [];
  await page.route("**/logout", (route) => {
    logouts.push({
      method: route.request().method(),
      body: route.request().postData(),
    });
    return route.fulfill({
      contentType: "text/html",
      body: '<!doctype html><html lang="en"><title>Signed out</title><h1>Signed out</h1></html>',
    });
  });
  await page.evaluate(() => {
    sessionStorage.setItem("shuffify:react-routine-continuation:v1", "retained routine choices");
    sessionStorage.setItem("unrelated-test", "keep");
  });
  await limit(page).fill("17");
  const logout = page.getByRole("button", { name: "Log out", exact: true });
  await logout.click();
  await page.getByRole("button", { name: "Stay", exact: true }).click();
  await expect(logout).toBeFocused();
  expect(logouts).toHaveLength(0);
  expect(await page.evaluate(() => sessionStorage.getItem("shuffify:react-routine-continuation:v1"))).toBe("retained routine choices");
  await logout.click();
  await page.keyboard.press("Escape");
  await expect(logout).toBeFocused();
  expect(logouts).toHaveLength(0);
  expect(await page.evaluate(() => sessionStorage.getItem("shuffify:react-routine-continuation:v1"))).toBe("retained routine choices");
  await logout.click();
  await page.getByRole("button", { name: "Leave page", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Signed out" })).toBeVisible();
  expect(logouts).toEqual([
    { method: "POST", body: `csrf_token=${signedIn.csrf_token}` },
  ]);
  expect(state.posts).toHaveLength(0);
  expect(await page.evaluate(() => sessionStorage.getItem("shuffify:react-routine-continuation:v1"))).toBeNull();
  expect(await page.evaluate(() => sessionStorage.getItem("unrelated-test"))).toBe("keep");
});
test("authentication loss retains draft, reconnect check refreshes CSRF, and never replays failed save", async ({
  page,
}) => {
  let state: Awaited<ReturnType<typeof setup>>;
  state = await setup(page, async (route, index) => {
    if (index === 1) {
      state.setSigned(false);
      await route.fulfill({
        status: 401,
        json: error("Your session ended.", {
          error_code: "authentication_required",
        }),
      });
    } else
      await route.fulfill({
        json: {
          ...initial,
          message: "Settings saved successfully.",
          settings: { ...initial.settings, max_snapshots_per_playlist: 19 },
        },
      });
  });
  await limit(page).fill("19");
  await save(page).click();
  await expect(
    page.getByRole("heading", { name: "Connect to Spotify", exact: true }),
  ).toBeVisible();
  expect(state.posts).toHaveLength(1);
  state.setSigned(true);
  state.setToken("fresh-settings-csrf");
  await page
    .getByRole("button", { name: "Check connection", exact: true })
    .click();
  await expect(limit(page)).toHaveValue("19");
  expect(state.posts).toHaveLength(1);
  await save(page).click();
  await expect(
    page
      .locator(".sh-toast")
      .getByText("Settings saved successfully.", { exact: true }),
  ).toBeVisible();
  expect(state.posts[1]?.csrf).toBe("fresh-settings-csrf");
});
test("settings load error is recoverable and does not expose an empty editable form", async ({
  page,
}) => {
  await page.route("**/api/session", (route) =>
    route.fulfill({ json: signedIn }),
  );
  let gets = 0;
  await page.route("**/api/settings", (route) =>
    route.fulfill(
      ++gets === 1
        ? { status: 503, json: error("Settings are temporarily unavailable.") }
        : { json: initial },
    ),
  );
  await page.goto("/settings");
  await expect(
    page.getByRole("heading", { name: "Settings could not be loaded" }),
  ).toBeVisible();
  await expect(limit(page)).toHaveCount(0);
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(limit(page)).toHaveValue("10");
  expect(gets).toBe(2);
});

test("clean signout uses native POST without a confirmation dialog", async ({
  page,
}) => {
  const state = await setup(page);
  await page.evaluate(() => {
    sessionStorage.setItem("shuffify:react-routine-continuation:v1", "retained routine choices");
    sessionStorage.setItem("unrelated-test", "keep");
  });
  const calls: string[] = [];
  await page.route("**/logout", (route) => {
    calls.push(route.request().method());
    return route.fulfill({
      contentType: "text/html",
      body: '<!doctype html><html lang="en"><title>Signed out</title><h1>Signed out</h1></html>',
    });
  });
  await page.getByRole("button", { name: "Log out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Signed out" })).toBeVisible();
  expect(calls).toEqual(["POST"]);
  expect(state.posts).toHaveLength(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem("shuffify:react-routine-continuation:v1"))).toBeNull();
  expect(await page.evaluate(() => sessionStorage.getItem("unrelated-test"))).toBe("keep");
});
test("repeated successful saves produce distinct accessible announcement events", async ({
  page,
}) => {
  await setup(page);
  await save(page).click();
  await expect(page.locator(".sh-toast")).toContainText(
    "Settings saved successfully.",
  );
  const live = page.locator('[aria-live="polite"] > span');
  const first = await live.elementHandle();
  await page.getByRole("button", { name: "Dismiss", exact: true }).click();
  await save(page).click();
  await expect(page.locator(".sh-toast")).toContainText(
    "Settings saved successfully.",
  );
  await expect
    .poll(async () => first?.evaluate((node) => node.isConnected))
    .toBe(false);
});
test("read rate limit waits before an explicit GET retry and does not retry automatically", async ({
  page,
}) => {
  await page.route("**/api/session", (route) =>
    route.fulfill({ json: signedIn }),
  );
  let gets = 0;
  await page.route("**/api/settings", (route) =>
    route.fulfill(
      ++gets === 1
        ? {
            status: 429,
            headers: { "Retry-After": "1" },
            json: error("Wait before loading settings.", { retry_after: 1 }),
          }
        : { json: initial },
    ),
  );
  await page.goto("/settings");
  await expect(
    page.getByRole("heading", { name: "Settings could not be loaded" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Try again", exact: true })
    .click({ force: true });
  expect(gets).toBe(1);
  await expect(page.getByText(/Try again in/)).toHaveCount(0);
  expect(gets).toBe(1);
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(limit(page)).toHaveValue("10");
  expect(gets).toBe(2);
});

test("storage failure cannot block a clean native signout", async ({ page }) => {
  await setup(page);
  const calls: string[] = [];
  await page.route("**/logout", (route) => {
    calls.push(route.request().method());
    return route.fulfill({
      contentType: "text/html",
      body: '<!doctype html><html lang="en"><title>Signed out</title><h1>Signed out</h1></html>',
    });
  });
  await page.evaluate(() => {
    Storage.prototype.removeItem = () => { throw new DOMException("Storage is unavailable", "SecurityError"); };
  });
  await page.getByRole("button", { name: "Log out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Signed out" })).toBeVisible();
  expect(calls).toEqual(["POST"]);
});
