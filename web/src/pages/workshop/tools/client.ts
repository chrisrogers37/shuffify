import type { ApiClient, ApiRequestInit } from "../../../api/client.ts";
import type { WorkshopTrack } from "../../../api/contracts.ts";
import { ProtocolError } from "../../../api/errors.ts";
import { trackURI } from "./model.ts";
import type {
  ExternalIntent,
  ExternalPlaylist,
  ExternalResult,
  PlaylistChoice,
  PreviewBody,
  PreviewResult,
  SearchPage,
  Snapshot,
  TrackLock,
} from "./types.ts";
const record = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const nullable = (v: unknown): v is string | null =>
  v === null || typeof v === "string";
const id = (v: unknown): v is number =>
  Number.isSafeInteger(v) && Number(v) > 0;
const count = (v: unknown): v is number =>
  Number.isSafeInteger(v) && Number(v) >= 0;
const fail = (message: string): never => {
  throw new ProtocolError(message);
};
function track(v: unknown): WorkshopTrack | null {
  if (!record(v) || !trackURI(v.uri) || typeof v.id !== "string") return null;
  if (
    v.uri !== `spotify:track:${v.id}` ||
    typeof v.name !== "string" ||
    !Array.isArray(v.artists) ||
    !v.artists.every(nullable)
  )
    return fail("A returned track is incomplete.");
  const duration = v.duration_ms ?? null,
    added = v.added_at ?? null,
    album = v.album_name ?? null,
    image = v.album_image_url ?? null,
    url = v.track_url ?? null,
    artistURLs = v.artist_urls ?? [];
  if (
    !(duration === null || count(duration)) ||
    ![added, album, image, url].every(nullable) ||
    !Array.isArray(artistURLs) ||
    !artistURLs.every(nullable)
  )
    return fail("A returned track presentation is incomplete.");
  return {
    id: v.id,
    uri: v.uri,
    name: v.name,
    artists: v.artists as (string | null)[],
    artist_urls: artistURLs as (string | null)[],
    duration_ms: duration as number | null,
    is_local: v.is_local === true,
    added_at: added as string | null,
    album_name: album as string | null,
    album_image_url: image as string | null,
    track_url: url as string | null,
  };
}
function tracks(v: unknown): WorkshopTrack[] {
  if (!Array.isArray(v)) return fail("Track results are incomplete.");
  return v.flatMap((value) => {
    const t = track(value);
    return t ? [t] : [];
  });
}
function lock(v: unknown, target: string, uri?: string): TrackLock {
  if (
    !record(v) ||
    !id(v.id) ||
    !id(v.user_id) ||
    v.spotify_playlist_id !== target ||
    !trackURI(v.track_uri) ||
    (uri !== undefined && v.track_uri !== uri) ||
    !count(v.position) ||
    !["standard", "super"].includes(String(v.lock_tier)) ||
    !nullable(v.created_at) ||
    !nullable(v.expires_at)
  )
    return fail(
      "The lock response is incomplete or names a different playlist or track.",
    );
  return v as unknown as TrackLock;
}
function snapshot(v: unknown, target: string, expected?: number): Snapshot {
  if (
    !record(v) ||
    !id(v.id) ||
    (expected !== undefined && v.id !== expected) ||
    !id(v.user_id) ||
    v.playlist_id !== target ||
    typeof v.playlist_name !== "string" ||
    !Array.isArray(v.track_uris) ||
    !v.track_uris.every(trackURI) ||
    v.track_count !== v.track_uris.length ||
    typeof v.snapshot_type !== "string" ||
    !nullable(v.trigger_description) ||
    !nullable(v.created_at)
  )
    return fail(
      "The snapshot response is incomplete or names a different playlist.",
    );
  return v as unknown as Snapshot;
}
function playlist(v: unknown): ExternalPlaylist {
  if (
    !record(v) ||
    typeof v.id !== "string" ||
    !v.id ||
    typeof v.name !== "string" ||
    typeof v.owner_id !== "string" ||
    !nullable(v.description) ||
    !(v.track_count === null || count(v.track_count))
  )
    return fail("External playlist information is incomplete.");
  return v as unknown as ExternalPlaylist;
}
function choice(v: unknown): PlaylistChoice {
  if (
    !record(v) ||
    typeof v.id !== "string" ||
    !v.id ||
    typeof v.name !== "string" ||
    typeof v.owner_display_name !== "string" ||
    !nullable(v.image_url) ||
    !(v.total_tracks === null || count(v.total_tracks))
  )
    return fail("Playlist search results are incomplete.");
  return v as unknown as PlaylistChoice;
}
export function createToolsClient(api: ApiClient, target: string) {
  if (!/^[A-Za-z0-9_-]+$/u.test(target))
    throw TypeError("Invalid playlist identifier.");
  const base = `/api/workshop/${target}`;
  async function completed(
    path: string,
    init: ApiRequestInit = {},
  ): Promise<Record<string, unknown>> {
    const value = await api.request(path, { cache: "no-store", ...init });
    if (value.status !== "completed" || !record(value.data))
      return fail("This read or local change requires a completed response.");
    return value.data;
  }
  const snapshotId = (value: number) => {
    if (!id(value)) throw TypeError("Snapshot IDs must be positive integers.");
    return value;
  };
  return {
    preview: async (body: PreviewBody): Promise<PreviewResult> => {
      const v = await completed(`${base}/preview-shuffle`, {
        method: "POST",
        json: body,
      });
      if (
        !Array.isArray(v.shuffled_uris) ||
        !v.shuffled_uris.every(trackURI) ||
        typeof v.algorithm_name !== "string"
      )
        return fail("The preview order is incomplete.");
      return { uris: v.shuffled_uris, algorithm: v.algorithm_name };
    },
    locks: async (): Promise<TrackLock[]> => {
      const v = await completed(`${base}/locks`);
      if (!Array.isArray(v.locks))
        return fail("The track locks response is incomplete.");
      return v.locks.map((l) => lock(l, target));
    },
    toggleLock: async (
      uri: string,
      position: number,
    ): Promise<TrackLock | null> => {
      if (!trackURI(uri) || !count(position))
        throw TypeError("Choose a current track occurrence.");
      const v = await completed(`${base}/locks/toggle`, {
        method: "POST",
        json: { track_uri: uri, position },
      });
      return v.lock === null ? null : lock(v.lock, target, uri);
    },
    unlockAll: async (): Promise<number> => {
      const v = await completed(`${base}/locks/unlock-all`, {
        method: "POST",
        json: {},
      });
      if (!count(v.count)) return fail("The unlock result is incomplete.");
      return v.count;
    },
    search: async (query: string, offset: number): Promise<SearchPage> => {
      query = query.trim();
      if (!query || query.length > 200 || !count(offset))
        throw TypeError("Enter a query of at most 200 characters.");
      const v = await completed("/api/workshop/search", {
        method: "POST",
        json: { query, offset, limit: 10 },
      });
      if (v.query !== query || v.offset !== offset || v.limit !== 10)
        return fail("The search page does not match this query.");
      return { tracks: tracks(v.tracks), query, offset, limit: 10 };
    },
    external: async (intent: ExternalIntent): Promise<ExternalResult> => {
      const v = await completed("/api/workshop/load-external-playlist", {
        method: "POST",
        json: intent,
      });
      if (v.mode === "search") {
        if (!Array.isArray(v.playlists))
          return fail("Playlist results are incomplete.");
        return { mode: "search", playlists: v.playlists.map(choice) };
      }
      if (v.mode === "tracks")
        return {
          mode: "tracks",
          playlist: playlist(v.playlist),
          tracks: tracks(v.tracks),
        };
      if (
        v.mode === "restricted" &&
        Array.isArray(v.tracks) &&
        v.tracks.length === 0 &&
        typeof v.message === "string" &&
        typeof v.suggested_search === "string"
      )
        return {
          mode: "restricted",
          playlist: playlist(v.playlist),
          tracks: [],
          message: v.message,
          suggested_search: v.suggested_search,
        };
      return fail("The external playlist response has no recognized mode.");
    },
    snapshots: async (): Promise<Snapshot[]> => {
      const v = await completed(`/api/playlist/${target}/snapshots?limit=50`);
      if (!Array.isArray(v.snapshots))
        return fail("The snapshot timeline is incomplete.");
      return v.snapshots.map((s) => snapshot(s, target));
    },
    snapshot: (value: number): Promise<Snapshot> => {
      const valid = snapshotId(value);
      return completed(`/api/snapshots/${valid}`).then((v) =>
        snapshot(v.snapshot, target, valid),
      );
    },
    createSnapshot: async (
      name: string,
      order: readonly string[],
      description: string,
    ): Promise<Snapshot> => {
      if (
        !name ||
        name.length > 255 ||
        description.length > 500 ||
        !order.every(trackURI)
      )
        throw TypeError(
          "The snapshot name, description or track order is invalid.",
        );
      const v = await completed(`/api/playlist/${target}/snapshots`, {
        method: "POST",
        json: {
          playlist_name: name,
          track_uris: order,
          trigger_description: description.trim() || "Manual snapshot",
        },
      });
      return snapshot(v.snapshot, target);
    },
    deleteSnapshot: (value: number) =>
      completed(`/api/snapshots/${snapshotId(value)}`, { method: "DELETE" }),
  };
}
export type ToolsClient = ReturnType<typeof createToolsClient>;
