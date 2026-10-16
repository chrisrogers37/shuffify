import { createApiClient } from "../src/api/client.ts";
import { createAdmissionRegistry } from "../src/operations/admissions.ts";
export const operation = (id = 1, state = "queued", result = null, extra = {}) => ({
  id, kind: "shuffle", state, targets: ["target"], started_at: null,
  completed_at: null, progress: {}, result,
  recovery: { available: false, action: null, reason: null }, ...extra,
});
export const request = (key = "original", extra = {}) => ({
  key, feature: "library", label: "Shuffle Evening Collection",
  resources: [{ resource: "playlist:target", access: "write" }],
  path: "/api/shuffle/target", body: { encoding: "json", value: {} },
  expected: { kind: "shuffle", target: "target" }, ...extra,
});
export const accepted = (op = operation()) => Response.json({
  success: true, operation_id: op.id, operation: op,
}, { status: 202 });
export const busy = (id = 9) => Response.json({
  success: false, error_code: "conflict", message: "Another action holds this resource.",
  category: "error", operation_id: id,
}, { status: 409 });
export function harness(handler = () => accepted()) {
  const listeners = new Set(), calls = [], observed = [];
  let state = new Map();
  const operations = {
    getSnapshot: () => state,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    accept(op) { this.finish(op); },
    observe(id) { observed.push(id); },
    finish(op) {
      state = new Map(state).set(op.id, { id: op.id, operation: op, observation: ["succeeded", "failed", "rolled_back", "needs_attention"].includes(op.state) ? "settled" : "watching", error: null });
      listeners.forEach(fn => fn());
    },
    unavailable(id, op = null) {
      state = new Map(state).set(id, { id, operation: op, observation: "unavailable", error: Error("Status is unavailable") });
      listeners.forEach(fn => fn());
    },
  };
  const api = createApiClient({ getCsrfToken: () => "csrf", transport: async (path, init) => {
    calls.push({ path, init }); return handler(path, init);
  } });
  return { registry: createAdmissionRegistry(api, "alice", operations), operations, calls, observed };
}
