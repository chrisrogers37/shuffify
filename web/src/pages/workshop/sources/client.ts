import type { ApiClient, ApiRequestInit } from "../../../api/client.ts";
import type { JsonRecord, WorkshopTrack } from "../../../api/contracts.ts";
import { ApiRequestError, ProtocolError } from "../../../api/errors.ts";
import {
  count,
  playlistId,
  positive,
  record,
  sourceResult,
  taskPath,
  uri,
} from "./model.ts";
import type {
  ArchivePair,
  PendingTrack,
  PlaylistChoice,
  RaidLink,
  RaidStatus,
  SourceTask,
  UpstreamSource,
} from "./types.ts";
const text = (v: unknown) => v === null || typeof v === "string";
const invalid = (message: string): never => {
  throw new ProtocolError(message);
};
export function createSourcesClient(api: ApiClient, target: string) {
  const base = `/api/playlist/${encodeURIComponent(target)}`;
  async function completed(
    path: string,
    init: ApiRequestInit = {},
  ): Promise<Record<string, unknown>> {
    const result = await api.request<unknown>(path, {
      cache: "no-store",
      ...init,
    });
    if (result.status !== "completed" || !record(result.data))
      return invalid("The local response is incomplete.");
    return result.data;
  }
  function pair(v: unknown): ArchivePair | null {
    if (v === null) return null;
    if (
      !record(v) ||
      !positive(v.id) ||
      v.production_playlist_id !== target ||
      !playlistId(v.archive_playlist_id) ||
      v.archive_playlist_id === target ||
      !text(v.archive_playlist_name) ||
      typeof v.auto_archive_on_remove !== "boolean"
    )
      return invalid(
        "The archive pairing names a different playlist or is incomplete.",
      );
    return v as unknown as ArchivePair;
  }
  function link(v: unknown): RaidLink | null {
    if (v === null) return null;
    if (
      !record(v) ||
      !positive(v.id) ||
      v.target_playlist_id !== target ||
      !playlistId(v.raid_playlist_id) ||
      v.raid_playlist_id === target ||
      !text(v.raid_playlist_name) ||
      !positive(v.drip_count) ||
      v.drip_count > 50 ||
      typeof v.drip_enabled !== "boolean"
    )
      return invalid(
        "The collection binding is incomplete or names a different playlist.",
      );
    return v as unknown as RaidLink;
  }
  const write = (path: string, method: string, json?: unknown) =>
    completed(base + path, { method, ...(json === undefined ? {} : { json }) });
  return {
    async sourceContext(raid: boolean, archive: boolean) {
      const v = await completed(
        `/api/workshop/${encodeURIComponent(target)}/source-context?raid=${Number(raid)}&archive=${Number(archive)}`,
      );
      if (
        !record(v.target) ||
        v.target.id !== target ||
        typeof v.target.name !== "string"
      )
        return invalid("The source context names another target.");
      for (const name of ["raid", "archive"] as const) {
        const x = v[name];
        if (
          x !== null &&
          (!record(x) ||
            !playlistId(x.id) ||
            x.id === target ||
            typeof x.name !== "string" ||
            (name === "archive" &&
              (!Array.isArray(x.baseline_track_uris) ||
                !x.baseline_track_uris.every(uri))))
        )
          return invalid(
            "The current source identity or full archive order is unavailable.",
          );
      }
      return v as unknown as import("./types.ts").SourceContext;
    },
    async pair() {
      const v = await completed(base + "/pair");
      if (typeof v.paired !== "boolean")
        return invalid("The archive pairing response is incomplete.");
      return v.paired ? pair(v.pair) : null;
    },
    async status(): Promise<RaidStatus> {
      const v = (await completed(base + "/raid-status")).raid_status;
      if (
        !record(v) ||
        !Array.isArray(v.sources) ||
        !count(v.source_count) ||
        !positive(v.max_sources) ||
        v.source_count !== v.sources.length
      )
        return invalid("The source status is incomplete.");
      const sources = v.sources.map((s) => {
        if (
          !record(s) ||
          !positive(s.id) ||
          s.target_playlist_id !== target ||
          typeof s.source_type !== "string" ||
          ![
            s.source_playlist_id,
            s.source_name,
            s.source_url,
            s.search_query,
            s.last_resolve_status,
          ].every(text) ||
          !(s.last_track_count === null || count(s.last_track_count)) ||
          !positive(s.raid_count) ||
          s.raid_count > 100
        )
          return invalid(
            "A source row is incomplete or names a different target.",
          );
        return s as unknown as UpstreamSource;
      });
      if (new Set(sources.map((x) => x.id)).size !== sources.length)
        return invalid("Source identities are duplicated.");
      return {
        sources,
        raid_link: link(v.raid_link),
        source_count: v.source_count,
        max_sources: v.max_sources,
      };
    },
    async pending(): Promise<PendingTrack[]> {
      const v = await completed(base + "/pending-raids");
      if (!Array.isArray(v.tracks))
        return invalid("The pending inbox is incomplete.");
      const tracks = v.tracks.map((x) => {
        if (
          !record(x) ||
          !positive(x.id) ||
          x.target_playlist_id !== target ||
          !uri(x.track_uri) ||
          typeof x.track_name !== "string" ||
          ![
            x.track_artists,
            x.track_album,
            x.track_image_url,
            x.source_playlist_id,
            x.source_name,
            x.created_at,
            x.resolved_at,
          ].every(text) ||
          !(x.track_duration_ms === null || count(x.track_duration_ms)) ||
          x.status !== "pending"
        )
          return invalid(
            "A pending row is incomplete or names a different target playlist.",
          );
        return x as unknown as PendingTrack;
      });
      if (new Set(tracks.map((x) => x.id)).size !== tracks.length)
        return invalid("Pending identities are duplicated.");
      return tracks;
    },
    async archive() {
      const v = await completed(base + "/pair/archive-tracks");
      if (!Array.isArray(v.tracks) || !count(v.total))
        return invalid("The archive list is incomplete.");
      const tracks = v.tracks.map((x) => {
        if (
          !record(x) ||
          !uri(x.uri) ||
          typeof x.name !== "string" ||
          !Array.isArray(x.artists) ||
          !x.artists.every((a) => typeof a === "string") ||
          !text(x.album_name) ||
          !text(x.album_image_url) ||
          !count(x.duration_ms)
        )
          return invalid("An archived track is incomplete.");
        return {
          ...x,
          id: x.uri.split(":").pop()!,
          is_local: false,
          added_at: null,
          artist_urls: [],
          track_url: `https://open.spotify.com/track/${x.uri.split(":").pop()}`,
        } as unknown as WorkshopTrack;
      });
      if (v.total < tracks.length)
        return invalid("Archive count contradicts its tracks.");
      return { tracks: tracks.reverse(), total: v.total };
    },
    async owned(refresh = false): Promise<PlaylistChoice[]> {
      const v = await completed(
        "/api/user-playlists" + (refresh ? "?refresh=1" : ""),
      );
      if (!Array.isArray(v.playlists))
        return invalid("The owned playlist list is incomplete.");
      const rows = v.playlists
        .filter((x) => record(x) && x.id !== target)
        .map((x) => {
          if (
            !record(x) ||
            !playlistId(x.id) ||
            typeof x.name !== "string" ||
            !(
              x.total_tracks === undefined ||
              x.total_tracks === null ||
              count(x.total_tracks)
            )
          )
            return invalid("A playlist choice is incomplete.");
          return {
            id: x.id,
            name: x.name,
            image_url: typeof x.image_url === "string" ? x.image_url : null,
            total_tracks: count(x.total_tracks) ? x.total_tracks : null,
          };
        });
      if (new Set(rows.map((x) => x.id)).size !== rows.length)
        return invalid("Playlist identities are duplicated.");
      return rows;
    },
    addURL: (url: string, name: string) =>
      write("/raid-add-url", "POST", {
        url,
        target_playlist_name: name,
        auto_schedule: false,
      }),
    sourceCount: (source_id: number, raid_count: number) =>
      write("/raid-source-count", "PUT", { source_id, raid_count }),
    unwatch: (source_id: number) =>
      write("/raid-unwatch", "POST", { source_id }),
    async updateLink(values: { drip_enabled?: boolean; drip_count?: number }) {
      const v = await write("/raid-link", "PUT", values);
      return link(v.raid_link);
    },
    unlinkCollection: () => write("/raid-link", "DELETE"),
    async autoArchive(value: boolean) {
      return pair(
        (await write("/pair", "PATCH", { auto_archive_on_remove: value })).pair,
      );
    },
    unlinkArchive: () => write("/pair", "DELETE"),
    async inspect(task: SourceTask) {
      try {
        return sourceResult(
          task,
          await completed(taskPath(target, task.kind), {
            method: "POST",
            json: { ...task.body, inspect_only: true },
          }),
        );
      } catch (e) {
        if (e instanceof ApiRequestError)
          return sourceResult(task, e.error as unknown as JsonRecord);
        throw e;
      }
    },
    async runReview(kind: "raid" | "drip" | "raid_and_drip") {
      const v = await completed(base + "/live-context", {
        method: "POST",
        json: { kind },
      });
      if (
        !record(v.context) ||
        v.context.kind !== kind ||
        !record(v.context.target) ||
        v.context.target.id !== target ||
        !record(v.context.review_binding) ||
        typeof v.context.review_binding.scope !== "string" ||
        !/^[a-f0-9]{64}$/.test(v.context.review_binding.scope) ||
        !Array.isArray(v.context.resources)
      )
        return invalid(
          "The run review is incomplete or names a different action.",
        );
      return v.context as JsonRecord;
    },
  };
}
export type SourcesClient = ReturnType<typeof createSourcesClient>;
