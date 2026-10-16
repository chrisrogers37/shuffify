import assert from "node:assert/strict";
import test from "node:test";
test("motion timing accepts minified fractional seconds without losing shared token animation", async () => {
  const { motionMilliseconds } = await import("../src/design/motion.ts");
  for (const [value, expected] of [
    ["720ms", 720],
    [".72s", 720],
    ["0.72s", 720],
    [" 180ms ", 180],
    ["180.5ms", 180.5],
    ["0s", 0],
    ["-1s", 0],
    ["invalid", 0],
    ["1e3s", 0],
  ])
    assert.equal(motionMilliseconds(value), expected, value);
});
