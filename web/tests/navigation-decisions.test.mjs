import { test } from "node:test";
import assert from "node:assert/strict";
test("registered Workshop decision runs once and can keep generic navigation blocked", async () => {
  const { decideNavigation } = await import("../src/navigation/decisions.ts");
  let calls = 0,
    fallback = 0;
  assert.equal(
    await decideNavigation(
      [
        {
          message: "dirty",
          decide: async () => {
            calls++;
            return false;
          },
        },
      ],
      async () => {
        fallback++;
        return true;
      },
    ),
    false,
  );
  assert.equal(calls, 1);
  assert.equal(fallback, 0);
  assert.equal(
    await decideNavigation([{ message: "other" }], async (message) => {
      assert.equal(message, "other");
      return true;
    }),
    true,
  );
});
test("every distinct blocker must allow navigation and no empty generic prompt is added", async () => {
  const { decideNavigation } = await import("../src/navigation/decisions.ts");
  const calls = [];
  assert.equal(
    await decideNavigation(
      [
        {
          message: "one",
          decide: async () => {
            calls.push(1);
            return true;
          },
        },
        {
          message: "two",
          decide: async () => {
            calls.push(2);
            return false;
          },
        },
      ],
      async () => {
        calls.push(3);
        return true;
      },
    ),
    false,
  );
  assert.deepEqual(calls, [1, 2]);
});
