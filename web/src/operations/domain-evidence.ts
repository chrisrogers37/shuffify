import type { Operation } from "../api/contracts.ts";
const count = (v: unknown): v is number =>
  Number.isSafeInteger(v) && Number(v) >= 0;
const ids = (v: unknown): v is number[] =>
  Array.isArray(v) &&
  v.every((x) => Number.isSafeInteger(x) && x > 0) &&
  new Set(v).size === v.length;
const creators = new Set([
  "pair_create",
  "raid_link_create",
  "setup_playlist_create",
]);
export type UnidentifiedCreatorKind =
  | "pair_create"
  | "raid_link_create"
  | "setup_playlist_create";
export interface UnidentifiedCreationAcknowledgement {
  readonly key: string;
  readonly operationId: number;
  readonly label: string;
  readonly kind: UnidentifiedCreatorKind;
  readonly target: string;
}
/** This is not proof of absence: it permits an explicit orphan acknowledgement only. */
export function unidentifiedCreation(operation: Operation): boolean {
  const r = operation.result;
  return (
    creators.has(operation.kind) &&
    operation.state === "needs_attention" &&
    operation.targets.length === 1 &&
    r?.success === false &&
    r.outcome === "unknown" &&
    r.creation_unknown === true &&
    r.association_status === "not_started" &&
    (r.playlist === null || r.playlist === undefined) &&
    (r.pair === null || r.pair === undefined) &&
    (r.raid_link === null || r.raid_link === undefined) &&
    r.privacy_confirmed !== true &&
    (r.created_playlist_id === null || r.created_playlist_id === undefined) &&
    (r.operation === null || r.operation === undefined)
  );
}
/** Only positive authored domain facts settle these source commands. null means unrelated. */
export function sourceSettlement(operation: Operation): boolean | null {
  const r = operation.result;
  const counters: Record<string, string> = {
    workshop_finalize_raids: "promoted_count",
    workshop_archive_append: "archived_count",
    workshop_finalize_restore: "restored_count",
  };
  if (operation.kind === "pending_dismiss") {
    if (
      !r ||
      !["succeeded", "failed", "rolled_back"].includes(operation.state) ||
      !count(r.source_removed_count)
    )
      return false;
    const confirmedDatabase =
      r.database_status === "complete" &&
      ids(r.dismissed_ids) &&
      count(r.dismissed_count) &&
      r.dismissed_count === r.dismissed_ids.length &&
      ids(r.changed_ids) &&
      r.changed_ids.every((id) => (r.dismissed_ids as number[]).includes(id));
    const noDatabase =
      r.database_status === "not_started" &&
      (r.dismissed_ids === null || r.dismissed_ids === undefined) &&
      (r.dismissed_count === null || r.dismissed_count === 0) &&
      (r.changed_ids === null || r.changed_ids === undefined);
    if (operation.state === "succeeded")
      return (
        r.success === true &&
        confirmedDatabase &&
        ["complete", "not_required"].includes(String(r.source_status))
      );
    return (
      (confirmedDatabase || noDatabase) &&
      r.success === false &&
      r.source_removed_count === 0 &&
      (operation.state === "rolled_back"
        ? r.source_status === "restored"
        : ["failed", "not_started", "not_required"].includes(
            String(r.source_status),
          ))
    );
  }
  const counter = counters[operation.kind];
  if (!counter) return null;
  if (!r || !count(r.affected_count) || !count(r[counter])) return false;
  if (operation.state === "succeeded")
    return (
      r.success === true &&
      ["complete", "not_required"].includes(String(r.status)) &&
      (operation.kind !== "workshop_finalize_raids" ||
        (r.database_status === "complete" &&
          ["complete", "not_required"].includes(String(r.source_status))))
    );
  if (
    !["failed", "rolled_back"].includes(operation.state) ||
    r.success !== false ||
    r.status !== "failed" ||
    r.affected_count !== 0 ||
    r[counter] !== 0
  )
    return false;
  if (operation.kind === "workshop_finalize_raids") {
    if (operation.state === "rolled_back")
      return (
        ["restored", "not_required"].includes(String(r.source_status)) &&
        ["not_started", "complete"].includes(String(r.database_status))
      );
    return (
      ["not_started", "failed", "not_required"].includes(
        String(r.source_status),
      ) &&
      ["pending", "not_started", "complete"].includes(String(r.database_status))
    );
  }
  return ![r.source_status, r.database_status].some(
    (x) =>
      x !== undefined &&
      ![
        "restored",
        "not_required",
        "not_started",
        "failed",
        "complete",
      ].includes(String(x)),
  );
}
