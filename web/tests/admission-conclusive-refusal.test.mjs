import { test } from "node:test";
import assert from "node:assert/strict";
import { safelySettled } from "../src/operations/admissions.ts";
import { accepted, harness, operation, request } from "./admission-recovery-helpers.mjs";
const refusal = (code = "history_unavailable") => ({
  status: "failed", success: false, category: "error",
  message: "This attempt made no playlist changes.", error: "This attempt made no playlist changes.",
  operation: { kind: "shuffle", aggregate_status: "partial", resources: [], bookkeeping: [],
    error_code: code, retryable: false },
});
for (const code of ["history_unavailable", "history_changed", "preflight_failed"])
  test(`failed zero-resource ${code} refusal releases the original admission`, async () => {
    const h = harness(() => accepted());
    await h.registry.submit(request(), "alice");
    h.operations.finish(operation(1, "failed", refusal(code)));
    assert.equal(h.registry.getSnapshot().get("original").status, "settled");
    assert.equal(h.registry.conflict(request().resources), null);
    await h.registry.submit(request("new-reviewed-intent"), "alice");
    assert.equal(h.calls.length, 2);
  });
test("a history error code does not outweigh positive zero-resource refusal facts", () => {
  assert.equal(safelySettled(operation(1, "failed", { ...refusal(), error_code: "history_unavailable" })), true);
});
test("missing resources, unknown facts, dispatched writes and attention remain held", () => {
  for (const patch of [
    { state: "needs_attention", result: refusal() },
    { result: { ...refusal(), operation: { aggregate_status: "partial" } } },
    { result: { ...refusal(), operation: { ...refusal().operation, aggregate_status: "unknown" } } },
    { result: { ...refusal(), operation: { ...refusal().operation, resources: [{ resource: "target", dispatched: true, status: "unknown" }] } } },
    { result: { ...refusal(), source_status: "unknown" } },
  ]) assert.equal(safelySettled(operation(1, "failed", refusal(), patch)), false);
});
