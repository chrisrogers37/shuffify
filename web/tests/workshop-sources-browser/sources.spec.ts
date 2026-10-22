import { test, expect } from "@playwright/test";
import { checks, start, open, close, panel } from "./helpers";
checks();
test("one common Save freezes source review and finishes target before its exact pending cleanup", async ({
  page,
}) => {
  const calls = await start(page);
  await open(page, "Inbox");
  const row = panel(page).locator('[data-pending-id="11"]');
  await row.getByRole("button", { name: "Add to draft", exact: true }).click();
  await expect(row).toHaveCount(0);
  expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  await close(page);
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await expect(
    panel(page).getByText("Finish pending tracks", { exact: false }),
  ).toBeVisible();
  await panel(page)
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await expect
    .poll(
      () =>
        calls.filter((c) => c.path.endsWith("/pending-raids/finalize")).length,
    )
    .toBe(1);
  const writes = calls.filter((c) => c.method === "POST");
  expect(writes.findIndex((c) => c.path.endsWith("/commit"))).toBeLessThan(
    writes.findIndex((c) => c.path.endsWith("/finalize")),
  );
  const task = JSON.parse(
    writes.find((c) => c.path.endsWith("/finalize"))!.body!,
  );
  expect(task.track_ids).toEqual([11]);
  expect(task.source_playlist_id).toBe("collection");
  expect(task.target_track_uris).toHaveLength(4);
  await close(page);
  await open(page, "Inbox");
  await expect(
    panel(page).getByRole("heading", {
      name: "Saved source work",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    panel(page).getByText("Source task completed.", { exact: true }).first(),
  ).toBeVisible();
  expect(calls.filter((c) => c.path.endsWith("/finalize"))).toHaveLength(1);
});
test("unknown dismissal holds exact row, survives panel close and retries only its frozen key", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-dismiss-lost");
  await open(page, "Inbox");
  await panel(page)
    .locator('[data-pending-id="11"]')
    .getByRole("button", { name: "Dismiss now", exact: true })
    .click();
  await expect(
    panel(page).getByRole("button", {
      name: "Check the same request",
      exact: true,
    }),
  ).toBeVisible();
  expect(calls.filter((c) => c.path.endsWith("/dismiss"))).toHaveLength(1);
  await close(page);
  await open(page, "Inbox");
  await panel(page)
    .getByRole("button", { name: "Check the same request", exact: true })
    .click();
  await expect(
    panel(page).getByText("Pending tracks dismissed.", { exact: true }).first(),
  ).toBeVisible();
  const posts = calls.filter((c) => c.path.endsWith("/dismiss"));
  expect(posts).toHaveLength(2);
  expect(posts[0]!.key).toBe(posts[1]!.key);
  expect(posts[0]!.body).toBe(posts[1]!.body);
  await expect(panel(page).locator('[data-pending-id="11"]')).toHaveCount(0);
});

test("explicit Finish review replaces only the failed source key and releases its frozen dependent", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-finalize-failed");
  const row = page.locator("[data-occurrence]").first();
  await row.getByRole("button", { name: /^Actions for / }).click();
  await row
    .getByRole("button", { name: "Remove from draft", exact: true })
    .click();
  await open(page, "Archive");
  await panel(page)
    .getByRole("button", { name: "Add to draft", exact: true })
    .first()
    .click();
  await close(page);
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await expect(
    panel(page).getByText("Finish archiving removed tracks", { exact: false }),
  ).toBeVisible();
  await panel(page)
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await expect
    .poll(
      () => calls.filter((x) => x.path.endsWith("/finalize-restore")).length,
    )
    .toBe(1);
  await close(page);
  await open(page, "Archive");
  await expect(
    panel(page).getByRole("button", {
      name: "Review failed cleanup again",
      exact: true,
    }),
  ).toBeVisible();
  expect(calls.filter((x) => x.path.endsWith("/pair/archive"))).toHaveLength(0);
  await panel(page)
    .getByRole("button", { name: "Review failed cleanup again", exact: true })
    .click();
  await expect(
    panel(page).getByRole("button", {
      name: "Review failed cleanup again",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(
    calls.filter((x) => x.path.endsWith("/finalize-restore")),
  ).toHaveLength(1);
  await panel(page)
    .getByRole("button", { name: "Run reviewed cleanup", exact: true })
    .first()
    .click();
  await expect
    .poll(
      () => calls.filter((x) => x.path.endsWith("/finalize-restore")).length,
    )
    .toBe(2);
  const retry = calls.filter((x) => x.path.endsWith("/finalize-restore"));
  expect(retry[0]!.body).toBe(retry[1]!.body);
  expect(retry[0]!.key).not.toBe(retry[1]!.key);
  await expect(
    panel(page).getByText("Source task completed.", { exact: true }).first(),
  ).toBeVisible();
  await panel(page)
    .getByRole("button", { name: "Run reviewed cleanup", exact: true })
    .click();
  await expect
    .poll(() => calls.filter((x) => x.path.endsWith("/pair/archive")).length)
    .toBe(1);
});
test("archive bootstrap failure holds manual editing until explicit settings read succeeds", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-pair-error");
  const row = page.locator("[data-occurrence]").first();
  await row.getByRole("button", { name: /^Actions/ }).click();
  await expect(
    row.getByRole("button", { name: "Remove from draft", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Retry archive settings read", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: "Retry archive settings read",
      exact: true,
    }),
  ).toHaveCount(0);
  await row.getByRole("button", { name: /^Actions/ }).click();
  await expect(
    row.getByRole("button", { name: "Remove from draft", exact: true }),
  ).toBeEnabled();
  expect(calls.filter((x) => x.method === "POST")).toHaveLength(0);
});
test("archive Add all stages duplicate physical rows but appends missing target URIs once", async ({
  page,
}) => {
  const calls = await start(page);
  await open(page, "Archive");
  await expect(panel(page).locator("[data-archive-row]")).toHaveCount(3);
  await panel(page)
    .getByRole("button", { name: "Add all to draft", exact: true })
    .click();
  await expect(panel(page).locator("[data-archive-row]")).toHaveCount(0);
  await close(page);
  await expect(page.locator("[data-occurrence]")).toHaveCount(5);
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await expect
    .poll(
      () => calls.filter((x) => x.path.endsWith("/finalize-restore")).length,
    )
    .toBe(1);
  const body = JSON.parse(
    calls.find((x) => x.path.endsWith("/finalize-restore"))!.body!,
  );
  expect(body.track_uris).toEqual(
    [6, 7, 6].map((x) => `spotify:track:${String(x).padStart(22, "0")}`),
  );
  expect(body.target_track_uris).toHaveLength(5);
});
test("staged archive keeps its old identity across relink and changes only after an explicit new review", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-relink");
  await open(page, "Archive");
  await panel(page)
    .getByRole("button", { name: "Add to draft", exact: true })
    .first()
    .click();
  await panel(page)
    .getByRole("button", { name: "Archive setup", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Refresh archive setup", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Refresh archive setup", exact: true })
    .click();
  await expect(
    panel(page).getByText(
      /Staged work still names Evening archive \(archive\)/,
    ),
  ).toBeVisible();
  await close(page);
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await expect(
    panel(page)
      .getByText(/linked source changed/i)
      .first(),
  ).toBeVisible();
  expect(calls.filter((x) => x.path.endsWith("/commit"))).toHaveLength(0);
  await close(page);
  await open(page, "Archive");
  await panel(page)
    .getByRole("button", { name: "Archive setup", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Review new archive", exact: true })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Review new archive",
    exact: true,
  });
  await expect(dialog).toContainText("replacement");
  await dialog
    .getByRole("button", { name: "Use reviewed archive", exact: true })
    .click();
  await close(page);
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await expect
    .poll(
      () => calls.filter((x) => x.path.endsWith("/finalize-restore")).length,
    )
    .toBe(1);
  expect(
    JSON.parse(calls.find((x) => x.path.endsWith("/finalize-restore"))!.body!)
      .source_playlist_id,
  ).toBe("replacement");
});
test("unknown archive append keeps its saved target and separate inspection never invents settlement", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-append-unknown");
  const row = page.locator("[data-occurrence]").first();
  await row.getByRole("button", { name: /^Actions/ }).click();
  await row
    .getByRole("button", { name: "Remove from draft", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await expect
    .poll(() => calls.filter((x) => x.path.endsWith("/pair/archive")).length)
    .toBe(1);
  await close(page);
  await open(page, "Archive");
  await expect(
    panel(page)
      .getByText(
        "Archive append outcome is unknown. Inspect the original source before another action.",
        { exact: true },
      )
      .first(),
  ).toBeVisible();
  await expect(
    panel(page).getByRole("button", {
      name: "Review failed cleanup again",
      exact: true,
    }),
  ).toHaveCount(0);
  await panel(page)
    .getByRole("button", { name: "Inspect captured source", exact: true })
    .click();
  await expect(
    panel(page).getByRole("heading", {
      name: "Current inspection",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    panel(page)
      .getByText(
        "Archive append outcome is unknown. Inspect the original source before another action.",
        { exact: true },
      )
      .first(),
  ).toBeVisible();
  const source = calls.filter((x) => x.path.endsWith("/pair/archive"));
  expect(source).toHaveLength(2);
  expect(JSON.parse(source[1]!.body!).inspect_only).toBe(true);
  expect(source[1]!.key).toBeNull();
  expect(calls.filter((x) => x.path.endsWith("/commit"))).toHaveLength(1);
});
test("unknown terminal creator needs explicit orphan acknowledgement before an exact choice or a new create", async ({
  page,
}) => {
  const calls = await start(
    page,
    "workshop-sources-create-unknown",
    "/workshop/target?setup=archive",
  );
  await expect(
    panel(page)
      .getByRole("heading", { name: "Archive setup", exact: true })
      .first(),
  ).toBeVisible();
  await panel(page)
    .getByRole("button", { name: "Create archive playlist now", exact: true })
    .click();
  await expect(
    panel(page).getByRole("button", {
      name: "Acknowledge unidentified creation",
      exact: true,
    }),
  ).toBeVisible();
  await panel(page)
    .getByRole("button", { name: "Refresh owned playlists", exact: true })
    .click();
  expect(
    calls.filter(
      (x) => x.path === "/api/playlist/target/pair" && x.method === "POST",
    ),
  ).toHaveLength(1);
  await expect(
    panel(page).getByText("Known playlist:", { exact: false }),
  ).toHaveCount(0);
  await panel(page)
    .getByRole("button", {
      name: "Acknowledge unidentified creation",
      exact: true,
    })
    .click();
  const dialog = page.getByRole("dialog", {
    name: "Acknowledge unidentified creation?",
    exact: true,
  });
  await expect(dialog).toContainText("6001");
  await dialog
    .getByRole("button", {
      name: "Keep orphan record and continue",
      exact: true,
    })
    .click();
  await expect(
    panel(page).getByText(
      "Orphan record #6001 retained. No playlist was adopted.",
      { exact: true },
    ),
  ).toBeVisible();
  await panel(page)
    .getByRole("button", {
      name: "Create another archive playlist",
      exact: true,
    })
    .click();
  await expect
    .poll(
      () =>
        calls.filter(
          (x) => x.path === "/api/playlist/target/pair" && x.method === "POST",
        ).length,
    )
    .toBe(2);
  const posts = calls.filter(
    (x) => x.path === "/api/playlist/target/pair" && x.method === "POST",
  );
  expect(posts[0]!.key).not.toBe(posts[1]!.key);
});
test("source setup shows restricted unknown count and saves one count edit, exact watch and exact unwatch", async ({
  page,
}) => {
  const calls = await start(
    page,
    "workshop-sources",
    "/workshop/target?setup=raids",
  );
  await expect(
    panel(page)
      .getByRole("heading", { name: "Source setup", exact: true })
      .first(),
  ).toBeVisible();
  const row = panel(page).locator('[data-source-id="5"]');
  await expect(row).toContainText("Track count unavailable");
  const input = row.getByRole("spinbutton");
  await input.fill("9");
  await input.press("Enter");
  await expect
    .poll(
      () => calls.filter((x) => x.path.endsWith("/raid-source-count")).length,
    )
    .toBe(1);
  expect(
    JSON.parse(calls.find((x) => x.path.endsWith("/raid-source-count"))!.body!),
  ).toEqual({ source_id: 5, raid_count: 9 });
  await panel(page)
    .getByLabel("Spotify playlist link, URI or ID", { exact: true })
    .fill("https://open.spotify.com/playlist/1234567890123456789012");
  await panel(page)
    .getByRole("button", { name: "Watch source now", exact: true })
    .click();
  await expect(panel(page).locator('[data-source-id="99"]')).toBeVisible();
  expect(
    JSON.parse(calls.find((x) => x.path.endsWith("/raid-add-url"))!.body!)
      .auto_schedule,
  ).toBe(false);
  await row
    .getByRole("button", { name: "Unwatch source", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Unwatch source", exact: true })
    .click();
  await expect(row).toHaveCount(0);
  expect(
    JSON.parse(calls.find((x) => x.path.endsWith("/raid-unwatch"))!.body!)
      .source_id,
  ).toBe(5);
});
test("manual Collect and add keeps a completed Collect result when its Add fails, without enabling automation", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-run-partial");
  await open(page, "Inbox");
  await expect(
    panel(page).getByRole("checkbox", {
      name: "Add collected tracks automatically",
      exact: true,
    }),
  ).not.toBeChecked();
  await panel(page)
    .getByRole("button", { name: "Collect & add now", exact: true })
    .click();
  await expect(
    panel(page).getByRole("heading", {
      name: "Collect and add tracks",
      exact: true,
    }),
  ).toBeVisible();
  expect(calls.filter((x) => x.path.endsWith("/raid-and-drip"))).toHaveLength(
    0,
  );
  await panel(page)
    .getByRole("button", { name: "Run reviewed action", exact: true })
    .click();
  await expect(
    panel(page)
      .getByText("Collect completed. Add failed; collected tracks remain.", {
        exact: true,
      })
      .first(),
  ).toBeVisible();
  expect(calls.filter((x) => x.path.endsWith("/raid-and-drip"))).toHaveLength(
    1,
  );
  expect(
    calls.filter((x) => x.method === "PUT" && x.path.endsWith("/raid-link")),
  ).toHaveLength(0);
  await close(page);
  await open(page, "Inbox");
  expect(calls.filter((x) => x.path.endsWith("/raid-and-drip"))).toHaveLength(
    1,
  );
});
test("routine panel uses exact IDs and revisions for state, retained history and deletion focus", async ({
  page,
}) => {
  const calls = await start(page);
  await open(page, "Routines");
  const first = panel(page).locator('[data-routine-id="1"]');
  await expect(first).toContainText("Routine #1");
  await first.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(
    first.getByRole("button", { name: "Resume", exact: true }),
  ).toBeVisible();
  const change = calls.find((x) => x.path === "/api/routines/1/state");
  expect(JSON.parse(change!.body!)).toEqual({
    desired_enabled: false,
    expected_revision: "1".repeat(64),
  });
  await first.getByRole("button", { name: "History", exact: true }).click();
  await expect(
    first.getByText("No recorded executions.", { exact: true }),
  ).toBeVisible();
  await first
    .getByRole("button", { name: "Delete routine", exact: true })
    .click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Delete routine", exact: true })
    .click();
  await expect(first).toHaveCount(0);
  await expect(
    panel(page)
      .locator('[data-routine-id="2"]')
      .getByRole("link", { name: "Edit this routine", exact: true }),
  ).toBeFocused();
});
test("routine panel names the zone once, explains legacy timing plainly and labels the next run's zone", async ({
  page,
}) => {
  await start(page, "workshop-sources", "/workshop/target", (body) => {
    const rows = body.routines ?? body.data?.routines ?? body;
    rows[0].timing.text = "Every day at 09:30 UTC";
    rows[0].timing.legacy = true;
    rows[0].registration.status = "registered";
    rows[0].registration.next_run_at = "2026-01-01T12:00:00+00:00";
    rows[1].timing.text = "Every day at 09:30 timezone unavailable";
    rows[1].timing.effective_timezone = null;
  });
  await open(page, "Routines");
  const first = panel(page).locator('[data-routine-id="1"]');
  await expect(first).toContainText("Planned: Every day at 09:30 UTC");
  await expect(first).not.toContainText("UTC · UTC");
  await expect(first).toContainText(
    "This routine runs on the scheduler's clock (UTC). Change its timing to pick a timezone.",
  );
  await expect(first).not.toContainText("Legacy timing");
  await expect(first).not.toContainText("Registered next run");
  const zone = await page.evaluate(
    (iso) =>
      new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
        .formatToParts(new Date(iso))
        .find((part) => part.type === "timeZoneName")!.value,
    "2026-01-01T12:00:00+00:00",
  );
  await expect(first.getByText(/^Next run:/)).toBeVisible();
  expect(
    (await first.getByText(/^Next run:/).textContent())!.endsWith(` ${zone}`),
  ).toBe(true);
  await expect(
    panel(page).locator('[data-routine-id="2"]'),
  ).toContainText(/Planned: Every day at 09:30 timezone unavailable(?! ·)/);
});
test("a proved unstarted source refusal retains its facts and needs fresh exact review before a new key", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-finalize-refused");
  await open(page, "Inbox");
  await panel(page)
    .locator('[data-pending-id="11"]')
    .getByRole("button", { name: "Add to draft", exact: true })
    .click();
  await close(page);
  await page
    .getByRole("button", { name: "Review & save", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Save to Spotify", exact: true })
    .click();
  await expect
    .poll(() => calls.filter((x) => x.path.endsWith("/finalize")).length)
    .toBe(1);
  await close(page);
  await open(page, "Inbox");
  await expect(
    panel(page)
      .getByText("Source access was refused before admission.", { exact: true })
      .first(),
  ).toBeVisible();
  await panel(page)
    .getByRole("button", { name: "Review failed cleanup again", exact: true })
    .click();
  await expect(
    panel(page).getByRole("button", {
      name: "Review failed cleanup again",
      exact: true,
    }),
  ).toHaveCount(0);
  expect(calls.filter((x) => x.path.endsWith("/finalize"))).toHaveLength(1);
  await panel(page)
    .getByRole("button", { name: "Run reviewed cleanup", exact: true })
    .click();
  await expect
    .poll(() => calls.filter((x) => x.path.endsWith("/finalize")).length)
    .toBe(2);
  const posts = calls.filter((x) => x.path.endsWith("/finalize"));
  expect(posts[0]!.body).toBe(posts[1]!.body);
  expect(posts[0]!.key).not.toBe(posts[1]!.key);
});
test("operation rate limit waits its full server hint then observes the same ID without another accepted POST", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-rate");
  await open(page, "Inbox");
  await panel(page)
    .locator('[data-pending-id="11"]')
    .getByRole("button", { name: "Dismiss now", exact: true })
    .click();
  await expect(
    panel(page).getByText("Pending tracks dismissed.", { exact: true }).first(),
  ).toBeVisible();
  const polls = calls.filter((x) => /^\/api\/operations\/\d+$/.test(x.path));
  const limited = polls.findIndex((x) => x.status === 429);
  expect(limited).toBeGreaterThanOrEqual(0);
  expect(
    polls[limited + 1]!.time - polls[limited]!.time,
  ).toBeGreaterThanOrEqual(950);
  expect(new Set(polls.map((x) => x.path)).size).toBe(1);
  expect(calls.filter((x) => x.path.endsWith("/dismiss"))).toHaveLength(1);
});
test("Pause remains available during a running Collect while inbox staging and watched-source changes are held", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-running-collect");
  await open(page, "Inbox");
  await panel(page)
    .getByRole("button", { name: "Collect for review", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Run reviewed action", exact: true })
    .click();
  await expect
    .poll(() => calls.filter((x) => x.path.endsWith("/raid-now")).length)
    .toBe(1);
  await close(page);
  await expect(
    page
      .locator("[data-occurrence]")
      .first()
      .getByRole("button", { name: /^Actions/ }),
  ).toBeEnabled();
  await open(page, "Routines");
  await panel(page)
    .locator('[data-routine-id="1"]')
    .getByRole("button", { name: "Pause", exact: true })
    .click();
  await expect(
    panel(page)
      .locator('[data-routine-id="1"]')
      .getByRole("button", { name: "Resume", exact: true }),
  ).toBeVisible();
  expect(calls.filter((x) => x.path === "/api/routines/1/state")).toHaveLength(
    1,
  );
  await close(page);
  await open(page, "Inbox");
  await expect(
    panel(page)
      .getByRole("button", { name: "Dismiss now", exact: true })
      .first(),
  ).toBeDisabled();
});
test("combined source tools retain mobile track actions and present attributed panels with accessible task focus", async ({
  page,
}, info) => {
  await start(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.screenshot({ path: info.outputPath("workshop.png") });
  if (info.project.name === "sources-360") {
    const actions = await page
      .locator("[data-occurrence]")
      .first()
      .getByRole("button", { name: /^Actions/ })
      .boundingBox();
    const footer = await page
      .locator(".sh-workshop-draft-footer")
      .boundingBox();
    expect(actions!.y + actions!.height).toBeLessThanOrEqual(footer!.y);
  }
  for (const title of ["Inbox", "Archive", "Routines"]) {
    await open(page, title);
    await expect(
      panel(page).getByRole("heading", { level: 2, name: title, exact: true }),
    ).toBeFocused();
    await page.screenshot({
      path: info.outputPath(`${title.toLowerCase()}.png`),
    });
    await info.attach(`${title} accessibility`, {
      body: await panel(page).ariaSnapshot(),
      contentType: "text/plain",
    });
    if (title === "Inbox") {
      await panel(page)
        .getByRole("button", { name: "Source setup", exact: true })
        .click();
      await page.screenshot({ path: info.outputPath("source-setup.png") });
      await info.attach("Source setup accessibility", {
        body: await panel(page).ariaSnapshot(),
        contentType: "text/plain",
      });
    }
    await close(page);
  }
});
test("duplicate owned names use exact selected identity and creation records a known returned collection separately", async ({
  page,
}) => {
  const calls = await start(
    page,
    "workshop-sources-unlinked",
    "/workshop/target?setup=archive",
  );
  await panel(page)
    .getByRole("button", { name: "Refresh owned playlists", exact: true })
    .click();
  const choices = panel(page).getByRole("combobox", {
    name: "Link one of your playlists",
    exact: true,
  });
  await expect(choices.locator("option")).toHaveCount(3);
  await choices.selectOption("chooseB");
  await panel(page)
    .getByRole("button", { name: "Link as archive now", exact: true })
    .click();
  await expect(
    panel(page)
      .getByText("Playlist association confirmed.", { exact: true })
      .first(),
  ).toBeVisible();
  const post = calls.find(
    (x) => x.path === "/api/playlist/target/pair" && x.method === "POST",
  );
  expect(JSON.parse(post!.body!)).toEqual({
    create_new: false,
    production_playlist_name: "Evening Collection",
    archive_playlist_id: "chooseB",
    archive_playlist_name: "Evening collection",
  });
  await expect(
    panel(page).getByRole("link", {
      name: /Known playlist|Evening collection · chooseB/,
    }),
  ).toHaveCount(1);
  await close(page);
  await open(page, "Inbox");
  await panel(page)
    .getByRole("button", { name: "Source setup", exact: true })
    .click();
  await panel(page)
    .getByRole("button", { name: "Create raid playlist now", exact: true })
    .click();
  await expect(
    panel(page)
      .getByText("Playlist association confirmed.", { exact: true })
      .first(),
  ).toBeVisible();
  const create = calls.find(
    (x) => x.path.endsWith("/raid-link") && x.method === "POST",
  );
  expect(JSON.parse(create!.body!)).toEqual({
    create_new: true,
    target_playlist_name: "Evening Collection",
    drip_count: 3,
    drip_enabled: false,
  });
  await expect(
    panel(page).getByRole("link", {
      name: /Created private playlist · newasset/,
    }),
  ).toBeVisible();
});
test("confirmed dismissal survives source rollback and refresh without presenting cleanup as complete", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-dismiss-cleanup-failed");
  await open(page, "Inbox");
  await panel(page)
    .locator('[data-pending-id="11"]')
    .getByRole("button", { name: "Dismiss now", exact: true })
    .click();
  await expect(
    panel(page)
      .getByText(
        "Inbox dismissal confirmed; collection cleanup was restored.",
        { exact: true },
      )
      .first(),
  ).toBeVisible();
  await expect(panel(page).locator('[data-pending-id="11"]')).toHaveCount(0);
  await panel(page)
    .getByRole("button", { name: "Refresh pending tracks", exact: true })
    .click();
  await expect(panel(page).locator('[data-pending-id="11"]')).toHaveCount(0);
  await expect(
    panel(page).getByText("Confirmed pending IDs", { exact: true }),
  ).toBeVisible();
  await expect(
    panel(page).getByText("restored", { exact: true }),
  ).toBeVisible();
  expect(calls.filter((x) => x.path.endsWith("/dismiss"))).toHaveLength(1);
});
test("read retry re-enables after the complete hint but waits for an explicit click", async ({
  page,
}) => {
  const calls = await start(page, "workshop-sources-read-rate");
  await open(page, "Inbox");
  const retry = panel(page).getByRole("button", {
    name: "Retry pending tracks",
    exact: true,
  });
  await expect(retry).toBeDisabled();
  await expect(retry).toBeEnabled();
  expect(calls.filter((x) => x.path.endsWith("/pending-raids"))).toHaveLength(
    1,
  );
  await retry.click();
  await expect(panel(page).locator("[data-pending-id]")).toHaveCount(3);
  expect(calls.filter((x) => x.path.endsWith("/pending-raids"))).toHaveLength(
    2,
  );
});

test("verified manual Add refreshes the displayed target only from matching fresh Spotify tracks", async ({ page }) => {
  const { workshopTrack } = await import("../../src/fixtures/workshop");
  await start(page);
  const tracks = [workshopTrack(3), workshopTrack(2), workshopTrack(1), workshopTrack(1)];
  const order = tracks.map((track) => track.uri);
  const operation = {
    id: 6999, kind: "drip_now", state: "succeeded", targets: ["target", "collection"],
    started_at: null, completed_at: null, progress: {},
    recovery: {available: false, action: null, reason: null},
    result: {success: true, message: "The manual Add was verified.", operation: {aggregate_status: "complete", resources: [{
      resource: "target", status: "complete", verification: "ordered", current_track_uris: order, desired_track_uris: order,
    }], bookkeeping: []}},
  };
  let writes = 0, reads = 0;
  await page.route("**/api/playlist/target/drip-now", async (route) => {
    writes++;
    await route.fulfill({status: 202, headers: {Location: "/api/operations/6999"},
      json: {success: true, operation_id: 6999, operation}});
  });
  await page.route("**/api/workshop/target/current-state", async (route) => {
    reads++;
    await route.fulfill({json: {success: true, representable: true, loaded_at: "2026-10-15T12:00:00Z",
      current_track_uris: order, current_tracks: tracks}});
  });
  await open(page, "Inbox");
  await panel(page).getByRole("button", {name: "Add collected tracks now", exact: true}).click();
  await panel(page).getByRole("button", {name: "Run reviewed action", exact: true}).click();
  await expect(panel(page).getByRole("heading", {name: "Operation #6999 · succeeded", exact: true})).toBeVisible();
  await close(page);
  await expect(page.locator("[data-occurrence]")).toHaveCount(4);
  await expect(page.locator("[data-occurrence] h3")).toHaveText(tracks.map((track) => track.name));
  expect(writes).toBe(1);
  expect(reads).toBe(1);
  await expect(page.getByRole("button", {name: "Review & save", exact: true})).toBeDisabled();
});


for (const state of ["queued", "succeeded", "failed"] as const) {
  test(`reviewed target name and exact identity remain visible with a ${state} live-action receipt`, async ({ page }) => {
    const calls = await start(page);
    const operation = {
      id: 7201,
      kind: "raid_now",
      state,
      targets: ["target", "collection"],
      started_at: null,
      completed_at: null,
      progress: {},
      result: state === "queued" ? null : {
        success: state === "succeeded",
        message: state === "succeeded" ? "Collection completed." : "Collection failed after review.",
      },
      recovery: { available: false, action: null, reason: null },
    };
    let writes = 0;
    await page.route("**/api/playlist/target/raid-now", async (route) => {
      writes++;
      await route.fulfill({
        status: 202,
        headers: { Location: "/api/operations/7201" },
        json: { success: true, operation_id: operation.id, operation },
      });
    });
    await page.route("**/api/operations/7201", async (route) => {
      await route.fulfill({ json: { success: true, operation } });
    });
    await open(page, "Inbox");
    await panel(page).getByRole("button", { name: "Collect for review", exact: true }).click();
    await expect(panel(page)).toContainText("Evening Collection");
    expect(writes).toBe(0);
    await panel(page).getByRole("button", { name: "Run reviewed action", exact: true }).click();
    await expect(panel(page).getByRole("heading", { name: `Operation #7201 · ${state}`, exact: true })).toBeVisible();
    const reviewed = panel(page).getByRole("region", { name: "Reviewed target", exact: true });
    await expect(reviewed).toBeVisible();
    await expect(reviewed.getByRole("heading", { name: "Reviewed target", exact: true })).toBeVisible();
    await expect(reviewed.getByText("Evening Collection · target", { exact: true })).toBeVisible();
    expect(writes).toBe(1);
    expect(calls.filter((call) => call.path.endsWith("/live-context"))).toHaveLength(1);
    expect(calls.filter((call) => call.path.endsWith("/current-state"))).toHaveLength(0);
    await expect(panel(page).getByRole("button", { name: "Run reviewed action", exact: true })).toHaveCount(0);
  });
}
