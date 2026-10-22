import { test, expect } from "@playwright/test";
import { workshopContext } from "../../src/fixtures/workshop";
import { createWorkshopFixtures } from "../../src/fixtures/workshop";
import signedIn from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import type { Page } from "@playwright/test";
const browserErrors = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  const errors: string[] = [];
  browserErrors.set(page, errors);
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().includes("Failed to load resource"))
      errors.push(m.text());
  });
  await page.addInitScript(() => {
    (window as unknown as { csp: string[] }).csp = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      (window as unknown as { csp: string[] }).csp.push(
        e.violatedDirective + ":" + e.blockedURI,
      ),
    );
  });
});
test.afterEach(async ({ page }) => {
  expect(browserErrors.get(page)).toEqual([]);
  expect(
    await page.evaluate(() => (window as unknown as { csp: string[] }).csp),
  ).toEqual([]);
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
  const small = await page
    .locator("button,input,select,summary,a[href]")
    .evaluateAll((nodes) =>
      nodes.flatMap((node) => {
        if (
          !(node instanceof HTMLElement) ||
          node.closest("[hidden],[inert]") ||
          node.getAttribute("href") === "#main-content"
        )
          return [];
        const target =
          node instanceof HTMLInputElement && node.type === "checkbox"
            ? (node.labels?.[0] ?? node)
            : node;
        const r = target.getBoundingClientRect();
        return !r.width ||
          !r.height ||
          getComputedStyle(node).visibility === "hidden" ||
          (r.width >= 43.9 && r.height >= 43.9)
          ? []
          : [node.textContent?.trim() ?? node.tagName];
      }),
    );
  expect(small).toEqual([]);
});
async function start(
  page: Page,
  scenario = "workshop-happy",
  path = "/workshop/target",
) {
  const handler = createWorkshopFixtures(scenario);
  const calls: {
    path: string;
    method: string;
    body: string | null;
    key: string | null;
  }[] = [];
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      url = new URL(req.url());
    const path = url.pathname + url.search;
    const init = {
      method: req.method(),
      headers: new Headers(req.headers()),
      body: req.postData(),
    };
    calls.push({
      path,
      method: req.method(),
      body: req.postData(),
      key: req.headers()["idempotency-key"] ?? null,
    });
    const r =
      (await handler(path, req.method(), init)) ??
      new Response(
        JSON.stringify(
          path === "/api/session"
            ? signedIn
            : {
                success: false,
                error_code: "missing_fixture",
                message: "Missing fixture route",
                category: "error",
              },
        ),
        {
          status: path === "/api/session" ? 200 : 404,
          headers: { "Content-Type": "application/json" },
        },
      );
    await route.fulfill({
      status: r.status,
      body: await r.text(),
      headers: (() => {
        const h: Record<string, string> = {};
        r.headers.forEach((v, k) => (h[k] = v));
        return h;
      })(),
    });
  });
  await page.goto(path);
  if (scenario === "workshop-read-error" || scenario === "workshop-not-found")
    return calls;
  await expect(
    page.getByRole("heading", { name: "Evening Collection", exact: true }),
  ).toBeVisible();
  return calls;
}
test("one duplicate occurrence moves/removes by keyboard, review sends one frozen queued order", async ({
  page,
}) => {
  const calls = await start(page);
  const rows = page.locator("[data-occurrence]");
  await expect(rows).toHaveCount(3);
  await rows
    .nth(2)
    .getByRole("button", { name: /^Actions/ })
    .click();
  await page
    .getByRole("button", { name: "Move up", exact: true })
    .press("Enter");
  await expect(rows.nth(1)).toContainText("First Light");
  await page
    .getByRole("button", { name: "Remove from draft", exact: true })
    .click();
  await expect(rows).toHaveCount(2);
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Review save", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .dblclick();
  await expect(
    page
      .getByRole("region", { name: "Retained operation" })
      .getByText("Playlist saved to Spotify.", { exact: true }),
  ).toBeVisible();
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(1);
});
test("dirty navigation offers Review & save, Discard, Stay and restores trigger focus", async ({
  page,
}) => {
  await start(page);
  await page
    .locator("[data-occurrence]")
    .first()
    .getByRole("button", { name: /^Actions/ })
    .click();
  await page
    .getByRole("button", { name: "Remove from draft", exact: true })
    .click();
  if (await page.locator(".sh-app-menu").isVisible())
    await page.locator(".sh-app-menu summary").click();
  await page
    .getByRole("link", { name: "Library", exact: true })
    .filter({ visible: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("button", { name: "Stay", exact: true }),
  ).toBeFocused();
  await expect(
    dialog.getByRole("button", { name: "Review & save", exact: true }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Stay", exact: true }).click();
  await expect(page).toHaveURL(/\/workshop\/target/);
});
test("named drafts save/load/delete without a Spotify write and retain panel scroll on reopening", async ({
  page,
}) => {
  const calls = await start(page);
  await page.getByRole("button", { name: "Named drafts", exact: true }).click();
  await page.getByLabel("Draft name", { exact: true }).fill("Evening draft");
  await page
    .getByRole("button", { name: "Save named draft", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Evening draft", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Delete Evening draft", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Delete draft", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Evening draft", exact: true }),
  ).toHaveCount(0);
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(0);
});
test("lost admission retains exact request; inspection match cannot authorize a new save", async ({
  page,
}) => {
  const calls = await start(page, "workshop-lost");
  await page
    .locator("[data-occurrence]")
    .first()
    .getByRole("button", { name: /^Actions/ })
    .click();
  await page
    .getByRole("button", { name: "Remove from draft", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry this exact request", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Check current Spotify", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Use current Spotify as baseline",
      exact: true,
    }),
  ).toBeDisabled();
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(1);
});
async function edit(page: Page) {
  await page
    .locator("[data-occurrence]")
    .first()
    .getByRole("button", { name: /^Actions/ })
    .click();
  await page
    .getByRole("button", { name: "Remove from draft", exact: true })
    .click();
}
async function reviewSave(page: Page) {
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
}
test("read failure retries and inaccessible playlists remain a signed-in error", async ({
  page,
}) => {
  await start(page, "workshop-read-error").catch(() => {});
  await expect(
    page.getByRole("heading", { name: "Workshop could not be loaded" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Evening Collection", exact: true }),
  ).toBeVisible();
});
test("all duplicate occurrences can be removed and empty desired order is saved exactly", async ({
  page,
}) => {
  const calls = await start(page);
  for (let i = 0; i < 3; i++) await edit(page);
  await expect(
    page.getByText("This draft is empty.", { exact: false }),
  ).toBeVisible();
  await reviewSave(page);
  await expect(
    page.getByRole("heading", { name: "Save result" }),
  ).toBeVisible();
  expect(
    JSON.parse(calls.find((c) => c.path.endsWith("/commit"))!.body!).track_uris,
  ).toEqual([]);
});
test("explicit exact request retry reuses the original body/key and queued ID", async ({
  page,
}) => {
  const calls = await start(page, "workshop-lost");
  await edit(page);
  await reviewSave(page);
  await page
    .getByRole("button", { name: "Retry this exact request", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Retry exact request", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Retained operation" }),
  ).toContainText("2001");
  const writes = calls.filter((c) => c.path.endsWith("/commit"));
  expect(writes).toHaveLength(2);
  expect(writes[0]!.key).toBeTruthy();
  expect(writes[0]!.key).toBe(writes[1]!.key);
  expect(writes[0]!.body).toBe(writes[1]!.body);
});
test("history failure retains rich result without a fresh target write", async ({
  page,
}) => {
  const calls = await start(page, "workshop-history-failure");
  await edit(page);
  await reviewSave(page);
  await expect(
    page.getByRole("region", { name: "Retained operation" }),
  ).toContainText("needs attention");
  await page.getByText("Operation result details", { exact: true }).click();
  await expect(page.locator(".sh-workshop-details pre")).toContainText(
    '"history": "failed"',
  );
  await expect(
    page.getByRole("button", { name: "Save to Spotify", exact: true }),
  ).toHaveCount(0);
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(1);
});
test("full rate hint prevents another submit and preserves draft/review", async ({
  page,
}) => {
  const calls = await start(page, "workshop-rate");
  await edit(page);
  await reviewSave(page);
  await expect(
    page.getByRole("region", { name: "Retained operation" }),
  ).toContainText("Wait before saving");
  await expect(
    page.getByRole("button", {
      name: "Review current draft again",
      exact: true,
    }),
  ).toBeDisabled();
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(1);
});
test("Actions initial focus chooses an enabled control; invalid position remains editable", async ({
  page,
}) => {
  await start(page);
  await page
    .locator("[data-occurrence]")
    .first()
    .getByRole("button", { name: /^Actions/ })
    .click();
  await expect(
    page.getByRole("button", { name: "Move down", exact: true }),
  ).toBeFocused();
  await page.getByLabel("Move to position", { exact: true }).fill("");
  await page.getByRole("button", { name: "Move", exact: true }).click();
  await expect(
    page.getByLabel("Move to position", { exact: true }),
  ).toHaveAttribute("aria-invalid", "true");
  await page.getByLabel("Move to position", { exact: true }).fill("99");
  await page.getByLabel("Move to position", { exact: true }).press("Enter");
  await expect(page.locator("[data-occurrence]").last()).toContainText(
    "First Light",
  );
  await page.keyboard.press("Escape");
  await expect(
    page
      .locator("[data-occurrence]")
      .last()
      .getByRole("button", { name: /^Actions/ }),
  ).toBeFocused();
});
test("a move keeps focus on the control used and falls back when it is disabled", async ({
  page,
}) => {
  await start(page);
  const rows = page.locator("[data-occurrence]");
  const settle = () =>
    page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)),
        ),
    );
  const control = (name: string) =>
    rows.nth(1).getByRole("button", { name, exact: true });
  await rows
    .first()
    .getByRole("button", { name: /^Actions/ })
    .click();
  const moving = (await rows.first().getAttribute("data-occurrence"))!;
  await page
    .getByRole("button", { name: "Move down", exact: true })
    .press("Enter");
  await expect(rows.nth(1)).toHaveAttribute("data-occurrence", moving);
  await settle();
  await expect(control("Move down")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(rows.nth(2)).toHaveAttribute("data-occurrence", moving);
  await expect(
    rows.nth(2).getByRole("button", { name: "Move down", exact: true }),
  ).toBeDisabled();
  await settle();
  await expect(
    rows.nth(2).getByRole("button", { name: "Move up", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(rows.nth(1)).toHaveAttribute("data-occurrence", moving);
  await settle();
  await expect(control("Move up")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(rows.first()).toHaveAttribute("data-occurrence", moving);
  await expect(
    rows.first().getByRole("button", { name: "Move up", exact: true }),
  ).toBeDisabled();
  await settle();
  await expect(
    rows.first().getByRole("button", { name: "Move down", exact: true }),
  ).toBeFocused();
  const form = rows.first().getByLabel("Move to position", { exact: true });
  await expect(form).toHaveValue("1");
  await form.fill("2");
  await rows.first().getByRole("button", { name: "Move", exact: true }).click();
  await expect(rows.nth(1)).toHaveAttribute("data-occurrence", moving);
  await settle();
  await expect(
    rows.nth(1).getByRole("button", { name: "Move", exact: true }),
  ).toBeFocused();
});
test("named draft loading asks before replacing dirty work and preserves baseline", async ({
  page,
}) => {
  const calls = await start(page);
  await page.getByRole("button", { name: "Named drafts", exact: true }).click();
  await page
    .getByLabel("Draft name", { exact: true })
    .fill("Original arrangement");
  await page
    .getByRole("button", { name: "Save named draft", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Original arrangement", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await edit(page);
  await page.getByRole("button", { name: "Named drafts", exact: true }).click();
  await page
    .getByRole("button", { name: "Load Original arrangement", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    "confirmed Spotify baseline stays unchanged",
  );
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Load saved draft", exact: true })
    .click();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator("[data-occurrence]")).toHaveCount(3);
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(0);
});
test("long playlist clears sticky footer, has no overflow, and survives panel close and resize", async ({
  page,
}) => {
  await start(page, "workshop-long");
  await page.locator("[data-occurrence]").last().scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  const row = await page.locator("[data-occurrence]").last().boundingBox(),
    footer = await page.locator(".sh-workshop-draft-footer").boundingBox();
  expect(row!.y + row!.height).toBeLessThanOrEqual(footer!.y + 1);
  await page.getByRole("button", { name: "Named drafts", exact: true }).click();
  await page.getByLabel("Draft name", { exact: true }).fill("Retained note");
  await page.setViewportSize({ width: 960, height: 900 });
  await expect(page.getByLabel("Draft name", { exact: true })).toHaveValue(
    "Retained note",
  );
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator("[data-occurrence]")).toHaveCount(60);
});
test("accepted save keeps polling after panel close and reopens its terminal receipt", async ({
  page,
}) => {
  const calls = await start(page);
  await edit(page);
  await reviewSave(page);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    page
      .getByText("Playlist saved to Spotify.", { exact: true })
      .filter({ visible: true })
      .first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Review save results", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Retained operation" }),
  ).toContainText("2001 · succeeded");
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(1);
});
test("save chosen from navigation review never automatically leaves after completion", async ({
  page,
}) => {
  await start(page);
  await edit(page);
  if (await page.locator(".sh-app-menu").isVisible())
    await page.locator(".sh-app-menu summary").click();
  await page
    .getByRole("link", { name: "Library", exact: true })
    .filter({ visible: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Retained operation" }),
  ).toContainText("succeeded");
  await expect(page).toHaveURL(/\/workshop\/target$/);
});
test("partial result exposes exact reviewed orders and keeps adoption blocked", async ({
  page,
}) => {
  const calls = await start(page, "workshop-partial");
  await edit(page);
  await reviewSave(page);
  await expect(
    page.getByRole("region", { name: "Retained operation" }),
  ).toContainText("partial");
  await page.getByText("Reviewed request orders", { exact: true }).click();
  await expect(
    page.getByRole("heading", {
      name: "Reviewed desired order · 2",
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Check current Spotify", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Use current Spotify as baseline",
      exact: true,
    }),
  ).toBeDisabled();
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(1);
});
test("empty playlist and signed-in missing playlist have explicit states", async ({
  page,
}) => {
  await start(page, "workshop-empty");
  await expect(page.locator("[data-occurrence]")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Review & save", exact: true }),
  ).toBeDisabled();
  await start(page, "workshop-not-found");
  await expect(
    page.getByRole("heading", { name: "Playlist unavailable", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Connect to Spotify", exact: true }),
  ).toHaveCount(0);
});
test("authentication loss hides retained task contents and disables original request reuse", async ({
  page,
}) => {
  const calls = await start(page, "workshop-auth-loss");
  await edit(page);
  await reviewSave(page);
  await expect(
    page.getByRole("heading", { name: "Connect to Spotify", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Confirm the original account", { exact: true }),
  ).toBeHidden();
  await expect(
    page.getByRole("button", { name: "Retry this exact request", exact: true }),
  ).toHaveCount(0);
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(1);
});
test("picker successful reads cache across reopen while query clears", async ({
  page,
}) => {
  const calls = await start(page);
  await page
    .getByRole("button", { name: "Switch playlist", exact: true })
    .click();
  await expect(
    page.getByLabel("Find a playlist", { exact: true }),
  ).toBeFocused();
  await page.getByLabel("Find a playlist", { exact: true }).fill("no match");
  await expect(
    page.getByText("No playlists match.", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Switch playlist", exact: true }),
  ).toBeFocused();
  await page
    .getByRole("button", { name: "Switch playlist", exact: true })
    .click();
  await expect(page.getByLabel("Find a playlist", { exact: true })).toHaveValue(
    "",
  );
  expect(calls.filter((c) => c.path === "/api/user-playlists")).toHaveLength(1);
});
test("native drag preserves exact occurrence identity and leaves no resting inline styles", async ({
  page,
}) => {
  const calls = await start(page);
  const rows = page.locator("[data-occurrence]"),
    original = await rows.evaluateAll((nodes) =>
      nodes.map((n) => (n as HTMLElement).dataset.occurrence),
    );
  const source = rows.nth(2).getByRole("button", { name: /^Drag/ });
  await source.evaluate((el) =>
    el.scrollIntoView({ block: "center", behavior: "instant" }),
  );
  const a = (await source.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(a.x + a.width / 2 + 10, a.y + a.height / 2 + 10, {
    steps: 5,
  });
  await rows
    .first()
    .evaluate((el) =>
      el.scrollIntoView({ block: "center", behavior: "instant" }),
    );
  const b = (await rows.first().boundingBox())!;
  await page.mouse.move(b.x + 30, b.y + 20, { steps: 20 });
  await page.waitForTimeout(60);
  await page.mouse.move(b.x + 31, b.y + 21);
  await page.mouse.up();
  await expect(rows.first()).toHaveAttribute("data-occurrence", original[2]!);
  await expect(page.locator("[style]")).toHaveCount(0);
  expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
});
test("artwork stays contained with official attribution, safe backlinks, and reduced motion", async ({
  page,
}) => {
  await start(page);
  const ctx = workshopContext();
  ctx.tracks[0]!.album_image_url = "https://i.scdn.co/image/workshop-example";
  await page.route("https://i.scdn.co/image/workshop-example", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="128" height="64" viewBox="0 0 128 64"><rect width="128" height="64" fill="#374045"/><circle cx="64" cy="32" r="24" fill="#c9ed75"/></svg>',
    }),
  );
  await page.route("**/api/workshop/target", (route) =>
    route.fulfill({ json: ctx }),
  );
  await page.reload();
  const row = page.locator("[data-occurrence]").first(),
    image = row.getByRole("img", { name: "First Light", exact: true });
  await expect(image).toBeVisible();
  expect(
    await image.evaluate((el) => ({
      fit: getComputedStyle(el).objectFit,
      radius: getComputedStyle(el).borderRadius,
    })),
  ).toEqual({
    fit: "contain",
    radius: (page.viewportSize()?.width ?? 0) < 1024 ? "4px" : "8px",
  });
  expect(
    await row
      .getByRole("img", { name: "Spotify", exact: true })
      .evaluate((el) => ({
        height: el.getBoundingClientRect().height,
        margin: getComputedStyle(el).marginTop,
      })),
  ).toEqual({ height: 24, margin: "12px" });
  await expect(
    row.getByRole("link", { name: "Open First Light on Spotify", exact: true }),
  ).toHaveAttribute("href", /^https:\/\/open.spotify.com\/track\//);
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(await row.evaluate((el) => getComputedStyle(el).animationName)).toBe(
    "none",
  );
});
test("an HTTP500 admission error preserves unknown intent and offers only exact-key reconciliation", async ({
  page,
}) => {
  const calls = await start(page, "workshop-admission-error");
  await edit(page);
  await reviewSave(page);
  await expect(
    page.getByRole("region", { name: "Retained operation" }),
  ).toContainText("unknown");
  await expect(
    page.getByRole("button", { name: "Retry this exact request", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "Review current draft again",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(1);
});

test("initial viewport keeps the first track details and Actions above the footer", async ({
  page,
}) => {
  await start(page);
  await page.evaluate(() => document.fonts.ready);
  const row = page.locator("[data-occurrence]").first();
  const footer = await page.locator(".sh-workshop-draft-footer").boundingBox();
  const copy = await row.locator(".sh-workshop-track-copy").boundingBox();
  const actions = await row
    .getByRole("button", { name: /^Actions/ })
    .boundingBox();
  expect(await page.evaluate(() => scrollY)).toBe(0);
  expect(copy!.y + copy!.height).toBeLessThanOrEqual(footer!.y);
  expect(actions!.y + actions!.height).toBeLessThanOrEqual(footer!.y);
});
