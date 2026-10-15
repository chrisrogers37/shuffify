import { mkdir, writeFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { createLibraryFixtures } from "../../src/fixtures/library";
import { createRoutinesFixtures } from "../../src/fixtures/routines";
import { workshopContext } from "../../src/fixtures/workshop";
import session from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import type { Operation } from "../../src/api/contracts";
type Mode = "library" | "workshop" | "routine" | "setup";
type AdmissionCall = {
  path: string;
  key: string | null;
  body: unknown;
  actor: string;
};
const requestProofs = new WeakMap<Page, AdmissionCall[]>();
async function backend(
  page: Page,
  mode: Mode,
  firstStatus: number | "lost" = "lost",
) {
  const library = createLibraryFixtures("happy"),
    routines = createRoutinesFixtures("routines-setup-empty");
  const calls: AdmissionCall[] = [],
    receipts = new Map<string, Operation>();
  requestProofs.set(page, calls);
  let actor = "alice",
    unauthorized = false,
    lost = false;
  await page.route("**/api/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname,
      method = req.method(),
      headers = new Headers(req.headers());
    let body: BodyInit | undefined = req.postData() ?? undefined,
      parsed: unknown = null;
    if (
      body &&
      headers.get("content-type")?.startsWith("multipart/form-data")
    ) {
      body = await new Request(req.url(), { method, headers, body }).formData();
      const fields: Record<string, FormDataEntryValue> = {};
      body.forEach((v, k) => (fields[k] = v));
      parsed = fields;
    } else if (body) parsed = JSON.parse(String(body));
    if (path === "/api/session")
      return route.fulfill({
        json: {
          ...session,
          user: {
            ...session.user,
            id: actor,
            display_name: actor === "alice" ? "Alice" : "Bob",
          },
        },
      });
    if (path === "/api/library" && unauthorized) {
      unauthorized = false;
      return route.fulfill({
        status: 401,
        json: {
          success: false,
          error_code: "unauthorized",
          category: "error",
          message: "Confirm account",
        },
      });
    }
    const command =
      method === "POST" &&
      /^\/api\/(shuffle\/|workshop\/[^/]+\/commit|schedules\/\d+\/run|playlist\/[^/]+\/setup\/spotify-playlists)/u.test(
        path,
      );
    if (command) {
      const key = headers.get("Idempotency-Key")!;
      calls.push({ path, key, body: parsed, actor });
      let op = receipts.get(key);
      if (!op) {
        op = {
          id: 501 + receipts.size,
          kind: path.includes("/shuffle/")
            ? "shuffle"
            : path.endsWith("/commit")
              ? "workshop_commit"
              : path.endsWith("/run")
                ? "schedule_run"
                : "setup_playlist_create",
          state: "queued",
          targets: mode === "routine" ? ["other", "alice-mix"] : ["alice-mix"],
          started_at: null,
          completed_at: null,
          progress: {},
          result: null,
          recovery: { available: false, action: null, reason: null },
        };
        receipts.set(key, op);
      }
      const matches =
        mode === "library"
          ? path.includes("/shuffle/")
          : mode === "workshop"
            ? path.endsWith("/commit")
            : mode === "routine"
              ? path.endsWith("/run")
              : path.endsWith("/spotify-playlists");
      if (matches && !lost) {
        lost = true;
        if (firstStatus === "lost") return route.abort();
        return route.fulfill({
          status: firstStatus,
          json: {
            success: false,
            error_code: "upstream",
            category: "error",
            message: "Admission response is uncertain",
          },
        });
      }
      return route.fulfill({
        status: 202,
        json: { success: true, operation_id: op.id, operation: op },
      });
    }
    const poll = /^\/api\/operations\/(\d+)$/u.exec(path);
    if (poll) {
      const original = [...receipts.values()].find(
        (o) => o.id === Number(poll[1]),
      );
      if (!original) return route.abort();
      const result =
        original.kind === "setup_playlist_create"
          ? {
              success: true,
              outcome: "created",
              playlist: { id: "created", name: "Original holding room" },
              privacy_confirmed: true,
            }
          : original.kind === "workshop_commit"
            ? {
                success: true,
                target_status: "saved",
                confirmed_batches: 1,
                total_batches: 1,
                requires_refresh: false,
                retryable: false,
                message: "Saved",
                saved_track_uris: workshopContext()
                  .tracks.slice(1)
                  .map((t) => t.uri),
              }
            : { success: true, message: "Completed" };
      return route.fulfill({
        json: {
          success: true,
          operation: {
            ...original,
            state: "succeeded",
            completed_at: "2026-01-01T12:00:00Z",
            result,
          },
        },
      });
    }
    if (path === "/api/workshop/alice-mix/locks" && method === "GET")
      return route.fulfill({ json: { success: true, locks: [] } });
    if (path === "/api/workshop/alice-mix") {
      const ctx = workshopContext("alice-mix");
      ctx.playlist.name = "Evening rotation";
      ctx.playlist.owner_id = actor;
      return route.fulfill({ json: ctx });
    }
    if (path.includes("/source-context"))
      return route.fulfill({
        json: {
          success: true,
          target: { id: "alice-mix", name: "Evening rotation" },
          raid: null,
          archive: null,
        },
      });
    const rr = await routines(path, method, {
      headers,
      ...(body ? { body } : {}),
    }).catch(() => null);
    if (rr) {
      let text = (await rr.text()).replaceAll("target1", "alice-mix");
      if (mode === "routine" && path === "/api/routines") {
        const data = JSON.parse(text);
        for (const r of data.routines) {
          r.schedule.target_playlist_id = "other";
          r.definition.target_playlist_id = "other";
          r.schedule.target_playlist_name = "Other playlist";
          r.definition.target_playlist_name = "Other playlist";
        }
        text = JSON.stringify(data);
      }
      if (mode === "routine" && path.endsWith("/run-context")) {
        const data = JSON.parse(text);
        data.context.resources = [
          {
            resource: "other",
            resource_name: "Other playlist",
            role: "target",
            domain: "spotify",
            access: "write",
          },
          {
            resource: "alice-mix",
            resource_name: "Evening rotation",
            role: "source",
            domain: "spotify",
            access: "read",
          },
        ];
        text = JSON.stringify(data);
      }
      return route.fulfill({
        status: rr.status,
        body: text,
        contentType: "application/json",
      });
    }
    const r = await library(path, method, {
      headers,
      ...(body ? { body } : {}),
    }).catch(() => null);
    if (!r) return route.abort();
    return route.fulfill({
      status: r.status,
      body: await r.text(),
      contentType: "application/json",
    });
  });
  return {
    calls,
    receipts,
    setActor(value: string) {
      actor = value;
      unauthorized = true;
    },
  };
}
const row = (page: Page) => page.locator('[data-playlist-id="alice-mix"]');
async function nav(page: Page, name: string) {
  const menu = page.locator(".sh-app-menu");
  if (await menu.isVisible()) await menu.locator("summary").click();
  await page
    .getByRole("navigation", { name: "Main navigation", exact: true })
    .getByRole("link", { name, exact: true })
    .filter({ visible: true })
    .click();
}
async function libraryShuffle(page: Page) {
  await expect(
    page.getByRole("heading", { name: "Your playlists", exact: true }),
  ).toBeVisible();
  await row(page).locator("summary").first().click();
  await row(page)
    .getByRole("button", { name: "Shuffle now Evening rotation", exact: true })
    .click();
  await page.getByRole("button", { name: "Run shuffle", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Retry this exact request", exact: true }),
  ).toBeVisible();
}
const errors = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  const e: string[] = [];
  errors.set(page, e);
  page.on("pageerror", (x) => e.push(x.message));
  await page.addInitScript(() => {
    (window as unknown as { violations: string[] }).violations = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      (window as unknown as { violations: string[] }).violations.push(
        e.violatedDirective,
      ),
    );
  });
});
test.afterEach(async ({ page }, info) => {
  expect(errors.get(page)).toEqual([]);
  expect(
    await page.evaluate(
      () => (window as unknown as { violations: string[] }).violations,
    ),
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
  await mkdir(info.outputDir, { recursive: true });
  const screen = info.outputPath("final-state.png"),
    tree = info.outputPath("accessibility-tree.txt"),
    requests = info.outputPath("exact-requests.json");
  await page.screenshot({ path: screen });
  await writeFile(tree, await page.locator("body").ariaSnapshot());
  await writeFile(requests, JSON.stringify(requestProofs.get(page), null, 2));
  await info.attach("final-state", { path: screen, contentType: "image/png" });
  await info.attach("accessibility-tree", {
    path: tree,
    contentType: "text/plain",
  });
  await info.attach("exact-requests", {
    path: requests,
    contentType: "application/json",
  });
});
for (const response of ["lost", 503] as const)
  test(`Library ${response} admission blocks Workshop across navigation; explicit retry sends same frozen intent`, async ({
    page,
  }) => {
    const state = await backend(page, "library", response);
    await page.goto("/library");
    await libraryShuffle(page);
    await page.getByRole("button", { name: "Close", exact: true }).click();
    await row(page)
      .getByRole("link", {
        name: "Open playlist Evening rotation",
        exact: true,
      })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Leave page", exact: true })
      .click();
    await expect(
      page.getByRole("heading", {
        name: "Evening rotation",
        exact: true,
        level: 1,
      }),
    ).toBeVisible();
    await expect(
      page.getByText(/Shuffle now · Evening rotation is not reconciled/),
    ).toBeVisible();
    await page
      .locator("[data-occurrence]")
      .first()
      .getByRole("button", { name: /^Actions/ })
      .click();
    await page
      .getByRole("button", { name: "Remove from draft", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Review & save", exact: true }),
    ).toBeDisabled();
    expect(state.calls).toHaveLength(1);
    await nav(page, "Library");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Discard", exact: true })
      .click();
    await page
      .getByRole("button", { name: "View action results", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Retry this exact request", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "Operation #501", exact: true }),
    ).toBeVisible();
    expect(state.calls).toHaveLength(2);
    expect(state.calls[1]?.key).toBe(state.calls[0]?.key);
    expect(state.calls[1]?.body).toEqual(state.calls[0]?.body);
    expect(state.receipts.size).toBe(1);
  });
test("Workshop unknown admission blocks Library after Leave page without repeating a target POST", async ({
  page,
}) => {
  const state = await backend(page, "workshop");
  await page.goto("/workshop/alice-mix");
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
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await nav(page, "Library");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Leave page", exact: true })
    .click();
  await expect(
    row(page).getByText(/Save Evening rotation is awaiting reconciliation/),
  ).toBeVisible();
  await row(page).locator("summary").first().click();
  await expect(
    row(page).getByRole("button", {
      name: "Shuffle now Evening rotation",
      exact: true,
    }),
  ).toHaveAttribute("aria-disabled", "true");
  expect(state.calls).toHaveLength(1);
});
test("routine source read claim blocks another feature writing that source playlist", async ({
  page,
}) => {
  const state = await backend(page, "routine");
  await page.goto("/routines");
  await page
    .locator("#routine-1")
    .getByRole("button", { name: "Run now", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Run reviewed routine", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Check the same run request",
      exact: true,
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await nav(page, "Library");
  const leave = page.getByRole("dialog", { name: "Leave this page?", exact: true });
  await expect(leave).toContainText("A run request is unconfirmed.");
  await leave.getByRole("button", { name: "Leave page", exact: true }).click();
  await expect(
    row(page).getByText(
      /Run routine #1 · Other playlist is awaiting reconciliation/,
    ),
  ).toBeVisible();
  await row(page).locator("summary").first().click();
  await expect(
    row(page).getByRole("button", {
      name: "Shuffle now Evening rotation",
      exact: true,
    }),
  ).toHaveAttribute("aria-disabled", "true");
  expect(state.calls).toHaveLength(1);
});
test("setup creation no-ID hold survives navigation and retries original name/purpose/key", async ({
  page,
}) => {
  const state = await backend(page, "setup", 502);
  await page.goto("/playlist/alice-mix/setup?return_target=library");
  await page
    .getByLabel("Name a new collection playlist", { exact: true })
    .fill("Original holding room");
  await page
    .getByRole("button", {
      name: "Create collection playlist in Spotify",
      exact: true,
    })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Retry this exact creation request",
      exact: true,
    }),
  ).toBeVisible();
  await nav(page, "Library");
  await expect(
    row(page).getByText(/Create collection “Original holding room”/),
  ).toBeVisible();
  await page.goBack();
  await page
    .getByLabel("Name a new collection playlist", { exact: true })
    .fill("Changed local input");
  await page
    .getByRole("button", {
      name: "Retry this exact creation request",
      exact: true,
    })
    .click();
  await expect(
    page.getByText("Created: Original holding room", { exact: true }),
  ).toBeVisible({ timeout: 7000 });
  expect(state.calls).toHaveLength(2);
  expect(state.calls[0]?.key).toBe(state.calls[1]?.key);
  expect(state.calls[1]?.body).toEqual({
    purpose: "collection",
    name: "Original holding room",
  });
  expect(state.receipts.size).toBe(1);
});
test("authoritative actor change isolates the old unknown receipt and forbids its retry from Bob", async ({
  page,
}) => {
  const state = await backend(page, "library");
  await page.goto("/library");
  await libraryShuffle(page);
  await page.getByRole("button", { name: "Close", exact: true }).click();
  state.setActor("bob");
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "View action results", exact: true }),
  ).toHaveCount(0);
  await row(page).locator("summary").first().click();
  await expect(
    row(page).getByRole("button", {
      name: "Shuffle now Evening rotation",
      exact: true,
    }),
  ).not.toHaveAttribute("aria-disabled", "true");
  expect(state.calls).toHaveLength(1);
  state.setActor("alice");
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page
    .getByRole("button", { name: "View action results", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry this exact request", exact: true }),
  ).toBeVisible();
  expect(state.calls).toHaveLength(1);
});
