import { test } from "node:test";
import assert from "node:assert/strict";
import {
  safelySettled,
  createAdmissionRegistry,
} from "../src/operations/admissions.ts";
import { createApiClient } from "../src/api/client.ts";
import { operation, json } from "./workshop-data.mjs";
const sourceOp = (kind, state, result) => ({
  ...operation(1, state, result),
  kind,
});
for (const [kind, counter] of [
  ["workshop_finalize_raids", "promoted_count"],
  ["workshop_archive_append", "archived_count"],
  ["workshop_finalize_restore", "restored_count"],
])
  test(`${kind} positive no-write/restoration counts settle, unknown effects and bookkeeping do not`, () => {
    const facts = {
      success: false,
      status: "failed",
      aggregate_status: "partial",
      affected_count: 0,
      [counter]: 0,
      source_status: "not_started",
      ...(kind === "workshop_finalize_raids"
        ? { database_status: "pending" }
        : {}),
    };
    assert.equal(safelySettled(sourceOp(kind, "failed", facts)), true);
    assert.equal(
      safelySettled(
        sourceOp(kind, "rolled_back", {
          ...facts,
          source_status: "restored",
          database_status: "not_started",
        }),
      ),
      true,
    );
    for (const patch of [
      { affected_count: null },
      { [counter]: null },
      { status: "unknown" },
      { source_status: "unknown" },
      { database_status: "unknown" },
      { bookkeeping: [{ kind: "history", status: "failed" }] },
    ])
      assert.equal(
        safelySettled(sourceOp(kind, "failed", { ...facts, ...patch })),
        false,
      );
  });
test("dismissal separates a proved database result from proved no-write/restored source cleanup", () => {
  const base = {
    success: false,
    database_status: "complete",
    dismissed_ids: [11],
    changed_ids: [11],
    dismissed_count: 1,
    source_status: "failed",
    source_removed_count: 0,
  };
  assert.equal(
    safelySettled(sourceOp("pending_dismiss", "failed", base)),
    true,
  );
  assert.equal(
    safelySettled(
      sourceOp("pending_dismiss", "rolled_back", {
        ...base,
        source_status: "restored",
      }),
    ),
    true,
  );
  assert.equal(
    safelySettled(
      sourceOp("pending_dismiss", "failed", {
        ...base,
        database_status: "not_started",
        dismissed_ids: null,
        dismissed_count: null,
        changed_ids: null,
        source_status: "not_started",
      }),
    ),
    true,
  );
  for (const patch of [
    { source_status: "unknown" },
    { source_removed_count: null },
    { database_status: "unknown" },
    { dismissed_ids: [99, 11] },
    { dismissed_count: 2 },
    { changed_ids: [99] },
    { source_removed_count: 1 },
  ])
    assert.equal(
      safelySettled(
        sourceOp("pending_dismiss", "failed", { ...base, ...patch }),
      ),
      false,
    );
});
function creatorHarness() {
  let current;
  const listeners = new Set();
  const operations = {
    getSnapshot: () =>
      current ? new Map([[current.id, { operation: current }]]) : new Map(),
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    observe() {},
    accept(op) {
      current = op;
      listeners.forEach((f) => f());
    },
  };
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async () =>
      json(
        {
          success: true,
          operation_id: 1,
          operation: { ...operation(1), kind: "pair_create" },
        },
        202,
      ),
  });
  const r = createAdmissionRegistry(api, "alice", operations),
    request = {
      key: "creator-key",
      feature: "workshop",
      label: "Create archive for Evening Collection",
      path: "/api/playlist/target/pair",
      resources: [
        { resource: "playlist:target", access: "write" },
        { resource: "collection:spotify-playlists", access: "write" },
      ],
      body: { encoding: "json", value: { name: "Archive", create_new: true } },
      expected: { kind: "pair_create", target: "target" },
    };
  const unknown = {
    ...operation(1, "needs_attention", {
      success: false,
      outcome: "unknown",
      creation_unknown: true,
      association_status: "not_started",
    }),
    kind: "pair_create",
  };
  return { r, operations, request, unknown };
}
test("explicit same-actor unidentified creator acknowledgement retains receipt while releasing only validated admission holds", async () => {
  const h = creatorHarness();
  await h.r.submit(h.request, "alice");
  h.operations.accept(h.unknown);
  const a = h.r.unidentifiedCreationAcknowledgement("creator-key", "alice");
  assert.equal(a.operationId, 1);
  assert.equal(a.label, h.request.label);
  assert.ok(h.r.conflict(h.request.resources));
  assert.equal(h.r.acknowledgeUnidentifiedCreation(a, "alice"), true);
  const retained = h.r.getSnapshot().get("creator-key");
  assert.equal(retained.operationId, 1);
  assert.equal(retained.status, "attention");
  assert.equal(retained.acknowledgedOrphan.operationId, 1);
  assert.equal(h.r.conflict(h.request.resources), null);
  h.r.reconcile("creator-key", {
    ...h.unknown,
    state: "succeeded",
    result: { success: true, playlist: { id: "late" } },
  });
  assert.equal(h.r.getSnapshot().get("creator-key").status, "attention");
  assert.equal(
    h.r.getSnapshot().get("creator-key").request.body.value.name,
    "Archive",
  );
});
test("creator acknowledgement rejects wrong actor, active/no-ID/blocking, known orphan and mixed association/effect facts", async () => {
  const patches = [
    { state: "running" },
    { kind: "workshop_commit" },
    {
      result: {
        success: false,
        outcome: "unknown",
        creation_unknown: true,
        association_status: "unknown",
      },
    },
    {
      result: {
        success: false,
        outcome: "unknown",
        creation_unknown: true,
        association_status: "not_started",
        playlist: { id: "orphan" },
      },
    },
    { targets: ["target", "known-orphan"] },
    {
      result: {
        success: false,
        outcome: "unknown",
        creation_unknown: true,
        association_status: "not_started",
        pair: { id: 2 },
      },
    },
    {
      result: {
        success: true,
        outcome: "unknown",
        creation_unknown: true,
        association_status: "not_started",
      },
    },
  ];
  for (const patch of patches) {
    const h = creatorHarness();
    await h.r.submit(h.request, "alice");
    h.operations.accept({ ...h.unknown, ...patch });
    assert.equal(
      h.r.unidentifiedCreationAcknowledgement("creator-key", "alice"),
      null,
    );
    assert.ok(h.r.conflict(h.request.resources));
  }
  const h = creatorHarness();
  await h.r.submit(h.request, "alice");
  h.operations.accept(h.unknown);
  assert.equal(
    h.r.unidentifiedCreationAcknowledgement("creator-key", "bob"),
    null,
  );
  assert.equal(
    h.r.acknowledgeUnidentifiedCreation(
      { key: "creator-key", operationId: 1, label: h.request.label },
      "bob",
    ),
    false,
  );
  assert.ok(h.r.conflict(h.request.resources));
});
test("optional activity failure does not retain claims once required history and provider effects are confirmed", () => {
  const done = {
    ...operation(1, "succeeded", {
      success: true,
      operation: {
        aggregate_status: "complete",
        resources: [{ resource: "target", status: "complete" }],
        bookkeeping: [
          { kind: "activity", required: false, status: "failed" },
          { kind: "session_history", required: true, status: "complete" },
        ],
      },
    }),
    kind: "shuffle",
  };
  assert.equal(safelySettled(done), true);
  done.result.operation.bookkeeping[1].status = "unknown";
  assert.equal(safelySettled(done), false);
});
