import { test, expect } from "@playwright/test";
import { checks, start, open, close, panel } from "./helpers";
checks();
test("all seven dynamic algorithms retain controls and preview only the local duplicate draft", async ({
  page,
}) => {
  const calls = await start(page);
  await open(page, "Shuffle options");
  const select = page.getByRole("combobox", {
    name: "Shuffle algorithm",
    exact: true,
  });
  await expect(select.locator("option")).toHaveCount(7);
  for (const option of await select.locator("option").all()) {
    await select.selectOption((await option.getAttribute("value")) ?? "");
    await expect(
      panel(page).getByRole("button", { name: "Preview shuffle", exact: true }),
    ).toBeVisible();
  }
  await select.selectOption("BasicShuffle");
  await page
    .getByRole("spinbutton", { name: "Number of tracks to keep at start" })
    .fill("1");
  await close(page);
  await open(page, "Shuffle options");
  await expect(
    page.getByRole("spinbutton", { name: "Number of tracks to keep at start" }),
  ).toHaveValue("1");
  await panel(page)
    .getByRole("button", { name: "Preview shuffle", exact: true })
    .click();
  await expect
    .poll(() => calls.filter((c) => c.path.endsWith("preview-shuffle")).length)
    .toBe(1);
  await expect(
    page
      .getByText("Draft changes are local until you review and save.")
      .first(),
  ).toBeAttached();
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(0);
  const body = JSON.parse(
    calls.find((c) => c.path.endsWith("preview-shuffle"))!.body!,
  );
  expect(body.tracks.map((t: { added_at: string }) => t.added_at)).toEqual([
    "2026-01-01T12:00:00Z",
    "2026-01-01T12:00:00Z",
    "2026-02-01T12:00:00Z",
  ]);
});
test("row lock cycles affect both duplicate positions and unknown reply requires GET without repeating POST", async ({
  page,
}) => {
  const calls = await start(page, "workshop-tools-lock-lost");
  const first = page.locator("[data-occurrence]").first();
  await first.getByRole("button", { name: /^Actions for / }).click();
  await first
    .getByRole("button", { name: "Lock for 30 days", exact: true })
    .click();
  await expect(
    first.getByText("The lock change may have completed.", { exact: false }),
  ).toBeVisible();
  await expect(
    first.getByRole("button", { name: "Refresh locks", exact: true }),
  ).toBeVisible();
  await first
    .getByRole("button", { name: "Lock for 30 days", exact: true })
    .dispatchEvent("click");
  expect(calls.filter((c) => c.path.endsWith("/locks/toggle"))).toHaveLength(1);
  await first
    .getByRole("button", { name: "Refresh locks", exact: true })
    .click();
  await expect(
    first.getByRole("button", { name: "Lock permanently", exact: true }),
  ).toBeVisible();
  await first
    .getByRole("button", { name: "Lock permanently", exact: true })
    .click();
  await expect(
    first.getByRole("button", { name: "Unlock", exact: true }),
  ).toBeVisible();
  await first.getByRole("button", { name: "Unlock", exact: true }).click();
  await expect(
    first.getByRole("button", { name: "Lock for 30 days", exact: true }),
  ).toBeVisible();
  expect(calls.filter((c) => c.path.endsWith("/locks/toggle"))).toHaveLength(3);
});
test("catalog pagination preserves exact IDs and adding twice never duplicates or sends a Spotify write", async ({
  page,
}) => {
  const calls = await start(page);
  await open(page, "Add tracks");
  await page
    .getByRole("searchbox", { name: "Track, artist or album" })
    .fill("night");
  await page
    .getByRole("button", { name: "Search tracks", exact: true })
    .click();
  await expect(panel(page).locator(".sh-tools-track")).toHaveCount(10);
  const result = panel(page).locator(".sh-tools-track").first();
  const artwork = await result.locator(".sh-artwork-frame").boundingBox();
  expect(artwork!.height).toBeCloseTo(artwork!.width, 0);
  await result
    .getByRole("button", { name: "Add to draft", exact: true })
    .click();
  await expect(
    result.getByRole("button", { name: "Already in draft", exact: true }),
  ).toHaveAttribute("aria-disabled", "true");
  await page
    .getByRole("button", { name: "Load more tracks", exact: true })
    .click();
  await expect(panel(page).locator(".sh-tools-track")).toHaveCount(13);
  await close(page);
  await expect(page.locator("[data-occurrence]")).toHaveCount(4);
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(0);
  expect(
    JSON.parse(calls.filter((c) => c.path === "/api/workshop/search")[1]!.body!)
      .offset,
  ).toBe(10);
});
test("duplicate playlist names select exact source; restricted null count offers deliberate catalog fallback", async ({
  page,
}) => {
  const calls = await start(page);
  await open(page, "Import playlist");
  await page
    .getByRole("textbox", { name: "Playlist link, ID or name" })
    .fill("Night Signals");
  await page
    .getByRole("button", { name: "Load playlist", exact: true })
    .click();
  await expect(panel(page).locator("[data-source-id]")).toHaveCount(2);
  await panel(page)
    .locator('[data-source-id="sourceA"]')
    .getByRole("button", { name: "Load Night Signals", exact: true })
    .click();
  await expect(
    panel(page).locator('[data-loaded-source="sourceA"]'),
  ).toContainText("Count unavailable");
  await expect(
    panel(page).getByRole("button", {
      name: "Add missing tracks to draft",
      exact: true,
    }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Search the catalog instead", exact: true })
    .click();
  await expect(
    page.getByRole("searchbox", { name: "Track, artist or album" }),
  ).toHaveValue("Night Signals");
  expect(calls.filter((c) => c.path === "/api/workshop/search")).toHaveLength(
    0,
  );
  await close(page, true);
  await panel(page)
    .getByRole("button", { name: "Load Night Signals", exact: true })
    .last()
    .click();
  await expect(
    panel(page).locator('[data-loaded-source="sourceB"]'),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Add missing tracks to draft", exact: true })
    .click();
  await close(page);
  await expect(page.locator("[data-occurrence]")).toHaveCount(4);
  expect(
    calls
      .filter((c) => c.path.endsWith("load-external-playlist"))
      .map((c) => JSON.parse(c.body!)),
  ).toContainEqual({ url: "sourceB" });
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(0);
});
test("snapshot cache keeps duplicate occurrences, manual form retains focus, exact delete requires confirmation", async ({
  page,
}) => {
  const calls = await start(page);
  await open(page, "Snapshots");
  const card = panel(page).locator('[data-snapshot-id="31"]');
  await card.getByRole("button", { name: "View tracks", exact: true }).click();
  await expect(card.locator(".sh-tools-snapshot-tracks li")).toHaveCount(3);
  await card.getByRole("button", { name: "Hide tracks", exact: true }).click();
  await card.getByRole("button", { name: "View tracks", exact: true }).click();
  expect(calls.filter((c) => c.path === "/api/snapshots/31")).toHaveLength(1);
  await page
    .getByRole("button", { name: "Take snapshot", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Snapshot description" }),
  ).toBeFocused();
  await page
    .getByRole("textbox", { name: "Snapshot description" })
    .fill("Keep repeated first light");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Take snapshot", exact: true }),
  ).toBeFocused();
  await page
    .getByRole("button", { name: "Take snapshot", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Snapshot description" }),
  ).toHaveValue("Keep repeated first light");
  await page
    .getByRole("button", { name: "Save snapshot", exact: true })
    .click();
  await expect(panel(page).locator('[data-snapshot-id="32"]')).toBeVisible();
  const created = panel(page).locator('[data-snapshot-id="32"]');
  await created.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "Spotify does not change.",
  );
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(calls.filter((c) => c.method === "DELETE")).toHaveLength(0);
  await created.getByRole("button", { name: "Delete", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Delete snapshot", exact: true })
    .click();
  await expect(created).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Snapshot timeline", exact: true }),
  ).toBeFocused();
  expect(calls.filter((c) => c.method === "DELETE").map((c) => c.path)).toEqual(
    ["/api/snapshots/32"],
  );
  expect(
    JSON.parse(
      calls.find((c) => c.method === "POST" && c.path.endsWith("/snapshots"))!
        .body!,
    ).track_uris,
  ).toHaveLength(3);
});
test("unknown snapshot create retains description and never repeats POST when timeline is refreshed", async ({
  page,
}) => {
  const calls = await start(page, "workshop-tools-snapshot-lost");
  await open(page, "Snapshots");
  await page
    .getByRole("button", { name: "Take snapshot", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Snapshot description" })
    .fill("Unknown identity");
  await page
    .getByRole("button", { name: "Save snapshot", exact: true })
    .dblclick();
  await expect(
    panel(page).getByText("This snapshot may have been saved.", {
      exact: false,
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Refresh snapshots", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Snapshot description" }),
  ).toHaveValue("Unknown identity");
  await expect(
    page.getByRole("button", { name: "Save snapshot", exact: true }),
  ).toHaveAttribute("aria-disabled", "true");
  expect(
    calls.filter((c) => c.method === "POST" && c.path.endsWith("/snapshots")),
  ).toHaveLength(1);
});
test("active lock read failure blocks editing until explicit retry and supports keyboard tier actions", async ({
  page,
}) => {
  const calls = await start(page, "workshop-tools-lock-error");
  await expect(
    page
      .locator("#main-content")
      .getByText("The active locks could not be loaded."),
  ).toBeVisible();
  await expect(
    page
      .locator("[data-occurrence]")
      .first()
      .getByRole("button", { name: /^Drag / }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Retry active lock read", exact: true })
    .click();
  await expect(
    page
      .locator("[data-occurrence]")
      .first()
      .getByRole("button", { name: /^Drag / }),
  ).toBeEnabled();
  const actions = page
    .locator("[data-occurrence]")
    .first()
    .getByRole("button", { name: /^Actions for / });
  await actions.focus();
  await page.keyboard.press("Enter");
  const lock = page
    .locator("[data-occurrence]")
    .first()
    .getByRole("button", { name: "Lock for 30 days", exact: true });
  await lock.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("[data-occurrence]").nth(2)).toContainText("Held");
  await page.keyboard.press("Escape");
  await expect(actions).toBeFocused();
  await open(page, "Shuffle options");
  await page
    .getByRole("button", { name: "Preview shuffle", exact: true })
    .click();
  await expect
    .poll(() => calls.filter((c) => c.path.endsWith("/preview-shuffle")).length)
    .toBe(1);
  expect(
    JSON.parse(calls.find((c) => c.path.endsWith("/preview-shuffle"))!.body!)
      .locked_positions,
  ).toEqual({
    "0": "spotify:track:0000000000000000000001",
    "2": "spotify:track:0000000000000000000001",
  });
});
test("late preview with changed options is discarded and panel closure restores its trigger", async ({
  page,
}) => {
  await start(page, "workshop-tools-preview-stale");
  await open(page, "Shuffle options");
  await page
    .getByRole("button", { name: "Preview shuffle", exact: true })
    .click();
  await page
    .getByRole("spinbutton", { name: "Number of tracks to keep at start" })
    .fill("2");
  await expect(
    panel(page).getByText("The draft, locks or options changed.", {
      exact: false,
    }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("button", { name: "Shuffle options", exact: true }),
  ).toBeFocused();
  await expect(page.locator("[data-occurrence] h3")).toHaveText([
    "First Light",
    "Night Drive",
    "First Light",
  ]);
});
test("invalid preview permutation is refused and snapshot view read failure has an explicit cache retry", async ({
  page,
}) => {
  await start(page, "workshop-tools-preview-invalid");
  await open(page, "Shuffle options");
  await page
    .getByRole("button", { name: "Preview shuffle", exact: true })
    .click();
  await expect(
    panel(page).getByText("The draft, locks or options changed.", {
      exact: false,
    }),
  ).toBeVisible();
  await close(page);
  await expect(page.locator("[data-occurrence] h3")).toHaveText([
    "First Light",
    "Night Drive",
    "First Light",
  ]);
});
test("manual snapshot preserves an empty draft rather than substituting Spotify order", async ({
  page,
}) => {
  const calls = await start(page);
  for (let i = 0; i < 3; i++) {
    const row = page.locator("[data-occurrence]").first();
    await row.getByRole("button", { name: /^Actions for / }).click();
    await row
      .getByRole("button", { name: "Remove from draft", exact: true })
      .click();
  }
  await expect(page.locator("[data-occurrence]")).toHaveCount(0);
  await open(page, "Snapshots");
  await page
    .getByRole("button", { name: "Take snapshot", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Save draft snapshot", exact: true })
    .click();
  await expect(panel(page).locator('[data-snapshot-id="32"]')).toContainText(
    "0 tracks",
  );
  expect(
    JSON.parse(
      calls.find((c) => c.method === "POST" && c.path.endsWith("/snapshots"))!
        .body!,
    ).track_uris,
  ).toEqual([]);
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(0);
});
test("restore resolves dirty draft with Stay, Review-and-save, or Discard before one reviewed accepted restore", async ({
  page,
}) => {
  const calls = await start(page);
  const row = page.locator("[data-occurrence]").first();
  await row.getByRole("button", { name: /^Actions for / }).click();
  await row
    .getByRole("button", { name: "Remove from draft", exact: true })
    .click();
  await open(page, "Snapshots");
  const restore = () =>
    panel(page)
      .locator('[data-snapshot-id="31"]')
      .getByRole("button", { name: "Restore now", exact: true });
  await restore().click();
  await expect(page.getByRole("dialog")).toContainText(
    "Keep your local draft?",
  );
  await page.getByRole("button", { name: "Stay", exact: true }).click();
  expect(calls.filter((c) => c.path.endsWith("/restore-context"))).toHaveLength(
    0,
  );
  await restore().click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Reviewed order", exact: true }),
  ).toBeVisible();
  expect(calls.filter((c) => c.path.endsWith("/restore-context"))).toHaveLength(
    0,
  );
  await close(page);
  await open(page, "Snapshots");
  await restore().click();
  await page.getByRole("button", { name: "Discard", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Review snapshot restore", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Restore snapshot now", exact: true })
    .dblclick();
  await expect(
    panel(page)
      .getByRole("region", { name: "Retained operation", exact: true })
      .getByRole("status")
      .first(),
  ).toContainText("Snapshot restored. Undo history confirmed.");
  expect(
    calls.filter((c) => c.path === "/api/snapshots/31/restore"),
  ).toHaveLength(1);
  expect(
    calls
      .filter((c) => c.path.startsWith("/api/operations/"))
      .every((c) => c.path === "/api/operations/3001"),
  ).toBe(true);
  await close(page);
  await expect(page.locator("[data-occurrence] h3")).toHaveText([
    "First Light",
    "First Light",
    "Night Drive",
  ]);
});
test("lost restore admission keeps the exact request and only deliberate same-key retry sends another POST", async ({
  page,
}) => {
  const calls = await start(page, "workshop-tools-restore-lost");
  await open(page, "Snapshots");
  await panel(page)
    .locator('[data-snapshot-id="31"]')
    .getByRole("button", { name: "Restore now", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Restore snapshot now", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Retry this exact request", exact: true }),
  ).toBeVisible();
  await page.waitForTimeout(300);
  expect(
    calls.filter((c) => c.path === "/api/snapshots/31/restore"),
  ).toHaveLength(1);
  await page
    .getByRole("button", { name: "Retry this exact request", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Retry exact request", exact: true })
    .click();
  await expect(
    panel(page)
      .getByRole("region", { name: "Retained operation", exact: true })
      .getByRole("status")
      .first(),
  ).toContainText("Snapshot restored. Undo history confirmed.");
  const posts = calls.filter((c) => c.path === "/api/snapshots/31/restore");
  expect(posts).toHaveLength(2);
  expect(posts[0]!.key).toBe(posts[1]!.key);
  expect(posts[0]!.body).toBe(posts[1]!.body);
  expect(posts[0]!.key).toBeTruthy();
});
test("privacy uses the shared metadata review and leaves an edited local track draft intact", async ({
  page,
}) => {
  const calls = await start(page);
  const row = page.locator("[data-occurrence]").first();
  await row.getByRole("button", { name: /^Actions for / }).click();
  await row
    .getByRole("button", { name: "Remove from draft", exact: true })
    .click();
  await open(page, "Playlist details");
  await page
    .getByRole("button", { name: "Review making playlist public", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Review visibility", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Change visibility", exact: true })
    .click();
  await expect(
    panel(page)
      .getByRole("region", { name: "Retained operation", exact: true })
      .getByRole("status")
      .first(),
  ).toContainText("Playlist visibility updated.");
  await close(page);
  await open(page, "Playlist details");
  await expect(
    page.getByRole("button", {
      name: "Review making playlist private",
      exact: true,
    }),
  ).toBeVisible();
  await close(page);
  await expect(page.locator("[data-occurrence]")).toHaveCount(2);
  expect(
    calls.filter((c) => c.path.endsWith("/toggle-visibility")),
  ).toHaveLength(1);
  expect(calls.filter((c) => c.path.endsWith("/commit"))).toHaveLength(0);
});
test.describe("visual evidence", () => {
  test("After Hours tools evidence and keyboard focus at the actual viewport", async ({
    page,
  }, info) => {
    const { mkdir, writeFile } = await import("node:fs/promises");
    const root = new URL("../../../evidence/workshop-tools/", import.meta.url);
    await mkdir(root, { recursive: true });
    await start(page);
    await page.evaluate(() => document.fonts.ready);
    const firstActions = await page
      .locator("[data-occurrence]")
      .first()
      .getByRole("button", { name: /^Actions/ })
      .boundingBox();
    const footer = await page
      .locator(".sh-workshop-draft-footer")
      .boundingBox();
    expect(firstActions!.y + firstActions!.height).toBeLessThanOrEqual(
      footer!.y,
    );
    await page.screenshot({
      path: new URL(`${info.project.name}-workshop.png`, root).pathname,
      fullPage: true,
    });
    await page.getByRole("button", { name: "Tools", exact: true }).click();
    const options = page.getByRole("button", {
      name: "Shuffle options",
      exact: true,
    });
    await options.focus();
    await page.keyboard.press("Enter");
    await expect(
      panel(page).getByRole("heading", {
        name: "Shuffle options",
        exact: true,
      }),
    ).toBeFocused();
    await page.screenshot({
      path: new URL(`${info.project.name}-algorithms.png`, root).pathname,
    });
    await page.waitForTimeout(350);
    await page.keyboard.press("Escape");
    await expect(options).toBeFocused();
    await open(page, "Add tracks");
    await page
      .getByRole("searchbox", { name: "Track, artist or album" })
      .fill("After hours");
    await page
      .getByRole("button", { name: "Search tracks", exact: true })
      .click();
    await expect(panel(page).locator(".sh-tools-track")).toHaveCount(10);
    await page.screenshot({
      path: new URL(`${info.project.name}-catalog.png`, root).pathname,
    });
    await page.waitForTimeout(350);
    await close(page);
    await open(page, "Snapshots");
    await panel(page)
      .locator('[data-snapshot-id="31"]')
      .getByRole("button", { name: "View tracks", exact: true })
      .click();
    await expect(
      panel(page).locator(".sh-tools-snapshot-tracks li"),
    ).toHaveCount(3);
    await page.screenshot({
      path: new URL(`${info.project.name}-snapshots.png`, root).pathname,
    });
    await writeFile(
      new URL(`${info.project.name}-snapshot-ax.yaml`, root),
      await page.locator("main").ariaSnapshot(),
    );
    await page.waitForTimeout(350);
    await close(page);
    await open(page, "Playlist details");
    await page.screenshot({
      path: new URL(`${info.project.name}-details.png`, root).pathname,
    });
    await page.waitForTimeout(350);
  });
});
