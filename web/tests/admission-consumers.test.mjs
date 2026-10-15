import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createApiClient } from "../src/api/client.ts";
import { createSessionStore } from "../src/auth/session-store.ts";
import { createLibraryController } from "../src/pages/library/controller.ts";
import { createWorkshopController } from "../src/pages/workshop/core/controller.ts";
import {
  getAdmissionRegistry,
  spotifyResources,
} from "../src/operations/admissions.ts";
import { fixtureReview } from "../src/fixtures/library.ts";
import { workshopContext } from "../src/fixtures/workshop.ts";
import { signed } from "./workshop-data.mjs";
const initial = JSON.parse(
  await readFile(
    new URL("../fixtures/library/initial.json", import.meta.url),
    "utf8",
  ),
);
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
async function setup(errorExtra = {}) {
  let actor = "alice";
  const calls = [];
  const session = createSessionStore(async () => signed(actor));
  await session.load();
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({ path, init });
      if (path === "/api/library") return Response.json(initial);
      if (path === "/api/workshop/alice-mix")
        return Response.json(workshopContext("alice-mix"));
      if (path.includes("/source-context"))
        return Response.json({
          success: true,
          target: { id: "alice-mix", name: "Morning Collection" },
          raid: null,
          archive: null,
        });
      if (path.endsWith("/live-context"))
        return Response.json({
          success: true,
          context: fixtureReview("alice-mix", JSON.parse(init.body)),
        });
      return Response.json(
        {
          success: false,
          error_code: "uncertain",
          message: "An upstream failure followed the write",
          category: "error",
          ...errorExtra,
        },
        { status: 503 },
      );
    },
  });
  const operations = {
    subscribe: () => () => {},
    getSnapshot: () => new Map(),
    accept() {},
    observe() {},
  };
  const library = createLibraryController(api, session, "alice", {
    operations,
    key: () => "library-key",
  });
  const workshop = createWorkshopController(
    api,
    session,
    "alice",
    "alice-mix",
    { operations, key: () => "workshop-key" },
  );
  await Promise.all([library.load(), workshop.load()]);
  return {
    api,
    session,
    library,
    workshop,
    operations,
    calls,
    actor: async (value) => {
      actor = value;
      await session.refresh();
    },
  };
}
test("Library5xx holds same target in Workshop while independent local draft remains editable", async () => {
  const t = await setup();
  const id = await t.library.begin("alice-mix", "shuffle");
  await tick();
  await t.library.dispatch(id);
  assert.equal(t.library.getSnapshot().receipts.get(id).status, "unknown");
  const row = t.workshop.getSnapshot().working[0];
  assert.equal(t.workshop.removeOccurrence(row.key), true);
  assert.equal(t.workshop.permission("review").allowed, false);
  assert.match(
    t.workshop.permission("review").reason,
    /Shuffle now.*Evening rotation/,
  );
  await assert.rejects(t.workshop.prepareReview(), /not reconciled/);
  assert.equal(t.calls.filter((c) => c.path.endsWith("/commit")).length, 0);
});
test("Workshop unknown admission blocks Library before a second review or shuffle POST", async () => {
  const t = await setup();
  t.workshop.removeOccurrence(t.workshop.getSnapshot().working[0].key);
  const review = await t.workshop.prepareReview();
  await t.workshop.submitReviewed(review.id);
  assert.equal(t.workshop.getSnapshot().receipt.status, "unknown");
  assert.equal(t.library.blocked("alice-mix"), true);
  await assert.rejects(
    t.library.begin("alice-mix", "shuffle"),
    /Save Morning Collection/,
  );
  assert.equal(t.calls.filter((c) => c.path.includes("/shuffle/")).length, 0);
});
test("known routine source read holds the Library target across features without claiming unrelated playlists", async () => {
  const t = await setup();
  const r = getAdmissionRegistry(t.api, "alice", t.operations);
  await assert.rejects(
    r.submit(
      {
        key: "routine-key",
        feature: "routines",
        label: "Run Collect · Other playlist",
        resources: spotifyResources("other", [
          { resource: "alice-mix", domain: "spotify", access: "read" },
        ]),
        path: "/api/schedules/2/run",
        body: { encoding: "json", value: { reviewed_scope: "a".repeat(64) } },
        expected: { kind: "schedule_run", target: "other" },
      },
      "alice",
    ),
  );
  assert.equal(t.library.blocked("alice-mix"), true);
  assert.equal(r.conflict(spotifyResources("independent")), null);
});
test("actor change disables original exact retry while Bob has an isolated claim set", async () => {
  const t = await setup();
  const id = await t.library.begin("alice-mix", "shuffle");
  await tick();
  await t.library.dispatch(id);
  await t.actor("bob");
  await t.library.dispatch(id, true);
  assert.equal(t.calls.filter((c) => c.path.includes("/shuffle/")).length, 1);
  const alice = getAdmissionRegistry(t.api, "alice", t.operations),
    bob = getAdmissionRegistry(t.api, "bob", t.operations);
  assert.equal(alice.getSnapshot().get("library-key").status, "unknown");
  assert.equal(bob.getSnapshot().size, 0);
  assert.equal(bob.conflict(spotifyResources("alice-mix")), null);
});

test("Library never presents a5xx operation ID hint as its blocking or accepted operation", async () => {
  const t = await setup({ operation_id: 9 });
  const id = await t.library.begin("alice-mix", "shuffle");
  await tick();
  await t.library.dispatch(id);
  const r = t.library.getSnapshot().receipts.get(id);
  assert.equal(r.status, "unknown");
  assert.equal(r.operationId, null);
  assert.equal(r.busyId, null);
});
