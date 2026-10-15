import test from "node:test";
import assert from "node:assert/strict";
import {
  setup,
  retainParent,
  parent,
  child,
  json,
  error,
  flush,
} from "./restoration-data.mjs";

const posts = (value) =>
  value.calls.filter(
    (call) => call.method === "POST" && call.path.endsWith("/restore"),
  );
const resource = [{ resource: "playlist:target", access: "write" }];

test("review is read-only and one confirmation resolves only the matched parent-child family", async () => {
  const value = await setup();
  await retainParent(value);
  await value.controller.loadReview();
  assert.equal(value.controller.getSnapshot().phase, "ready");
  assert.equal(posts(value).length, 0);
  assert.ok(value.admissions.conflict(resource));
  await value.controller.confirm();
  await flush();
  assert.equal(posts(value).length, 1);
  assert.deepEqual(Object.keys(posts(value)[0].body), ["reviewed"]);
  assert.equal(value.controller.getSnapshot().phase, "resolved");
  assert.equal(value.controller.getSnapshot().child.id, 8);
  assert.equal(value.admissions.conflict(resource), null);
  await value.controller.confirm();
  assert.equal(posts(value).length, 1);
});

test("lost admission retains the exact body/key without inventing a child or automatically retrying", async () => {
  let first = true;
  const value = await setup((call) => {
    if (call.path.endsWith("/restore") && first) {
      first = false;
      throw Error("response lost");
    }
    return null;
  });
  await retainParent(value);
  await value.controller.loadReview();
  await value.controller.confirm();
  assert.equal(value.controller.getSnapshot().phase, "unknown");
  assert.equal(value.controller.getSnapshot().child, null);
  const count = value.calls.length;
  await value.controller.loadReview();
  await flush();
  assert.equal(value.calls.length, count);
  assert.ok(value.admissions.conflict(resource));
  await value.controller.retryExact();
  await flush();
  assert.equal(value.controller.getSnapshot().phase, "resolved");
  assert.equal(posts(value).length, 2);
  assert.equal(posts(value)[0].key, posts(value)[1].key);
  assert.deepEqual(posts(value)[0].body, posts(value)[1].body);
});

for (const fault of ["server", "malformed", "wrong_kind", "wrong_targets"]) {
  test(`${fault} admission stays unknown and never observes an unproved child`, async () => {
    const value = await setup((call) => {
      if (!call.path.endsWith("/restore")) return null;
      if (fault === "server")
        return error("unavailable", 503, { operation_id: 99 });
      const operation =
        fault === "wrong_kind"
          ? { ...child(99), kind: "shuffle" }
          : { ...child(99), targets: ["other"] };
      return fault === "malformed"
        ? json({ success: true }, 202)
        : json({ success: true, operation_id: 99, operation }, 202);
    });
    await value.controller.loadReview();
    await value.controller.confirm();
    await flush();
    assert.equal(value.controller.getSnapshot().phase, "unknown");
    assert.equal(value.controller.getSnapshot().child, null);
    assert.equal(
      value.calls.filter((call) => call.path === "/api/operations/99").length,
      0,
    );
    assert.equal(posts(value).length, 1);
  });
}

test("a later refusal does not erase the first unknown admission or its exact key", async () => {
  let count = 0;
  const value = await setup((call) => {
    if (!call.path.endsWith("/restore")) return null;
    if (++count === 1) throw Error("unknown first POST");
    return error("restoration_attempt_unresolved", 409, { operation_id: 99 });
  });
  await value.controller.loadReview();
  await value.controller.confirm();
  await value.controller.retryExact();
  assert.equal(value.controller.getSnapshot().phase, "unknown");
  assert.equal(value.controller.getSnapshot().child, null);
  assert.equal(posts(value)[0].key, posts(value)[1].key);
  assert.ok(value.admissions.conflict(resource));
});

for (const state of ["rolled_back", "needs_attention"]) {
  test(`${state} child keeps the family and prohibits another review/POST`, async () => {
    const value = await setup((call) =>
      call.path === "/api/operations/8"
        ? json({ success: true, operation: child(8, state) })
        : null,
    );
    await retainParent(value);
    await value.controller.loadReview();
    await value.controller.confirm();
    await flush();
    const count = value.calls.length;
    await value.controller.loadReview();
    assert.equal(value.calls.length, count);
    assert.equal(value.controller.getSnapshot().phase, "attention");
    assert.ok(value.admissions.conflict(resource));
    assert.equal(posts(value).length, 1);
  });
}

test("a successful child without the fresh matching parent marker cannot release claims", async () => {
  const value = await setup((call) =>
    call.path === "/api/operations/8"
      ? json({ success: true, operation: child() })
      : null,
  );
  await retainParent(value);
  await value.controller.loadReview();
  await value.controller.confirm();
  await flush();
  assert.equal(value.controller.getSnapshot().phase, "attention");
  assert.ok(value.admissions.conflict(resource));
  assert.equal(posts(value).length, 1);
});

test("a changed actor cannot send the retained restoration and returning keeps its exact intent", async () => {
  let first = true;
  const value = await setup((call) => {
    if (call.path.endsWith("/restore") && first) {
      first = false;
      throw Error("unknown");
    }
    return null;
  });
  await value.controller.loadReview();
  await value.controller.confirm();
  value.setActor("someone-else");
  await value.session.refresh();
  await value.controller.retryExact();
  assert.equal(posts(value).length, 1);
  value.setActor("owner");
  await value.session.refresh();
  await value.controller.retryExact();
  await flush();
  assert.equal(posts(value).length, 2);
  assert.equal(posts(value)[0].key, posts(value)[1].key);
});

test("Retry-After blocks a repeated exact POST without creating a new key or timer retry", async (t) => {
  let now = 10000,
    count = 0;
  t.mock.method(Date, "now", () => now);
  const value = await setup((call) => {
    if (!call.path.endsWith("/restore")) return null;
    if (++count === 1) throw Error("unknown");
    if (count === 2) return error("rate_limited", 429, { retry_after: 2 });
    return null;
  });
  await value.controller.loadReview();
  await value.controller.confirm();
  await value.controller.retryExact();
  assert.equal(value.controller.getSnapshot().retryUntil, 12000);
  await value.controller.retryExact();
  await flush();
  assert.equal(posts(value).length, 2);
  now = 12001;
  await value.controller.retryExact();
  await flush();
  assert.equal(posts(value).length, 3);
  assert.ok(posts(value).every((call) => call.key === "restore-1"));
  assert.equal(value.controller.getSnapshot().phase, "resolved");
});

test("accepted child observation continues after every panel subscriber is removed", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const value = await setup(async (call) => {
    if (call.path === "/api/operations/8") await gate;
    return null;
  });
  const off = value.controller.subscribe(() => {});
  await value.controller.loadReview();
  await value.controller.confirm();
  off();
  assert.equal(value.controller.getSnapshot().child.id, 8);
  release();
  await flush();
  assert.equal(value.controller.getSnapshot().phase, "resolved");
  assert.equal(posts(value).length, 1);
});

test("an unavailable parent-resolution read retains known child and claims until an explicit read succeeds", async () => {
  let fail = true,
    reads = 0;
  const value = await setup((call) => {
    if (call.path === "/api/operations/7" && ++reads > 1 && fail)
      throw Error("unavailable relation read");
    return null;
  });
  await retainParent(value);
  await value.controller.loadReview();
  await value.controller.confirm();
  await flush();
  assert.equal(value.controller.getSnapshot().phase, "unavailable");
  assert.equal(value.controller.getSnapshot().child.id, 8);
  assert.ok(value.admissions.conflict(resource));
  fail = false;
  await value.controller.checkResolution();
  assert.equal(value.controller.getSnapshot().phase, "resolved");
  assert.equal(value.admissions.conflict(resource), null);
  assert.equal(posts(value).length, 1);
});

test("a fresh attempt follows only an explicit successful review after the known failed child", async () => {
  let admissions = 0;
  const value = await setup((call) => {
    if (call.path.endsWith("/restore") && ++admissions === 2)
      return json(
        {
          success: true,
          category: "info",
          message: "Accepted.",
          operation_id: 9,
          operation: { ...child(9), state: "queued", result: null },
        },
        202,
      );
    if (call.path === "/api/operations/8")
      return json({ success: true, operation: child(8, "failed") });
    if (call.path === "/api/operations/9")
      return json({ success: true, operation: child(9) });
    if (call.path === "/api/operations/7" && admissions > 1)
      return json({
        success: true,
        operation: {
          ...parent(),
          recovery: {
            available: false,
            action: null,
            reason: "operation_already_restored",
            resolved_operation_id: 9,
          },
        },
      });
    return null;
  });
  await retainParent(value);
  await value.controller.loadReview();
  await value.controller.confirm();
  await flush();
  assert.equal(value.controller.getSnapshot().phase, "attention");
  assert.ok(value.admissions.conflict(resource));
  assert.equal(posts(value).length, 1);
  await value.controller.loadReview();
  assert.equal(value.controller.getSnapshot().phase, "ready");
  await value.controller.confirm();
  await flush();
  assert.deepEqual(
    posts(value).map((call) => call.key),
    ["restore-1", "restore-2"],
  );
  assert.deepEqual(
    value.controller
      .getSnapshot()
      .attempts.map((attempt) => attempt.operationId),
    [8, 9],
  );
  assert.equal(value.controller.getSnapshot().phase, "resolved");
});

test("a failed child whose fresh review is refused cannot mint or send another attempt", async () => {
  let admitted = false;
  const value = await setup((call) => {
    if (call.path.endsWith("/restore")) admitted = true;
    if (call.path === "/api/operations/8")
      return json({ success: true, operation: child(8, "failed") });
    if (call.path.endsWith("/restore-context") && admitted)
      return error("restoration_attempt_unresolved", 409, { operation_id: 8 });
    return null;
  });
  await value.controller.loadReview();
  await value.controller.confirm();
  await flush();
  await value.controller.loadReview();
  await value.controller.confirm();
  assert.equal(posts(value).length, 1);
  assert.equal(value.controller.getSnapshot().attempts.length, 1);
  assert.equal(value.controller.getSnapshot().relatedChildId, 8);
});

test("document cache retains a restoration across surfaces and isolates actors", async () => {
  const value = await setup();
  const { getRestorationController, getRestorationWorkspace } = await import(
    "../src/operations/restoration/controller.ts"
  );
  const first = getRestorationController(value.api, value.session, "owner", 7);
  const workspace = getRestorationWorkspace(value.api, value.session, "owner");
  let changes = 0;
  const off = workspace.subscribe(() => changes++);
  assert.equal(workspace.get(7), first);
  await first.loadReview();
  assert.ok(changes > 0);
  assert.ok(workspace.getSnapshot().includes(first));
  off();
  assert.equal(
    getRestorationController(value.api, value.session, "owner", 7),
    first,
  );
  assert.notEqual(
    getRestorationController(value.api, value.session, "someone-else", 7),
    first,
  );
});

test("later unrelated operation observations never undo a confirmed restoration relation", async () => {
  const value = await setup();
  await retainParent(value);
  await value.controller.loadReview();
  await value.controller.confirm();
  await flush();
  assert.equal(value.controller.getSnapshot().phase, "resolved");
  const count = value.calls.length;
  value.operations.accept({
    ...child(99),
    kind: "shuffle",
    targets: ["other"],
    result: { success: true },
  });
  await flush();
  assert.equal(value.controller.getSnapshot().phase, "resolved");
  assert.equal(value.calls.length, count);
});
