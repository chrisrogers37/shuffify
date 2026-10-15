import type {
  JsonRecord,
  Operation,
  PlaylistRef,
  WorkshopContext,
  WorkshopTrack,
} from "../../../api/contracts.ts";
export type DeepReadonly<T> = T extends readonly (infer E)[]
  ? readonly DeepReadonly<E>[]
  : T extends object
    ? { readonly [K in keyof T]: DeepReadonly<T[K]> }
    : T;
export interface Occurrence {
  readonly key: string;
  readonly uri: string;
  readonly track: DeepReadonly<WorkshopTrack>;
}
export type SourceStagingSnapshot =
  | {
      readonly kind: "raid_promotion";
      readonly source_playlist_id: string | null;
      readonly pending_rows: readonly {
        readonly id: number;
        readonly track_uri: string;
        readonly draft_occurrence_key?: string;
      }[];
    }
  | {
      readonly kind: "archive_append";
      readonly source_playlist_id: string;
      readonly track_uris: readonly string[];
      readonly baseline_track_uris: readonly string[];
    }
  | {
      readonly kind: "archive_restore";
      readonly source_playlist_id: string;
      readonly track_uris: readonly string[];
    };
export interface SourceCoordinator {
  snapshot(): readonly SourceStagingSnapshot[];
  /** Return uncommitted staging; this must never discard saved/uncertain effects. */
  discard(): void;
  subscribe(invalidate: () => void): () => void;
}
export type PermissionGroup =
  | "draft"
  | "preview"
  | "locks"
  | "review"
  | "save"
  | "history"
  | "snapshot"
  | "snapshot-read"
  | "snapshot-create"
  | "snapshot-delete"
  | "metadata"
  | "collect"
  | "routine"
  | "pause";
export interface Permission {
  allowed: boolean;
  reason: string;
}
export interface ResourceClaim {
  readonly owner: string;
  readonly resource: string;
  readonly access: "read" | "write";
  readonly reason: string;
}
export interface PreviewInput {
  readonly draftRevision: number;
  readonly lockRevision: number;
  readonly track_uris: readonly string[];
  readonly locked_positions: Readonly<Record<number, string>>;
}
export interface OrderSummary {
  added_count: number;
  removed_count: number;
  order_changed: boolean;
}
export interface CommitBody {
  readonly baseline_track_uris: readonly string[];
  readonly track_uris: readonly string[];
}
export interface ReviewBinding {
  version: 1;
  scope: string;
  before_order: string | null;
  history: string | null;
}
export interface SnapshotRestoreReview {
  version: 1;
  kind: "snapshot_restore";
  label: string;
  target: PlaylistRef;
  snapshot: { id: number; created_at: string };
  resources: {
    resource: string;
    resource_name: string;
    role: "target";
    domain: "spotify";
    access: "write";
    mutation_type: "track_order" | "track_membership";
    field: null;
  }[];
  steps: JsonRecord[];
  required_bookkeeping: string[];
  review_binding: ReviewBinding;
  current: { track_count: number; track_uris: string[]; loaded_at: string };
  action: {
    snapshot_id: number;
    desired_track_uris: string[];
    summary: OrderSummary;
  };
}
export interface VisibilityReview {
  version: 1;
  kind: "privacy";
  label: string;
  target: PlaylistRef;
  resources: {
    resource: string;
    resource_name: string;
    role: "target";
    domain: "spotify";
    access: "write";
    mutation_type: "track_order" | "track_membership" | "metadata";
    field: "public" | null;
  }[];
  required_bookkeeping: string[];
  review_binding: ReviewBinding;
  current: { public: boolean; loaded_at: string };
  action: { public: boolean };
}
export type ReviewedAction = DeepReadonly<
  {
    id: number;
    revision: number;
    configRevision: number;
    lockRevision: number;
    sourceRevision: number;
    key: string;
    target: PlaylistRef;
    baseline: Occurrence[];
    desired: Occurrence[];
    staging: SourceStagingSnapshot[];
    summary: OrderSummary;
  } & (
    | { kind: "commit"; body: CommitBody }
    | {
        kind: "snapshot_restore";
        snapshotId: number;
        context: SnapshotRestoreReview;
      }
    | { kind: "privacy"; public: boolean; context: VisibilityReview }
  )
>;
export interface CurrentObservation {
  readonly id: number;
  readonly track_uris: readonly string[];
  readonly tracks: readonly DeepReadonly<WorkshopTrack>[];
  readonly loaded_at: string;
  readonly representable: true;
}
export interface TargetResult {
  target_status:
    "saved" | "already_applied" | "not_written" | "partial" | "unknown";
  message: string;
  error_code: string | null;
  requires_refresh: boolean;
  confirmed_batches: number;
  total_batches: number;
  retryable: boolean;
  saved_track_uris?: string[];
  playlist_state?: JsonRecord;
  /** Keep all rich worker facts, including target effects when history fails. */
  facts: JsonRecord;
}
export interface Receipt {
  readonly review: ReviewedAction;
  readonly status:
    "submitting" | "unknown" | "accepted" | "settled" | "refused";
  readonly operationId: number | null;
  readonly operation: Operation | null;
  readonly result: TargetResult | null;
  readonly message: string;
  readonly retryUntil: number;
}
export interface NamedDraft {
  id: number;
  user_id: number;
  playlist_id: string;
  session_name: string;
  track_uris: string[];
  track_count: number;
  created_at: string | null;
  updated_at: string | null;
}
export type ToolId =
  | "algorithms"
  | "locks"
  | "catalog"
  | "external"
  | "snapshots"
  | "details"
  | "sources"
  | "archive"
  | "routines";
export interface WorkshopState {
  readonly phase: "idle" | "loading" | "ready" | "error";
  readonly context: WorkshopContext | null;
  readonly error: Error | null;
  readonly baseline: readonly Occurrence[];
  readonly working: readonly Occurrence[];
  readonly revision: number;
  readonly configRevision: number;
  readonly lockRevision: number;
  readonly sourceRevision: number;
  readonly locks: Readonly<Record<string, boolean>>;
  readonly staging: readonly SourceStagingSnapshot[];
  readonly claims: readonly ResourceClaim[];
  readonly reviewing: boolean;
  readonly review: ReviewedAction | null;
  readonly receipt: Receipt | null;
  readonly observation: CurrentObservation | null;
  readonly checking: boolean;
  readonly notice: string;
  readonly retryUntil: number;
  readonly drafts: readonly NamedDraft[];
  readonly draftsLoaded: boolean;
  readonly draftsBusy: boolean;
  readonly draftsUnknown: boolean;
  readonly draftsError: string;
}
