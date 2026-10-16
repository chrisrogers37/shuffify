import type { PlaylistRef } from "../../../api/contracts.ts";
import type {
  DeepReadonly,
  Occurrence,
  SourceStagingSnapshot,
} from "./types.ts";

export type SourceContext = DeepReadonly<{
  target: PlaylistRef;
  raid: PlaylistRef | null;
  archive: (PlaylistRef & { baseline_track_uris: string[] }) | null;
}>;
export interface SourcePrepareInput {
  readonly actor: string;
  readonly target: DeepReadonly<PlaylistRef>;
  readonly reviewId: number;
  readonly draftRevision: number;
  readonly configRevision: number;
  readonly lockRevision: number;
  readonly sourceRevision: number;
  readonly baseline: readonly string[];
  readonly desired: readonly string[];
  readonly staging: readonly SourceStagingSnapshot[];
  /** Fresh authoritative identities read by core, never mutable panel selection. */
  readonly sourceContext: SourceContext;
}
interface SourceTaskBase {
  readonly id: string;
  readonly key: string;
  readonly label: string;
  readonly source: DeepReadonly<PlaylistRef> | null;
  readonly dependsOn: readonly string[];
  readonly selectedCount: number;
}
interface SourceTaskBody {
  readonly source_playlist_id: string | null;
  readonly target_track_uris: readonly string[];
}
export type SourceReviewedTask = DeepReadonly<
  SourceTaskBase &
    (
      | {
          kind: "raid_promotion";
          body: SourceTaskBody & { track_ids: readonly number[] };
        }
      | {
          kind: "archive_restore";
          body: SourceTaskBody & {
            source_playlist_id: string;
            track_uris: readonly string[];
          };
        }
      | {
          kind: "archive_append";
          body: SourceTaskBody & {
            source_playlist_id: string;
            track_uris: readonly string[];
            baseline_track_uris: readonly string[];
          };
        }
    )
>;
export interface SourceReviewedWork {
  readonly tasks: readonly SourceReviewedTask[];
}
/** Detached proof for only the exact original target write and reviewed source work.
 * The explicit Save review authorizes the initial frozen chain once. This synchronous
 * hook performs no HTTP itself; the coordinator may schedule that authorized chain.
 * Failure/ambiguity needs explicit Finish/review, never an automatic retry/new key.
 */
export interface SourceTargetConfirmation {
  readonly actor: string;
  readonly target: DeepReadonly<PlaylistRef>;
  readonly reviewId: number;
  readonly operationId: number;
  readonly key: string;
  readonly savedTrackUris: readonly string[];
  readonly sourceReview: SourceReviewedWork;
  readonly staging: readonly SourceStagingSnapshot[];
  readonly sourceRevision: number;
}
export interface SourceRetainedWork {
  readonly id: string;
  /** Presentation only: this never selects an endpoint or permission. */
  readonly kind: string;
  readonly label: string;
  readonly source: DeepReadonly<PlaylistRef> | null;
  readonly operationId: number | null;
  readonly status:
    | "ready"
    | "submitting"
    | "unknown"
    | "accepted"
    | "failed"
    | "complete";
}
/** Only successful manual removal/append emits these events. Named draft loads,
 * reorder/preview, reset and adopting a current observation never imply archival.
 */
export type ManualDraftMutation = DeepReadonly<
  {
    beforeRevision: number;
    afterRevision: number;
  } & (
    | {
        kind: "remove";
        occurrence: Occurrence;
        beforeCount: number;
        afterCount: number;
        baselineCount: number;
      }
    | {
        kind: "append";
        occurrences: readonly Occurrence[];
        afterCounts: Readonly<Record<string, number>>;
        baselineCounts: Readonly<Record<string, number>>;
        origin: string;
      }
  )
>;
