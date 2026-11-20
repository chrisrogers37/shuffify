import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
import { createSourcesClient } from "../src/pages/workshop/sources/client.ts";
import { json, uri } from "./workshop-data.mjs";
const pending = {
  id: 7,
  target_playlist_id: "target",
  track_uri: uri(1),
  track_name: "One",
  track_artists: null,
  track_album: null,
  track_image_url: null,
  track_duration_ms: null,
  source_playlist_id: null,
  source_name: null,
  status: "pending",
  created_at: null,
  resolved_at: null,
};
test("pending parser rejects foreign target and duplicate row identities before exposing staging", async () => {
  let tracks = [pending, { ...pending }];
  const client = createSourcesClient(
    createApiClient({ transport: async () => json({ success: true, tracks }) }),
    "target",
  );
  await assert.rejects(client.pending(), /identit|duplicat/i);
  tracks = [{ ...pending, target_playlist_id: "foreign" }];
  await assert.rejects(client.pending(), /target|playlist/i);
});
test("raid status carries both count ranges and checks each count against them", async () => {
  const source = {
    id: 5,
    target_playlist_id: "target",
    source_type: "external",
    source_playlist_id: "upstream",
    source_name: "Upstream",
    source_url: null,
    search_query: null,
    raid_count: 7,
    last_track_count: null,
    last_resolve_status: null,
  };
  const link = {
    id: 3,
    target_playlist_id: "target",
    raid_playlist_id: "raid",
    raid_playlist_name: null,
    drip_count: 9,
    drip_enabled: false,
  };
  let ranges = { raid_count_range: [1, 7], drip_count_range: [2, 9] };
  const client = createSourcesClient(
    createApiClient({
      transport: async () =>
        json({
          success: true,
          raid_status: { sources: [source], raid_link: link, source_count: 1, max_sources: 10, ...ranges },
        }),
    }),
    "target",
  );
  const status = await client.status();
  assert.deepEqual([status.raid_count_range, status.drip_count_range], [[1, 7], [2, 9]]);
  ranges = { raid_count_range: [8, 100], drip_count_range: [2, 9] };
  await assert.rejects(client.status(), /source row/u);
  ranges = { raid_count_range: [1, 7], drip_count_range: [2, 8] };
  await assert.rejects(client.status(), /collection binding/u);
  for (const malformed of [{}, { raid_count_range: [1, 7] }, { raid_count_range: [7, 1], drip_count_range: [2, 9] }]) {
    ranges = malformed;
    await assert.rejects(client.status(), /status is incomplete/u);
  }
});
test("archive response retains total and duplicate physical occurrences", async () => {
  const track = {
    id: "1",
    uri: uri(1),
    name: "One",
    artists: [],
    album_name: "",
    album_image_url: null,
    duration_ms: 0,
  };
  const client = createSourcesClient(
    createApiClient({
      transport: async () =>
        json({ success: true, tracks: [track, track], total: 90 }),
    }),
    "target",
  );
  const result = await client.archive();
  assert.equal(result.total, 90);
  assert.equal(result.tracks.length, 2);
});
test("inspection sends only original closed task body with inspect_only and never a mutation key", async () => {
  let request;
  const client = createSourcesClient(
    createApiClient({
      getCsrfToken: () => "csrf",
      transport: async (path, init) => {
        request = { path, init };
        return json(
          {
            success: false,
            resource: "archive",
            status: "unknown",
            retryable: false,
            message: "Cannot establish prior effect",
            error_code: "cleanup_unverified",
            category: "error",
          },
          409,
        );
      },
    }),
    "target",
  );
  const task = {
    kind: "archive_append",
    source: { id: "archive", name: "Archive" },
    body: {
      source_playlist_id: "archive",
      target_track_uris: [uri(1)],
      track_uris: [uri(2)],
      baseline_track_uris: [],
    },
  };
  const result = await client.inspect(task);
  assert.equal(result.status, "unknown");
  assert.equal(request.path, "/api/playlist/target/pair/archive");
  assert.deepEqual(JSON.parse(request.init.body), {
    ...task.body,
    inspect_only: true,
  });
  assert.equal(new Headers(request.init.headers).get("Idempotency-Key"), null);
});
