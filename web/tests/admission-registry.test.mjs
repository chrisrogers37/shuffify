import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
import {
  createAdmissionRegistry,
  spotifyResources,
} from "../src/operations/admissions.ts";
const op = (id = 1, state = "queued", result = null) => ({
  id,
  kind: "workshop_commit",
  state,
  targets: ["target"],
  started_at: null,
  completed_at: null,
  progress: {},
  result,
  recovery: { available: false, action: null, reason: null },
});
function harness(transport) {
  const listeners = new Set();
  let snapshot = new Map();
  const operations = {
    getSnapshot: () => snapshot,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    accept(operation) {
      this.finish(operation);
    },
    observe() {},
    finish(operation) {
      snapshot = new Map(snapshot).set(operation.id, {
        id: operation.id,
        operation,
        observation: "settled",
        error: null,
      });
      listeners.forEach((fn) => fn());
    },
  };
  const calls = [];
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({ path, init });
      return transport(path, init);
    },
  });
  return {
    r: createAdmissionRegistry(api, "alice", operations),
    api,
    operations,
    calls,
  };
}
const request = (
  key = "key",
  resources = [{ resource: "playlist:target", access: "write" }],
) => ({
  key,
  feature: "workshop",
  label: "Save Evening Collection",
  resources,
  path: "/api/workshop/target/commit",
  body: { encoding: "json", value: { track_uris: ["a", "a", "b"] } },
  expected: { kind: "workshop_commit", target: "target" },
});
const accepted = (operation = op()) =>
  Response.json(
    { success: true, operation_id: operation.id, operation },
    { status: 202 },
  );
test("lost no-ID admission freezes visible body/key, blocks another feature, and exact retry transfers to observed ID", async () => {
  let n = 0;
  const t = harness(() => {
    if (++n === 1) throw Error("lost");
    return accepted();
  });
  const spec = request();
  await assert.rejects(t.r.submit(spec, "alice"));
  spec.body.value.track_uris.reverse();
  const retained = t.r.getSnapshot().get("key");
  assert.deepEqual(retained.request.body.value.track_uris, ["a", "a", "b"]);
  assert.equal(retained.status, "unknown");
  assert.equal(retained.operationId, null);
  assert.ok(Object.isFrozen(retained.request.body.value.track_uris));
  await assert.rejects(
    t.r.submit({ ...request("other"), feature: "library" }, "alice"),
    /Save Evening Collection/,
  );
  assert.equal(t.calls.length, 1);
  await t.r.retry("key", "alice");
  assert.equal(t.calls.length, 2);
  assert.equal(t.calls[0].init.body, t.calls[1].init.body);
  assert.equal(
    t.calls[0].init.headers.get("Idempotency-Key"),
    t.calls[1].init.headers.get("Idempotency-Key"),
  );
  assert.equal(t.r.getSnapshot().get("key").operationId, 1);
  assert.equal(t.r.getSnapshot().get("key").status, "accepted");
});
for (const status of [500, 502, 503, 599])
  test(`HTTP${status} remains unknown and blocks another resource intent`, async () => {
    const t = harness(() =>
      Response.json(
        {
          success: false,
          error_code: "upstream",
          message: "Uncertain",
          category: "error",
        },
        { status },
      ),
    );
    await assert.rejects(t.r.submit(request(), "alice"));
    assert.equal(t.r.getSnapshot().get("key").status, "unknown");
    await assert.rejects(t.r.submit(request("other"), "alice"));
    assert.equal(t.calls.length, 1);
  });
test("a 4xx on exact retry cannot erase earlier uncertainty", async () => {
  let n = 0;
  const t = harness(() =>
    ++n === 1
      ? Promise.reject(Error("lost"))
      : Response.json(
          {
            success: false,
            error_code: "unauthorized",
            message: "Sign in",
            category: "error",
          },
          { status: 401 },
        ),
  );
  await assert.rejects(t.r.submit(request(), "alice"));
  await assert.rejects(t.r.retry("key", "alice"));
  assert.equal(t.r.getSnapshot().get("key").status, "unknown");
  assert.ok(t.r.conflict(request().resources));
});
test("initial proven refusal releases but accepted needs-attention and partial history remain held", async () => {
  let n = 0;
  const t = harness(() =>
    ++n === 1
      ? Response.json(
          {
            success: false,
            error_code: "bad_request",
            message: "Refused",
            category: "error",
          },
          { status: 400 },
        )
      : accepted(),
  );
  await assert.rejects(t.r.submit(request(), "alice"));
  assert.equal(t.r.conflict(request().resources), null);
  await t.r.submit(request("second"), "alice");
  t.operations.finish(
    op(1, "needs_attention", {
      success: false,
      target_status: "saved",
      error_code: "history_confirmation_failed",
    }),
  );
  assert.ok(t.r.conflict(request().resources));
  t.operations.finish(
    op(1, "rolled_back", {
      success: false,
      operation: { aggregate_status: "rolled_back" },
    }),
  );
  assert.equal(t.r.conflict(request().resources), null);
});
test("source read claims conflict with source writes but allow independent reads/resources", async () => {
  const t = harness(() => Promise.reject(Error("lost")));
  await assert.rejects(
    t.r.submit(
      request(
        "first",
        spotifyResources("target", [
          { domain: "spotify", resource: "source", access: "read" },
        ]),
      ),
      "alice",
    ),
  );
  assert.ok(t.r.conflict([{ resource: "playlist:source", access: "write" }]));
  assert.equal(
    t.r.conflict([{ resource: "playlist:source", access: "read" }]),
    null,
  );
  assert.equal(
    t.r.conflict([{ resource: "playlist:other", access: "write" }]),
    null,
  );
});
test("same-key changed payload is refused and submitting claim prevents concurrent duplicate POST", async () => {
  let finish;
  const t = harness(() => new Promise((resolve) => (finish = resolve)));
  const pending = t.r.submit(request(), "alice");
  await assert.rejects(t.r.submit(request(), "alice"));
  await assert.rejects(
    t.r.submit(
      { ...request(), body: { encoding: "json", value: { track_uris: [] } } },
      "alice",
    ),
  );
  assert.equal(t.calls.length, 1);
  finish(accepted());
  await pending;
});
test("actor confirmation is required for an explicit retry and does not discard owned unknown facts", async () => {
  const t = harness(() => Promise.reject(Error("lost")));
  await assert.rejects(t.r.submit(request(), "alice"));
  await assert.rejects(t.r.retry("key", "bob"));
  assert.equal(t.calls.length, 1);
  assert.equal(t.r.getSnapshot().get("key").status, "unknown");
});
test("wrong accepted identity stays unknown without adopting the unrelated ID", async () => {
  const t = harness(() => accepted({ ...op(), kind: "other" }));
  await assert.rejects(t.r.submit(request(), "alice"));
  assert.equal(t.r.getSnapshot().get("key").status, "unknown");
  assert.equal(t.r.getSnapshot().get("key").operationId, null);
});
test("known IDs discovered by reconciliation extend source/new-playlist claims without replacing the original body", async () => {
  const t = harness(() => accepted());
  await t.r.submit(request(), "alice");
  t.operations.finish(
    op(1, "needs_attention", {
      success: false,
      playlist: { id: "created", name: "Created" },
      operation: {
        aggregate_status: "partial",
        resources: [{ resource: "archive", status: "unknown" }],
      },
    }),
  );
  assert.ok(t.r.conflict([{ resource: "playlist:created", access: "write" }]));
  assert.ok(t.r.conflict([{ resource: "playlist:archive", access: "write" }]));
  assert.deepEqual(t.r.getSnapshot().get("key").request.body.value.track_uris, [
    "a",
    "a",
    "b",
  ]);
});
test("initial busy IDs are blocking observations, not original accepted identities; retry busy does not erase unknown", async () => {
  let first = true;
  const t = harness(() =>
    first
      ? ((first = false), Promise.reject(Error("lost")))
      : Response.json(
          {
            success: false,
            error_code: "conflict",
            message: "Other action retains target",
            category: "error",
            operation_id: 9,
          },
          { status: 409 },
        ),
  );
  await assert.rejects(t.r.submit(request(), "alice"));
  await assert.rejects(t.r.retry("key", "alice"));
  assert.equal(t.r.getSnapshot().get("key").operationId, null);
  assert.equal(t.r.getSnapshot().get("key").relation, null);
  t.operations.finish(op(9, "succeeded", { success: true }));
  assert.ok(t.r.conflict(request().resources));
  const u = harness(() =>
    Response.json(
      {
        success: false,
        error_code: "conflict",
        message: "Busy",
        category: "error",
        operation_id: 9,
      },
      { status: 409 },
    ),
  );
  await assert.rejects(u.r.submit(request(), "alice"));
  assert.equal(u.r.getSnapshot().get("key").relation, "blocking");
  u.operations.finish(op(9, "succeeded", { success: true }));
  assert.equal(u.r.conflict(request().resources), null);
});
test("a full Retry-After on unknown replay prevents another POST while preserving its frozen identity", async () => {
  let n = 0;
  const t = harness(() =>
    ++n === 1
      ? Promise.reject(Error("lost"))
      : new Response(
          JSON.stringify({
            success: false,
            error_code: "rate_limited",
            message: "Wait for Spotify",
            category: "error",
          }),
          {
            status: 429,
            headers: {
              "Content-Type": "application/json",
              "Retry-After": "60",
            },
          },
        ),
  );
  await assert.rejects(t.r.submit(request(), "alice"));
  await assert.rejects(t.r.retry("key", "alice"));
  await assert.rejects(t.r.retry("key", "alice"), /Wait .* seconds/);
  assert.equal(t.calls.length, 2);
  assert.equal(t.r.getSnapshot().get("key").status, "unknown");
});
test("source continuation transfers original unknown ID and reconciles restoration without another POST", async () => {
  const t = harness(() => Promise.reject(Error("lost")));
  await assert.rejects(t.r.submit(request(), "alice"));
  t.r.accept("key", op(77));
  assert.equal(t.r.getSnapshot().get("key").operationId, 77);
  assert.equal(t.r.getSnapshot().get("key").relation, "original");
  assert.equal(
    t.r.reconcile(
      "key",
      op(77, "rolled_back", {
        success: false,
        operation: { aggregate_status: "rolled_back" },
      }),
    ),
    true,
  );
  assert.equal(t.r.conflict(request().resources), null);
  assert.equal(t.calls.length, 1);
});
test("a5xx operation_id hint cannot transfer or settle an unknown original admission", async () => {
  const t = harness(() =>
    Response.json(
      {
        success: false,
        error_code: "upstream",
        message: "Reply was lost after admission",
        category: "error",
        operation_id: 9,
      },
      { status: 503 },
    ),
  );
  await assert.rejects(t.r.submit(request(), "alice"));
  assert.equal(t.r.getSnapshot().get("key").status, "unknown");
  assert.equal(t.r.getSnapshot().get("key").operationId, null);
  t.operations.finish({
    ...op(9, "succeeded", { success: true }),
    kind: "other",
  });
  assert.ok(t.r.conflict(request().resources));
});
test("real routine verified rollback releases restored resources despite aggregate partial; unknown bookkeeping still holds", async () => {
  const { safelySettled } = await import("../src/operations/admissions.ts");
  const restored = op(1, "rolled_back", {
    success: false,
    error_code: "write_rejected",
    operation: {
      aggregate_status: "partial",
      resources: [{ resource: "target", status: "restored" }],
      bookkeeping: [
        { kind: "execution_record", status: "complete", affected_count: 0 },
      ],
    },
  });
  assert.equal(safelySettled(restored), true);
  const uncertain = structuredClone(restored);
  uncertain.result.operation.bookkeeping[0].status = "unknown";
  assert.equal(safelySettled(uncertain), false);
});
test("unknown source or required database facts cannot release a failed no-target-write result", async () => {
  const { safelySettled } = await import("../src/operations/admissions.ts");
  for (const field of ["source_status", "database_status", "aggregate_status"])
    assert.equal(
      safelySettled(
        op(1, "failed", {
          success: false,
          target_status: "not_written",
          [field]: "unknown",
        }),
      ),
      false,
    );
});
