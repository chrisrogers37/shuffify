import { test } from "node:test";
import assert from "node:assert/strict";
import { createAdmissionRegistry } from "../src/operations/admissions.ts";
import { createApiClient } from "../src/api/client.ts";
import { operation, json } from "./workshop-data.mjs";
const parent = () => ({
  ...operation(10, "needs_attention", {
    success: false,
    target_status: "unknown",
  }),
  targets: ["target", "source"],
  recovery: { available: true, action: "restore", reason: null },
});
const context = () => ({
  kind: "operation_restore",
  operation_id: 10,
  available: true,
  reviewed: {
    version: 1,
    reviewed_scope: "a".repeat(64),
    reviewed_resources: [
      { playlist_id: "source", digest: "b".repeat(64) },
      { playlist_id: "target", digest: "c".repeat(64) },
    ],
  },
});
const request = (key = "restore-key") => ({
  key,
  feature: "restoration",
  label: "Restore operation10",
  path: "/api/operations/10/restore",
  resources: [
    { resource: "playlist:target", access: "write" },
    { resource: "playlist:source", access: "read" },
  ],
  body: { encoding: "json", value: { reviewed: context().reviewed } },
  expected: { kind: "operation_restore", target: "target" },
});
const child = (state = "queued", result = null) => ({
  ...operation(20, state, result),
  kind: "operation_restore",
  targets: ["source", "target"],
});
const good = () =>
  child("succeeded", {
    success: true,
    status: "complete",
    parent_operation_id: 10,
    parent_resolution: "complete",
    resources: [{ playlist_id: "target", status: "complete" }],
  });
const resolved = () => ({
  ...parent(),
  recovery: {
    available: false,
    action: null,
    reason: "operation_already_restored",
    resolved_operation_id: 20,
  },
});
function harness(
  handler = () =>
    json({ success: true, operation_id: 20, operation: child() }, 202),
) {
  let snapshot = new Map();
  const listeners = new Set(),
    calls = [];
  const operations = {
    getSnapshot: () => snapshot,
    subscribe: (fn) => (listeners.add(fn), () => listeners.delete(fn)),
    observe() {},
    accept(op) {
      this.finish(op);
    },
    finish(op) {
      snapshot = new Map(snapshot).set(op.id, { id: op.id, operation: op });
      listeners.forEach((fn) => fn());
    },
  };
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({ path, init });
      return handler(path, init);
    },
  });
  return {
    r: createAdmissionRegistry(api, "alice", operations),
    operations,
    calls,
  };
}
test("owned fresh restoration retains all original targets until matched child and fresh parent relation proof", async () => {
  const h = harness();
  await h.r.submitRestoration(
    { parent: parent(), context: context(), request: request() },
    "alice",
  );
  h.operations.finish(good());
  assert.ok(h.r.conflict([{ resource: "playlist:source", access: "write" }]));
  assert.equal(
    h.r.reconcileRestoration(
      "restore-key",
      { child: good(), parent: resolved() },
      "alice",
    ),
    true,
  );
  assert.equal(h.r.conflict(request().resources), null);
  assert.equal(h.calls.length, 1);
});
test("restoration unknown admission freezes original review/key; no fresh review or sibling bypass can replace it", async () => {
  const h = harness(() => Promise.reject(Error("lost")));
  const spec = request();
  await assert.rejects(
    h.r.submitRestoration(
      { parent: parent(), context: context(), request: spec },
      "alice",
    ),
  );
  spec.body.value.reviewed.reviewed_scope = "changed";
  await assert.rejects(
    h.r.submitRestoration(
      { parent: parent(), context: context(), request: request("other") },
      "alice",
    ),
  );
  await assert.rejects(h.r.retry("restore-key", "alice"));
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[0].init.body, h.calls[1].init.body);
  assert.equal(h.r.getSnapshot().get("restore-key").operationId, null);
});
test("parent-specific restoration bypasses only owned original parent claims, never another unknown or blocking claim", async () => {
  let mode = "parent";
  const h = harness(() =>
    mode === "parent"
      ? json(
          {
            success: true,
            operation_id: 10,
            operation: { ...parent(), state: "queued" },
          },
          202,
        )
      : json({ success: true, operation_id: 20, operation: child() }, 202),
  );
  await h.r.submit(
    {
      ...request("original"),
      feature: "workshop",
      path: "/api/workshop/target/commit",
      expected: { kind: "workshop_commit", target: "target" },
    },
    "alice",
  );
  h.operations.finish(parent());
  mode = "restore";
  await h.r.submitRestoration(
    { parent: parent(), context: context(), request: request() },
    "alice",
  );
  assert.equal(h.calls.length, 2);
  assert.ok(h.r.conflict(request().resources));
  const g = harness(() => Promise.reject(Error("unknown")));
  await assert.rejects(
    g.r.submit({ ...request("unknown"), feature: "library" }, "alice"),
  );
  await assert.rejects(
    g.r.submitRestoration(
      { parent: parent(), context: context(), request: request() },
      "alice",
    ),
  );
  assert.equal(g.calls.length, 1);
});
test("wrong actor, target sets, body binding, child identity and missing parent relation keep the family held", async () => {
  const h = harness();
  await h.r.submitRestoration(
    { parent: parent(), context: context(), request: request() },
    "alice",
  );
  for (const patch of [
    { child: { ...good(), targets: ["target"] }, parent: resolved() },
    { child: { ...good(), id: 21 }, parent: resolved() },
    { child: good(), parent: parent() },
    {
      child: {
        ...good(),
        result: { ...good().result, parent_operation_id: 11 },
      },
      parent: resolved(),
    },
    {
      child: child("needs_attention", {
        success: true,
        parent_resolution: "complete",
        parent_operation_id: 10,
      }),
      parent: resolved(),
    },
  ])
    assert.equal(
      h.r.reconcileRestoration("restore-key", patch, "alice"),
      false,
    );
  assert.equal(
    h.r.reconcileRestoration(
      "restore-key",
      { child: good(), parent: resolved() },
      "bob",
    ),
    false,
  );
  assert.ok(h.r.conflict(request().resources));
  const g = harness();
  await assert.rejects(
    g.r.submitRestoration(
      {
        parent: parent(),
        context: context(),
        request: {
          ...request(),
          body: {
            encoding: "json",
            value: {
              reviewed: {
                ...context().reviewed,
                reviewed_scope: "d".repeat(64),
              },
            },
          },
        },
      },
      "alice",
    ),
  );
  assert.equal(g.calls.length, 0);
});
test("known failed child keeps claims and requires explicit fresh server review for another child; rollback/attention cannot retry fresh", async () => {
  for (const state of ["failed", "rolled_back", "needs_attention"]) {
    const h = harness();
    await h.r.submitRestoration(
      { parent: parent(), context: context(), request: request() },
      "alice",
    );
    h.operations.finish(
      child(state, {
        success: false,
        parent_resolution: "not_started",
        parent_operation_id: 10,
      }),
    );
    assert.ok(h.r.conflict(request().resources));
    if (state === "failed") {
      await h.r.submitRestoration(
        {
          parent: parent(),
          context: context(),
          request: request("reviewed-again"),
        },
        "alice",
      );
      assert.equal(h.calls.length, 2);
    } else {
      await assert.rejects(
        h.r.submitRestoration(
          {
            parent: parent(),
            context: context(),
            request: request("reviewed-again"),
          },
          "alice",
        ),
      );
      assert.equal(h.calls.length, 1);
    }
  }
});
test("only the exact known-parent blocking record joins restoration and remains a refused caller intent after proof", async () => {
  let mode = "busy";
  const h = harness(() =>
    mode === "busy"
      ? json(
          {
            success: false,
            error_code: "conflict",
            message: "Busy parent",
            category: "error",
            operation_id: 10,
          },
          409,
        )
      : json({ success: true, operation_id: 20, operation: child() }, 202),
  );
  await assert.rejects(
    h.r.submit(
      {
        ...request("busy-caller"),
        feature: "library",
        expected: { kind: "shuffle", target: "target" },
      },
      "alice",
    ),
  );
  h.operations.finish(parent());
  mode = "restore";
  await h.r.submitRestoration(
    { parent: parent(), context: context(), request: request() },
    "alice",
  );
  assert.equal(h.r.getSnapshot().get("busy-caller").relation, "blocking");
  assert.ok(h.r.conflict(request().resources));
  assert.equal(
    h.r.reconcileRestoration(
      "restore-key",
      { child: good(), parent: resolved() },
      "alice",
    ),
    true,
  );
  const a = h.r.getSnapshot().get("busy-caller");
  assert.equal(a.status, "refused");
  assert.equal(a.relation, "blocking");
  assert.equal(a.operationId, 10);
  assert.equal(a.resolution.operationId, 20);
  assert.equal(h.r.getSnapshot().get("restore-key").status, "settled");
});
test("another known blocking operation or an unconfirmed actor cannot join the restoration family", async () => {
  const h = harness(() =>
    json(
      {
        success: false,
        error_code: "conflict",
        message: "Another parent",
        category: "error",
        operation_id: 11,
      },
      409,
    ),
  );
  await assert.rejects(
    h.r.submit(
      {
        ...request("other-busy"),
        feature: "library",
        expected: { kind: "shuffle", target: "target" },
      },
      "alice",
    ),
  );
  await assert.rejects(
    h.r.submitRestoration(
      { parent: parent(), context: context(), request: request() },
      "alice",
    ),
  );
  await assert.rejects(
    h.r.submitRestoration(
      { parent: parent(), context: context(), request: request() },
      "bob",
    ),
  );
  assert.equal(h.calls.length, 1);
  assert.equal(h.r.getSnapshot().has("restore-key"), false);
});
