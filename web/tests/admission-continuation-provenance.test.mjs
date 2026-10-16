import { test } from "node:test";
import assert from "node:assert/strict";
import { accepted, busy, harness, operation } from "./admission-recovery-helpers.mjs";
const uris = ["spotify:track:0000000000000000000001"];
const parent = { key: "target-save", feature: "workshop", label: "Save Evening Collection",
  resources: ["target", "collection", "archive"].map(id => ({ resource: `playlist:${id}`, access: "write" })),
  path: "/api/workshop/target/commit", body: { encoding: "json", value: { baseline_track_uris: [], track_uris: uris } },
  expected: { kind: "workshop_commit", target: "target" } };
const source = { key: "remove-pending", feature: "workshop", label: "Finish pending tracks",
  resources: [{ resource: "playlist:target", access: "read" }, { resource: "playlist:collection", access: "write" }],
  path: "/api/playlist/target/pending-raids/finalize",
  body: { encoding: "json", value: { source_playlist_id: "collection", target_track_uris: uris, track_ids: [1] } },
  expected: { kind: "workshop_finalize_raids", target: "target" } };
const later = { key: "append-archive", feature: "workshop", label: "Finish archive",
  resources: [{ resource: "playlist:target", access: "read" }, { resource: "playlist:archive", access: "write" }],
  path: "/api/playlist/target/pair/archive",
  body: { encoding: "json", value: { source_playlist_id: "archive", target_track_uris: uris, track_uris: uris, baseline_track_uris: [] } },
  expected: { kind: "workshop_archive_append", target: "target" } };
const sourceDone = (extra = {}) => operation(9, "succeeded", {
  success: true, status: "complete", promoted_count: 1, affected_count: 1,
  source_status: "complete", database_status: "complete",
}, { kind: "workshop_finalize_raids", ...extra });
async function prepared() {
  const h = harness(path => path.endsWith("/commit")
    ? accepted(operation(1, "queued", null, { kind: "workshop_commit" }))
    : path.endsWith("/finalize") ? busy() : accepted(operation(10, "queued", null, { kind: "workshop_archive_append" })));
  const reservation = h.registry.reserveReviewedContinuations(parent, [
    { request: source, dependsOn: [] }, { request: later, dependsOn: [source.key] },
  ], "alice");
  await reservation.submitTarget("alice");
  const saved = operation(1, "succeeded", { success: true, target_status: "saved", saved_track_uris: uris }, { kind: "workshop_commit" });
  h.operations.finish(saved);
  assert.equal(h.registry.activateReviewedContinuations(parent.key, saved, "alice"), true);
  await assert.rejects(h.registry.submitContinuation(source.key, "alice"));
  return h;
}
test("a same-kind successful blocking operation cannot authorize the caller's dependent continuation", async () => {
  const h = await prepared();
  h.operations.finish(sourceDone());
  await assert.rejects(h.registry.submitContinuation(later.key, "alice"), /earlier reviewed source task/);
  assert.equal(h.calls.length, 2);
});
for (const extra of [{ kind: "other" }, { targets: ["different-target"] }])
  test(`a dependency with ${extra.kind ? "another kind" : "another target"} never authorizes a continuation`, async () => {
    const h = await prepared(); h.operations.finish(sourceDone(extra));
    await assert.rejects(h.registry.submitContinuation(later.key, "alice"));
    assert.equal(h.calls.length, 2);
  });
for (const observation of ["terminal", "unavailable"])
  test(`${observation} continuation blocker permits only a new exact reviewed retry`, async () => {
    const h = await prepared();
    if (observation === "terminal") h.operations.finish(sourceDone());
    else h.operations.unavailable(9);
    const replacement = { ...source, key: "explicit-new-review" };
    const review = { failedKey: source.key, request: replacement,
      currentTarget: { id: "target", track_uris: uris },
      sourceContext: { target: { id: "target", name: "Evening Collection" }, raid: { id: "collection", name: "Collection" }, archive: { id: "archive", name: "Archive" } } };
    assert.equal(h.registry.reserveContinuationRetry(review, "alice"), true);
    assert.equal(h.registry.getSnapshot().get(source.key).status, "refused");
    assert.equal(h.registry.getSnapshot().get(source.key).relation, "blocking");
    assert.deepEqual(h.registry.getSnapshot().get(later.key).continuation.dependsOn, [replacement.key]);
    assert.equal(h.calls.length, 2);
  });
