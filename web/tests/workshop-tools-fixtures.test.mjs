import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import Ajv from "ajv";
import { createApiClient } from "../src/api/client.ts";
import { createToolsClient } from "../src/pages/workshop/tools/client.ts";
import { createWorkshopFixtures } from "../src/fixtures/workshop.ts";
import { createWorkshopToolsFixtures } from "../src/fixtures/workshop-tools.ts";
const schema = (name) =>
  JSON.parse(
    readFileSync(
      new URL(`../schemas/${name}.schema.json`, import.meta.url),
      "utf8",
    ),
  );
const ajv = new Ajv({ strict: false });
function harness(scenario = "workshop-tools") {
  const core = createWorkshopFixtures("workshop-happy"),
    tools = createWorkshopToolsFixtures(scenario, core),
    calls = [];
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({ path, init });
      const reply =
        (await tools(path, init.method, init)) ??
        (await core(path, init.method, init));
      assert.ok(reply, `Missing fixture ${path}`);
      return reply;
    },
  });
  return { api, client: createToolsClient(api, "target"), calls };
}
test("complete tools fixture context validates the shared backend contract and all registry metadata", async () => {
  const h = harness(),
    r = await h.api.request("/api/workshop/target"),
    validate = ajv.compile(schema("workshop-context"));
  assert.equal(r.status, "completed");
  assert.equal(validate(r.data), true, JSON.stringify(validate.errors));
  assert.equal(r.data.algorithms.length, 7);
  assert.equal(r.data.recently_loaded[0].track_count, null);
});
test("tool fixture routes satisfy strict client DTOs, exact snapshot IDs and nullable source counts", async () => {
  const { client } = harness();
  assert.deepEqual(await client.locks(), []);
  const lock = await client.toggleLock(
    "spotify:track:0000000000000000000001",
    0,
  );
  assert.equal(lock.lock_tier, "standard");
  assert.equal((await client.locks()).length, 1);
  assert.equal(await client.unlockAll(), 1);
  const rows = await client.snapshots();
  assert.equal(rows[0].id, 31);
  assert.deepEqual((await client.snapshot(31)).track_uris, rows[0].track_uris);
  const empty = await client.createSnapshot("Evening Collection", [], "");
  assert.equal(empty.track_count, 0);
  await client.deleteSnapshot(empty.id);
  assert.ok(!(await client.snapshots()).some((s) => s.id === empty.id));
  const restricted = await client.external({ url: "sourceA" });
  assert.equal(restricted.mode, "restricted");
  assert.equal(restricted.playlist.track_count, null);
});
test("fixture restore accepted202 and same-ID polling retain actual rich direct result without invented target aliases", async () => {
  const h = harness(),
    review = await h.api.request("/api/snapshots/31/restore-context");
  const binding = review.data.context.review_binding;
  const accepted = await h.api.request("/api/snapshots/31/restore", {
    method: "POST",
    headers: { "Idempotency-Key": "one-frozen-key" },
    json: {
      reviewed_scope: binding.scope,
      reviewed_before_order: binding.before_order,
    },
  });
  assert.equal(accepted.status, "accepted");
  assert.equal(accepted.operation.kind, "snapshot_restore");
  const first = await h.api.getOperation(accepted.operation.id),
    final = await h.api.getOperation(accepted.operation.id);
  assert.equal(first.state, "running");
  assert.equal(final.state, "succeeded");
  assert.equal(final.result.operation.resources[0].status, "complete");
  assert.equal(final.result.target_status, undefined);
  assert.equal(final.result.confirmed_batches, undefined);
  assert.equal(h.calls.filter((c) => c.init.method === "POST").length, 1);
  const validate = ajv.compile(schema("operation-context"));
  assert.equal(
    validate({ success: true, operation: final }),
    true,
    JSON.stringify(validate.errors),
  );
});
