import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createApiClient } from "../src/api/client.ts";
import {
  createOperationStore,
  getOperationStore,
} from "../src/operations/store.ts";
const queued = JSON.parse(
  await readFile(
    new URL("../fixtures/valid/operation-queued.json", import.meta.url),
    "utf8",
  ),
).operation;
const response = (operation) =>
  new Response(JSON.stringify({ success: true, operation }), {
    headers: { "Content-Type": "application/json" },
  });
const flush = () => new Promise((resolve) => setImmediate(resolve));
test("accepted operation observation survives removal of every component subscriber", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  let calls = 0;
  const api = createApiClient({
    transport: async () => {
      calls++;
      await gate;
      return response({
        ...queued,
        state: "succeeded",
        result: { success: true, message: "Verified." },
      });
    },
  });
  const store = createOperationStore(api, { intervalMs: 0 });
  let notifications = 0;
  const off = store.subscribe(() => notifications++);
  store.accept(queued);
  off();
  release();
  await flush();
  await flush();
  assert.equal(calls, 1);
  assert.equal(store.getSnapshot().get(queued.id).operation.state, "succeeded");
  assert.equal(store.getSnapshot().get(queued.id).observation, "settled");
  assert.equal(notifications, 1);
});
test("repeated accept and observe join one loop and never overwrite a terminal result with stale queued admission", async () => {
  let calls = 0;
  const api = createApiClient({
    transport: async () => {
      calls++;
      return response({
        ...queued,
        state: "succeeded",
        result: { success: true },
      });
    },
  });
  const store = createOperationStore(api, { intervalMs: 0 });
  store.accept(queued);
  store.accept(queued);
  store.observe(queued.id);
  await flush();
  await flush();
  store.accept(queued);
  assert.equal(calls, 1);
  assert.equal(store.getSnapshot().get(queued.id).operation.state, "succeeded");
});
test("unavailable observation retains the known ID/progress and explicit retry only resumes GET", async () => {
  let calls = 0;
  const api = createApiClient({
    transport: async () => {
      calls++;
      return calls === 1
        ? new Response("{broken", { status: 200 })
        : response({
            ...queued,
            state: "needs_attention",
            result: { success: false },
            recovery: {
              available: true,
              action: "restore",
              reason: "Review current contents.",
            },
          });
    },
  });
  const store = createOperationStore(api, { intervalMs: 0, maxReadRetries: 0 });
  store.accept(queued);
  await flush();
  await flush();
  assert.equal(store.getSnapshot().get(queued.id).observation, "unavailable");
  assert.equal(store.getSnapshot().get(queued.id).operation.id, queued.id);
  assert.equal(calls, 1);
  store.observe(queued.id);
  await flush();
  await flush();
  assert.equal(calls, 2);
  assert.equal(
    store.getSnapshot().get(queued.id).operation.state,
    "needs_attention",
  );
});
test("document store identity is stable per client and actor, with no cross-account cache", () => {
  const api = createApiClient();
  const alice = getOperationStore(api, "alice"),
    bob = getOperationStore(api, "bob");
  assert.equal(alice, getOperationStore(api, "alice"));
  assert.notEqual(alice, bob);
  alice.accept({ ...queued, state: "failed", result: { success: false } });
  assert.equal(bob.getSnapshot().size, 0);
  assert.throws(() => getOperationStore(api, ""), TypeError);
});

test("recovered terminal receipt replaces retained queued progress without a fresh poll or later regression", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  let calls = 0;
  const api = createApiClient({
    transport: async () => {
      calls++;
      await gate;
      return response(queued);
    },
  });
  const store = createOperationStore(api, { intervalMs: 0 });
  store.accept(queued);
  store.accept({ ...queued, state: "succeeded", result: { success: true } });
  assert.equal(store.getSnapshot().get(queued.id).operation.state, "succeeded");
  release();
  await flush();
  await flush();
  assert.equal(store.getSnapshot().get(queued.id).operation.state, "succeeded");
  assert.equal(calls, 1);
});
