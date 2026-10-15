import test from "node:test";
import assert from "node:assert/strict";
import {
  parseNavigation,
  normalizeRow,
  activationBody,
  stageChange,
  readContinuation,
  makeContinuation,
  operationLabel,
  listDeepLink,
  safeAvailableDetail,
  unconfirmedMutation,
} from "../src/pages/routines/model.ts";
import { createRoutinesFixtures } from "../src/fixtures/routines.ts";
import { createApiClient } from "../src/api/client.ts";
import { createRoutineClient } from "../src/pages/routines/client.ts";

test("deep links accept exact positive IDs and finite origin codes only", () => {
  assert.deepEqual(parseNavigation("?schedule_id=12&from=routines"), {
    scheduleId: 12,
    origin: "routines",
  });
  for (const value of ["0", "-1", "１２", "1.1", "9007199254740993", "x"])
    assert.throws(() => parseNavigation("?schedule_id=" + value));
  assert.throws(() => parseNavigation("?origin=https://outside.invalid"));
});
test("row state normalization uses documented server state only and checks exact identities", () => {
  const row = {
    schedule_id: 7,
    schedule: { id: 7, target_playlist_id: "target" },
    definition: { target_playlist_id: "target" },
    schedule_revision: "a".repeat(64),
    timing: {},
    registration: {},
    state: "Paused",
  };
  assert.equal(normalizeRow(row).state, "paused");
  assert.equal(normalizeRow(row).state_label, "Paused");
  assert.throws(() =>
    normalizeRow({ ...row, schedule: { ...row.schedule, id: 8 } }),
  );
  assert.throws(() => normalizeRow({ ...row, state: "maybe" }));
});
test("activation sends the exact normalized definition and signed intent with original request key", () => {
  const request = {
    request_key: "stable",
    definition: { job_type: "shuffle" },
    schedule_id: 7,
    expected_revision: "a".repeat(64),
    create_another: false,
  };
  const review = {
    definition: { job_type: "shuffle", algorithm_params: { keep_first: 2 } },
    intent: { signed: "authored" },
    dependency_revision: "b".repeat(64),
    schedule_revision: "a".repeat(64),
    candidate_schedule_ids: [],
  };
  const result = activationBody(request, review);
  assert.deepEqual(result.definition, review.definition);
  assert.deepEqual(result.intent, review.intent);
  assert.equal(result.request_key, "stable");
  assert.throws(() =>
    activationBody(request, { ...review, schedule_revision: "c".repeat(64) }),
  );
  assert.throws(() =>
    activationBody(
      { ...request, schedule_id: null, expected_revision: null },
      { ...review, schedule_revision: null, candidate_schedule_ids: [5] },
    ),
  );
});
test("setup stages one change per subject, retaining unrelated draft and server acknowledgment boundary", () => {
  const first = stageChange([], { op: "set_drip", drip_count: 3 });
  assert.deepEqual(stageChange(first, { op: "set_drip", drip_count: 4 }), [
    { op: "set_drip", drip_count: 4 },
  ]);
  assert.equal(
    stageChange(first, { op: "pair_archive", playlist_id: "archive" }).length,
    2,
  );
});
test("continuation is bounded and bound to signed-in owner/target/exact edit revision, without signed intent", () => {
  const draft = {
    job_type: "shuffle",
    algorithm_name: "BasicShuffle",
    algorithm_params: { keep_first: 1 },
    timing: { kind: "interval", interval: "daily", timezone: "UTC" },
  };
  const value = makeContinuation(
    "owner",
    "target",
    7,
    "a".repeat(64),
    "routines",
    draft,
    1000,
  );
  assert.equal(
    readContinuation(JSON.stringify(value), "owner", "target", 1500).scheduleId,
    7,
  );
  assert.equal(
    readContinuation(JSON.stringify(value), "other", "target", 1500),
    null,
  );
  assert.equal(
    readContinuation(JSON.stringify(value), "owner", "target", 2000000),
    null,
  );
  assert.equal("intent" in value, false);
});
test("run request is sent once and carries only reviewed scope and a stable key; queued result stays distinct", async () => {
  const fixture = createRoutinesFixtures("routines-happy");
  const calls = [];
  const api = createApiClient({
    getCsrfToken: () => "fixture-csrf-token",
    transport: async (path, init) => {
      calls.push([path, init]);
      return (
        (await fixture(String(path), init.method, init)) ??
        new Response("{}", { status: 404 })
      );
    },
  });
  const client = createRoutineClient(api);
  const result = await client.run(1, "a".repeat(64), "same-intent");
  assert.equal(result.status, "accepted");
  assert.equal(calls.length, 1);
  assert.equal(
    new Headers(calls[0][1].headers).get("Idempotency-Key"),
    "same-intent",
  );
  assert.deepEqual(JSON.parse(calls[0][1].body), {
    reviewed_scope: "a".repeat(64),
  });
});
test("operation labels preserve Collect and combined declared behavior", () => {
  assert.equal(operationLabel("raid"), "Collect");
  assert.equal(operationLabel("raid_and_drip"), "Collect and add");
});

test("Activity exact History links and card hashes preserve identity", () => {
  assert.equal(listDeepLink("?routine_id=2&view=history", ""), 2);
  assert.equal(listDeepLink("", "#routine-11"), 11);
  assert.throws(() => listDeepLink("?routine_id=２", ""));
});
test("unsafe nested details never become inferred verified facts", () => {
  const detail = {
    detail_status: "available",
    version: 1,
    execution_id: 4,
    steps: [
      { step: "drip", status: "unknown", resources: [null], bookkeeping: [] },
    ],
  };
  assert.equal(safeAvailableDetail(detail, 4), null);
  assert.equal(safeAvailableDetail({ ...detail, steps: [] }, 4), null);
});
test("a repeated accepted run intent returns its original ID", async () => {
  const fixture = createRoutinesFixtures("routines-happy");
  const api = createApiClient({
    getCsrfToken: () => "fixture",
    transport: (path, init) => fixture(String(path), init.method, init),
  });
  const client = createRoutineClient(api),
    first = await client.run(1, "a".repeat(64), "request");
  const second = await client.run(1, "a".repeat(64), "request");
  assert.equal(first.operation.id, second.operation.id);
});

test("authored refusal stays distinct from unconfirmed transport or protocol failures", async () => {
  const { ApiRequestError, NetworkError, ProtocolError } =
    await import("../src/api/errors.ts");
  const body = {
    success: false,
    error_code: "refused",
    category: "error",
    message: "Refused",
  };
  assert.equal(unconfirmedMutation(new ApiRequestError(409, body)), false);
  assert.equal(unconfirmedMutation(new NetworkError("POST", Error())), true);
  assert.equal(
    unconfirmedMutation(new ProtocolError("Invalid reply", 202)),
    true,
  );
});
