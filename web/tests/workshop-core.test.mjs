import { test } from "node:test";
import assert from "node:assert/strict";
const uri = (n) => `spotify:track:${String(n).padStart(22, "0")}`;
const track = (n, added = "2026-01-01T00:00:00Z") => ({
  id: String(n).padStart(22, "0"),
  uri: uri(n),
  name: `Track ${n}`,
  artists: ["Example Artist"],
  artist_urls: [null],
  duration_ms: 180000,
  is_local: false,
  added_at: added,
  album_name: "Example Album",
  album_image_url: null,
  track_url: `https://open.spotify.com/track/${String(n).padStart(22, "0")}`,
});
export { uri, track };
test("FIFO occurrence keys and duplicate provenance survive reorder while new copies get new keys", async () => {
  const m = await import("../src/pages/workshop/core/occurrences.ts");
  const original = m.occurrences([
    track(1, "first"),
    track(2),
    track(1, "second"),
  ]);
  const moved = m.remap(original, [uri(1), uri(1), uri(2), uri(1)]);
  assert.deepEqual(
    moved.slice(0, 3).map((o) => o.key),
    [original[0].key, original[2].key, original[1].key],
  );
  assert.deepEqual(
    moved.slice(0, 2).map((o) => o.track.added_at),
    ["first", "second"],
  );
  assert.ok(!original.some((o) => o.key === moved[3].key));
  assert.equal(
    m.removeOccurrence(moved, moved[0].key).filter((o) => o.uri === uri(1))
      .length,
    2,
  );
  assert.deepEqual(m.uris(original), [uri(1), uri(2), uri(1)]);
});
test("duplicate-aware summary distinguishes membership and shared occurrence order", async () => {
  const { summarize } =
    await import("../src/pages/workshop/core/occurrences.ts");
  assert.deepEqual(
    summarize([uri(1), uri(2), uri(1)], [uri(1), uri(1), uri(2), uri(3)]),
    { added_count: 1, removed_count: 0, order_changed: true },
  );
  assert.deepEqual(summarize([uri(1), uri(2)], []), {
    added_count: 0,
    removed_count: 2,
    order_changed: false,
  });
});
test("adopting an observed order keeps FIFO keys but replaces each occurrence presentation authoritatively", async () => {
  const { observedOccurrences, occurrences } =
    await import("../src/pages/workshop/core/occurrences.ts");
  const old = occurrences([track(1, "old-first"), track(1, "old-second")]);
  const current = observedOccurrences(old, [
    track(1, "new-first"),
    track(1, "new-second"),
  ]);
  assert.deepEqual(
    current.map((r) => r.key),
    old.map((r) => r.key),
  );
  assert.deepEqual(
    current.map((r) => r.track.added_at),
    ["new-first", "new-second"],
  );
});
