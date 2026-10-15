import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
const uri = (n) => `spotify:track:${String(n).padStart(22, "0")}`;
const response = (body, status = 200) =>
  new Response(JSON.stringify({ success: true, ...body }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
async function build(reply) {
  const calls = [];
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      calls.push({
        path,
        method: init.method,
        body: init.body ? JSON.parse(init.body) : null,
        headers: new Headers(init.headers),
      });
      return reply(path, init);
    },
  });
  const { createToolsClient } = await import(
    "../src/pages/workshop/tools/client.ts"
  );
  return { client: createToolsClient(api, "target"), calls };
}
test("preview sends complete occurrence metadata and locked duplicate positions without provider writes", async () => {
  const { client, calls } = await build(() =>
    response({
      shuffled_uris: [uri(1), uri(2), uri(1)],
      algorithm_name: "Basic",
    }),
  );
  const tracks = [
    { uri: uri(1), added_at: "first" },
    { uri: uri(2), added_at: null },
    { uri: uri(1), added_at: "second" },
  ];
  const value = await client.preview({
    algorithm: "BasicShuffle",
    keep_first: "0",
    tracks,
    locked_positions: { 0: uri(1), 2: uri(1) },
  });
  assert.deepEqual(value.uris, [uri(1), uri(2), uri(1)]);
  assert.deepEqual(calls[0].body.tracks, tracks);
  assert.equal(calls[0].path, "/api/workshop/target/preview-shuffle");
  assert.equal(calls[0].headers.get("X-CSRFToken"), "csrf");
});
test("catalog normalizes nullable presentation but skips absent track identity", async () => {
  const { client } = await build(() =>
    response({
      tracks: [
        {
          id: String(1).padStart(22, "0"),
          uri: uri(1),
          name: "Track",
          artists: ["Artist"],
          duration_ms: null,
          album_name: null,
          album_image_url: null,
        },
        { id: null, uri: null },
      ],
      query: "night",
      offset: 0,
      limit: 10,
    }),
  );
  const result = await client.search("night", 0);
  assert.equal(result.tracks.length, 1);
  assert.equal(result.tracks[0].added_at, null);
  assert.equal(result.tracks[0].is_local, false);
  assert.deepEqual(result.tracks[0].artist_urls, []);
  assert.equal(result.tracks[0].duration_ms, null);
});
test("external restricted count stays null and duplicate playlist names retain exact identities", async () => {
  let mode = "restricted";
  const { client, calls } = await build(() =>
    mode === "restricted"
      ? response({
          mode,
          playlist: {
            id: "a".repeat(22),
            name: "Night",
            owner_id: "other",
            description: null,
            track_count: null,
          },
          tracks: [],
          message: "Membership unavailable.",
          suggested_search: "Night",
        })
      : response({
          mode: "search",
          playlists: [
            {
              id: "a".repeat(22),
              name: "Same",
              owner_display_name: "A",
              image_url: null,
              total_tracks: null,
            },
            {
              id: "b".repeat(22),
              name: "Same",
              owner_display_name: "B",
              image_url: null,
              total_tracks: 3,
            },
          ],
        }),
  );
  const restricted = await client.external({ url: "a".repeat(22) });
  assert.equal(restricted.playlist.track_count, null);
  assert.equal(restricted.mode, "restricted");
  mode = "search";
  const results = await client.external({ query: "Same" });
  assert.deepEqual(
    results.playlists.map((p) => p.id),
    ["a".repeat(22), "b".repeat(22)],
  );
  assert.deepEqual(calls[1].body, { query: "Same" });
});
test("locks and snapshots refuse cross-target or contradictory identities", async () => {
  const { client } = await build(() =>
    response({
      locks: [
        {
          id: 1,
          user_id: 1,
          spotify_playlist_id: "other",
          track_uri: uri(1),
          position: 0,
          lock_tier: "super",
          created_at: null,
          expires_at: null,
        },
      ],
    }),
  );
  await assert.rejects(client.locks(), /different playlist|incomplete/u);
  const b = await build(() =>
    response({
      snapshot: {
        id: 1,
        user_id: 1,
        playlist_id: "other",
        playlist_name: "Other",
        track_uris: [uri(1)],
        track_count: 1,
        snapshot_type: "manual",
        created_at: null,
        trigger_description: null,
      },
    }),
  );
  await assert.rejects(b.client.snapshot(1), /different playlist|incomplete/u);
  assert.throws(() => b.client.snapshot(0));
});
test("DB-only tools require completed responses and preserve exact DELETE identity", async () => {
  const { client, calls } = await build(() => response({}));
  await client.deleteSnapshot(71);
  assert.equal(calls[0].path, "/api/snapshots/71");
  assert.equal(calls[0].method, "DELETE");
  assert.equal(calls[0].body, null);
});
