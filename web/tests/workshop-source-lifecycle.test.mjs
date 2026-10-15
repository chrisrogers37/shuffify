import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
import { createSessionStore } from "../src/auth/session-store.ts";
import { createWorkshopController } from "../src/pages/workshop/core/controller.ts";
import { getAdmissionRegistry } from "../src/operations/admissions.ts";
import {
  uri,
  track,
  context,
  signed,
  operation,
  json,
} from "./workshop-data.mjs";
const sourceContext = () => ({
  success: true,
  target: { id: "target", name: "Evening Collection" },
  raid: { id: "collection", name: "Holding room" },
  archive: { id: "archive", name: "Archive", baseline_track_uris: [uri(4)] },
});
const stage = () => [
  {
    kind: "raid_promotion",
    source_playlist_id: "collection",
    pending_rows: [{ id: 11, track_uri: uri(1) }],
  },
];
const task = () => ({
  id: "source-1",
  key: "source-key",
  kind: "raid_promotion",
  label: "Promote 1 track from Holding room",
  source: { id: "collection", name: "Holding room" },
  dependsOn: [],
  selectedCount: 1,
  body: {
    source_playlist_id: "collection",
    target_track_uris: [uri(1), uri(2), uri(1)],
    track_ids: [11],
  },
});
async function harness(handler = () => null) {
  const calls = [],
    listeners = new Set();
  let actor = "alice",
    observed = new Map(),
    staging = stage(),
    work = [],
    prepareInput,
    proofs = [],
    events = [];
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({ path, init });
      return (
        (await handler(path, init)) ??
        json(
          path === "/api/workshop/target"
            ? context()
            : path.includes("source-context")
              ? sourceContext()
              : path.endsWith("current-state")
                ? {
                    success: true,
                    representable: true,
                    current_track_uris: [uri(1), uri(2), uri(1)],
                    current_tracks: context().tracks,
                    loaded_at: "now",
                  }
                : { success: true, sessions: [] },
        )
      );
    },
  });
  const session = createSessionStore(async () => signed(actor));
  await session.load();
  const watchers = new Set();
  const operations = {
    getSnapshot: () => observed,
    subscribe: (fn) => (watchers.add(fn), () => watchers.delete(fn)),
    observe() {},
    accept(op) {
      finish(op);
    },
  };
  function finish(op) {
    observed = new Map(observed).set(op.id, {
      id: op.id,
      operation: op,
      observation: "settled",
      error: null,
    });
    watchers.forEach((fn) => fn());
  }
  const c = createWorkshopController(api, session, "alice", "target", {
    operations,
    key: () => "target-key",
  });
  await c.load();
  const notify = () => listeners.forEach((fn) => fn());
  const coordinator = {
    snapshot: () => staging,
    discard() {
      staging = [];
      notify();
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    prepareReview(input) {
      prepareInput = input;
      return { tasks: [task()] };
    },
    targetConfirmed(proof) {
      proofs.push(proof);
      staging = staging.filter(
        (x) =>
          x.kind !== "raid_promotion" ||
          !x.pending_rows.some((r) => r.id === 11),
      );
      work = [
        {
          id: "source-1",
          kind: "raid_promotion",
          label: task().label,
          source: task().source,
          operationId: null,
          status: "ready",
        },
      ];
      notify();
    },
    retainedWork: () => work,
    manualDraftMutation(event) {
      events.push(event);
    },
  };
  c.registerSourceCoordinator(coordinator);
  return {
    c,
    api,
    calls,
    operations,
    coordinator,
    finish,
    events,
    proofs,
    prepareInput: () => prepareInput,
    setStaging(v) {
      staging = v;
      notify();
    },
    setWork(v) {
      work = v;
      notify();
    },
    async actor(v) {
      actor = v;
      await session.refresh();
    },
  };
}
const saved = (order = [uri(1), uri(2), uri(1)]) =>
  operation(2001, "succeeded", {
    success: true,
    target_status: "saved",
    saved_track_uris: order,
    message: "Saved",
    confirmed_batches: 1,
    total_batches: 1,
  });
test("common Save freezes authoritative source tasks and exact body once before confirmation", async () => {
  const h = await harness();
  const r = await h.c.prepareReview();
  assert.equal(
    h.calls.find((x) => x.path.includes("source-context")).path,
    "/api/workshop/target/source-context?raid=1&archive=0",
  );
  assert.equal(h.prepareInput().actor, "alice");
  assert.deepEqual(h.prepareInput().desired, r.body.track_uris);
  assert.equal(r.sourceReview.tasks[0].key, "source-key");
  assert.ok(Object.isFrozen(r.sourceReview.tasks[0].body.track_ids));
  assert.equal(h.calls.filter((x) => x.init.method === "POST").length, 0);
});
test("source review invalidation during an asynchronous coordinator read prevents submission", async () => {
  const h = await harness();
  let resolve;
  h.coordinator.prepareReview = () => new Promise((r) => (resolve = r));
  const pending = h.c.prepareReview();
  await new Promise((r) => setTimeout(r, 0));
  h.setStaging([
    ...stage(),
    {
      kind: "archive_append",
      source_playlist_id: "archive",
      track_uris: [uri(2)],
      baseline_track_uris: [uri(4)],
    },
  ]);
  resolve({ tasks: [task()] });
  await assert.rejects(pending, /changed/i);
  assert.equal(h.c.getSnapshot().review, null);
  assert.equal(h.calls.filter((x) => x.init.method === "POST").length, 0);
});
for (const change of [
  (t) => ({ ...t, source: { id: "replacement", name: "Wrong" } }),
  (t) => ({ ...t, body: { ...t.body, target_track_uris: [uri(3)] } }),
  (t) => ({ ...t, body: { ...t.body, track_ids: [99] } }),
  (t) => ({ ...t, dependsOn: ["absent"] }),
])
  test("source review refuses replaced sources, wrong saved order, unselected identities and invalid dependencies", async () => {
    const h = await harness();
    h.coordinator.prepareReview = () => ({ tasks: [change(task())] });
    await assert.rejects(
      h.c.prepareReview(),
      /source|review|task|selected|depend|order/i,
    );
    assert.equal(h.c.getSnapshot().review, null);
  });
test("only proved original target success hands off its detached exact source work once", async () => {
  const h = await harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : null,
  );
  const r = await h.c.prepareReview();
  await h.c.submitReviewed(r.id);
  assert.equal(h.proofs.length, 0);
  h.finish(saved());
  h.finish(saved());
  assert.equal(h.proofs.length, 1);
  const p = h.proofs[0];
  assert.equal(p.key, "target-key");
  assert.equal(p.reviewId, r.id);
  assert.equal(p.operationId, 2001);
  assert.equal(p.target.id, "target");
  assert.deepEqual(p.staging, stage());
  assert.ok(Object.isFrozen(p.sourceReview.tasks));
  assert.equal(h.c.hasDraft(), false);
  assert.equal(h.c.hasRetainedSourceWork(), true);
  assert.equal(h.c.permission("review").allowed, false);
  assert.equal(h.c.discardLocal(), true);
  assert.equal(h.c.hasRetainedSourceWork(), true);
  assert.equal(h.proofs.length, 1);
});
test("history uncertainty, a matching read and unrelated successful observations never authorize source handoff", async () => {
  const h = await harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : null,
  );
  const r = await h.c.prepareReview();
  await h.c.submitReviewed(r.id);
  h.finish(
    operation(2001, "needs_attention", {
      success: false,
      target_status: "saved",
      saved_track_uris: r.body.track_uris,
      error_code: "history_confirmation_failed",
    }),
  );
  await h.c.checkCurrent();
  h.finish({ ...saved(), id: 2002 });
  assert.equal(h.proofs.length, 0);
  assert.equal(h.c.hasRetainedSourceWork(), false);
});
test("target handoff consumes only submitted staging while preserving later source staging", async () => {
  const h = await harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : null,
  );
  const r = await h.c.prepareReview();
  await h.c.submitReviewed(r.id);
  h.setStaging([
    ...stage(),
    {
      kind: "raid_promotion",
      source_playlist_id: "collection",
      pending_rows: [{ id: 12, track_uri: uri(2) }],
    },
  ]);
  h.finish(saved());
  assert.equal(h.proofs.length, 1);
  assert.deepEqual(h.proofs[0].staging, stage());
  assert.equal(h.c.getSnapshot().staging[0].pending_rows[0].id, 12);
  assert.equal(h.c.hasDraft(), true);
});
test("manual duplicate removal and filtered append publish exact events atomically with source staging", async () => {
  const h = await harness();
  h.setStaging([]);
  const seen = [];
  h.coordinator.manualDraftMutation = (e) => {
    h.events.push(e);
    h.setStaging([
      {
        kind: "archive_append",
        source_playlist_id: "archive",
        track_uris: [uri(1)],
        baseline_track_uris: [uri(4)],
      },
    ]);
  };
  h.c.subscribe(() =>
    seen.push({
      count: h.c.getSnapshot().working.length,
      staging: h.c.getSnapshot().staging.length,
    }),
  );
  const row = h.c.getSnapshot().working[0];
  assert.equal(h.c.removeOccurrence(row.key), true);
  assert.equal(h.events[0].occurrence.key, row.key);
  assert.equal(h.events[0].beforeCount, 2);
  assert.equal(h.events[0].afterCount, 1);
  assert.equal(h.events[0].baselineCount, 2);
  assert.equal(h.events[0].afterRevision, h.events[0].beforeRevision + 1);
  assert.deepEqual(seen, [{ count: 2, staging: 1 }]);
  h.c.appendTracks([track(1), track(3), { ...track(4), is_local: true }], {
    mode: "append-missing",
    origin: "catalog",
  });
  const e = h.events[1];
  assert.deepEqual(
    e.occurrences.map((x) => x.uri),
    [uri(3)],
  );
  assert.equal(e.afterCounts[uri(3)], 1);
  assert.equal(e.baselineCounts[uri(3)], 0);
  assert.equal(e.origin, "catalog");
  assert.ok(Object.isFrozen(e.occurrences));
});
test("reorder, preview, replacement, adoption and reset never fabricate manual archive events", async () => {
  const h = await harness();
  h.setStaging([]);
  const rows = h.c.getSnapshot().working;
  h.c.moveOccurrence(rows[0].key, 3);
  const p = h.c.getPreviewInput();
  h.c.applyPreview(p, [uri(1), uri(2), uri(1)]);
  h.c.replaceDraftOrder([uri(2)], {
    expectedRevision: h.c.getSnapshot().revision,
  });
  h.c.discardLocal();
  assert.equal(await h.c.checkCurrent(), true, h.c.getSnapshot().notice);
  h.c.adoptCurrent(h.c.getSnapshot().observation.id);
  assert.equal(h.events.length, 0);
});
test("unregistered coordinator cannot silently downgrade nonempty source staging to target-only Save", async () => {
  const h = await harness();
  const old = h.coordinator.prepareReview;
  h.coordinator.prepareReview = undefined;
  await assert.rejects(h.c.prepareReview(), /source coordinator/i);
  h.coordinator.prepareReview = old;
});

test("a saved target observed under another actor retains the exact handoff until explicit original-account continuation", async () => {
  const h = await harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : null,
  );
  const r = await h.c.prepareReview();
  await h.c.submitReviewed(r.id);
  await h.actor("bob");
  h.finish(saved());
  assert.equal(h.proofs.length, 0);
  assert.equal(h.c.hasRetainedSourceWork(), true);
  assert.equal(h.c.retrySourceHandoff(), false);
  await h.actor("alice");
  assert.equal(h.proofs.length, 0);
  assert.equal(h.c.retrySourceHandoff(), true);
  assert.equal(h.proofs.length, 1);
  assert.equal(h.proofs[0].key, r.key);
});
test("handoff callback failure retains the saved proof for one explicit same-review recovery", async () => {
  const h = await harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : null,
  );
  const original = h.coordinator.targetConfirmed;
  h.coordinator.targetConfirmed = () => {
    throw Error("tool state unavailable");
  };
  const r = await h.c.prepareReview();
  await h.c.submitReviewed(r.id);
  h.finish(saved());
  assert.equal(h.c.getSnapshot().sourceHandoff.reviewId, r.id);
  assert.equal(h.c.hasRetainedSourceWork(), true);
  h.coordinator.targetConfirmed = original;
  assert.equal(h.c.retrySourceHandoff(), true);
  assert.equal(h.proofs.length, 1);
  assert.equal(h.c.getSnapshot().sourceHandoff, null);
});
test("proved restoration clears only receipt attention while retaining original facts and independent draft order", async () => {
  const h = await harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : path.endsWith("/restore")
        ? json(
            {
              success: true,
              operation_id: 2003,
              operation: { ...operation(2003), kind: "operation_restore" },
            },
            202,
          )
        : null,
  );
  h.setStaging([]);
  h.coordinator.prepareReview = () => ({ tasks: [] });
  h.c.removeOccurrence(h.c.getSnapshot().working[0].key);
  const r = await h.c.prepareReview();
  await h.c.submitReviewed(r.id);
  const parent = {
    ...operation(2001, "needs_attention", {
      success: false,
      target_status: "unknown",
      message: "Original unknown target facts",
      confirmed_batches: 0,
      total_batches: 1,
    }),
    recovery: { available: true, action: "restore", reason: null },
  };
  h.finish(parent);
  const before = h.c.getSnapshot(),
    registry = getAdmissionRegistry(h.api, "alice", h.operations);
  const reviewed = {
    version: 1,
    reviewed_scope: "a".repeat(64),
    reviewed_resources: [{ playlist_id: "target", digest: "b".repeat(64) }],
  };
  await registry.submitRestoration(
    {
      parent,
      context: {
        kind: "operation_restore",
        operation_id: 2001,
        available: true,
        reviewed,
      },
      request: {
        key: "restore",
        feature: "restoration",
        label: "Restore original target",
        resources: [{ resource: "playlist:target", access: "write" }],
        path: "/api/operations/2001/restore",
        body: { encoding: "json", value: { reviewed } },
        expected: { kind: "operation_restore", target: "target" },
      },
    },
    "alice",
  );
  const child = {
    ...operation(2003, "succeeded", {
      success: true,
      parent_operation_id: 2001,
      parent_resolution: "complete",
    }),
    kind: "operation_restore",
  };
  assert.equal(
    registry.reconcileRestoration(
      "restore",
      {
        child,
        parent: {
          ...parent,
          recovery: {
            available: false,
            action: null,
            reason: "operation_already_restored",
            resolved_operation_id: 2003,
          },
        },
      },
      "alice",
    ),
    true,
  );
  assert.equal(h.c.getSnapshot().receipt.restoredBy, 2003);
  assert.equal(
    h.c.getSnapshot().receipt.result.facts.message,
    "Original unknown target facts",
  );
  assert.deepEqual(h.c.getSnapshot().working, before.working);
  assert.deepEqual(h.c.getSnapshot().baseline, before.baseline);
  assert.equal(h.c.permission("review").allowed, true);
});
