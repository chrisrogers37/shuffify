import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
import { context } from "./workshop-data.mjs";
const uri = (n) => `spotify:track:${String(n).padStart(22, "0")}`;
const track = (n) => ({
  id: String(n).padStart(22, "0"),
  uri: uri(n),
  name: `Track ${n}`,
  artists: ["Example"],
  duration_ms: 20,
  added_at: null,
  is_local: false,
  album_name: null,
  album_image_url: null,
  track_url: null,
});
const json = (v) =>
  new Response(JSON.stringify(v), {
    headers: { "content-type": "application/json" },
  });
test("current-state normalizes its actual artist URL-free DTO and rejects contradictory orders", async () => {
  const { createWorkshopClient } =
    await import("../src/pages/workshop/core/client.ts");
  let bad = false;
  const api = createApiClient({
    transport: async () =>
      json({
        success: true,
        representable: true,
        current_track_uris: [uri(1)],
        current_tracks: [track(bad ? 2 : 1)],
        loaded_at: "2026-01-01T00:00:00Z",
      }),
  });
  const client = createWorkshopClient(api, "target");
  const current = await client.current();
  assert.deepEqual(current.tracks[0].artist_urls, []);
  bad = true;
  await assert.rejects(client.current(), /incomplete|contradictory/i);
});
test("commit admission sends exact duplicate/empty arrays and original key and refuses completed 200", async () => {
  const { createWorkshopClient } =
    await import("../src/pages/workshop/core/client.ts");
  const calls = [];
  const client = createWorkshopClient(
    createApiClient({
      getCsrfToken: () => "token",
      transport: async (path, init) => {
        calls.push([path, init]);
        return json({ success: true });
      },
    }),
    "target",
  );
  await assert.rejects(
    client.commit(
      { baseline_track_uris: [uri(1), uri(1)], track_uris: [] },
      "original-key",
    ),
    /queued|202/i,
  );
  assert.equal(calls[0][0], "/api/workshop/target/commit");
  assert.equal(calls[0][1].headers.get("Idempotency-Key"), "original-key");
  assert.deepEqual(JSON.parse(calls[0][1].body), {
    baseline_track_uris: [uri(1), uri(1)],
    track_uris: [],
  });
});
test("target-only review asks no source scopes and validates target identity", async () => {
  const { createWorkshopClient } =
    await import("../src/pages/workshop/core/client.ts");
  const paths = [];
  const client = createWorkshopClient(
    createApiClient({
      transport: async (path) => {
        paths.push(path);
        return json({
          success: true,
          target: { id: "other", name: "Other" },
          raid: null,
          archive: null,
        });
      },
    }),
    "target",
  );
  await assert.rejects(client.sourceContext(false, false), /different|target/i);
  assert.equal(
    paths[0],
    "/api/workshop/target/source-context?raid=0&archive=0",
  );
});
test("incomplete rich results retain raw facts without inventing batch counts", async () => {
  const { targetResult } = await import("../src/pages/workshop/core/client.ts");
  assert.equal(
    targetResult({ target_status: "partial", message: "Uncertain" }),
    null,
  );
});
test("Workshop fixtures do not intercept another phase user-playlist request", async () => {
  const { createWorkshopFixtures } =
    await import("../src/fixtures/workshop.ts");
  assert.equal(
    await createWorkshopFixtures("library-empty")("/api/user-playlists", "GET"),
    null,
  );
});
test("running Workshop fixture has no completed result or timestamp", async () => {
  const { createWorkshopFixtures } =
    await import("../src/fixtures/workshop.ts");
  const handler = createWorkshopFixtures("workshop-happy");
  const first = await handler("/api/operations/2001", "GET");
  const running = (await first.json()).operation;
  assert.equal(running.state, "running");
  assert.equal(running.result, null);
  assert.equal(running.completed_at, null);
  const next = (await (await handler("/api/operations/2001", "GET")).json())
    .operation;
  assert.equal(next.state, "succeeded");
  assert.equal(next.result.success, true);
});
test("Workshop context loads whether or not its playlist carries audio_features", async () => {
  const { createWorkshopClient } =
    await import("../src/pages/workshop/core/client.ts");
  const load = (playlist) =>
    createWorkshopClient(
      createApiClient({
        transport: async () => json({ ...context(), playlist }),
      }),
      "target",
    ).context();
  const without = { ...context().playlist };
  delete without.audio_features;
  const omitted = await load(without);
  assert.equal(omitted.playlist.id, "target");
  assert.equal("audio_features" in omitted.playlist, false);
  const present = await load({ ...without, audio_features: {} });
  assert.equal(present.playlist.id, "target");
  assert.deepEqual(present.playlist.audio_features, {});
});
