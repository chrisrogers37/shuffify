import { test } from "node:test";
import assert from "node:assert/strict";

test("session bootstrap is single flight and never consumes a notice twice", async () => {
  const { createSessionStore } = await import("../src/auth/session-store.ts");
  let calls = 0;
  const session = {
    success: true,
    signed_in: false,
    user: null,
    notice: {
      code: "login_failed",
      category: "error",
      message: "Please reconnect.",
    },
  };
  const store = createSessionStore(async () => {
    calls++;
    return session;
  });
  await Promise.all([store.load(), store.load()]);
  assert.equal(calls, 1);
  assert.equal(store.getSnapshot().status, "ready");
  store.consumeNotice();
  assert.equal(store.getSnapshot().session.notice, null);
  await store.load();
  assert.equal(calls, 1);
});

test("task transitions reject reentry while an asynchronous close guard is pending", async () => {
  const { createTransitionGate } =
    await import("../src/components/tasks/task-state.ts");
  const gate = createTransitionGate();
  let release;
  const waiting = gate.run(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  assert.equal(await gate.run(() => true), false);
  release(true);
  assert.equal(await waiting, true);
  assert.equal(await gate.run(() => true), true);
});

test("task scroll restoration is explicit, actor scoped, and absent for ordinary tasks", async () => {
  const { createTaskScrollCache } =
    await import("../src/components/tasks/task-state.ts");
  assert.equal(typeof createTaskScrollCache, "function");
  const cache = createTaskScrollCache();
  cache.save("alice:playlist:history", { scroll: 431, pageScroll: 120 });
  assert.deepEqual(cache.read("alice:playlist:history"), {
    scroll: 431,
    pageScroll: 120,
  });
  assert.deepEqual(cache.read("bob:playlist:history"), {
    scroll: 0,
    pageScroll: 0,
  });
  assert.deepEqual(cache.read(), { scroll: 0, pageScroll: 0 });
  const view = cache.read("alice:playlist:history");
  view.scroll = 0;
  assert.equal(cache.read("alice:playlist:history").scroll, 431);
});
