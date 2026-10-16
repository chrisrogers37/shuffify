import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
import { createAdmissionRegistry } from "../src/operations/admissions.ts";
import { createOperationStore } from "../src/operations/store.ts";
import { busy, harness, operation, request } from "./admission-recovery-helpers.mjs";
const archiveRequest = () => request("caller", { resources: [
  { resource: "playlist:target", access: "write" }, { resource: "playlist:archive", access: "write" },
] });
for (const state of ["succeeded", "failed", "rolled_back", "needs_attention"])
  test(`archive-only ${state} blocker releases the caller as refused, not its completed action`, async () => {
    const h = harness(() => busy());
    await assert.rejects(h.registry.submit(archiveRequest(), "alice"));
    const blocker = operation(9, state, { success: state === "succeeded" }, {
      kind: "workshop_archive_append", targets: ["other-target", "archive"],
    });
    h.operations.finish(blocker);
    const caller = h.registry.getSnapshot().get("caller");
    assert.equal(caller.status, "refused");
    assert.equal(caller.relation, "blocking");
    assert.equal(caller.operationId, 9);
    assert.equal(h.registry.conflict(archiveRequest().resources), null);
    assert.equal(h.calls.length, 1);
  });
for (const prior of [null, operation(9, "running", null, { targets: ["other-target", "archive"] })])
  test(`unobservable blocker with ${prior ? "an earlier read" : "no readable owned result"} releases only the refused caller`, async () => {
    const h = harness(() => busy());
    await assert.rejects(h.registry.submit(archiveRequest(), "alice"));
    h.operations.unavailable(9, prior);
    assert.equal(h.registry.getSnapshot().get("caller").status, "refused");
    assert.equal(h.registry.conflict(archiveRequest().resources), null);
    assert.equal(h.calls.length, 1);
  });
test("an active blocker still holds, while an unavailable original admission remains unresolved", async () => {
  const h = harness(() => busy());
  await assert.rejects(h.registry.submit(archiveRequest(), "alice"));
  h.operations.finish(operation(9, "running", null, { kind: "other", targets: ["archive"] }));
  assert.ok(h.registry.conflict(archiveRequest().resources));
  const own = harness();
  await own.registry.submit(request(), "alice");
  own.operations.unavailable(1);
  assert.equal(own.registry.getSnapshot().get("original").relation, "original");
  assert.ok(own.registry.conflict(request().resources));
});
test("an unreadable owned blocker through the real operation store releases without another POST", async () => {
  const calls = [];
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({ path, method: init.method });
      return init.method === "POST" ? busy() : Response.json({
        success: false, error_code: "not_found", category: "error", message: "Operation not found.",
      }, { status: 404 });
    },
  });
  const operations = createOperationStore(api, { maxReadRetries: 0 });
  const registry = createAdmissionRegistry(api, "alice", operations);
  await assert.rejects(registry.submit(archiveRequest(), "alice"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(operations.getSnapshot().get(9).observation, "unavailable");
  assert.equal(registry.getSnapshot().get("caller").status, "refused");
  assert.equal(registry.conflict(archiveRequest().resources), null);
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  assert.equal(calls.filter(call => call.method === "GET").length, 1);
});
