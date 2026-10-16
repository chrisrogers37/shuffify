import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
import { createSessionStore } from "../src/auth/session-store.ts";
import {
  uri,
  track,
  context,
  signed,
  operation,
  json,
} from "./workshop-data.mjs";
async function harness(handler = () => null) {
  const calls = [];
  let actor = "alice";
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
              ? {
                  success: true,
                  target: { id: "target", name: "Evening Collection" },
                  raid: null,
                  archive: null,
                }
              : { success: true, sessions: [] },
        )
      );
    },
  });
  const session = createSessionStore(async () => signed(actor));
  await session.load();
  const listeners = new Set();
  let snap = new Map();
  const operations = {
    subscribe: (fn) => (listeners.add(fn), () => listeners.delete(fn)),
    getSnapshot: () => snap,
    accept: (op) => {
      snap = new Map(snap).set(op.id, {
        id: op.id,
        operation: op,
        observation: "watching",
        error: null,
      });
      listeners.forEach((fn) => fn());
    },
    observe: () => {},
  };
  const { createWorkshopController } =
    await import("../src/pages/workshop/core/controller.ts");
  const c = createWorkshopController(api, session, "alice", "target", {
    operations,
    key: () => "frozen-key",
  });
  await c.load();
  return {
    c,
    calls,
    session,
    changeActor: async (v) => {
      actor = v;
      await session.refresh();
    },
    finish: (op) => {
      snap = new Map(snap).set(op.id, {
        id: op.id,
        operation: op,
        observation: "settled",
        error: null,
      });
      listeners.forEach((fn) => fn());
    },
  };
}
test("a busy Workshop commit stays refused without adopting the blocking operation", async () => {
  const h = await harness((path) => path.endsWith("/commit") ? json({
    success: false, error_code: "playlist_busy", message: "This playlist is busy.",
    category: "error", operation_id: 7777,
  }, 409) : null);
  h.c.removeOccurrence(h.c.getSnapshot().working[1].key);
  await h.c.submitReviewed((await h.c.prepareReview()).id);
  assert.equal(h.c.getSnapshot().receipt.status, "refused");
  assert.equal(h.c.getSnapshot().receipt.operationId, null);
  assert.equal(h.c.getSnapshot().receipt.busyId, 7777);
  h.finish({...operation(7777, "succeeded", {
    success: true, target_status: "saved", saved_track_uris: [uri(1), uri(1)],
    message: "Another request saved its playlist.",
  }), kind: "shuffle"});
  const receipt = h.c.getSnapshot().receipt;
  assert.equal(receipt.status, "refused");
  assert.equal(receipt.operationId, null);
  assert.equal(receipt.operation, null);
  assert.equal(receipt.result, null);
  assert.deepEqual(h.c.getSnapshot().baseline.map(row => row.uri), [uri(1), uri(2), uri(1)]);
  assert.equal(h.c.hasDraft(), true);
});
test("Workshop operation observations must match the reviewed kind and target", async () => {
  for (const mismatch of [{kind: "shuffle"}, {targets: ["another-playlist"]}]) {
    const h = await harness((path) => path.endsWith("/commit")
      ? json({success: true, operation_id: 2001, operation: operation()}, 202) : null);
    h.c.removeOccurrence(h.c.getSnapshot().working[1].key);
    await h.c.submitReviewed((await h.c.prepareReview()).id);
    h.finish({...operation(2001, "succeeded", {
      success: true, target_status: "saved", saved_track_uris: [uri(1), uri(1)],
      message: "A mismatched operation succeeded.",
    }), ...mismatch});
    assert.equal(h.c.getSnapshot().receipt.operation.kind, "workshop_commit");
    assert.equal(h.c.getSnapshot().receipt.operation.state, "queued");
    assert.equal(h.c.getSnapshot().receipt.result, null);
    assert.deepEqual(h.c.getSnapshot().baseline.map(row => row.uri), [uri(1), uri(2), uri(1)]);
    assert.equal(h.c.hasDraft(), true);
  }
});
test("locks block moving held rows/landing on held positions but removal remains allowed", async () => {
  const { c } = await harness();
  const rows = c.getSnapshot().working;
  c.setLocks({ [uri(2)]: true });
  assert.equal(c.moveOccurrence(rows[0].key, 2), false);
  assert.equal(c.moveOccurrence(rows[1].key, 3), false);
  assert.equal(c.removeOccurrence(rows[1].key), true);
  assert.deepEqual(
    c.getSnapshot().working.map((o) => o.uri),
    [uri(1), uri(1)],
  );
});
test("stale previews and reviews cannot overwrite a newer draft or lock configuration", async () => {
  let release;
  const h = await harness((path) =>
    path.includes("source-context") ? new Promise((r) => (release = r)) : null,
  );
  const { c } = h;
  const input = c.getPreviewInput();
  c.moveOccurrence(c.getSnapshot().working[0].key, 3);
  assert.equal(c.applyPreview(input, [uri(1), uri(1), uri(2)]), false);
  const preparation = c.prepareReview();
  c.removeOccurrence(c.getSnapshot().working[0].key);
  release(
    json({
      success: true,
      target: { id: "target", name: "Evening Collection" },
      raid: null,
      archive: null,
    }),
  );
  await assert.rejects(preparation, /changed/i);
  assert.equal(c.getSnapshot().review, null);
});
test("review is deeply frozen; duplicate activation dispatches one exact queued body", async () => {
  let release;
  const { c, calls } = await harness((path) =>
    path.endsWith("/commit") ? new Promise((r) => (release = r)) : null,
  );
  c.moveOccurrence(c.getSnapshot().working[0].key, 3);
  const review = await c.prepareReview();
  assert.ok(Object.isFrozen(review.body.track_uris));
  const first = c.submitReviewed(review.id);
  await c.submitReviewed(review.id);
  assert.equal(calls.filter((r) => r.path.endsWith("/commit")).length, 1);
  release(
    json({ success: true, operation_id: 2001, operation: operation() }, 202),
  );
  await first;
  assert.equal(c.getSnapshot().receipt.operationId, 2001);
  assert.deepEqual(
    JSON.parse(calls.find((r) => r.path.endsWith("/commit")).init.body),
    {
      baseline_track_uris: [uri(1), uri(2), uri(1)],
      track_uris: [uri(2), uri(1), uri(1)],
    },
  );
});
test("lost admission retains same frozen key/body, read match does not settle it, actor change forbids retry", async () => {
  let attempt = 0;
  const { c, calls, changeActor } = await harness((path) =>
    path.endsWith("/commit")
      ? ++attempt === 1
        ? Promise.reject(Error("connection lost"))
        : json(
            { success: true, operation_id: 2001, operation: operation() },
            202,
          )
      : path.endsWith("current-state")
        ? json({
            success: true,
            representable: true,
            current_track_uris: [uri(2), uri(1), uri(1)],
            current_tracks: [track(2), track(1), track(1)],
            loaded_at: "2026-01-01Z",
          })
        : null,
  );
  c.moveOccurrence(c.getSnapshot().working[0].key, 3);
  await c.submitReviewed((await c.prepareReview()).id);
  assert.equal(c.getSnapshot().receipt.status, "unknown");
  await c.checkCurrent();
  assert.equal(c.adoptCurrent(c.getSnapshot().observation.id), false);
  await c.retryExactRequest();
  const sent = calls.filter((r) => r.path.endsWith("/commit"));
  assert.equal(sent.length, 2);
  assert.equal(sent[0].init.body, sent[1].init.body);
  assert.equal(sent[1].init.headers.get("Idempotency-Key"), "frozen-key");
  await changeActor("bob");
  assert.equal(c.permission("save").allowed, false);
});
test("verified target plus failed history preserves rich facts and blocks new writes", async () => {
  const h = await harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : null,
  );
  const { c } = h;
  c.removeOccurrence(c.getSnapshot().working[1].key);
  await c.submitReviewed((await c.prepareReview()).id);
  h.finish(
    operation(2001, "needs_attention", {
      success: false,
      target_status: "saved",
      saved_track_uris: [uri(1), uri(1)],
      message: "History confirmation failed",
      confirmed_batches: 1,
      total_batches: 1,
      bookkeeping: { history: "failed" },
    }),
  );
  assert.equal(c.getSnapshot().receipt.result.target_status, "saved");
  assert.equal(
    c.getSnapshot().receipt.result.facts.bookkeeping.history,
    "failed",
  );
  assert.equal(c.permission("save").allowed, false);
  assert.equal(c.getSnapshot().baseline.length, 3);
});
test("failed second inspection clears prior adoption; discard restores baseline and retains unknown receipt", async () => {
  let reads = 0;
  const { c } = await harness((path) =>
    path.endsWith("current-state")
      ? ++reads === 1
        ? json({
            success: true,
            representable: true,
            current_track_uris: [],
            current_tracks: [],
            loaded_at: "now",
          })
        : json(
            {
              success: false,
              error_code: "read_failed",
              message: "Unavailable",
              category: "error",
            },
            503,
          )
      : path.endsWith("/commit")
        ? Promise.reject(Error("lost"))
        : null,
  );
  c.removeOccurrence(c.getSnapshot().working[0].key);
  await c.checkCurrent();
  assert.ok(c.getSnapshot().observation);
  await c.checkCurrent();
  assert.equal(c.getSnapshot().observation, null);
  await c.submitReviewed((await c.prepareReview()).id);
  c.discardLocal();
  assert.equal(c.getSnapshot().working.length, 3);
  assert.equal(c.getSnapshot().receipt.status, "unknown");
});
test("source-only staging is dirty and cannot be declared saved by a target-only commit", async () => {
  const { c, calls } = await harness();
  let staged = [
    {
      kind: "archive_append",
      source_playlist_id: "archive",
      track_uris: [uri(1)],
      baseline_track_uris: [],
    },
  ];
  let invalidate;
  const off = c.registerSourceCoordinator({
    snapshot: () => staged,
    discard: () => {
      staged = [];
    },
    subscribe: (fn) => ((invalidate = fn), () => {}),
  });
  assert.equal(c.hasDraft(), true);
  await assert.rejects(c.prepareReview(), /source/i);
  assert.equal(calls.filter((r) => r.path.endsWith("/commit")).length, 0);
  c.discardLocal();
  assert.equal(c.hasDraft(), false);
  off();
});
test("named draft lost response is reconciled by an exact read and never automatically repeated", async () => {
  let saved = null,
    posts = 0;
  const { c } = await harness((path, init) => {
    if (path.endsWith("/sessions") && init.method === "POST") {
      posts++;
      const body = JSON.parse(init.body);
      saved = {
        id: 17,
        user_id: 1,
        playlist_id: "target",
        ...body,
        track_count: body.track_uris.length,
        created_at: null,
        updated_at: null,
      };
      throw Error("lost reply");
    }
    if (path.endsWith("/sessions") && init.method === "GET")
      return json({ success: true, sessions: saved ? [saved] : [] });
    return null;
  });
  assert.equal(await c.saveDraft("Evening"), false);
  assert.equal(c.getSnapshot().draftsUnknown, true);
  assert.equal(await c.saveDraft("Another"), false);
  assert.equal(posts, 1);
  await c.loadDrafts();
  assert.equal(c.getSnapshot().draftsUnknown, false);
  assert.equal(c.getSnapshot().drafts[0].id, 17);
  assert.equal(posts, 1);
});
test("snapshot restore shares frozen queue while refusing an independent dirty draft", async () => {
  const review = {
    version: 1,
    kind: "snapshot_restore",
    label: "Restore saved snapshot now",
    target: { id: "target", name: "Evening Collection" },
    snapshot: { id: 3, created_at: "2026-01-01Z" },
    resources: [
      {
        resource: "target",
        resource_name: "Evening Collection",
        role: "target",
        domain: "spotify",
        access: "write",
        mutation_type: "track_membership",
        field: null,
      },
    ],
    steps: [],
    required_bookkeeping: ["session_history"],
    review_binding: {
      version: 1,
      scope: "a".repeat(64),
      before_order: "b".repeat(64),
      history: null,
    },
    current: {
      track_count: 3,
      track_uris: [uri(1), uri(2), uri(1)],
      loaded_at: "now",
    },
    action: {
      snapshot_id: 3,
      desired_track_uris: [uri(2)],
      summary: { added_count: 0, removed_count: 2, order_changed: false },
    },
  };
  const { c, calls } = await harness((path) =>
    path.endsWith("/restore-context")
      ? json({ success: true, context: review })
      : path.endsWith("/restore")
        ? json(
            {
              success: true,
              operation_id: 2001,
              operation: { ...operation(), kind: "snapshot_restore" },
            },
            202,
          )
        : null,
  );
  c.removeOccurrence(c.getSnapshot().working[0].key);
  await assert.rejects(c.prepareSnapshotRestore(3), /independent local draft/i);
  c.discardLocal();
  const r = await c.prepareSnapshotRestore(3);
  await c.submitReviewed(r.id);
  const sent = calls.find((c) => c.path.endsWith("/restore"));
  assert.deepEqual(JSON.parse(sent.init.body), {
    reviewed_scope: "a".repeat(64),
    reviewed_before_order: "b".repeat(64),
  });
  assert.equal(c.getSnapshot().receipt.operationId, 2001);
});
test("snapshot local CRUD claims stay resource scoped while Pause and unrelated routines remain available", async () => {
  const { c } = await harness();
  const release = c.claim({
    owner: "live",
    resource: "tracks:target",
    access: "write",
    reason: "Live target action in progress.",
  });
  assert.equal(c.permission("draft").allowed, false);
  assert.equal(c.permission("collect").allowed, true);
  assert.equal(c.permission("routine", "other").allowed, true);
  assert.equal(c.permission("pause").allowed, true);
  release();
  assert.equal(c.permission("draft").allowed, true);
});
test("a complete confirmed save advances this baseline only with exact desired order", async () => {
  const h = await harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : null,
  );
  const { c } = h;
  c.removeOccurrence(c.getSnapshot().working[1].key);
  await c.submitReviewed((await c.prepareReview()).id);
  h.finish(
    operation(2001, "succeeded", {
      success: true,
      target_status: "saved",
      saved_track_uris: [uri(1), uri(1)],
      message: "Saved",
      error_code: null,
      requires_refresh: false,
      retryable: false,
      confirmed_batches: 1,
      total_batches: 1,
      bookkeeping: { history: "confirmed" },
    }),
  );
  assert.deepEqual(
    c.getSnapshot().baseline.map((o) => o.uri),
    [uri(1), uri(1)],
  );
  assert.equal(c.hasDraft(), false);
});
test("a queued visibility change holds metadata only and never replaces an edited track draft", async () => {
  const review = {
    version: 1,
    kind: "privacy",
    label: "Make public",
    target: { id: "target", name: "Evening Collection" },
    resources: [
      {
        resource: "target",
        resource_name: "Evening Collection",
        role: "target",
        domain: "spotify",
        access: "write",
        mutation_type: "metadata",
        field: "public",
      },
    ],
    required_bookkeeping: [],
    review_binding: {
      version: 1,
      scope: "a".repeat(64),
      before_order: null,
      history: null,
    },
    current: { public: false, loaded_at: "2026-01-01T12:00:00Z" },
    action: { public: true },
  };
  const h = await harness((path) =>
    path.endsWith("live-context")
      ? json({ success: true, context: review })
      : path.endsWith("toggle-visibility")
        ? json(
            {
              success: true,
              operation_id: 2001,
              operation: { ...operation(), kind: "visibility" },
            },
            202,
          )
        : null,
  );
  const { c } = h;
  await c.submitReviewed((await c.prepareVisibility(true)).id);
  assert.equal(c.permission("metadata").allowed, false);
  assert.equal(c.permission("draft").allowed, true);
  c.removeOccurrence(c.getSnapshot().working[1].key);
  assert.equal(c.discardLocal(), true);
  assert.equal(c.getSnapshot().receipt.status, "accepted");
  c.removeOccurrence(c.getSnapshot().working[1].key);
  h.finish({
    ...operation(2001, "succeeded", { success: true, public: true }),
    kind: "visibility",
  });
  assert.equal(c.getSnapshot().context.playlist.public, true);
  assert.equal(c.getSnapshot().working.length, 2);
  assert.equal(c.getSnapshot().baseline.length, 3);
});
test("a malformed terminal success cannot unlock a fresh save or advance the baseline", async () => {
  const h = await harness((path) =>
    path.endsWith("/commit")
      ? json({ success: true, operation_id: 2001, operation: operation() }, 202)
      : null,
  );
  const { c } = h;
  c.removeOccurrence(c.getSnapshot().working[1].key);
  await c.submitReviewed((await c.prepareReview()).id);
  h.finish(
    operation(2001, "succeeded", {
      success: true,
      target_status: "saved",
      saved_track_uris: [uri(2)],
      message: "Saved",
      confirmed_batches: 1,
      total_batches: 1,
    }),
  );
  assert.equal(c.getSnapshot().baseline.length, 3);
  assert.equal(c.permission("save").allowed, false);
});
test("snapshot restore adopts only verified resource order plus returned occurrence DTOs and confirmed history", async () => {
  const { verifiedRestore } =
    await import("../src/pages/workshop/core/client.ts");
  const r = {
    kind: "snapshot_restore",
    target: { id: "target" },
    desired: [{ uri: uri(2) }],
  };
  const op = operation(2001, "succeeded", {
    success: true,
    playlist: {
      id: "target",
      tracks: [(({ artist_urls, ...rest }) => rest)(track(2))],
    },
    operation: {
      kind: "snapshot_restore",
      aggregate_status: "complete",
      resources: [
        {
          resource: "target",
          status: "complete",
          current_track_uris: [uri(2)],
        },
      ],
      bookkeeping: [{ kind: "session_history", status: "complete" }],
    },
  });
  assert.deepEqual(verifiedRestore(op, r).track_uris, [uri(2)]);
  const wrong = structuredClone(op);
  wrong.result.operation.resources[0].current_track_uris = [uri(1)];
  assert.equal(verifiedRestore(wrong, r), null);
  const noHistory = structuredClone(op);
  noHistory.result.operation.bookkeeping = [];
  assert.equal(verifiedRestore(noHistory, r), null);
});
test("saved-draft read cannot discard source staging that arrived after it began", async () => {
  let release;
  const { c } = await harness((path) =>
    path === "/api/workshop/sessions/1"
      ? new Promise((r) => (release = r))
      : null,
  );
  let invalidate,
    staged = [];
  c.registerSourceCoordinator({
    snapshot: () => staged,
    discard: () => {
      staged = [];
    },
    subscribe: (fn) => ((invalidate = fn), () => {}),
  });
  const load = c.loadNamedDraft(1, true);
  staged = [
    {
      kind: "archive_restore",
      source_playlist_id: "archive",
      track_uris: [uri(2)],
    },
  ];
  invalidate();
  release(
    json({
      success: true,
      session: {
        id: 1,
        user_id: 1,
        playlist_id: "target",
        session_name: "Saved",
        track_uris: [uri(1)],
        track_count: 1,
        created_at: null,
        updated_at: null,
      },
    }),
  );
  assert.equal(await load, false);
  assert.equal(c.getSnapshot().staging.length, 1);
});
test("current and named-draft rate limits retain the full hint and do not repeat requests during it", async () => {
  const { c, calls } = await harness((path) =>
    path.endsWith("/current-state") || path.endsWith("/sessions")
      ? json(
          {
            success: false,
            error_code: "rate_limited",
            message: "Wait before trying again.",
            category: "error",
            retry_after: 30,
          },
          429,
          { "Retry-After": "60" },
        )
      : null,
  );
  await c.checkCurrent();
  assert.ok(c.getSnapshot().retryUntil >= Date.now() + 59000);
  await c.checkCurrent();
  assert.equal(
    calls.filter((x) => x.path.endsWith("/current-state")).length,
    1,
  );
  await c.saveDraft("Another");
  assert.equal(calls.filter((x) => x.path.endsWith("/sessions")).length, 0);
});
test("a server error after an unsafe admission remains unknown and cannot mint a fresh save", async () => {
  const { c } = await harness((path) =>
    path.endsWith("/commit")
      ? json(
          {
            success: false,
            error_code: "internal_error",
            message: "Reply unavailable.",
            category: "error",
          },
          500,
        )
      : null,
  );
  c.removeOccurrence(c.getSnapshot().working[0].key);
  await c.submitReviewed((await c.prepareReview()).id);
  assert.equal(c.getSnapshot().receipt.status, "unknown");
  assert.equal(c.permission("save").allowed, false);
});
test("manual moves preserve legacy source/destination lock checks and permit crossing, while preview holds every index", async () => {
  const { c } = await harness();
  c.removeOccurrence(c.getSnapshot().working[2].key);
  c.appendTracks([track(3)], { mode: "append", origin: "test" });
  const rows = c.getSnapshot().working;
  c.setLocks({ [uri(2)]: true });
  assert.equal(c.moveOccurrence(rows[0].key, 3), true);
  assert.deepEqual(
    c.getSnapshot().working.map((r) => r.uri),
    [uri(2), uri(3), uri(1)],
  );
  const input = c.getPreviewInput();
  assert.equal(c.applyPreview(input, [uri(3), uri(2), uri(1)]), false);
});
test("a settled blocking ID cannot adopt another action as this refused Workshop save", async () => {
  const t = await harness((path) =>
    path.endsWith("/commit")
      ? json(
          {
            success: false,
            error_code: "conflict",
            message: "Another request owns this target",
            category: "error",
            operation_id: 9,
          },
          409,
        )
      : null,
  );
  t.c.removeOccurrence(t.c.getSnapshot().working[0].key);
  const reviewed = await t.c.prepareReview();
  await t.c.submitReviewed(reviewed.id);
  t.finish(
    operation(9, "succeeded", {
      success: true,
      target_status: "saved",
      confirmed_batches: 1,
      total_batches: 1,
      requires_refresh: false,
      retryable: false,
      saved_track_uris: reviewed.desired.map((r) => r.uri),
    }),
  );
  assert.equal(t.c.getSnapshot().baseline.length, 3);
  assert.equal(t.c.getSnapshot().receipt.status, "refused");
  assert.equal(t.c.permission("review").allowed, true);
});
