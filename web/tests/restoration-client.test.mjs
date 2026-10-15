import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createApiClient } from "../src/api/client.ts";

const fixture = () =>
  JSON.parse(
    readFileSync(
      new URL(
        "../fixtures/valid/operation-restore-context.json",
        import.meta.url,
      ),
    ),
  );
export const parent = () => ({
  id: 7,
  kind: "shuffle",
  state: "needs_attention",
  targets: ["target"],
  started_at: null,
  completed_at: null,
  progress: {},
  result: { success: false },
  recovery: { available: true, action: "restore", reason: "external_edit" },
});
const child = () => ({
  ...parent(),
  id: 8,
  kind: "operation_restore",
  state: "succeeded",
  result: {
    success: true,
    parent_operation_id: 7,
    parent_resolution: "complete",
  },
  recovery: { available: false, action: null, reason: null },
});
const module = () => import("../src/operations/restoration/client.ts");

test("loading a restoration review reads only the owned parent endpoint and preserves duplicate previews", async () => {
  const calls = [],
    value = fixture();
  const api = createApiClient({
    transport: async (path, init) => {
      calls.push({ path, method: init.method });
      return Response.json(value);
    },
  });
  const { loadRestoreReview } = await module();
  const context = await loadRestoreReview(api, parent());
  assert.deepEqual(calls, [
    { path: "/api/operations/7/restore-context", method: "GET" },
  ]);
  assert.deepEqual(context.resources[0].desired_preview.slice(0, 2), [
    "spotify:track:0000000000000000000001",
    "spotify:track:0000000000000000000001",
  ]);
  assert.ok(Object.isFrozen(context.reviewed.reviewed_resources));
});

test("confirmation constructs only the exact server reviewed object and cannot send desired lists", async () => {
  const { assertRestoreContext, restoreRequest } = await module();
  const context = fixture().context;
  assertRestoreContext(context, parent());
  const body = restoreRequest(context);
  assert.deepEqual(Object.keys(body), ["reviewed"]);
  assert.deepEqual(body.reviewed, context.reviewed);
  context.reviewed.reviewed_scope = "f".repeat(64);
  assert.notEqual(
    body.reviewed.reviewed_scope,
    context.reviewed.reviewed_scope,
  );
  assert.ok(Object.isFrozen(body.reviewed));
});

for (const damage of [
  "parent",
  "targets",
  "digest",
  "boolean_version",
  "oversized_preview",
  "private_field",
  "guard_write",
  "metadata_scope_tracks",
]) {
  test(`invalid review ${damage} refuses confirmation`, async () => {
    const { assertRestoreContext } = await module();
    const value = fixture().context;
    if (damage === "parent") value.operation_id = 9;
    else if (damage === "targets") value.resources[0].playlist_id = "other";
    else if (damage === "digest")
      value.reviewed.reviewed_resources[0].digest = "not-a-digest";
    else if (damage === "boolean_version") value.reviewed.version = true;
    else if (damage === "oversized_preview")
      value.resources[0].current_preview = Array(21).fill(
        "spotify:track:" + "1".repeat(22),
      );
    else if (damage === "private_field") value.journal = { plans: [] };
    else if (damage === "guard_write") value.resources[0].role = "guard";
    else value.resources[0].scope = "metadata";
    assert.throws(
      () => assertRestoreContext(value, parent()),
      /review|resource|restor|scope|parent/i,
    );
  });
}

for (const damage of ["kind", "targets", "same_id"]) {
  test(`a wrong accepted restoration ${damage} cannot become the original child`, async () => {
    const { assertRestoreChild } = await module();
    const value = child();
    if (damage === "kind") value.kind = "shuffle";
    else if (damage === "targets") value.targets = ["target", "unreviewed"];
    else value.id = 7;
    assert.throws(() => assertRestoreChild(value, parent(), fixture().context));
  });
}

test("resolution requires exact fresh owned parent marker and successful child relation", async () => {
  const { restorationResolved } = await module();
  const original = parent(),
    current = parent(),
    accepted = child();
  current.recovery = {
    available: false,
    action: null,
    reason: "operation_already_restored",
    resolved_operation_id: 8,
  };
  assert.equal(restorationResolved(original, accepted, current), true);
  for (const [left, right] of [
    [{ ...accepted, state: "needs_attention" }, current],
    [{ ...accepted, state: "rolled_back" }, current],
    [
      { ...accepted, result: { ...accepted.result, parent_operation_id: 9 } },
      current,
    ],
    [
      {
        ...accepted,
        result: { ...accepted.result, parent_resolution: "unknown" },
      },
      current,
    ],
    [accepted, { ...current, targets: ["other"] }],
    [
      accepted,
      {
        ...current,
        recovery: { ...current.recovery, resolved_operation_id: 99 },
      },
    ],
    [
      accepted,
      {
        ...current,
        recovery: { ...current.recovery, resolved_operation_id: true },
      },
    ],
  ])
    assert.equal(restorationResolved(original, left, right), false);
});

test("reviewing an active or locally unavailable parent cannot authorize restoration", async () => {
  const { assertRestoreContext } = await module();
  for (const value of [
    { ...parent(), state: "running" },
    {
      ...parent(),
      recovery: {
        available: false,
        action: null,
        reason: "write_completion_unknown",
      },
    },
  ])
    assert.throws(() => assertRestoreContext(fixture().context, value));
});

test("a restoration child cannot open a recursive restoration workflow", async () => {
  const { assertRestoreContext } = await module();
  assert.throws(() =>
    assertRestoreContext(fixture().context, {
      ...parent(),
      kind: "operation_restore",
    }),
  );
});
