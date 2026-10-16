import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
import {
  assertReview,
  sendReviewed,
  loadReview,
  preference,
  resultFacts,
} from "../src/pages/library/api.ts";
const id = "alice",
  intent = {
    kind: "shuffle",
    form: { algorithm: "BasicShuffle", keep_first: "1" },
  };
const review = {
  version: 1,
  kind: "shuffle",
  label: "Shuffle",
  target: { id, name: "Alice mix" },
  resources: [
    {
      resource: id,
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
    algorithm: { id: "BasicShuffle", name: "Basic" },
    params: { keep_first: 1 },
  },
};
test("fresh review binds playlist, action, algorithm and exact canonical option values", () => {
  assertReview(review, id, intent);
  for (const bad of [
    { ...review, target: { id: "bob", name: "Bob" } },
    { ...review, kind: "undo" },
    { ...review, action: { ...review.action, params: { keep_first: 2 } } },
    { ...review, review_binding: { ...review.review_binding, scope: "bogus" } },
  ])
    assert.throws(() => assertReview(bad, id, intent));
});
test("only reviewed commands use form encoding and a stable idempotency key; read-only review is separate", async () => {
  const calls = [];
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({ path, init });
      return Response.json({ success: true, context: review });
    },
  });
  await loadReview(api, id, intent);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/api/playlist/alice/live-context");
  await sendReviewed(api, id, intent, review, "stable-key");
  await sendReviewed(api, id, intent, review, "stable-key");
  for (const call of calls.slice(1)) {
    assert.equal(call.path, "/api/shuffle/alice");
    assert.deepEqual(Object.fromEntries(call.init.body), {
      algorithm: "BasicShuffle",
      keep_first: "1",
      reviewed_scope: "a".repeat(64),
    });
    assert.equal(call.init.headers.get("Idempotency-Key"), "stable-key");
    assert.equal(call.init.headers.get("X-CSRFToken"), "csrf");
  }
});
test("DB preference writes reject unexpected queue admission instead of treating it as saved", async () => {
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async () => Response.json({ success: true, count: 2 }),
  });
  assert.equal(
    (await preference(api, "order", { playlist_ids: ["alice", "bob"] })).count,
    2,
  );
});
test("result display retains unknown and per-resource/bookkeeping facts without inventing verification", () => {
  assert.deepEqual(resultFacts(null).resources, []);
  const facts = resultFacts({
    message: "Needs review",
    operation: {
      resources: [
        {
          resource: "alice",
          status: "unknown",
          verification: "ordered",
          affected_count: null,
          error_code: "verification_failed",
        },
      ],
      bookkeeping: [
        { kind: "session_history", status: "failed", affected_count: 0 },
      ],
    },
  });
  assert.equal(facts.resources[0].status, "unknown");
  assert.equal(facts.resources[0].count, null);
  assert.equal(facts.bookkeeping[0].name, "Saved playlist history");
});
test("a review refuses mismatched resource domains, required history, malformed scalar options and load timestamps", () => {
  for (const value of [
    {
      ...review,
      resources: [
        { ...review.resources[0], mutation_type: "metadata", field: "public" },
      ],
    },
    { ...review, required_bookkeeping: [] },
    { ...review, current: { ...review.current, loaded_at: "not a timestamp" } },
    { ...review, action: { ...review.action, params: { keep_first: [1] } } },
  ])
    assert.throws(() => assertReview(value, id, intent));
});
test("read-only inspection preserves mismatch facts on normalized HTTP502 and verifies exact duplicate order, never inferring a shuffle intent", async () => {
  const { inspectCurrent } = await import("../src/pages/library/api.ts");
  const calls = [];
  let current = [
    "spotify:track:alice",
    "spotify:track:alice",
    "spotify:track:bob",
  ];
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      const body = JSON.parse(init.body);
      calls.push({ path, body });
      const match =
        JSON.stringify(body.desired_track_uris) === JSON.stringify(current);
      return Response.json(
        {
          success: match,
          inspection_only: true,
          message: match
            ? "Current order matches."
            : "Current contents do not establish the earlier outcome.",
          category: match ? "success" : "error",
          ...(match ? {} : { error_code: "upstream_error" }),
          operation: {
            kind: "track_inspection",
            aggregate_status: match ? "complete" : "unknown",
            error_code:
              !Object.hasOwn(body, "desired_track_uris")
                ? "intent_unavailable"
                : match
                  ? null
                  : "intent_not_observed",
            resources: [
              {
                resource: "alice",
                status: match ? "not_required" : "unknown",
                current_track_uris: current,
              },
            ],
          },
        },
        { status: match ? 200 : 502 },
      );
    },
  });
  assert.equal(
    (
      await inspectCurrent(api, "alice", {
        kind: "tracks",
        desired_track_uris: null,
      })
    ).status,
    "unavailable",
  );
  assert.equal(
    (
      await inspectCurrent(api, "alice", {
        kind: "tracks",
        desired_track_uris: [
          "spotify:track:alice",
          "spotify:track:bob",
          "spotify:track:alice",
        ],
      })
    ).status,
    "mismatch",
  );
  const matched = await inspectCurrent(api, "alice", {
    kind: "tracks",
    desired_track_uris: current,
  });
  assert.equal(matched.status, "match");
  assert.equal(calls.length, 3);
  assert.equal(calls[0].path, "/api/playlist/alice/inspect-live");
  assert.deepEqual(
    matched.raw.operation.resources[0].current_track_uris,
    current,
  );
});
test("a privacy inspection cannot verify a missing intent even if an invalid response claims a null match", async () => {
  const { inspectCurrent } = await import("../src/pages/library/api.ts");
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async () =>
      Response.json({
        success: true,
        inspection_only: true,
        message: "Invalid null match",
        public: null,
        operation: {
          kind: "privacy",
          aggregate_status: "complete",
          resources: [
            {
              resource: "alice",
              status: "not_required",
              current_metadata: { public: null },
            },
          ],
        },
      }),
  });
  assert.equal(
    (
      await inspectCurrent(api, "alice", {
        kind: "privacy",
        desired_public: null,
      })
    ).status,
    "failed",
  );
});
