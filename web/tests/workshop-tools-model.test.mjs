import test from "node:test";
import assert from "node:assert/strict";
import algorithms from "../fixtures/workshop-tools/algorithms.json" with { type: "json" };
const uri = (n) => `spotify:track:${String(n).padStart(22, "0")}`;
test("all seven algorithms use server defaults and metadata constraints", async () => {
  const m = await import("../src/pages/workshop/tools/model.ts");
  assert.equal(algorithms.length, 7);
  for (const algorithm of algorithms) {
    const values = m.parameterDefaults(algorithm);
    assert.deepEqual(Object.keys(values), Object.keys(algorithm.parameters));
    assert.deepEqual(m.parameterErrors(algorithm, values), {});
  }
  const balanced = algorithms.find((a) => a.class_name === "BalancedShuffle");
  assert.deepEqual(
    m.parameterErrors(balanced, { keep_first: "-1", section_count: "2.5" }),
    { keep_first: "Enter at least 0.", section_count: "Enter a whole number." },
  );
  assert.equal(
    m.parameterErrors(balanced, { keep_first: "0", section_count: "11" })
      .section_count,
    "Enter at most 10.",
  );
  const percentage = algorithms.find(
    (a) => a.class_name === "PercentageShuffle",
  );
  assert.equal(
    m.parameterErrors(percentage, {
      shuffle_percentage: "",
      shuffle_location: "middle",
    }).shuffle_percentage,
    "Enter a number.",
  );
  assert.equal(
    m.parameterErrors(percentage, {
      shuffle_percentage: "12.5",
      shuffle_location: "back",
    }).shuffle_percentage,
    undefined,
  );
});
test("active URI locks preserve tiers, expiry and every duplicate position", async () => {
  const m = await import("../src/pages/workshop/tools/model.ts");
  const now = Date.parse("2026-10-15T12:00:00Z");
  const locks = [
    {
      track_uri: uri(1),
      lock_tier: "standard",
      expires_at: "2026-10-16T12:00:00Z",
    },
    {
      track_uri: uri(2),
      lock_tier: "standard",
      expires_at: "2026-10-14T12:00:00Z",
    },
    { track_uri: uri(3), lock_tier: "super", expires_at: null },
  ];
  assert.deepEqual(m.activeLockMap(locks, now), {
    [uri(1)]: true,
    [uri(3)]: true,
  });
  assert.match(m.lockLabel(locks[0]), /^Locked until /u);
  assert.equal(m.lockLabel(locks[2]), "Super-locked");
  assert.equal(m.lockAction(undefined), "Lock for 30 days");
  assert.equal(m.lockAction(locks[0]), "Lock permanently");
  assert.equal(m.lockAction(locks[2]), "Unlock");
});
test("external identity parsing preserves exact URL URI and ID instead of guessing names", async () => {
  const { externalIntent } = await import(
    "../src/pages/workshop/tools/model.ts"
  );
  const id = "a".repeat(22);
  assert.deepEqual(externalIntent("  Road songs  "), { query: "Road songs" });
  assert.deepEqual(externalIntent(id), { url: id });
  assert.deepEqual(externalIntent(`spotify:playlist:${id}`), {
    url: `spotify:playlist:${id}`,
  });
  assert.deepEqual(
    externalIntent(`https://open.spotify.com/playlist/${id}?si=example`),
    { url: `https://open.spotify.com/playlist/${id}?si=example` },
  );
  assert.throws(() => externalIntent("   "));
  assert.throws(() => externalIntent("x".repeat(201)));
});
test("snapshot groups use local calendar days, keep service order, nullable dates and signed draft difference", async () => {
  const m = await import("../src/pages/workshop/tools/model.ts");
  const now = new Date(2026, 9, 15, 12),
    today = new Date(2026, 9, 15, 8),
    yesterday = new Date(2026, 9, 14, 23);
  const rows = [
    { id: 3, created_at: today.toISOString() },
    { id: 2, created_at: yesterday.toISOString() },
    { id: 1, created_at: null },
  ];
  assert.deepEqual(
    m
      .groupSnapshots(rows, now.getTime())
      .map((g) => [g.label, g.snapshots.map((s) => s.id)]),
    [
      ["Today", [3]],
      ["Yesterday", [2]],
      ["Older", [1]],
    ],
  );
  assert.equal(m.snapshotType("auto_pre_commit"), "Workshop backup");
  assert.equal(m.snapshotType("manual"), "Manual");
  assert.equal(m.trackDifference(5, 3), "+2 tracks");
  assert.equal(m.trackDifference(3, 3), "Same track count");
  assert.equal(m.trackDifference(1, 3), "−2 tracks");
  assert.equal(m.duration(null), "Duration unavailable");
  assert.equal(m.duration(183000), "3:03");
});
test("naive lock expiry uses server UTC rather than the browser timezone", async () => {
  const { activeLockMap } = await import(
    "../src/pages/workshop/tools/model.ts"
  );
  assert.deepEqual(
    activeLockMap(
      [
        {
          track_uri: "spotify:track:0000000000000000000001",
          lock_tier: "standard",
          expires_at: "2026-01-01T12:00:00",
        },
      ],
      Date.parse("2026-01-01T13:00:00Z"),
    ),
    {},
  );
});
