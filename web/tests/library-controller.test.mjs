import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createApiClient } from "../src/api/client.ts";
import { createLibraryController } from "../src/pages/library/controller.ts";
import { createOperationStore } from "../src/operations/store.ts";
const initial = JSON.parse(
  await readFile(
    new URL("../fixtures/valid/library-context.json", import.meta.url),
    "utf8",
  ),
);
const signed = JSON.parse(
  await readFile(
    new URL("../fixtures/valid/session-signed-in.json", import.meta.url),
    "utf8",
  ),
);
const wait = () => new Promise((resolve) => setTimeout(resolve, 0));
const op = (id = 1, state = "queued") => ({
  id,
  kind: "shuffle",
  state,
  targets: ["alice-mix"],
  started_at: null,
  completed_at: null,
  progress: {},
  result: null,
  recovery: { available: false, action: null, reason: null },
});
function review(body) {
  return {
    version: 1,
    kind: body.kind,
    label: "Shuffle now",
    target: { id: "alice-mix", name: "Alice mix" },
    resources: [
      {
        resource: "alice-mix",
        resource_name: "Alice mix",
        role: "target",
        domain: "spotify",
        access: "write",
        mutation_type: "track_order",
        field: null,
      },
    ],
    required_bookkeeping: ["session_history"],
    review_binding: {
      version: 1,
      scope: "a".repeat(64),
      before_order: null,
      history: null,
    },
    current: { track_count: 3, loaded_at: "2026-01-01T00:00:00Z" },
    action: {
      algorithm: { id: body.form.algorithm, name: "Basic" },
      params: { keep_first: Number(body.form.keep_first) },
    },
  };
}
function setup(handler, context = initial) {
  let actor = signed.user.id;
  const calls = [];
  const session = {
    getSnapshot: () => ({
      status: "ready",
      session: { ...signed, user: { ...signed.user, id: actor } },
    }),
  };
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({ path, init });
      if (path === "/api/library" || path === "/api/library?refresh=1")
        return typeof context === "function"
          ? context(path, init)
          : Response.json(context);
      if (path.endsWith("/live-context"))
        return Response.json({
          success: true,
          context: review(JSON.parse(init.body)),
        });
      return handler(path, init);
    },
  });
  const operations = createOperationStore(api, {
    intervalMs: 0,
    wait: async () => {},
    maxReadRetries: 0,
  });
  const c = createLibraryController(api, session, signed.user.id, {
    key: () => "original-key",
    operations,
  });
  return {
    c,
    calls,
    actor: (value) => {
      actor = value;
    },
  };
}
test("load coalesces StrictMode reads and cancel review never dispatches a Spotify action", async () => {
  const t = setup(() => {
    throw Error("unexpected");
  });
  await Promise.all([t.c.load(), t.c.load()]);
  assert.equal(t.calls.length, 1);
  const id = await t.c.begin("alice-mix", "shuffle");
  await wait();
  assert.equal(t.c.getSnapshot().receipts.get(id).status, "ready");
  t.c.closeReview(id);
  await t.c.dispatch(id);
  assert.equal(
    t.calls.filter((x) => x.path === "/api/shuffle/alice-mix").length,
    0,
  );
});
test("editing a reviewed draft makes it stale; dispatch does not POST", async () => {
  const t = setup(() => {
    throw Error("unexpected");
  });
  await t.c.load();
  const id = await t.c.begin("alice-mix", "shuffle");
  await wait();
  t.c.editParameter("alice-mix", "keep_first", "2");
  await t.c.dispatch(id);
  assert.equal(t.c.stale(t.c.getSnapshot().receipts.get(id)), true);
  assert.equal(t.calls.filter((x) => x.path.includes("/shuffle/")).length, 0);
});
test("unknown admission blocks new work; explicit retry reuses exact frozen body/key and same actor; no automatic POST replay", async () => {
  let attempts = 0;
  const t = setup(async (path, init) => {
    if (path.startsWith("/api/operations/"))
      return Response.json({ success: true, operation: op(1, "succeeded") });
    attempts++;
    if (attempts === 1) throw Error("lost reply");
    return Response.json(
      { success: true, operation_id: 1, operation: op() },
      { status: 202 },
    );
  });
  await t.c.load();
  const id = await t.c.begin("alice-mix", "shuffle");
  await wait();
  await t.c.dispatch(id);
  assert.equal(attempts, 1);
  assert.equal(t.c.getSnapshot().receipts.get(id).status, "unknown");
  await assert.rejects(t.c.begin("alice-mix", "shuffle"));
  t.c.editParameter("alice-mix", "keep_first", "2");
  await wait();
  assert.equal(attempts, 1);
  t.actor("different-account");
  await t.c.dispatch(id, true);
  assert.equal(attempts, 1);
  t.actor(signed.user.id);
  await t.c.dispatch(id, true);
  await wait();
  const writes = t.calls.filter((x) => x.path === "/api/shuffle/alice-mix");
  assert.equal(writes.length, 2);
  for (const x of writes) {
    assert.equal(x.init.headers.get("Idempotency-Key"), "original-key");
    assert.equal(x.init.body.get("keep_first"), "1");
  }
  assert.equal(t.c.getSnapshot().receipts.get(id).operationId, 1);
  assert.equal(t.c.blocked(), false);
});
test("synchronous duplicate dispatch guard allows one command, accepted work survives panel closure", async () => {
  let release;
  const t = setup(async (path) => {
    if (path.startsWith("/api/operations/"))
      return Response.json({ success: true, operation: op(1, "succeeded") });
    await new Promise((resolve) => {
      release = resolve;
    });
    return Response.json(
      { success: true, operation_id: 1, operation: op() },
      { status: 202 },
    );
  });
  await t.c.load();
  const id = await t.c.begin("alice-mix", "shuffle");
  await wait();
  const sending = t.c.dispatch(id);
  await t.c.dispatch(id);
  assert.equal(
    t.calls.filter((x) => x.path === "/api/shuffle/alice-mix").length,
    1,
  );
  release();
  await sending;
  t.c.closeReview(id);
  await wait();
  assert.equal(
    t.c.operations.getSnapshot().get(1).operation.state,
    "succeeded",
  );
});
test("order writes serialize/coalesce latest IDs; Hidden never enters body, known refusal preserves unsaved state", async () => {
  let release,
    orderCalls = 0;
  const value = structuredClone(initial);
  value.sections.playlists = [
    { ...value.sections.favorites[0], id: "bob", pinned: false },
    { ...value.sections.favorites[0], id: "claire", pinned: false },
    { ...value.sections.favorites[0], id: "dave", pinned: false },
  ];
  const t = setup(async (path) => {
    if (path.endsWith("/order")) {
      orderCalls++;
      if (orderCalls === 1)
        await new Promise((resolve) => {
          release = resolve;
        });
      return Response.json({ success: true, count: 4 });
    }
    throw Error("unexpected");
  });
  await t.c.load();
  t.c.getSnapshot().context.sections.playlists = value.sections.playlists;
  t.c.move("bob", 3);
  t.c.move("claire", 3);
  t.c.move("dave", 3);
  assert.equal(orderCalls, 1);
  release();
  await wait();
  await wait();
  assert.equal(orderCalls, 2);
  const writes = t.calls
    .filter((x) => x.path.endsWith("/order"))
    .map((x) => JSON.parse(x.init.body).playlist_ids);
  assert.deepEqual(writes[0], ["alice-mix", "claire", "dave", "bob"]);
  assert.deepEqual(writes[1], ["alice-mix", "bob", "claire", "dave"]);
  assert.equal(t.c.getSnapshot().orderDirty, false);
});
test("unknown preference blocks further toggles until a fresh authoritative GET; rejected toggle never moves a row", async () => {
  let writes = 0;
  const t = setup(async () => {
    writes++;
    throw Error("lost response");
  });
  await t.c.load();
  await t.c.toggle("alice-mix", "hidden");
  assert.equal(t.c.getSnapshot().preferenceUnknown, true);
  assert.equal(t.c.getSnapshot().context.sections.favorites.length, 1);
  await t.c.toggle("alice-mix", "hidden");
  assert.equal(writes, 1);
  await t.c.load(true);
  assert.equal(t.c.getSnapshot().preferenceUnknown, false);
});
test("preference rate limit preserves full retry hint and blocks another unsafe request until eligible", async () => {
  let writes = 0;
  const t = setup(async () => {
    writes++;
    return Response.json(
      {
        success: false,
        error_code: "rate_limited",
        message: "Wait before saving.",
        category: "error",
        retry_after: 60,
      },
      { status: 429, headers: { "Retry-After": "90" } },
    );
  });
  await t.c.load();
  const start = Date.now();
  await t.c.toggle("alice-mix", "hidden");
  assert.ok(t.c.getSnapshot().preferenceRetryUntil >= start + 90000);
  await t.c.toggle("alice-mix", "hidden");
  await t.c.reset();
  assert.equal(writes, 1);
});
test("a terminal failed receipt with unknown or partial resource outcome still blocks fresh Spotify writes", async () => {
  for (const aggregate of ["unknown", "partial"]) {
    const t = setup(async (path) =>
      path.startsWith("/api/operations/")
        ? Response.json({
            success: true,
            operation: {
              ...op(1, "failed"),
              result: {
                success: false,
                operation: {
                  aggregate_status: aggregate,
                  resources: [{ status: "unknown" }],
                },
              },
            },
          })
        : Response.json(
            { success: true, operation_id: 1, operation: op() },
            { status: 202 },
          ),
    );
    await t.c.load();
    const id = await t.c.begin("alice-mix", "shuffle");
    await wait();
    await t.c.dispatch(id);
    await wait();
    assert.equal(t.c.blocked(), true);
    await assert.rejects(t.c.begin("alice-mix", "shuffle"));
  }
});

test("a refused order save keeps shown order and dirty state; only explicit retry saves the retained order", async () => {
  let posts = 0;
  const value = structuredClone(initial);
  value.sections.playlists = [
    { ...value.sections.favorites[0], id: "bob", pinned: false },
    { ...value.sections.favorites[0], id: "claire", pinned: false },
  ];
  const t = setup(
    async () =>
      ++posts === 1
        ? Response.json(
            {
              success: false,
              error_code: "not_saved",
              message: "Order not saved.",
              category: "error",
            },
            { status: 503 },
          )
        : Response.json({ success: true, count: 3 }),
    value,
  );
  await t.c.load();
  t.c.move("bob", 2);
  await wait();
  assert.equal(t.c.getSnapshot().orderDirty, true);
  assert.equal(t.c.getSnapshot().context.sections.playlists[0].id, "claire");
  assert.equal(posts, 1);
  await wait();
  assert.equal(posts, 1);
  await t.c.retryOrder();
  assert.equal(posts, 2);
  assert.equal(t.c.getSnapshot().orderDirty, false);
  assert.deepEqual(
    JSON.parse(t.calls.filter((x) => x.path.endsWith("/order"))[1].init.body),
    { playlist_ids: ["alice-mix", "claire", "bob"] },
  );
});

test("inspection is a duplicate-guarded read, retains exact observations and never reconciles unknown queue admission", async () => {
  let inspections = 0;
  const t = setup(async (path) => {
    if (path.endsWith("/inspect-live")) {
      inspections++;
      return Response.json(
        {
          success: false,
          error_code: "upstream_error",
          category: "error",
          inspection_only: true,
          message: "Earlier outcome remains unknown.",
          operation: {
            kind: "track_inspection",
            aggregate_status: "unknown",
            error_code: "intent_unavailable",
            resources: [
              {
                resource: "alice-mix",
                status: "unknown",
                current_track_uris: ["spotify:track:alice"],
              },
            ],
          },
        },
        { status: 502 },
      );
    }
    throw Error("lost admission");
  });
  await t.c.load();
  const id = await t.c.begin("alice-mix", "shuffle");
  await wait();
  await t.c.dispatch(id);
  await Promise.all([t.c.inspect(id), t.c.inspect(id)]);
  assert.equal(inspections, 1);
  assert.equal(
    t.c.getSnapshot().receipts.get(id).inspections[0].status,
    "unavailable",
  );
  assert.equal(t.c.getSnapshot().receipts.get(id).status, "unknown");
  assert.equal(t.c.blocked(), true);
  assert.equal(
    t.calls.filter((call) => call.path.includes("/shuffle/")).length,
    1,
  );
  t.c.closeReview(id);
  assert.equal(t.c.getSnapshot().receipts.get(id).inspections.length, 1);
});
test("Reset waiting for order cannot pass an order response loss; authoritative Refresh is required first", async () => {
  let rejectOrder;
  const value = structuredClone(initial);
  value.sections.playlists = [
    { ...value.sections.favorites[0], id: "bob", pinned: false },
    { ...value.sections.favorites[0], id: "claire", pinned: false },
  ];
  let resetPosts = 0;
  const t = setup(async (path) => {
    if (path.endsWith("/order"))
      return new Promise((resolve, reject) => {
        rejectOrder = reject;
      });
    if (path.endsWith("/reset")) {
      resetPosts++;
      return Response.json({ success: true, count: 3 });
    }
    throw Error("unexpected");
  }, value);
  await t.c.load();
  t.c.move("bob", 2);
  const resetting = t.c.reset();
  rejectOrder(Error("lost order response"));
  await resetting;
  assert.equal(t.c.getSnapshot().preferenceUnknown, true);
  assert.equal(resetPosts, 0);
});

test("pending Refresh blocks every new preference mutation until its authoritative read finishes", async () => {
  let reads = 0,
    release,
    writes = 0;
  const t = setup(
    async () => {
      writes++;
      throw Error("lost preference");
    },
    async () =>
      ++reads === 2
        ? new Promise((resolve) => {
            release = () => resolve(Response.json(initial));
          })
        : Response.json(initial),
  );
  await t.c.load();
  const refreshing = t.c.load(true);
  await t.c.toggle("alice-mix", "hidden");
  await t.c.reset();
  assert.equal(writes, 0);
  assert.equal(t.c.getSnapshot().preferenceUnknown, false);
  release();
  await refreshing;
  await t.c.toggle("alice-mix", "hidden");
  assert.equal(writes, 1);
  assert.equal(t.c.getSnapshot().preferenceUnknown, true);
});
test("a refresh begun before a verified worker projection cannot overwrite that newer playlist state", async () => {
  let reads = 0,
    releaseRead,
    releaseOperation;
  const t = setup(
    async (path) =>
      path.startsWith("/api/operations/")
        ? new Promise((resolve) => {
            releaseOperation = () =>
              resolve(
                Response.json({
                  success: true,
                  operation: {
                    ...op(1, "succeeded"),
                    result: {
                      success: true,
                      playlist: { id: "alice-mix", total_tracks: 10 },
                      playlist_state: { current_index: 0 },
                    },
                  },
                }),
              );
          })
        : Response.json(
            { success: true, operation_id: 1, operation: op() },
            { status: 202 },
          ),
    async () =>
      ++reads === 2
        ? new Promise((resolve) => {
            releaseRead = () => resolve(Response.json(initial));
          })
        : Response.json(initial),
  );
  await t.c.load();
  const id = await t.c.begin("alice-mix", "shuffle");
  await wait();
  await t.c.dispatch(id);
  const refreshing = t.c.load(true);
  releaseOperation();
  await wait();
  assert.equal(t.c.getSnapshot().context.sections.favorites[0].track_count, 10);
  releaseRead();
  await refreshing;
  assert.equal(t.c.getSnapshot().context.sections.favorites[0].track_count, 10);
  assert.match(t.c.getSnapshot().error, /changed while refreshing/i);
});
