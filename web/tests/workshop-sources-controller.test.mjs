import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
import { createSessionStore } from "../src/auth/session-store.ts";
import { createWorkshopController } from "../src/pages/workshop/core/controller.ts";
import { createSourcesController } from "../src/pages/workshop/sources/controller.ts";
import { context, signed, json, uri } from "./workshop-data.mjs";
const pending = (id = 7) => ({
  id,
  target_playlist_id: "target",
  track_uri: uri(3),
  track_name: "Three",
  track_artists: "Artist",
  track_album: null,
  track_image_url: null,
  track_duration_ms: 0,
  source_playlist_id: null,
  source_name: "Source",
  status: "pending",
  created_at: null,
  resolved_at: null,
});
const pair = {
  id: 9,
  production_playlist_id: "target",
  archive_playlist_id: "archive",
  archive_playlist_name: "Archive",
  auto_archive_on_remove: true,
};
async function harness(handle = () => null, providedOperations = null) {
  const calls = [];
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({ path, init });
      return (
        (await handle(path, init)) ??
        json(
          path === "/api/workshop/target"
            ? context()
            : path.endsWith("/pair")
              ? { success: true, paired: true, pair }
              : path.endsWith("/pending-raids")
                ? { success: true, tracks: [pending()] }
                : path.endsWith("/raid-status")
                  ? {
                      success: true,
                      raid_status: {
                        sources: [],
                        raid_link: null,
                        source_count: 0,
                        max_sources: 10,
                      },
                    }
                  : { success: true },
        )
      );
    },
  });
  let actor = "alice";
  const session = createSessionStore(async () => signed(actor));
  await session.load();
  const operations = providedOperations ?? {
    subscribe: () => () => {},
    getSnapshot: () => new Map(),
    observe: () => {},
    accept: () => {},
  };
  const c = createWorkshopController(api, session, "alice", "target", {
    operations,
  });
  const t = createSourcesController(c, { operations });
  return {
    c,
    t,
    calls,
    changeActor: async (value) => {
      actor = value;
      await session.refresh();
    },
  };
}
test("pair bootstrap claim precedes context load, remains after outage, explicit retry releases only its own claim", async () => {
  let fail = true;
  const h = await harness((path) =>
    path.endsWith("/pair") && fail ? Promise.reject(Error("offline")) : null,
  );
  await h.t.initialize();
  assert.equal(h.c.permission("draft").allowed, false);
  assert.equal(h.t.getSnapshot().pair.phase, "error");
  const release = h.c.claim({
    owner: "other",
    resource: "tracks:target",
    access: "write",
    reason: "Other action",
  });
  fail = false;
  await h.t.loadPair();
  assert.equal(h.c.permission("draft").allowed, false);
  release();
  assert.equal(h.c.permission("draft").allowed, true);
});
test("staging exact pending IDs is local and discard returns rows without a promote POST", async () => {
  const h = await harness();
  await h.t.initialize();
  await h.t.loadPending();
  assert.equal(h.t.stagePending([7]), true);
  assert.equal(h.t.snapshot()[0].pending_rows[0].id, 7);
  assert.equal(h.c.getSnapshot().working.at(-1).uri, uri(3));
  h.c.discardLocal();
  assert.deepEqual(h.t.snapshot(), []);
  assert.equal(h.t.visiblePending().length, 1);
  assert.equal(h.calls.filter((x) => x.init.method === "POST").length, 0);
});
test("dismissal cannot stage rows while an unknown admission is retained; no automatic replay", async () => {
  const h = await harness((path) =>
    path.endsWith("/dismiss") ? Promise.reject(Error("lost")) : null,
  );
  await h.t.initialize();
  await h.t.loadPending();
  await h.t.dismiss([7]);
  assert.equal(h.t.getSnapshot().actions[0].phase, "unknown");
  assert.equal(h.t.stagePending([7]), false);
  assert.equal(h.calls.filter((x) => x.path.endsWith("/dismiss")).length, 1);
});

function operationStore() {
  let snapshot = new Map();
  const listeners = new Set();
  return {
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    getSnapshot: () => snapshot,
    observe() {},
    accept(operation) {
      snapshot = new Map(snapshot).set(operation.id, {
        id: operation.id,
        operation,
        observation: [
          "succeeded",
          "failed",
          "needs_attention",
          "rolled_back",
        ].includes(operation.state)
          ? "settled"
          : "watching",
        error: null,
      });
      listeners.forEach((fn) => fn());
    },
  };
}
const tick = () => new Promise((r) => setTimeout(r, 0));
test("an authorized source chain dispatches once, and unrelated operation observations do not revise the source review", async () => {
  const store = operationStore();
  const op = {
    id: 501,
    kind: "workshop_finalize_raids",
    state: "queued",
    targets: ["target"],
    started_at: null,
    completed_at: null,
    progress: {},
    result: null,
    recovery: { available: false, action: null, reason: null },
  };
  const h = await harness(
    (path) =>
      path.endsWith("/finalize")
        ? json({ success: true, operation_id: 501, operation: op }, 202, {
            Location: "/api/operations/501",
          })
        : null,
    store,
  );
  await h.t.initialize();
  h.t.stagePending([7]);
  const staging = h.t.snapshot(),
    desired = h.c.getSnapshot().working.map((x) => x.uri);
  const sourceReview = await h.t.prepareReview({
    staging,
    desired,
    sourceContext: {
      target: { id: "target", name: "Target" },
      raid: null,
      archive: null,
    },
  });
  h.t.targetConfirmed({
    operationId: 2001,
    sourceReview,
    staging,
    savedTrackUris: desired,
  });
  await tick();
  store.accept({
    ...op,
    state: "succeeded",
    result: {
      success: true,
      resource: null,
      status: "not_required",
      message: "Promoted",
      database_status: "complete",
      promoted_count: 1,
    },
  });
  await tick();
  assert.equal(h.t.getSnapshot().retained[0].phase, "complete");
  const revision = h.c.getSnapshot().sourceRevision;
  store.accept({
    ...op,
    id: 999,
    kind: "unrelated",
    state: "succeeded",
    result: { success: true },
  });
  await tick();
  assert.equal(h.c.getSnapshot().sourceRevision, revision);
  h.t.targetConfirmed({
    operationId: 2001,
    sourceReview,
    staging,
    savedTrackUris: desired,
  });
  await tick();
  assert.equal(h.calls.filter((x) => x.path.endsWith("/finalize")).length, 1);
});

test("a blocking operation is observed separately and can never become the original dismissal receipt", async () => {
  const h = await harness((path) =>
    path.endsWith("/dismiss")
      ? json(
          {
            success: false,
            category: "error",
            message: "Busy",
            error_code: "playlist_busy",
            operation_id: 998,
          },
          409,
        )
      : null,
  );
  await h.t.initialize();
  await h.t.dismiss([7]);
  const a = h.t.getSnapshot().actions[0];
  assert.equal(a.operationId, null);
  assert.equal(a.blockingOperationId, 998);
  assert.equal(h.t.visiblePending().length, 0);
});
test("same-key retry is disabled when the authenticated actor changes", async () => {
  const h = await harness((path) =>
    path.endsWith("/dismiss") ? Promise.reject(Error("lost")) : null,
  );
  await h.t.initialize();
  await h.t.dismiss([7]);
  const a = h.t.getSnapshot().actions[0];
  await h.changeActor("bob");
  assert.equal(await h.t.submit(a.id, true), false);
  assert.equal(h.calls.filter((x) => x.path.endsWith("/dismiss")).length, 1);
});
test("shown-only dismissal freezes 200-ID batches; failed cleanup retains confirmed IDs and leaves unsubmitted rows available", async () => {
  const store = operationStore();
  let id = 800;
  const rows = Array.from({ length: 201 }, (_, i) => ({
    ...pending(i + 1),
    track_uri: uri(i + 3),
  }));
  const h = await harness(
    (path, init) =>
      path.endsWith("/pending-raids")
        ? json({ success: true, tracks: rows })
        : path.endsWith("/dismiss")
          ? (() => {
              const selected = JSON.parse(init.body).track_ids;
              return json(
                {
                  success: true,
                  operation_id: ++id,
                  operation: {
                    id,
                    kind: "pending_dismiss",
                    state: "rolled_back",
                    targets: ["target"],
                    started_at: null,
                    completed_at: null,
                    progress: {},
                    result: {
                      success: false,
                      database_status: "complete",
                      source_status: "restored",
                      dismissed_ids: selected,
                      dismissed_count: selected.length,
                      source_removed_count: 0,
                      message: "Inbox kept, cleanup restored",
                    },
                    recovery: { available: false, action: null, reason: null },
                  },
                },
                202,
                { Location: `/api/operations/${id}` },
              );
            })()
          : null,
    store,
  );
  await h.t.initialize();
  assert.equal(await h.t.dismissMany(rows.map((x) => x.id)), false);
  const posts = h.calls.filter((x) => x.path.endsWith("/dismiss"));
  assert.equal(posts.length, 1);
  assert.equal(JSON.parse(posts[0].init.body).track_ids.length, 200);
  assert.deepEqual(
    h.t.visiblePending().map((x) => x.id),
    [201],
  );
});
test("malformed successful dismissal receipt never releases requested rows for staging", async () => {
  const store = operationStore();
  const h = await harness(
    (path) =>
      path.endsWith("/dismiss")
        ? json(
            {
              success: true,
              operation_id: 900,
              operation: {
                id: 900,
                kind: "pending_dismiss",
                state: "succeeded",
                targets: ["target"],
                started_at: null,
                completed_at: null,
                progress: {},
                result: {
                  success: true,
                  database_status: "complete",
                  source_status: "complete",
                  dismissed_ids: [99],
                  dismissed_count: 1,
                },
                recovery: { available: false, action: null, reason: null },
              },
            },
            202,
            { Location: "/api/operations/900" },
          )
        : null,
    store,
  );
  await h.t.initialize();
  await h.t.dismiss([7]);
  assert.equal(h.t.visiblePending().length, 0);
  assert.equal(h.t.stagePending([7]), false);
});
test("an archive binding change invalidates its cached rows and late reads cannot label old tracks as the replacement", async () => {
  let archiveId = "archive";
  let release;
  let delayed = false;
  const h = await harness((path) =>
    path.endsWith("/pair")
      ? json({
          success: true,
          paired: true,
          pair: { ...pair, archive_playlist_id: archiveId },
        })
      : path.endsWith("/archive-tracks")
        ? delayed
          ? new Promise((resolve) => {
              release = resolve;
            })
          : json({
              success: true,
              tracks: [
                {
                  id: "6",
                  uri: uri(6),
                  name: "Six",
                  artists: [],
                  album_name: null,
                  album_image_url: null,
                  duration_ms: 0,
                },
              ],
              total: 1,
            })
        : null,
  );
  await h.t.initialize();
  await h.t.loadArchive();
  assert.equal(h.t.visibleArchive().length, 1);
  delayed = true;
  const old = h.t.loadArchive();
  archiveId = "replacement";
  await h.t.loadPair();
  assert.equal(h.t.getSnapshot().archive.phase, "idle");
  assert.equal(h.t.visibleArchive().length, 0);
  release(
    json({
      success: true,
      tracks: [
        {
          id: "6",
          uri: uri(6),
          name: "Six",
          artists: [],
          album_name: null,
          album_image_url: null,
          duration_ms: 0,
        },
      ],
      total: 1,
    }),
  );
  await old;
  assert.equal(h.t.visibleArchive().length, 0);
  assert.equal(h.t.stageArchive(true), false);
});
test("source count drafts survive reads, revert known refusal, and retain an unknown write until explicit read", async () => {
  const source = {
    id: 5,
    target_playlist_id: "target",
    source_type: "external",
    source_playlist_id: "upstream",
    source_name: "Upstream",
    source_url: null,
    search_query: null,
    raid_count: 5,
    last_track_count: null,
    last_resolve_status: null,
  };
  let failure = "known";
  const h = await harness((path, init) =>
    path.endsWith("/raid-status")
      ? json({
          success: true,
          raid_status: {
            sources: [source],
            raid_link: null,
            source_count: 1,
            max_sources: 10,
          },
        })
      : path.endsWith("/raid-source-count")
        ? failure === "known"
          ? json(
              {
                success: false,
                category: "error",
                error_code: "invalid_count",
                message: "Refused",
              },
              400,
            )
          : Promise.reject(Error("lost"))
        : null,
  );
  await h.t.initialize();
  h.t.setSourceCountInput(5, "9");
  await h.t.loadStatus();
  assert.equal(h.t.getSnapshot().sourceCountInputs[5], "9");
  assert.equal(await h.t.updateSourceCount(5, 9), false);
  assert.equal(h.t.getSnapshot().sourceCountInputs[5], "5");
  failure = "unknown";
  h.t.setSourceCountInput(5, "8");
  assert.equal(await h.t.updateSourceCount(5, 8), false);
  assert.equal(h.t.getSnapshot().sourceCountInputs[5], "8");
  assert.equal(h.t.getSnapshot().configurationUnknown, true);
  assert.equal(await h.t.updateSourceCount(5, 8), false);
  assert.equal(
    h.calls.filter((x) => x.path.endsWith("/raid-source-count")).length,
    2,
  );
});
test("queued Collect holds inbox and source edits while leaving draft and Pause available; queued Add holds target editing", async () => {
  const store = operationStore();
  let kind = "raid";
  const op = {
    id: 700,
    kind: "raid_now",
    state: "queued",
    targets: ["target", "collection"],
    started_at: null,
    completed_at: null,
    progress: {},
    result: null,
    recovery: { available: false, action: null, reason: null },
  };
  const h = await harness(
    (path, init) =>
      path.endsWith("/live-context")
        ? json({
            success: true,
            context: {
              kind,
              target: { id: "target" },
              review_binding: { scope: "a".repeat(64) },
              resources: [
                {
                  resource: "target",
                  domain: "spotify",
                  access: kind === "raid" ? "read" : "write",
                },
                { resource: "collection", domain: "spotify", access: "write" },
              ],
              steps: [kind],
            },
          })
        : path.endsWith("/raid-now") || path.endsWith("/drip-now")
          ? json({ success: true, operation_id: op.id, operation: op }, 202, {
              Location: `/api/operations/${op.id}`,
            })
          : null,
    store,
  );
  await h.t.initialize();
  assert.equal(await h.t.prepareRun("raid"), true);
  await h.t.runReviewed();
  assert.equal(h.c.permission("draft").allowed, true);
  assert.equal(h.c.permission("collect").allowed, false);
  assert.equal(h.c.permission("pause").allowed, true);
  h.t.setURL("1234567890123456789012");
  assert.equal(await h.t.addURL(), false);
  assert.equal(
    h.calls.filter((x) => x.path.endsWith("/raid-add-url")).length,
    0,
  );
  store.accept({
    ...op,
    state: "succeeded",
    result: {
      success: true,
      status: "success",
      tracks_added: 0,
      tracks_total: 3,
    },
  });
  kind = "drip";
  op.kind = "drip_now";
  op.id = 701;
  assert.equal(await h.t.prepareRun("drip"), true);
  await h.t.runReviewed();
  assert.equal(h.c.permission("draft").allowed, false);
  assert.equal(h.c.permission("pause").allowed, true);
});
test("manual removal during a staged archive relink keeps every removed saved copy on the original unresolved staging", async () => {
  let archiveId = "archive";
  const h = await harness((path) =>
    path.endsWith("/pair")
      ? json({
          success: true,
          paired: true,
          pair: { ...pair, archive_playlist_id: archiveId },
        })
      : path.endsWith("/archive-tracks")
        ? json({
            success: true,
            tracks: [
              {
                id: "6",
                uri: uri(6),
                name: "Six",
                artists: [],
                album_name: null,
                album_image_url: null,
                duration_ms: 0,
              },
            ],
            total: 1,
          })
        : null,
  );
  await h.t.initialize();
  await h.t.loadArchive();
  h.t.stageArchive(true);
  archiveId = "replacement";
  await h.t.loadPair();
  h.c.removeOccurrence(h.c.getSnapshot().working[0].key);
  assert.deepEqual(h.t.getSnapshot().archiveQueue, [uri(1)]);
  assert.equal(h.t.getSnapshot().archiveSource.id, "archive");
  assert.equal(h.calls.filter((x) => x.init.method === "POST").length, 0);
});
test("a full admission retry hint cannot be bypassed by another source action with a fresh key", async () => {
  const h = await harness((path) =>
    path.endsWith("/dismiss")
      ? json(
          {
            success: false,
            category: "error",
            error_code: "rate_limited",
            message: "Wait",
            retry_after: 2,
          },
          429,
        )
      : null,
  );
  await h.t.initialize();
  await h.t.dismiss([7]);
  assert.equal(await h.t.createAsset("archive", true), false);
  assert.equal(h.calls.filter((x) => x.init.method === "POST").length, 1);
});
test("a mismatched polled kind never replaces the original receipt and is reported without releasing its rows", async () => {
  const store = operationStore(),
    op = {
      id: 9991,
      kind: "pending_dismiss",
      state: "queued",
      targets: ["target"],
      started_at: null,
      completed_at: null,
      progress: {},
      result: null,
      recovery: { available: false, action: null, reason: null },
    };
  const h = await harness(
    (path) =>
      path.endsWith("/dismiss")
        ? json({ success: true, operation_id: op.id, operation: op }, 202, {
            Location: `/api/operations/${op.id}`,
          })
        : null,
    store,
  );
  await h.t.initialize();
  await h.t.dismiss([7]);
  store.accept({
    ...op,
    kind: "pair_create",
    state: "succeeded",
    result: { success: true },
  });
  assert.match(h.t.getSnapshot().actions[0].error, /identity/);
  assert.equal(h.t.getSnapshot().actions[0].operation.kind, "pending_dismiss");
  assert.equal(h.t.visiblePending().length, 0);
});
test("confirmed archive cleanup invalidates its cache and a later fresh occurrence is not hidden by an old completed task", async () => {
  const store = operationStore(),
    track = {
      id: "6",
      uri: uri(6),
      name: "Six",
      artists: [],
      album_name: null,
      album_image_url: null,
      duration_ms: 0,
    };
  let archive = [track];
  const op = {
    id: 4321,
    kind: "workshop_finalize_restore",
    state: "queued",
    targets: ["target", "archive"],
    started_at: null,
    completed_at: null,
    progress: {},
    result: null,
    recovery: { available: false, action: null, reason: null },
  };
  const h = await harness(
    (path) =>
      path.endsWith("/archive-tracks")
        ? json({ success: true, tracks: archive, total: archive.length })
        : path.endsWith("/finalize-restore")
          ? json({ success: true, operation_id: op.id, operation: op }, 202, {
              Location: `/api/operations/${op.id}`,
            })
          : null,
    store,
  );
  await h.t.initialize();
  await h.t.loadArchive();
  h.t.stageArchive(true);
  const staging = h.t.snapshot(),
    desired = h.c.getSnapshot().working.map((x) => x.uri),
    sourceReview = await h.t.prepareReview({
      staging,
      desired,
      sourceContext: {
        target: { id: "target", name: "Target" },
        raid: null,
        archive: {
          id: "archive",
          name: "Archive",
          baseline_track_uris: [uri(6)],
        },
      },
    });
  h.t.targetConfirmed({
    operationId: 123,
    sourceReview,
    staging,
    savedTrackUris: desired,
  });
  await tick();
  archive = [];
  store.accept({
    ...op,
    state: "succeeded",
    result: {
      success: true,
      resource: "archive",
      status: "complete",
      source_status: "complete",
      restored_count: 1,
      affected_count: 1,
      message: "Restored",
    },
  });
  await tick();
  assert.equal(h.t.getSnapshot().archive.value.tracks.length, 0);
  archive = [track];
  await h.t.loadArchive();
  assert.equal(h.t.visibleArchive().length, 1);
});

async function targetRunHarness(kind = "drip", currentRead = null) {
  const store = operationStore();
  const op = {
    id: 901, kind: kind === "schedule" ? "schedule_run" : "drip_now",
    state: "queued", targets: ["target", "collection"], started_at: null,
    completed_at: null, progress: {}, result: null,
    recovery: { available: false, action: null, reason: null },
  };
  const tracks = [context().tracks[1], context().tracks[0]];
  const order = tracks.map((track) => track.uri);
  const h = await harness((path) => {
    if (path.endsWith("/live-context") || path.endsWith("/run-context")) return json({success: true, context: {
      kind, target: {id: "target", name: "Target"}, review_binding: {scope: "a".repeat(64)},
      resources: [{resource: "target", domain: "spotify", access: "write"}], steps: [kind === "schedule" ? "rotate" : "drip"],
    }});
    if (path.endsWith("/drip-now") || path.endsWith("/run")) return json({success: true, operation_id: op.id, operation: op}, 202,
      {Location: `/api/operations/${op.id}`});
    if (path.endsWith("/current-state")) return currentRead ? currentRead() : json({success: true, representable: true,
      loaded_at: "2026-10-15T12:00:00Z", current_track_uris: order, current_tracks: tracks});
    return null;
  }, store);
  await h.t.initialize();
  assert.equal(await h.t.prepareRun(kind, kind === "schedule" ? 12 : undefined), true);
  await h.t.runReviewed();
  const done = { ...op, state: "succeeded", result: {success: true, operation: {aggregate_status: "complete", resources: [{
    resource: "target", status: "complete", current_track_uris: order, desired_track_uris: order, verification: "ordered",
  }], bookkeeping: []}}};
  return {...h, store, op, done, tracks, order};
}
for (const kind of ["drip", "schedule"]) {
  test(`verified ${kind === "schedule" ? "Rotate" : "Drip"} refreshes the exact target baseline only after a matching fresh read`, async () => {
    const h = await targetRunHarness(kind);
    assert.equal(h.calls.filter((call) => call.path.endsWith("/current-state")).length, 0);
    h.store.accept(h.done);
    await tick(); await tick();
    assert.deepEqual(h.c.getSnapshot().baseline.map((row) => row.uri), h.order);
    assert.deepEqual(h.c.getSnapshot().working.map((row) => row.uri), h.order);
    assert.equal(h.calls.filter((call) => call.path.endsWith("/current-state")).length, 1);
    h.store.accept({...h.done});
    await tick();
    assert.equal(h.calls.filter((call) => call.path.endsWith("/current-state")).length, 1);
  });
}

test("a live target result never adopts an external order that changed after verification", async () => {
  const h = await targetRunHarness("drip", () => json({success: true, representable: true,
    loaded_at: "2026-10-15T12:00:00Z", current_track_uris: context().tracks.map((track) => track.uri),
    current_tracks: context().tracks}));
  const baseline = h.c.getSnapshot().baseline;
  h.store.accept(h.done);
  await tick(); await tick();
  assert.deepEqual(h.c.getSnapshot().baseline, baseline);
  assert.match(h.t.getSnapshot().error, /Spotify has changed again/);
  assert.equal(h.t.getSnapshot().actions[0].operation.id, h.op.id);
});

test("a newer local edit wins a race with the live target refresh", async () => {
  let resolveRead;
  const h = await targetRunHarness("drip", () => new Promise((resolve) => { resolveRead = resolve; }));
  const baseline = h.c.getSnapshot().baseline;
  h.store.accept(h.done);
  await tick();
  assert.equal(typeof resolveRead, "function");
  assert.equal(h.c.appendTracks([context().tracks[0]], {mode: "append", origin: "catalog"}), true);
  const local = h.c.getSnapshot().working;
  resolveRead(json({success: true, representable: true, loaded_at: "2026-10-15T12:00:00Z",
    current_track_uris: h.order, current_tracks: h.tracks}));
  await tick(); await tick();
  assert.deepEqual(h.c.getSnapshot().baseline, baseline);
  assert.deepEqual(h.c.getSnapshot().working, local);
});

for (const [state, aggregate] of [["needs_attention", "unknown"], ["failed", "partial"], ["succeeded", "unknown"]]) {
  test(`live ${state}/${aggregate} retains uncertainty without a fresh baseline adoption`, async () => {
    const h = await targetRunHarness();
    const baseline = h.c.getSnapshot().baseline;
    h.store.accept({...h.done, state, result: {...h.done.result, operation: {...h.done.result.operation, aggregate_status: aggregate}}});
    await tick(); await tick();
    assert.deepEqual(h.c.getSnapshot().baseline, baseline);
    assert.equal(h.calls.filter((call) => call.path.endsWith("/current-state")).length, 0);
  });
}

test("a failed current read retains the verified live receipt without repeating its command", async () => {
  const h = await targetRunHarness("drip", () => Promise.reject(Error("offline")));
  const baseline = h.c.getSnapshot().baseline;
  h.store.accept(h.done);
  await tick(); await tick();
  assert.deepEqual(h.c.getSnapshot().baseline, baseline);
  assert.equal(h.t.getSnapshot().actions[0].phase, "complete");
  assert.match(h.t.getSnapshot().error, /completed, but its current target could not be read/);
  assert.equal(h.calls.filter((call) => call.path.endsWith("/drip-now")).length, 1);
});


async function collectReconciliationHarness() {
  const store = operationStore();
  const op = {
    id: 1001,
    kind: "raid_now",
    state: "queued",
    targets: ["target", "collection"],
    started_at: null,
    completed_at: null,
    progress: {},
    result: null,
    recovery: { available: false, action: null, reason: null },
  };
  const h = await harness((path) => {
    if (path.endsWith("/live-context")) return json({
      success: true,
      context: {
        kind: "raid",
        target: { id: "target", name: "Target" },
        review_binding: { scope: "a".repeat(64) },
        resources: [
          { resource: "target", domain: "spotify", access: "read" },
          { resource: "collection", domain: "spotify", access: "write" },
        ],
        steps: ["raid"],
      },
    });
    if (path.endsWith("/raid-now")) return json({
      success: true, operation_id: op.id, operation: op,
    }, 202, { Location: `/api/operations/${op.id}` });
    if (path.includes("/source-context?")) return json({
      success: true, target: { id: "target", name: "Target" }, raid: null, archive: null,
    });
    return null;
  }, store);
  await h.t.initialize();
  const before = h.c.getSnapshot();
  const draft = before.working.slice(1).map((row) => row.uri);
  assert.equal(h.c.replaceDraftOrder(draft, { expectedRevision: before.revision }), true);
  assert.equal(await h.t.prepareRun("raid"), true);
  assert.equal(await h.t.runReviewed(), true);
  assert.equal(h.c.permission("review").allowed, false);
  assert.equal(h.c.getSnapshot().retainedSourceWork[0].status, "accepted");
  return { ...h, store, op, draft };
}

test("completed Collect reconciles retained source work before reviewing the unchanged local draft", async () => {
  const h = await collectReconciliationHarness();
  const revision = h.c.getSnapshot().sourceRevision;
  const done = { ...h.op, state: "succeeded", result: { success: true } };
  h.store.accept(done);
  assert.equal(h.t.getSnapshot().actions[0].phase, "complete");
  assert.deepEqual(h.c.getSnapshot().retainedSourceWork, []);
  assert.equal(h.c.hasRetainedSourceWork(), false);
  assert.equal(h.c.permission("review").allowed, true);
  assert.deepEqual(h.c.getSnapshot().working.map((row) => row.uri), h.draft);
  assert.equal(h.c.getSnapshot().sourceRevision, revision);
  const review = await h.c.prepareReview();
  assert.deepEqual(review.body.track_uris, h.draft);
  assert.equal(h.c.reviewIsCurrent(), true);
  h.store.accept({ ...done, id: 2001 });
  h.store.accept({ ...done });
  assert.equal(h.c.getSnapshot().sourceRevision, revision);
  assert.equal(h.c.reviewIsCurrent(), true);
  assert.equal(h.calls.filter((call) => call.path.endsWith("/raid-now")).length, 1);
});

for (const [state, result] of [
  ["failed", { success: false }],
  ["needs_attention", { success: false }],
  ["succeeded", { success: false }],
  ["succeeded", null],
]) {
  test(`Collect ${state}/${result?.success ?? "missing"} keeps its reconciled failed source work blocking a fresh save`, async () => {
    const h = await collectReconciliationHarness();
    h.store.accept({ ...h.op, state, result });
    assert.equal(h.t.getSnapshot().actions[0].phase, "failed");
    assert.equal(h.c.getSnapshot().retainedSourceWork[0].status, "failed");
    assert.equal(h.c.getSnapshot().retainedSourceWork[0].operationId, h.op.id);
    assert.equal(h.c.permission("review").allowed, false);
    await assert.rejects(h.c.prepareReview(), /retained source work/);
    assert.deepEqual(h.c.getSnapshot().working.map((row) => row.uri), h.draft);
    assert.equal(h.calls.filter((call) => call.path.endsWith("/raid-now")).length, 1);
    assert.equal(h.calls.filter((call) => call.path.includes("/source-context?")).length, 0);
  });
}
