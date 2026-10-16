import type {
  JsonRecord,
  Operation,
  WorkshopTrack,
  RoutineRow,
  JobExecution,
} from "../../../api/contracts.ts";
import type { DeepReadonly, SourceStagingSnapshot } from "../core/types.ts";
export interface PendingTrack {
  id: number;
  target_playlist_id: string;
  track_uri: string;
  track_name: string;
  track_artists: string | null;
  track_album: string | null;
  track_image_url: string | null;
  track_duration_ms: number | null;
  source_playlist_id: string | null;
  source_name: string | null;
  status: "pending" | "promoted" | "dismissed";
  created_at: string | null;
  resolved_at: string | null;
}
export interface UpstreamSource {
  id: number;
  target_playlist_id: string;
  source_type: string;
  source_playlist_id: string | null;
  source_name: string | null;
  source_url: string | null;
  search_query: string | null;
  raid_count: number;
  last_track_count: number | null;
  last_resolve_status: string | null;
}
export interface RaidLink {
  id: number;
  target_playlist_id: string;
  raid_playlist_id: string;
  raid_playlist_name: string | null;
  drip_count: number;
  drip_enabled: boolean;
}
export interface ArchivePair {
  id: number;
  production_playlist_id: string;
  archive_playlist_id: string;
  archive_playlist_name: string | null;
  auto_archive_on_remove: boolean;
}
export interface RaidStatus {
  sources: UpstreamSource[];
  raid_link: RaidLink | null;
  source_count: number;
  max_sources: number;
}
export interface PlaylistChoice {
  id: string;
  name: string;
  image_url: string | null;
  total_tracks: number | null;
}
export type { SourceContext } from "../core/source-types.ts";
export type SourceTask = import("../core/source-types.ts").SourceReviewedTask;
export type SourceWork = import("../core/source-types.ts").SourceReviewedWork;
export type TaskKind = SourceTask["kind"];
export interface SourceResult {
  status: "complete" | "not_required" | "failed" | "unknown";
  retryable: boolean;
  message: string;
  facts: JsonRecord;
}
export type ActionPhase =
  | "ready"
  | "submitting"
  | "unknown"
  | "accepted"
  | "failed"
  | "complete"
  | "refused";
export interface QueuedAction {
  id: string;
  key: string;
  label: string;
  kind: string;
  path: string;
  body: JsonRecord;
  resources: readonly {
    resource: string;
    access: "read" | "write";
  }[];
  phase: ActionPhase;
  operationId: number | null;
  blockingOperationId: number | null;
  operation: Operation | null;
  error: string;
  retryUntil: number;
  result: JsonRecord | null;
  requestedIds: readonly number[];
  declaredSteps: readonly string[];
}
export interface RetainedTask {
  task: SourceTask;
  targetOperationId: number;
  phase: ActionPhase;
  result: SourceResult | null;
  inspection: SourceResult | null;
}
export interface ReadState<T> {
  phase: "idle" | "loading" | "ready" | "error";
  value: T;
  error: string;
  retryUntil: number;
}
export interface SourceState {
  pair: ReadState<ArchivePair | null>;
  status: ReadState<RaidStatus | null>;
  pending: ReadState<PendingTrack[]>;
  archive: ReadState<{
    tracks: WorkshopTrack[];
    total: number;
  }>;
  owned: ReadState<PlaylistChoice[]>;
  stagedPending: readonly PendingTrack[];
  stagedRestore: readonly WorkshopTrack[];
  archiveQueue: readonly string[];
  pendingSource: {
    id: string;
    name: string;
  } | null;
  archiveSource: {
    id: string;
    name: string;
  } | null;
  retained: readonly RetainedTask[];
  actions: readonly QueuedAction[];
  url: string;
  dripInput: string;
  sourceCountInputs: Record<number, string>;
  selectedCollection: string;
  selectedArchive: string;
  message: string;
  error: string;
  busy: boolean;
  retryUntil: number;
  configurationUnknown: boolean;
  routines: ReadState<RoutineRow[]>;
  latest: Record<number, JobExecution | null>;
  histories: Record<number, ReadState<JobExecution[]>>;
  runReview: {
    id: string;
    label: string;
    context: JsonRecord;
    revision: number;
    configRevision: number;
    sourceRevision: number;
    actionId: string | null;
    kind: string;
    scheduleId: number | null;
  } | null;
}
export type Staging = readonly SourceStagingSnapshot[];
