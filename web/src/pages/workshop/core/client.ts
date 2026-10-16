import { loadReview } from "../../library/api.ts";
import type { ApiClient } from "../../../api/client.ts";
import type {
  JsonRecord,
  PlaylistRef,
  Operation,
  WorkshopContext,
  WorkshopTrack,
} from "../../../api/contracts.ts";
import { ProtocolError } from "../../../api/errors.ts";
import type {
  CommitBody,
  CurrentObservation,
  NamedDraft,
  SnapshotRestoreReview,
  TargetResult,
  VisibilityReview,
  ReviewedAction,
} from "./types.ts";
export const record = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
export const strings = (v: unknown): v is string[] =>
  Array.isArray(v) && v.every((u) => typeof u === "string");
const nullable = (v: unknown) => v === null || typeof v === "string";
export const trackURI = (v: unknown): v is string =>
  typeof v === "string" && /^spotify:track:[A-Za-z0-9]{22}$/u.test(v);
export function parseTrack(v: unknown, current = false): WorkshopTrack {
  if (
    !record(v) ||
    !trackURI(v.uri) ||
    typeof v.id !== "string" ||
    v.uri !== `spotify:track:${v.id}` ||
    typeof v.name !== "string" ||
    !Array.isArray(v.artists) ||
    !v.artists.every(nullable) ||
    !nullable(v.added_at) ||
    v.is_local !== false ||
    !(
      v.duration_ms === null ||
      (Number.isFinite(v.duration_ms) && Number(v.duration_ms) >= 0)
    ) ||
    ![v.album_name, v.album_image_url, v.track_url].every(nullable) ||
    (!current &&
      (!Array.isArray(v.artist_urls) || !v.artist_urls.every(nullable)))
  )
    throw new ProtocolError("The complete track presentation is unavailable.");
  return {
    ...v,
    artist_urls: current ? [] : v.artist_urls,
  } as unknown as WorkshopTrack;
}
export function parseContext(v: unknown, id: string): WorkshopContext {
  if (
    !record(v) ||
    !record(v.playlist) ||
    v.playlist.id !== id ||
    typeof v.playlist.name !== "string" ||
    typeof v.playlist.owner_id !== "string" ||
    !nullable(v.playlist.description) ||
    !(v.playlist.public === null || typeof v.playlist.public === "boolean") ||
    !record(v.playlist.audio_features) ||
    !Array.isArray(v.tracks) ||
    !Array.isArray(v.algorithms) ||
    !record(v.upstream_sources) ||
    !Array.isArray(v.recently_loaded)
  )
    throw new ProtocolError(
      "The Workshop response is incomplete or names a different target.",
    );
  v.tracks.forEach((t) => parseTrack(t));
  return v as unknown as WorkshopContext;
}
export function parseCurrent(v: unknown): Omit<CurrentObservation, "id"> {
  if (
    !record(v) ||
    v.representable !== true ||
    !strings(v.current_track_uris) ||
    !v.current_track_uris.every(trackURI) ||
    !Array.isArray(v.current_tracks) ||
    typeof v.loaded_at !== "string"
  )
    throw new ProtocolError(
      "The current playlist is incomplete or unrepresentable.",
    );
  const order = v.current_track_uris;
  const tracks = v.current_tracks.map((t) => parseTrack(t, true));
  if (
    tracks.length !== v.current_track_uris.length ||
    tracks.some((t, i) => t.uri !== order[i])
  )
    throw new ProtocolError(
      "The current playlist has contradictory occurrence data.",
    );
  return {
    track_uris: v.current_track_uris,
    tracks,
    loaded_at: v.loaded_at,
    representable: true,
  };
}
export function targetResult(facts: JsonRecord | null): TargetResult | null {
  if (!facts) return null;
  const v = record(facts.target) ? facts.target : facts;
  if (
    !["saved", "already_applied", "not_written", "partial", "unknown"].includes(
      String(v.target_status),
    ) ||
    ![v.confirmed_batches, v.total_batches].every(
      (n) => Number.isSafeInteger(n) && Number(n) >= 0,
    )
  )
    return null;
  return {
    target_status: v.target_status as TargetResult["target_status"],
    message:
      typeof v.message === "string"
        ? v.message
        : "The target result needs review.",
    error_code: typeof v.error_code === "string" ? v.error_code : null,
    requires_refresh: v.requires_refresh === true,
    confirmed_batches:
      typeof v.confirmed_batches === "number" ? v.confirmed_batches : 0,
    total_batches: typeof v.total_batches === "number" ? v.total_batches : 0,
    retryable: v.retryable === true,
    ...(strings(v.saved_track_uris)
      ? { saved_track_uris: v.saved_track_uris }
      : {}),
    ...(record(v.playlist_state)
      ? { playlist_state: v.playlist_state as JsonRecord }
      : {}),
    facts,
  };
}
function draft(v: unknown, target: string): NamedDraft {
  if (
    !record(v) ||
    !Number.isSafeInteger(v.id) ||
    Number(v.id) < 1 ||
    !Number.isSafeInteger(v.user_id) ||
    v.playlist_id !== target ||
    typeof v.session_name !== "string" ||
    !strings(v.track_uris) ||
    !v.track_uris.every(trackURI) ||
    v.track_count !== v.track_uris.length ||
    !nullable(v.created_at) ||
    !nullable(v.updated_at)
  )
    throw new ProtocolError(
      "The saved draft is incomplete or belongs to a different playlist.",
    );
  return v as unknown as NamedDraft;
}
const digest = (v: unknown) =>
  typeof v === "string" && /^[a-f0-9]{64}$/u.test(v);
export function parseSnapshotReview(
  v: unknown,
  target: string,
  id: number,
): SnapshotRestoreReview {
  if (
    !record(v) ||
    v.version !== 1 ||
    v.kind !== "snapshot_restore" ||
    typeof v.label !== "string" ||
    !record(v.target) ||
    v.target.id !== target ||
    typeof v.target.name !== "string" ||
    !record(v.snapshot) ||
    v.snapshot.id !== id ||
    typeof v.snapshot.created_at !== "string" ||
    !record(v.action) ||
    v.action.snapshot_id !== id ||
    !strings(v.action.desired_track_uris) ||
    !v.action.desired_track_uris.every(trackURI) ||
    !record(v.action.summary) ||
    !record(v.current) ||
    !strings(v.current.track_uris) ||
    !v.current.track_uris.every(trackURI) ||
    v.current.track_count !== v.current.track_uris.length ||
    typeof v.current.loaded_at !== "string" ||
    !record(v.review_binding) ||
    v.review_binding.version !== 1 ||
    !digest(v.review_binding.scope) ||
    !digest(v.review_binding.before_order) ||
    !Array.isArray(v.resources) ||
    v.resources.length !== 1 ||
    !v.resources.every(
      (r) =>
        record(r) &&
        r.resource === target &&
        r.role === "target" &&
        r.access === "write" &&
        r.domain === "spotify",
    ) ||
    !strings(v.required_bookkeeping) ||
    !v.required_bookkeeping.includes("session_history") ||
    !Array.isArray(v.steps)
  )
    throw new ProtocolError("This snapshot restore cannot be safely reviewed.");
  return v as unknown as SnapshotRestoreReview;
}
export function createWorkshopClient(api: ApiClient, target: string) {
  if (!/^[A-Za-z0-9_-]+$/u.test(target))
    throw new TypeError("Invalid playlist identifier.");
  const base = `/api/workshop/${target}`;
  async function completed<T>(
    path: string,
    init: Parameters<ApiClient["request"]>[1] = {},
  ): Promise<T> {
    const r = await api.request<T>(path, { cache: "no-store", ...init });
    if (r.status !== "completed")
      throw new ProtocolError(
        "This read or local change requires a completed response.",
      );
    return r.data;
  }
  const queued = async (path: string, json: unknown, key: string) => {
    const r = await api.request(path, {
      method: "POST",
      json,
      headers: { "Idempotency-Key": key },
    });
    if (r.status !== "accepted")
      throw new ProtocolError(
        "This Spotify change requires queued HTTP202 admission.",
        202,
      );
    return r.operation;
  };
  return {
    context: async () => parseContext(await completed(base), target),
    current: async () => parseCurrent(await completed(`${base}/current-state`)),
    sourceContext: async (raid: boolean, archive: boolean) => {
      const v = await completed<{
        success: true;
        target: PlaylistRef;
        raid: PlaylistRef | null;
        archive: (PlaylistRef & { baseline_track_uris: string[] }) | null;
      }>(`${base}/source-context?raid=${+raid}&archive=${+archive}`);
      if (
        !record(v.target) ||
        v.target.id !== target ||
        typeof v.target.name !== "string" ||
        !(v.raid === null || record(v.raid)) ||
        !(v.archive === null || record(v.archive))
      )
        throw new ProtocolError("Source review names a different target.");
      return v;
    },
    commit: (body: CommitBody, key: string) =>
      queued(`${base}/commit`, body, key),
    visibilityReview: async (publicValue: boolean) =>
      (await loadReview(api, target, {
        kind: "privacy",
        public: publicValue,
      })) as VisibilityReview,
    visibility: (publicValue: boolean, scope: string, key: string) =>
      queued(
        `/api/playlist/${target}/toggle-visibility`,
        { public: publicValue, reviewed_scope: scope },
        key,
      ),
    snapshotReview: async (id: number) => {
      const v = await completed<{
        success: true;
        context: SnapshotRestoreReview;
      }>(`/api/snapshots/${id}/restore-context`);
      return parseSnapshotReview(v.context, target, id);
    },
    restore: (
      id: number,
      binding: SnapshotRestoreReview["review_binding"],
      key: string,
    ) =>
      queued(
        `/api/snapshots/${id}/restore`,
        {
          reviewed_scope: binding.scope,
          reviewed_before_order: binding.before_order,
        },
        key,
      ),
    drafts: async () => {
      const v = await completed<{ success: true; sessions: NamedDraft[] }>(
        `${base}/sessions`,
      );
      if (!Array.isArray(v.sessions))
        throw new ProtocolError("Saved drafts are unavailable.");
      return v.sessions.map((d) => draft(d, target));
    },
    draft: async (id: number) =>
      draft(
        (
          await completed<{ success: true; session: NamedDraft }>(
            `/api/workshop/sessions/${id}`,
          )
        ).session,
        target,
      ),
    saveDraft: async (name: string, uris: readonly string[], id?: number) =>
      draft(
        (
          await completed<{ success: true; session: NamedDraft }>(
            id ? `/api/workshop/sessions/${id}` : `${base}/sessions`,
            {
              method: id ? "PUT" : "POST",
              json: { session_name: name, track_uris: uris },
            },
          )
        ).session,
        target,
      ),
    deleteDraft: (id: number) =>
      completed(`/api/workshop/sessions/${id}`, { method: "DELETE" }),
  };
}
export type WorkshopClient = ReturnType<typeof createWorkshopClient>;
export async function loadPicker(api: ApiClient): Promise<PlaylistRef[]> {
  const r = await api.request<{ success: true; playlists: PlaylistRef[] }>(
    "/api/user-playlists",
    { cache: "no-store" },
  );
  if (
    r.status !== "completed" ||
    !Array.isArray(r.data.playlists) ||
    !r.data.playlists.every(
      (p) =>
        record(p) && typeof p.id === "string" && typeof p.name === "string",
    )
  )
    throw new ProtocolError("Your playlist list could not be loaded.");
  return r.data.playlists;
}

/** Direct snapshot results expose observed resource order and playlist tracks, never Workshop batch counts. */
export function verifiedRestore(
  operation: Operation,
  review: ReviewedAction,
): { track_uris: string[]; tracks: WorkshopTrack[] } | null {
  if (
    review.kind !== "snapshot_restore" ||
    operation.state !== "succeeded" ||
    operation.result?.success !== true
  )
    return null;
  const facts = operation.result,
    detail = facts.operation,
    playlist = facts.playlist;
  if (
    !record(detail) ||
    detail.kind !== "snapshot_restore" ||
    detail.aggregate_status !== "complete" ||
    !Array.isArray(detail.resources) ||
    !Array.isArray(detail.bookkeeping) ||
    !record(playlist) ||
    playlist.id !== review.target.id ||
    !Array.isArray(playlist.tracks)
  )
    return null;
  const resource = detail.resources.find(
    (r) => record(r) && r.resource === review.target.id,
  );
  if (
    !record(resource) ||
    !["complete", "not_required"].includes(String(resource.status)) ||
    !strings(resource.current_track_uris) ||
    !detail.bookkeeping.some(
      (b) =>
        record(b) &&
        b.kind === "session_history" &&
        ["complete", "not_required"].includes(String(b.status)),
    )
  )
    return null;
  const order = resource.current_track_uris;
  if (
    order.length !== review.desired.length ||
    order.some((u, i) => u !== review.desired[i]?.uri)
  )
    return null;
  try {
    const tracks = playlist.tracks.map((t) => parseTrack(t, true));
    return tracks.length === order.length &&
      tracks.every((t, i) => t.uri === order[i])
      ? { track_uris: order, tracks }
      : null;
  } catch {
    return null;
  }
}
