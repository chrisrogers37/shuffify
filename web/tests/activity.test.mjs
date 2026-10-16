import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createApiClient } from "../src/api/client.ts";
import { ProtocolError } from "../src/api/errors.ts";
import {
  parseActivityContext,
  loadActivity,
  exactDetail,
  runCategory,
  runOperation,
  runTitle,
  eventDescription,
  originLabel,
  eventTypes,
  filterRuns,
  filterEvents,
  countLines,
} from "../src/pages/activity/model.ts";
const base = JSON.parse(
  await readFile(
    new URL("../fixtures/valid/activity-context.json", import.meta.url),
    "utf8",
  ),
);
test("Activity uses authoritative detail identity and never labels a legacy aggregate as verified", () => {
  const record = structuredClone(base.executions[0]);
  assert.ok(exactDetail(record));
  assert.equal(runCategory(record), "complete");
  record.result_detail.execution_id = 999;
  assert.equal(exactDetail(record), null);
  assert.equal(runCategory(record), "unavailable");
  assert.equal(runTitle(record), "Completed");
  assert.equal(runCategory(base.executions[1]), "unavailable");
  assert.equal(runTitle(base.executions[1]), "Run failed");
});
test("Activity categorizes running, partial and unknown without conflating partial or restored with complete", () => {
  const record = structuredClone(base.executions[0]);
  record.result_detail.outcome = "partial";
  assert.equal(runCategory(record), "partial");
  assert.match(runTitle(record), /Partly completed/);
  record.result_detail.run_state = "running";
  assert.equal(runCategory(record), "running");
  assert.match(runTitle(record), /In progress/);
  record.result_detail.run_state = "finished";
  record.result_detail.outcome = "unknown";
  assert.match(runTitle(record), /could not be verified/);
  assert.equal(runOperation(record), "raid_and_drip");
});
test("Activity origin never infers from metadata, activity type or descriptions", () => {
  const event = {
    ...base.activities[0],
    triggered_by: null,
    metadata: { triggered_by: "manual" },
  };
  assert.equal(originLabel(event.triggered_by), "Origin unavailable");
  assert.deepEqual(filterEvents([event], "manual"), []);
  event.triggered_by = "manual";
  event.description = "Manual raid_and_drip: Alice mix";
  assert.equal(
    eventDescription(event),
    "Manual Collect then add to playlist: Alice mix",
  );
  assert.equal(originLabel(event.triggered_by), "Manual action");
});
test("Activity filters only loaded rows with exact categories and safe event type values", () => {
  const records = [...base.executions];
  assert.deepEqual(filterRuns(records, "raid_and_drip", "unavailable"), [
    records[1],
  ]);
  assert.deepEqual(filterRuns(records, "shuffle", "complete"), []);
  assert.deepEqual(
    eventTypes([
      { ...base.activities[0], activity_type: "manual" },
      ...base.activities,
    ]),
    ["manual", "schedule_run", "shuffle"],
  );
  assert.equal(filterEvents(base.activities, "manual").length, 1);
  assert.equal(filterEvents(base.activities, "type:schedule_run").length, 1);
});
test("Activity renders only observed counts, including honest zeros; failed and pending steps suppress counts", () => {
  const counts = {
    tracks_collected: 0,
    tracks_reordered: 3,
    tracks_archived: 2,
  };
  assert.deepEqual(
    countLines({
      step: "shuffle",
      status: "complete",
      counts,
      resources: [],
      bookkeeping: [],
    }),
    ["No new tracks collected", "3 positions changed", "2 moved to archive"],
  );
  for (const status of ["failed", "not_started", "pending"])
    assert.deepEqual(
      countLines({
        step: "shuffle",
        status,
        counts,
        resources: [],
        bookkeeping: [],
      }),
      [],
    );
});
test("Activity accepts full contexts but rejects malformed/oversized wire payloads without truncating silently", () => {
  assert.deepEqual(parseActivityContext(base), base);
  for (const malformed of [
    { ...base, stats: { ...base.stats, total_shuffles: "4" } },
    { ...base, activities: Array(101).fill(base.activities[0]) },
    { ...base, executions: Array(21).fill(base.executions[0]) },
    {
      ...base,
      activities: [{ ...base.activities[0], triggered_by: undefined }],
    },
  ])
    assert.throws(() => parseActivityContext(malformed), ProtocolError);
});
test("Activity reads use completed contexts and perform no queued-operation observation", async () => {
  let calls = 0;
  const api = createApiClient({
    transport: async () => {
      calls++;
      return new Response(JSON.stringify(base), {
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  assert.deepEqual(await loadActivity(api), base);
  assert.equal(calls, 1);
});

test("Activity initial and maximum-size fixtures satisfy schema; scenarios remain read-only and deterministic", async () => {
  const { createActivityFixtures } =
    await import("../src/fixtures/activity.ts");
  const { default: Ajv } = await import("ajv");
  const schema = JSON.parse(
    await readFile(
      new URL("../schemas/activity-context.schema.json", import.meta.url),
      "utf8",
    ),
  );
  const validate = new Ajv({
    allErrors: true,
    strict: true,
    coerceTypes: false,
    useDefaults: false,
  }).compile(schema);
  for (const scenario of ["happy", "activity-large", "activity-empty"]) {
    const fixture = createActivityFixtures(scenario),
      value = await (await fixture("/api/activity", "GET")).json();
    assert.equal(validate(value), true, JSON.stringify(validate.errors));
    assert.deepEqual(parseActivityContext(value), value);
    assert.equal(
      value.executions.length,
      scenario === "activity-large"
        ? 20
        : scenario === "activity-empty"
          ? 0
          : 7,
    );
    assert.equal(
      value.activities.length,
      scenario === "activity-large"
        ? 100
        : scenario === "activity-empty"
          ? 0
          : 4,
    );
    assert.equal((await fixture("/api/activity", "POST")).status, 405);
  }
  for (const [scenario, status] of [
    ["activity-failure", 503],
    ["activity-rate-limit", 429],
    ["activity-auth-loss", 401],
  ]) {
    const fixture = createActivityFixtures(scenario);
    assert.equal((await fixture("/api/activity", "GET")).status, status);
    assert.equal((await fixture("/api/activity", "GET")).status, 200);
  }
});
