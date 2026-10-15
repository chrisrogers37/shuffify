import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createApiClient } from "../src/api/client.ts";
import { createSessionStore } from "../src/auth/session-store.ts";
import { createWorkshopController } from "../src/pages/workshop/core/controller.ts";
import { context, signed, json, track, uri } from "./workshop-data.mjs";
const algorithms = JSON.parse(
  readFileSync(
    new URL("../fixtures/workshop-tools/algorithms.json", import.meta.url),
    "utf8",
  ),
);
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
};
const lock = (n = 1, tier = "standard") => ({
  id: n,
  user_id: 1,
  spotify_playlist_id: "target",
  track_uri: uri(n),
  position: n - 1,
  lock_tier: tier,
  created_at: "2026-01-01T00:00:00Z",
  expires_at: tier === "super" ? null : "2099-01-01T00:00:00Z",
});
const snapshot = (id = 1, uris = [uri(1), uri(2), uri(1)]) => ({
  id,
  user_id: 1,
  playlist_id: "target",
  playlist_name: "Evening Collection",
  track_uris: uris,
  track_count: uris.length,
  snapshot_type: "manual",
  trigger_description: "My snapshot",
  created_at: "2026-01-01T00:00:00Z",
});
async function harness(handle = () => null) {
  let actor = "alice";
  const calls = [];
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({ path, init });
      return (
        (await handle(path, init)) ??
        json(
          path === "/api/workshop/target"
            ? { ...context(), algorithms }
            : path.endsWith("/locks")
              ? { success: true, locks: [] }
              : path.includes("/snapshots?")
                ? { success: true, snapshots: [] }
                : { success: true },
        )
      );
    },
  });
  const session = createSessionStore(async () => signed(actor));
  await session.load();
  const c = createWorkshopController(api, session, "alice", "target", {
    operations: {
      subscribe: () => () => {},
      getSnapshot: () => new Map(),
      observe: () => {},
      accept: () => {},
    },
  });
  await c.load();
  const { createToolsController } = await import(
    "../src/pages/workshop/tools/controller.ts"
  );
  const t = createToolsController(c);
  return {
    c,
    t,
    calls,
    changeActor: async (v) => {
      actor = v;
      await session.refresh();
    },
  };
}
test("initial lock read blocks editing until all duplicate occurrences are held; error requires explicit read retry", async () => {
  const read = deferred();
  let fail = true;
  const h = await harness((path) =>
    path.endsWith("/locks")
      ? fail
        ? Promise.reject(Error("read offline"))
        : read.promise
      : null,
  );
  await h.t.initialize();
  assert.equal(h.c.permission("draft").allowed, false);
  assert.equal(h.t.getSnapshot().locks.phase, "error");
  fail = false;
  const retry = h.t.loadLocks();
  assert.equal(h.c.permission("draft").allowed, false);
  read.resolve(json({ success: true, locks: [lock()] }));
  await retry;
  assert.equal(h.c.permission("draft").allowed, true);
  assert.deepEqual(h.c.getPreviewInput().locked_positions, {
    0: uri(1),
    2: uri(1),
  });
});
test("preview freezes complete duplicate metadata and cannot apply after either option or core revision changes", async () => {
  const pending = deferred();
  const h = await harness((path) =>
    path.endsWith("/preview-shuffle") ? pending.promise : null,
  );
  await h.t.initialize();
  const input = h.t.preview();
  assert.equal(h.t.getSnapshot().algorithm.busy, true);
  h.t.selectAlgorithm(algorithms[1].class_name);
  pending.resolve(
    json({
      success: true,
      shuffled_uris: [uri(2), uri(1), uri(1)],
      algorithm_name: "Example",
    }),
  );
  await input;
  assert.deepEqual(
    h.c.getSnapshot().working.map((o) => o.uri),
    [uri(1), uri(2), uri(1)],
  );
  assert.match(h.t.getSnapshot().algorithm.message, /changed|stale/i);
  const body = JSON.parse(
    h.calls.find((c) => c.path.endsWith("/preview-shuffle")).init.body,
  );
  assert.equal(body.tracks[0].added_at, "first");
  assert.equal(body.tracks[2].added_at, "second");
});
test("unconfirmed lock cycle is never retried; read reconciliation clears the protective claim", async () => {
  let reads = 0;
  const h = await harness((path) =>
    path.endsWith("/locks/toggle")
      ? Promise.reject(Error("lost reply"))
      : path.endsWith("/locks")
        ? json({ success: true, locks: reads++ ? [lock()] : [] })
        : null,
  );
  await h.t.initialize();
  const key = h.c.getSnapshot().working[0].key;
  await h.t.toggleLock(key);
  await h.t.toggleLock(key);
  assert.equal(
    h.calls.filter((c) => c.path.endsWith("/locks/toggle")).length,
    1,
  );
  assert.equal(h.c.permission("draft").allowed, false);
  assert.equal(h.t.getSnapshot().locks.unknown, true);
  await h.t.loadLocks();
  assert.equal(h.c.permission("draft").allowed, true);
  assert.equal(h.c.getSnapshot().locks[uri(1)], true);
});
test("confirmed lock reply waits for an independent claim before installing, without repeating the write", async () => {
  const pending = deferred();
  const h = await harness((path) =>
    path.endsWith("/locks/toggle") ? pending.promise : null,
  );
  await h.t.initialize();
  const toggle = h.t.toggleLock(h.c.getSnapshot().working[0].key);
  const release = h.c.claim({
    owner: "other",
    resource: "tracks:target",
    access: "write",
    reason: "Independent write",
  });
  pending.resolve(json({ success: true, lock: lock() }));
  await toggle;
  assert.equal(h.t.getSnapshot().locks.phase, "installing");
  assert.equal(h.c.getSnapshot().locks[uri(1)], undefined);
  release();
  assert.equal(h.t.getSnapshot().locks.phase, "ready");
  assert.equal(h.c.getSnapshot().locks[uri(1)], true);
  assert.equal(h.calls.filter((c) => c.path.endsWith("/toggle")).length, 1);
});
test("catalog stale response cannot replace newer input, load-more failure retains rows, additions are missing-only", async () => {
  const first = deferred();
  let count = 0;
  const h = await harness((path, init) =>
    path === "/api/workshop/search"
      ? ++count === 1
        ? first.promise
        : count === 2
          ? json({
              success: true,
              query: "new",
              offset: 0,
              limit: 10,
              tracks: [track(3)],
            })
          : Promise.reject(Error("more offline"))
      : null,
  );
  await h.t.initialize();
  h.t.setQuery("old");
  const old = h.t.search();
  h.t.setQuery("new");
  await h.t.search();
  first.resolve(
    json({
      success: true,
      query: "old",
      offset: 0,
      limit: 10,
      tracks: [track(4)],
    }),
  );
  await old;
  assert.equal(h.t.getSnapshot().catalog.tracks[0].uri, uri(3));
  await h.t.search(true);
  assert.equal(h.t.getSnapshot().catalog.tracks.length, 1);
  assert.equal(h.t.addTrack(uri(3)), true);
  assert.equal(h.t.addTrack(uri(3)), false);
  assert.equal(
    h.c.getSnapshot().working.filter((o) => o.uri === uri(3)).length,
    1,
  );
  assert.equal(
    h.calls.some((c) => /\/commit|\/replace|\/add-tracks/.test(c.path)),
    false,
  );
});
test("external restricted source keeps null count and never appends; selected exact identity loads only that source", async () => {
  const h = await harness((path, init) =>
    path.endsWith("load-external-playlist")
      ? json(
          JSON.parse(init.body).query
            ? {
                success: true,
                mode: "search",
                playlists: [
                  {
                    id: "sourceA",
                    name: "Same",
                    owner_display_name: "A",
                    image_url: null,
                    total_tracks: null,
                  },
                  {
                    id: "sourceB",
                    name: "Same",
                    owner_display_name: "B",
                    image_url: null,
                    total_tracks: 2,
                  },
                ],
              }
            : {
                success: true,
                mode: "restricted",
                playlist: {
                  id: "sourceB",
                  name: "Same",
                  owner_id: "B",
                  description: null,
                  track_count: null,
                },
                tracks: [],
                message: "Cannot read tracks",
                suggested_search: "Same",
              },
        )
      : null,
  );
  await h.t.initialize();
  h.t.setExternalInput("Same");
  await h.t.loadExternal();
  await h.t.loadExternal("sourceB");
  assert.equal(h.t.getSnapshot().external.result.playlist.track_count, null);
  assert.equal(h.t.appendExternal(), false);
  assert.equal(h.c.hasDraft(), false);
  assert.deepEqual(
    JSON.parse(
      h.calls.filter((c) => c.path.endsWith("load-external-playlist")).at(-1)
        .init.body,
    ),
    { url: "sourceB" },
  );
});
test("snapshot detail cache retains duplicates and timeline list changes wait until create form closes", async () => {
  let list = 0;
  const h = await harness((path) =>
    path.includes("/snapshots?")
      ? json({ success: true, snapshots: [snapshot(++list)] })
      : path === "/api/snapshots/1"
        ? json({ success: true, snapshot: snapshot() })
        : null,
  );
  await h.t.initialize();
  await h.t.loadSnapshots();
  await h.t.viewSnapshot(1);
  h.t.closeSnapshot(1);
  await h.t.viewSnapshot(1);
  assert.equal(h.calls.filter((c) => c.path === "/api/snapshots/1").length, 1);
  assert.equal(
    h.t.getSnapshot().snapshots.details[1].value.track_uris.length,
    3,
  );
  h.t.showSnapshotForm(true);
  await h.t.loadSnapshots();
  assert.equal(h.t.getSnapshot().snapshots.items[0].id, 1);
  h.t.showSnapshotForm(false);
  assert.equal(h.t.getSnapshot().snapshots.items[0].id, 2);
});
test("unknown manual snapshot save retains intent and disables repeat until explicit create-another acknowledgement", async () => {
  const h = await harness((path, init) =>
    path.endsWith("/snapshots") && init.method === "POST"
      ? new Response("lost reply")
      : null,
  );
  await h.t.initialize();
  h.t.showSnapshotForm(true);
  h.t.setSnapshotDescription("Keep duplicates");
  await h.t.createSnapshot();
  await h.t.createSnapshot();
  assert.equal(
    h.calls.filter(
      (c) => c.init.method === "POST" && c.path.endsWith("/snapshots"),
    ).length,
    1,
  );
  assert.equal(h.t.getSnapshot().snapshots.createUnknown, true);
  assert.equal(h.t.getSnapshot().snapshots.description, "Keep duplicates");
  await h.t.loadSnapshots();
  assert.equal(h.t.getSnapshot().snapshots.createUnknown, true);
  h.t.allowAnotherSnapshot();
  await h.t.createSnapshot();
  assert.equal(
    h.calls.filter(
      (c) => c.init.method === "POST" && c.path.endsWith("/snapshots"),
    ).length,
    2,
  );
});
test("actor switch blocks tool mutation and late results cannot install another actors data", async () => {
  const pending = deferred();
  const h = await harness((path) =>
    path.endsWith("/locks/toggle") ? pending.promise : null,
  );
  await h.t.initialize();
  const action = h.t.toggleLock(h.c.getSnapshot().working[0].key);
  await h.changeActor("bob");
  pending.resolve(json({ success: true, lock: lock() }));
  await action;
  assert.equal(h.c.getSnapshot().locks[uri(1)], undefined);
  assert.equal(h.c.permission("draft").allowed, false);
  assert.equal(h.t.addTrack(uri(3)), false);
});
test("authored 5xx after unsafe local writes remains unknown and cannot automatically repeat the POST", async () => {
  const h = await harness((path, init) =>
    init.method === "POST" &&
    (path.endsWith("/locks/toggle") || path.endsWith("/snapshots"))
      ? json(
          {
            success: false,
            error_code: "internal_error",
            message: "The local result could not be published.",
            category: "error",
          },
          500,
        )
      : null,
  );
  await h.t.initialize();
  await h.t.toggleLock(h.c.getSnapshot().working[0].key);
  assert.equal(h.t.getSnapshot().locks.unknown, true);
  await h.t.toggleLock(h.c.getSnapshot().working[0].key);
  assert.equal(
    h.calls.filter((c) => c.path.endsWith("/locks/toggle")).length,
    1,
  );
  await h.t.loadLocks();
  h.t.showSnapshotForm(true);
  await h.t.createSnapshot();
  assert.equal(h.t.getSnapshot().snapshots.createUnknown, true);
  await h.t.createSnapshot();
  assert.equal(
    h.calls.filter(
      (c) => c.path.endsWith("/snapshots") && c.init.method === "POST",
    ).length,
    1,
  );
});
test("context read failure followed by core retry cannot expose edits before the active lock read", async () => {
  let unavailable = true;
  const pending = deferred();
  const h = await harness((path) =>
    path === "/api/workshop/target" && unavailable
      ? json(
          {
            success: false,
            error_code: "upstream_unavailable",
            message: "Context offline",
            category: "error",
          },
          503,
        )
      : path.endsWith("/locks")
        ? pending.promise
        : null,
  );
  await h.t.initialize();
  assert.equal(h.c.getSnapshot().phase, "error");
  unavailable = false;
  await h.c.load();
  assert.equal(h.c.permission("draft").allowed, false);
  assert.equal(h.calls.filter((c) => c.path.endsWith("/locks")).length, 1);
  pending.resolve(json({ success: true, locks: [lock()] }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.t.getSnapshot().locks.phase, "ready");
  assert.deepEqual(h.c.getPreviewInput().locked_positions, {
    0: uri(1),
    2: uri(1),
  });
});
test("full lock retry hint is respected without any automatic POST replay", async () => {
  let count = 0;
  const h = await harness((path) =>
    path.endsWith("/locks/toggle")
      ? ++count === 1
        ? json(
            {
              success: false,
              error_code: "rate_limited",
              message: "Wait for this lock.",
              category: "error",
              retry_after: 120,
            },
            429,
          )
        : json({ success: true, lock: lock() })
      : null,
  );
  await h.t.initialize();
  const key = h.c.getSnapshot().working[0].key;
  await h.t.toggleLock(key);
  await h.t.toggleLock(key);
  assert.equal(count, 1);
  assert.ok(h.t.getSnapshot().locks.retryUntil - Date.now() > 119000);
  const clock = Date.now;
  try {
    Date.now = () => clock() + 120001;
    await h.t.toggleLock(key);
    assert.equal(count, 2);
    assert.equal(h.c.getSnapshot().locks[uri(1)], true);
  } finally {
    Date.now = clock;
  }
});
test("only the snapshot reserved by another action is protected from exact-ID deletion", async () => {
  const h = await harness();
  await h.t.initialize();
  const release = h.c.claim({
    owner: "restore",
    resource: "snapshot:31",
    access: "write",
    reason: "This exact snapshot is being restored.",
  });
  assert.equal(await h.t.deleteSnapshot(31), false);
  assert.equal(
    h.calls.some((c) => c.path === "/api/snapshots/31"),
    false,
  );
  assert.equal(await h.t.deleteSnapshot(30), true);
  assert.equal(
    h.calls.filter((c) => c.init.method === "DELETE")[0].path,
    "/api/snapshots/30",
  );
  release();
});
test("busy local change retains the exact known operation ID for inspection", async () => {
  const h = await harness((path) =>
    path.endsWith("/locks/toggle")
      ? json(
          {
            success: false,
            error_code: "playlist_busy",
            message: "This target is reserved.",
            category: "error",
            operation_id: 42,
          },
          409,
        )
      : null,
  );
  await h.t.initialize();
  await h.t.toggleLock(h.c.getSnapshot().working[0].key);
  assert.equal(h.t.getSnapshot().locks.operationId, 42);
  assert.equal(h.t.getSnapshot().locks.unknown, false);
});
