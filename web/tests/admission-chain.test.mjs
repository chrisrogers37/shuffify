import { test } from "node:test";
import assert from "node:assert/strict";
import { createAdmissionRegistry } from "../src/operations/admissions.ts";
import { createApiClient } from "../src/api/client.ts";
import { operation, uri, json } from "./workshop-data.mjs";
const parent = () => ({
  key: "target-key",
  feature: "workshop",
  label: "Save Evening Collection",
  resources: [
    { resource: "playlist:target", access: "write" },
    { resource: "playlist:archive", access: "write" },
  ],
  path: "/api/workshop/target/commit",
  body: {
    encoding: "json",
    value: { baseline_track_uris: [uri(1)], track_uris: [uri(2)] },
  },
  expected: { kind: "workshop_commit", target: "target" },
});
const child = (key = "source-key", dependsOn = []) => ({
  request: {
    key,
    feature: "workshop",
    label: "Finish archive",
    resources: [
      { resource: "playlist:target", access: "read" },
      { resource: "playlist:archive", access: "write" },
    ],
    path: "/api/playlist/target/pair/archive",
    body: {
      encoding: "json",
      value: {
        source_playlist_id: "archive",
        target_track_uris: [uri(2)],
        track_uris: [uri(1)],
        baseline_track_uris: [],
      },
    },
    expected: { kind: "workshop_archive_append", target: "target" },
  },
  dependsOn,
});
const proof = () =>
  operation(2001, "succeeded", {
    success: true,
    target_status: "saved",
    saved_track_uris: [uri(2)],
  });
function harness(
  handler = () =>
    json({ success: true, operation_id: 2001, operation: operation() }, 202),
) {
  const calls = [],
    listeners = new Set();
  let state = new Map();
  const operations = {
    subscribe: (f) => (listeners.add(f), () => listeners.delete(f)),
    getSnapshot: () => state,
    observe() {},
    accept(op) {
      this.finish(op);
    },
    finish(op) {
      state = new Map(state).set(op.id, { id: op.id, operation: op });
      listeners.forEach((f) => f());
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
    calls,
    operations,
  };
}
test("source reservation acquires all claims atomically with zero HTTP and keeps target/source held after target settles", async () => {
  const h = harness();
  const reservation = h.r.reserveReviewedContinuations(
    parent(),
    [child()],
    "alice",
  );
  assert.equal(h.calls.length, 0);
  assert.equal(h.r.getSnapshot().get("source-key").status, "reserved");
  await assert.rejects(
    h.r.submitContinuation("source-key", "alice"),
    /target|confirm/i,
  );
  assert.equal(h.calls.length, 0);
  await reservation.submitTarget("alice");
  h.operations.finish(proof());
  assert.ok(h.r.conflict([{ resource: "playlist:archive", access: "write" }]));
  assert.equal(
    h.r.activateReviewedContinuations("target-key", proof(), "alice"),
    true,
  );
  assert.equal(h.calls.length, 1);
});
test("failed reservation leaves no partial claims and no target POST; invalid keys, target, source body or dependencies are rejected", async () => {
  const h = harness(() => Promise.reject(Error("lost")));
  await assert.rejects(
    h.r.submit(
      {
        ...parent(),
        key: "other",
        resources: [{ resource: "playlist:archive", access: "write" }],
      },
      "alice",
    ),
  );
  assert.throws(() =>
    h.r.reserveReviewedContinuations(parent(), [child()], "alice"),
  );
  assert.equal(h.r.getSnapshot().size, 1);
  assert.equal(h.calls.length, 1);
  for (const spec of [
    { ...child(), request: { ...child().request, key: "target-key" } },
    {
      ...child(),
      request: {
        ...child().request,
        expected: { kind: "workshop_archive_append", target: "wrong" },
      },
    },
    child("source-key", ["absent"]),
  ]) {
    const g = harness();
    assert.throws(() =>
      g.r.reserveReviewedContinuations(parent(), [spec], "alice"),
    );
    assert.equal(g.r.getSnapshot().size, 0);
    assert.equal(g.calls.length, 0);
  }
});
test("proven target admission refusal releases only unstarted source reservations; target5xx and later4xx keep them", async () => {
  for (const status of [400, 503]) {
    const h = harness(() =>
      json(
        {
          success: false,
          error_code: "refused",
          category: "error",
          message: "Refused",
        },
        status,
      ),
    );
    const res = h.r.reserveReviewedContinuations(parent(), [child()], "alice");
    await assert.rejects(res.submitTarget("alice"));
    assert.equal(
      h.r.getSnapshot().get("source-key").status,
      status === 400 ? "refused" : "reserved",
    );
    assert.equal(
      !!h.r.conflict([{ resource: "playlist:archive", access: "write" }]),
      status === 503,
    );
  }
});
test("only matching original proved saved target activates children; actor, blocking, history and order mismatches do not", async () => {
  const h = harness();
  const res = h.r.reserveReviewedContinuations(parent(), [child()], "alice");
  await res.submitTarget("alice");
  for (const p of [
    { ...proof(), id: 999 },
    { ...proof(), kind: "visibility" },
    operation(2001, "needs_attention", {
      success: false,
      target_status: "saved",
      saved_track_uris: [uri(2)],
    }),
    operation(2001, "succeeded", {
      success: true,
      target_status: "saved",
      saved_track_uris: [uri(3)],
    }),
  ])
    assert.equal(
      h.r.activateReviewedContinuations("target-key", p, "alice"),
      false,
    );
  assert.equal(
    h.r.activateReviewedContinuations("target-key", proof(), "bob"),
    false,
  );
  assert.equal(h.r.getSnapshot().get("source-key").status, "reserved");
  assert.equal(h.calls.length, 1);
});
test("activated source dispatch preserves frozen body/key and unknown replay without admitting unrelated same-family changes", async () => {
  let n = 0;
  const h = harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : ++n === 1
        ? Promise.reject(Error("lost"))
        : json(
            {
              success: true,
              operation_id: 2002,
              operation: {
                ...operation(2002),
                kind: "workshop_archive_append",
              },
            },
            202,
          ),
  );
  const spec = child(),
    res = h.r.reserveReviewedContinuations(parent(), [spec], "alice");
  spec.request.body.value.track_uris = [];
  await res.submitTarget("alice");
  assert.equal(
    h.r.activateReviewedContinuations("target-key", proof(), "alice"),
    true,
  );
  await assert.rejects(h.r.submitContinuation("source-key", "alice"));
  await assert.rejects(h.r.submit({ ...parent(), key: "intruder" }, "alice"));
  await h.r.retry("source-key", "alice");
  assert.equal(h.calls.length, 3);
  assert.equal(h.calls[1].init.body, h.calls[2].init.body);
  assert.deepEqual(JSON.parse(h.calls[1].init.body).track_uris, [uri(1)]);
  assert.equal(h.calls[2].init.headers.get("Idempotency-Key"), "source-key");
});
const retrySourceContext = () => ({
  target: { id: "target", name: "Evening Collection" },
  raid: null,
  archive: { id: "archive", name: "Archive" },
});
test("explicit proved source retry reserves identical new intent and rebinds only unsubmitted same-family dependencies atomically", async () => {
  const h = harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : json(
          {
            success: true,
            operation_id: 2002,
            operation: { ...operation(2002), kind: "workshop_archive_append" },
          },
          202,
        ),
  );
  const first = child(),
    later = child("later-key", ["source-key"]);
  const res = h.r.reserveReviewedContinuations(
    parent(),
    [first, later],
    "alice",
  );
  await res.submitTarget("alice");
  h.r.activateReviewedContinuations("target-key", proof(), "alice");
  await h.r.submitContinuation("source-key", "alice");
  h.operations.finish({
    ...operation(2002, "rolled_back", {
      success: false,
      status: "failed",
      source_status: "restored",
      affected_count: 0,
      archived_count: 0,
    }),
    kind: "workshop_archive_append",
  });
  const replacement = { ...first.request, key: "reviewed-retry" };
  const before = h.calls.length;
  assert.equal(
    h.r.reserveContinuationRetry(
      {
        failedKey: "source-key",
        request: replacement,
        currentTarget: { id: "target", track_uris: [uri(2)] },
        sourceContext: retrySourceContext(),
      },
      "alice",
    ),
    true,
  );
  assert.equal(h.calls.length, before);
  assert.equal(h.r.getSnapshot().get("reviewed-retry").status, "reserved");
  assert.deepEqual(h.r.getSnapshot().get("later-key").continuation.dependsOn, [
    "reviewed-retry",
  ]);
  assert.equal(h.r.getSnapshot().get("source-key").operationId, 2002);
  assert.deepEqual(
    h.r.getSnapshot().get("later-key").request.body.value,
    later.request.body.value,
  );
  assert.ok(h.r.conflict([{ resource: "playlist:archive", access: "write" }]));
});
test("source retry cannot rebind unknown or accepted dependencies, change the body, follow replacement source, or switch actor", async () => {
  const h = harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : json(
          {
            success: true,
            operation_id: 2002,
            operation: { ...operation(2002), kind: "workshop_archive_append" },
          },
          202,
        ),
  );
  const first = child();
  await h.r
    .reserveReviewedContinuations(parent(), [first], "alice")
    .submitTarget("alice");
  h.r.activateReviewedContinuations("target-key", proof(), "alice");
  await h.r.submitContinuation("source-key", "alice");
  h.operations.finish({
    ...operation(2002, "failed", {
      success: false,
      status: "failed",
      affected_count: 0,
      archived_count: 0,
    }),
    kind: "workshop_archive_append",
  });
  const base = {
    failedKey: "source-key",
    request: { ...first.request, key: "new" },
    currentTarget: { id: "target", track_uris: [uri(2)] },
    sourceContext: retrySourceContext(),
  };
  assert.equal(h.r.reserveContinuationRetry(base, "bob"), false);
  for (const spec of [
    { ...base, currentTarget: { id: "target", track_uris: [uri(3)] } },
    {
      ...base,
      sourceContext: {
        ...retrySourceContext(),
        archive: { id: "replacement", name: "Other" },
      },
    },
    {
      ...base,
      request: {
        ...base.request,
        body: {
          encoding: "json",
          value: { ...base.request.body.value, track_uris: [] },
        },
      },
    },
  ])
    assert.equal(h.r.reserveContinuationRetry(spec, "alice"), false);
  assert.equal(h.r.getSnapshot().has("new"), false);
  assert.equal(h.calls.length, 2);
});

test("an accepted dependent prevents failed-task replacement without changing any reservation or issuing HTTP", async () => {
  const h = harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : json(
          {
            success: true,
            operation_id: 2002,
            operation: { ...operation(2002), kind: "workshop_archive_append" },
          },
          202,
        ),
  );
  const first = child();
  await h.r
    .reserveReviewedContinuations(
      parent(),
      [first, child("later", ["source-key"])],
      "alice",
    )
    .submitTarget("alice");
  h.r.activateReviewedContinuations("target-key", proof(), "alice");
  await h.r.submitContinuation("source-key", "alice");
  h.r.accept("later", { ...operation(2003), kind: "workshop_archive_append" });
  h.operations.finish({
    ...operation(2002, "failed", {
      success: false,
      status: "failed",
      affected_count: 0,
      archived_count: 0,
    }),
    kind: "workshop_archive_append",
  });
  assert.equal(
    h.r.reserveContinuationRetry(
      {
        failedKey: "source-key",
        request: { ...first.request, key: "retry" },
        currentTarget: { id: "target", track_uris: [uri(2)] },
        sourceContext: retrySourceContext(),
      },
      "alice",
    ),
    false,
  );
  assert.equal(h.r.getSnapshot().has("retry"), false);
  assert.deepEqual(h.r.getSnapshot().get("later").continuation.dependsOn, [
    "source-key",
  ]);
  assert.equal(h.calls.length, 2);
});
test("only conclusive original400/401/403 refusal can be freshly reviewed without an accepted ID", async () => {
  for (const status of [400, 401, 403, 409, 429, 503]) {
    const h = harness((path) =>
      path.endsWith("/commit")
        ? json(
            { success: true, operation_id: 2001, operation: operation() },
            202,
          )
        : json(
            {
              success: false,
              error_code: "refused",
              category: "error",
              message: "Refused admission",
            },
            status,
          ),
    );
    const spec = child();
    await h.r
      .reserveReviewedContinuations(parent(), [spec], "alice")
      .submitTarget("alice");
    h.r.activateReviewedContinuations("target-key", proof(), "alice");
    await assert.rejects(h.r.submitContinuation("source-key", "alice"));
    const before = h.calls.length;
    const reviewed = h.r.reserveContinuationRetry(
      {
        failedKey: "source-key",
        request: { ...spec.request, key: "explicit-review" },
        currentTarget: { id: "target", track_uris: [uri(2)] },
        sourceContext: retrySourceContext(),
      },
      "alice",
    );
    assert.equal(reviewed, [400, 401, 403].includes(status));
    assert.equal(h.calls.length, before);
    assert.equal(h.r.getSnapshot().get("source-key").operationId, null);
  }
});
