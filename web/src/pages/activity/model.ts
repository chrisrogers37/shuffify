import type { ApiClient } from "../../api/client.ts";
import type {
  Activity,
  ActivityContext,
  ActivityExecution,
  AvailableExecutionDetail,
  ExecutionCounts,
  ExecutionStep,
  JsonValue,
  JobExecution,
  JobType,
  TriggeredBy,
} from "../../api/contracts.ts";
import { ProtocolError } from "../../api/errors.ts";
export const operations: Record<JobType, string> = {
  raid: "Collect for review",
  shuffle: "Reorder existing tracks",
  raid_and_shuffle: "Collect then reorder",
  raid_and_drip: "Collect then add to playlist",
  drip: "Add collected tracks",
  rotate: "Rotate with archive",
};
export const steps = {
  raid: "Collect",
  shuffle: "Reorder",
  drip: "Add to playlist",
  rotate: "Rotate",
} as const;
export const stepStatus = {
  complete: "Completed",
  partial: "Partly completed",
  unknown: "Could not be verified",
  failed: "Failed",
  restored: "Restoration verified",
  not_started: "Did not start",
  pending: "Not started yet",
} as const;
export const factStatus = {
  complete: "Verified",
  failed: "Failed before a confirmed change",
  unknown: "Changes could not be verified",
  restored: "Restoration verified",
  not_required: "No change needed",
} as const;
export const roles = {
  target: "Target playlist",
  collection: "Linked collection",
  archive: "Archive",
} as const;
export const bookkeeping = {
  pending_stage: "Review inbox staging",
  pending_promotion: "Review inbox promotion",
  pending_restore: "Review inbox restoration",
  execution_record: "Run history",
} as const;
export const bookkeepingStatus = {
  complete: "Verified",
  failed: "Failed; no change confirmed",
  unknown: "Change could not be verified",
  restored: "Restoration verified",
  not_required: "No change needed",
} as const;
export const restoreStatus = {
  complete: "Restoration verified",
  failed: "Restoration failed",
  unknown: "Restoration could not be verified",
  restored: "Restoration verified",
  not_required: "No restoration needed",
} as const;
export const recordStatus = {
  complete: "Saved",
  failed: "Save failed",
  unknown: "Save could not be verified",
  restored: "Restoration verified",
  not_required: "No save needed",
} as const;
const legacy: Record<string, string> = {
  success: "Completed",
  failed: "Run failed",
  running: "In progress",
  skipped: "Skipped",
  failed_rolled_back: "Run failed; rollback reported",
  partial: "Partly completed",
  unknown: "Needs review — result could not be verified",
};
const countLabels: Record<keyof ExecutionCounts, readonly [string, string]> = {
  tracks_collected: ["collected for review", "No new tracks collected"],
  tracks_moved_to_target: ["added to target", "No tracks added to target"],
  tracks_removed_from_collection: [
    "removed from collection",
    "No tracks removed from collection",
  ],
  tracks_reordered: ["positions changed", "Order unchanged"],
  tracks_archived: ["moved to archive", "No tracks moved to archive"],
  tracks_returned: ["returned to target", "No tracks returned to target"],
  archive_overlaps_removed: [
    "overlaps removed from archive",
    "No archive overlaps removed",
  ],
  tracks_removed_from_target: [
    "removed from target",
    "No tracks removed from target",
  ],
  tracks_removed_from_archive: [
    "removed from archive",
    "No tracks removed from archive",
  ],
};
export function exactDetail(
  record: Pick<JobExecution, "id" | "result_detail">,
): AvailableExecutionDetail | null {
  const detail = record.result_detail;
  return detail.detail_status === "available" &&
    detail.execution_id === record.id
    ? detail
    : null;
}
export function runCategory(
  record: ActivityExecution,
): "unavailable" | "running" | "complete" | "partial" | "unknown" {
  const detail = exactDetail(record);
  return !detail
    ? "unavailable"
    : detail.run_state === "running"
      ? "running"
      : detail.outcome;
}
export function runOperation(record: ActivityExecution): JobType | "unknown" {
  return exactDetail(record)?.operation ?? record.job_type;
}
export function runTitle(record: ActivityExecution): string {
  const detail = exactDetail(record);
  return detail
    ? `${operations[detail.operation]} — ${detail.run_state === "running" ? "In progress" : detail.outcome === "complete" ? "Completed" : detail.outcome === "partial" ? "Partly completed" : "Needs review — result could not be verified"}`
    : Object.hasOwn(legacy, record.status)
      ? legacy[record.status]!
      : "Run status unavailable";
}
export function originLabel(origin: TriggeredBy): string {
  return origin === "manual"
    ? "Manual action"
    : origin === "scheduler"
      ? "Scheduled run"
      : "Origin unavailable";
}
export function eventDescription(event: Activity): string {
  const description = event.description || displayType(event.activity_type),
    split = description.indexOf(":");
  if (
    event.triggered_by === "manual" &&
    description.startsWith("Manual ") &&
    split >= 0
  ) {
    const key = description.slice(7, split);
    if (Object.hasOwn(operations, key))
      return `Manual ${operations[key as JobType]}:${description.slice(split + 1)}`;
  }
  return description;
}
export function displayType(type: string): string {
  return type
    .replaceAll("_", " ")
    .replace(/\b\w/gu, (letter) => letter.toUpperCase());
}
export function eventTypes(events: Activity[]): string[] {
  return [
    ...new Set(events.map((event) => event.activity_type).filter(Boolean)),
  ].sort();
}
export function filterRuns(
  runs: ActivityExecution[],
  operation: string,
  category: string,
): ActivityExecution[] {
  return runs.filter(
    (run) =>
      (operation === "all" || runOperation(run) === operation) &&
      (category === "all" || runCategory(run) === category),
  );
}
export function filterEvents(events: Activity[], filter: string): Activity[] {
  return events.filter(
    (event) =>
      filter === "all" ||
      (filter === "manual"
        ? event.triggered_by === "manual"
        : filter === `type:${event.activity_type}`),
  );
}
export function countLines(step: ExecutionStep): string[] {
  if (["failed", "not_started", "pending"].includes(step.status)) return [];
  return (Object.keys(countLabels) as (keyof ExecutionCounts)[]).flatMap(
    (key) => {
      const value = step.counts?.[key];
      return value === undefined
        ? []
        : [
            value === 0
              ? countLabels[key][1]
              : `${value} ${countLabels[key][0]}`,
          ];
    },
  );
}
export function dateText(value: string | null, utc = false): string {
  if (!value || Number.isNaN(Date.parse(value))) return "Time unavailable";
  return (
    new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
      ...(utc ? { timeZone: "UTC" } : {}),
    }).format(new Date(value)) + (utc ? " UTC" : "")
  );
}
export function routineHref(id: number | null): string | null {
  return id === null ? null : `/routines?routine_id=${id}&view=history`;
}
export function playlistHref(id: string | null): string | null {
  return id ? `/workshop/${encodeURIComponent(id)}` : null;
}
const record = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);
const text = (v: unknown) => typeof v === "string";
const optionalText = (v: unknown) => v === undefined || text(v);
const nullableText = (v: unknown) => v === null || text(v);
const count = (v: unknown) => Number.isSafeInteger(v) && Number(v) >= 0;
const id = (v: unknown) => count(v) && Number(v) > 0;
const origin = (v: unknown) =>
  v === null || v === "manual" || v === "scheduler";
const choice = (v: unknown, values: object) =>
  typeof v === "string" && Object.hasOwn(values, v);
const json = (v: unknown): v is JsonValue =>
  v === null ||
  text(v) ||
  typeof v === "boolean" ||
  (typeof v === "number" && Number.isFinite(v)) ||
  (Array.isArray(v) && v.every(json)) ||
  (record(v) && Object.values(v).every(json));
const counts = (v: unknown) =>
  v === undefined ||
  (record(v) &&
    Object.entries(v).every(
      ([key, value]) => Object.hasOwn(countLabels, key) && count(value),
    ));
function detail(v: unknown): boolean {
  if (!record(v)) return false;
  if (v.detail_status === "unavailable")
    return v.version === null && v.steps === null;
  if (
    v.detail_status !== "available" ||
    v.version !== 1 ||
    !id(v.execution_id) ||
    !choice(v.operation, operations) ||
    !["complete", "partial", "unknown"].includes(String(v.outcome)) ||
    !(
      v.run_state === undefined ||
      v.run_state === "running" ||
      v.run_state === "finished"
    ) ||
    !(
      v.execution_record === undefined ||
      v.execution_record === "complete" ||
      v.execution_record === "unknown"
    ) ||
    !(
      v.resource_ids === undefined ||
      (record(v.resource_ids) &&
        Object.entries(v.resource_ids).every(
          ([key, value]) => Object.hasOwn(roles, key) && text(value),
        ))
    ) ||
    !Array.isArray(v.steps) ||
    v.steps.length < 1 ||
    v.steps.length > 4
  )
    return false;
  return v.steps.every(
    (step) =>
      record(step) &&
      choice(step.step, steps) &&
      choice(step.status, stepStatus) &&
      counts(step.counts) &&
      Array.isArray(step.resources) &&
      step.resources.length <= 12 &&
      step.resources.every(
        (fact) =>
          record(fact) &&
          text(fact.resource) &&
          choice(fact.status, factStatus) &&
          (fact.phase === undefined ||
            fact.phase === "archive_purge" ||
            choice(fact.phase, steps)) &&
          (fact.kind === undefined || choice(fact.kind, roles)) &&
          (fact.mutation_type === undefined ||
            fact.mutation_type === "track_order" ||
            fact.mutation_type === "track_membership") &&
          (fact.affected_count === undefined || count(fact.affected_count)) &&
          typeof fact.dispatched === "boolean",
      ) &&
      Array.isArray(step.bookkeeping) &&
      step.bookkeeping.length <= 12 &&
      step.bookkeeping.every(
        (fact) =>
          record(fact) &&
          choice(fact.kind, bookkeeping) &&
          choice(fact.status, factStatus) &&
          optionalText(fact.resource) &&
          (fact.affected_count === undefined || count(fact.affected_count)),
      ),
  );
}
export function parseActivityContext(value: unknown): ActivityContext {
  if (
    !record(value) ||
    value.success !== true ||
    (value.message !== undefined && !text(value.message)) ||
    (value.category !== undefined && value.category !== "success") ||
    !record(value.stats) ||
    ![
      "total_shuffles",
      "total_scheduled_runs",
      "total_snapshots",
      "active_schedule_count",
    ].every((key) =>
      count(value.stats && record(value.stats) ? value.stats[key] : undefined),
    ) ||
    !Array.isArray(value.activities) ||
    value.activities.length > 100 ||
    !value.activities.every(
      (event) =>
        record(event) &&
        id(event.id) &&
        id(event.user_id) &&
        text(event.activity_type) &&
        text(event.description) &&
        nullableText(event.playlist_id) &&
        nullableText(event.playlist_name) &&
        (event.metadata === null ||
          (record(event.metadata) && json(event.metadata))) &&
        nullableText(event.created_at) &&
        origin(event.triggered_by),
    ) ||
    !Array.isArray(value.executions) ||
    value.executions.length > 20 ||
    !value.executions.every(
      (run) =>
        record(run) &&
        id(run.id) &&
        (run.schedule_id === null || id(run.schedule_id)) &&
        nullableText(run.started_at) &&
        nullableText(run.completed_at) &&
        text(run.status) &&
        (run.tracks_added === null || count(run.tracks_added)) &&
        (run.tracks_total === null || count(run.tracks_total)) &&
        nullableText(run.error_message) &&
        nullableText(run.schedule_name) &&
        (run.job_type === "unknown" || choice(run.job_type, operations)) &&
        origin(run.triggered_by) &&
        detail(run.result_detail),
    )
  )
    throw new ProtocolError(
      "The activity response is incomplete or exceeds its history bounds.",
      200,
    );
  return value as unknown as ActivityContext;
}
export async function loadActivity(api: ApiClient): Promise<ActivityContext> {
  const result = await api.request<unknown>("/api/activity");
  if (result.status !== "completed")
    throw new ProtocolError("Activity reads cannot accept operations.", 202);
  return parseActivityContext(result.data);
}
