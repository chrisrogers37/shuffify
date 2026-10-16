import type {
  Algorithm,
  JobExecution,
  JobType,
  JsonRecord,
  PlaylistRef,
  RoutineDefinition,
  RoutineRow,
  RoutinesContext,
} from "../../api/contracts.ts";
import { ApiRequestError, ProtocolError } from "../../api/errors.ts";
export const labels: Record<JobType, string> = {
  raid: "Collect",
  shuffle: "Shuffle",
  raid_and_shuffle: "Collect and shuffle",
  raid_and_drip: "Collect and add",
  drip: "Add collected tracks",
  rotate: "Rotate",
};
export const operationLabel = (kind: string) =>
  labels[kind as JobType] ?? "Operation unavailable";
export const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
export const id = (value: unknown): value is number =>
  Number.isSafeInteger(value) && Number(value) > 0;
export const digest = (value: unknown): value is string =>
  typeof value === "string" && /^[a-f0-9]{64}$/u.test(value);
export const playlistPath = (target: string) =>
  `/playlist/${encodeURIComponent(target)}`;
export function parseNavigation(search: string) {
  const query = new URLSearchParams(search),
    raw = query.get("schedule_id"),
    origin = query.get("origin") ?? query.get("from") ?? "playlist";
  if (
    !["playlist", "routines"].includes(origin) ||
    (raw !== null && (!/^[0-9]{1,18}$/u.test(raw) || !id(Number(raw))))
  )
    throw new TypeError("This routine link is unavailable.");
  return {
    scheduleId: raw === null ? null : Number(raw),
    origin: origin as "playlist" | "routines",
  };
}
export function normalizeRow(value: unknown): RoutineRow {
  if (
    !record(value) ||
    !id(value.schedule_id) ||
    !record(value.schedule) ||
    value.schedule.id !== value.schedule_id ||
    !record(value.definition) ||
    value.definition.target_playlist_id !== value.schedule.target_playlist_id ||
    typeof value.schedule_revision !== "string" ||
    !record(value.timing) ||
    !record(value.registration)
  )
    throw new ProtocolError("The exact routine response is incomplete.");
  const states: Record<string, RoutineRow["state"]> = {
    Active: "active",
    Paused: "paused",
    "Saved—registration needs attention": "needs_attention",
    active: "active",
    paused: "paused",
    needs_attention: "needs_attention",
  };
  const state =
    typeof value.state === "string" ? states[value.state] : undefined;
  if (!state) throw new ProtocolError("The routine state is unavailable.");
  return {
    ...value,
    state,
    state_label:
      state === "active"
        ? "Active"
        : state === "paused"
          ? "Paused"
          : "Saved—registration needs attention",
  } as unknown as RoutineRow;
}
export function parseRoutines(value: unknown): RoutinesContext {
  if (
    !record(value) ||
    value.success !== true ||
    !Array.isArray(value.routines) ||
    !Array.isArray(value.playlists)
  )
    throw new ProtocolError("The routines response is incomplete.");
  const rows = value.routines.map(normalizeRow);
  if (new Set(rows.map((row) => row.schedule_id)).size !== rows.length)
    throw new ProtocolError("Routine identities are duplicated.");
  return { ...value, routines: rows } as RoutinesContext;
}
export interface SetupContext {
  version: 1;
  target: PlaylistRef;
  sources: {
    id: number;
    type: string;
    playlist_id: string | null;
    search_query: string | null;
    name: string;
    raid_count: number;
  }[];
  routine_only_sources: { playlist_id: string; routine_ids: number[] }[];
  collection: {
    playlist_id: string;
    name: string;
    drip_count: number;
    drip_enabled: boolean;
  } | null;
  archive: {
    playlist_id: string;
    name: string;
    auto_archive_on_remove: boolean;
  } | null;
  routines: {
    id: number;
    operation: string;
    is_enabled: boolean;
    timing_text: string;
  }[];
  limits: JsonRecord;
  configuration_revision: string;
  section_digests: Record<
    "sources" | "collection" | "archive" | "routines",
    string
  >;
  state_digest: string;
}
export interface GuidedContext {
  success: true;
  target: PlaylistRef;
  prerequisites: SetupContext;
  dependency_revision: string;
  routines: RoutineRow[];
  algorithms: Algorithm[];
  capabilities: Partial<
    Record<JobType, { available: boolean; reason: string | null }>
  >;
  requested_schedule_id: number | null;
  origin: "playlist" | "routines";
}
export interface RoutineDraft {
  job_type: JobType;
  algorithm_name: string | null;
  algorithm_params: JsonRecord;
  timing: JsonRecord;
}
export interface ReviewRequest {
  request_key: string;
  definition: Record<string, unknown>;
  schedule_id?: number | null;
  expected_revision?: string | null;
  desired_enabled?: boolean;
  create_another: boolean;
}
export interface RoutineReview {
  success: true;
  definition: RoutineDefinition;
  intent: JsonRecord;
  dependency_revision: string;
  schedule_revision: string | null;
  candidate_schedule_ids: number[];
  warnings: string[];
  resource_scope: JsonRecord;
}
export interface ActivationReply {
  success: true;
  accepted: true;
  replayed: boolean;
  schedule_id: number;
  accepted_at: string;
  dependency_revision: string;
  state: string;
  schedule: RoutineRow["schedule"] | null;
  schedule_revision?: string;
  registration?: RoutineRow["registration"];
}
export type SetupChange = {
  op: string;
  [key: string]: string | number | boolean;
};
export interface SetupReview {
  success: true;
  configuration_revision: string;
  changes: unknown[];
  affected_routines: unknown[];
  unaffected_routines: unknown[];
  notes: string[];
  expected_state_digest: string;
  acknowledgment: JsonRecord;
}
export interface RunReview {
  review_binding: { scope: string };
  resources: {
    resource: string;
    resource_name: string | null;
    role: string;
    access: string;
    domain: string;
  }[];
  [name: string]: unknown;
}
export function parseSetup(value: unknown): SetupContext {
  if (
    !record(value) ||
    value.version !== 1 ||
    !record(value.target) ||
    typeof value.target.id !== "string" ||
    typeof value.target.name !== "string" ||
    !Array.isArray(value.sources) ||
    !Array.isArray(value.routines) ||
    !Array.isArray(value.routine_only_sources) ||
    !digest(value.configuration_revision) ||
    !record(value.section_digests) ||
    !["sources", "collection", "archive", "routines"].every((name) =>
      digest((value.section_digests as Record<string, unknown>)[name]),
    )
  )
    throw new ProtocolError("The playlist settings response is incomplete.");
  return structuredClone(value) as unknown as SetupContext;
}
export function parseGuided(value: unknown): GuidedContext {
  if (
    !record(value) ||
    value.success !== true ||
    !record(value.target) ||
    !digest(value.dependency_revision) ||
    !Array.isArray(value.routines) ||
    !Array.isArray(value.algorithms) ||
    !record(value.capabilities)
  )
    throw new ProtocolError("The routine setup response is incomplete.");
  return {
    ...value,
    prerequisites: parseSetup(value.prerequisites),
    routines: value.routines.map(normalizeRow),
  } as unknown as GuidedContext;
}
export function activationBody(request: ReviewRequest, review: RoutineReview) {
  if (
    !record(review.definition) ||
    !record(review.intent) ||
    !digest(review.dependency_revision) ||
    review.schedule_revision !== (request.expected_revision ?? null)
  )
    throw new ProtocolError("The review no longer matches this exact routine.");
  if (
    !request.schedule_id &&
    !request.create_another &&
    review.candidate_schedule_ids.length
  )
    throw new ProtocolError(
      "Choose an exact routine or Create another routine, then review again.",
    );
  return {
    ...structuredClone(request),
    definition: structuredClone(review.definition),
    intent: structuredClone(review.intent),
    dependency_revision: review.dependency_revision,
  };
}
function subject(change: SetupChange) {
  if (
    ["link_collection", "replace_collection", "unlink_collection"].includes(
      change.op,
    )
  )
    return "collection";
  if (["pair_archive", "replace_archive", "unpair_archive"].includes(change.op))
    return "archive";
  if (change.op === "set_drip") return "drip";
  if (change.op === "set_auto_archive") return "auto-archive";
  if (change.op === "add_playlist_source")
    return `playlist:${change.playlist_id}`;
  if (change.op === "add_search_source") return `search:${change.search_query}`;
  return `source:${change.source_id ?? change.playlist_id}`;
}
export function stageChange(changes: SetupChange[], change: SetupChange) {
  const previous = changes.find((item) => subject(item) === subject(change));
  return [
    ...changes.filter((item) => subject(item) !== subject(change)),
    change.op === "set_drip" ? { ...previous, ...change } : change,
  ];
}
export function draftFromRow(row: RoutineRow): RoutineDraft {
  return {
    job_type: row.definition.job_type,
    algorithm_name: row.definition.algorithm_name,
    algorithm_params: structuredClone(row.definition.algorithm_params),
    timing: structuredClone(row.timing) as unknown as JsonRecord,
  };
}
export interface Continuation {
  v: 1;
  ownerId: string;
  targetId: string;
  scheduleId: number | null;
  revision: string | null;
  origin: "playlist" | "routines";
  draft: RoutineDraft;
  expiresAt: number;
}
export function makeContinuation(
  ownerId: string,
  targetId: string,
  scheduleId: number | null,
  revision: string | null,
  origin: "playlist" | "routines",
  draft: RoutineDraft,
  now = Date.now(),
): Continuation {
  return {
    v: 1,
    ownerId,
    targetId,
    scheduleId,
    revision,
    origin,
    draft: structuredClone(draft),
    expiresAt: now + 1800000,
  };
}
export function readContinuation(
  raw: string | null,
  owner: string,
  target: string,
  now = Date.now(),
): Continuation | null {
  try {
    if (!raw || raw.length > 8192) return null;
    const value: unknown = JSON.parse(raw);
    if (
      !record(value) ||
      value.v !== 1 ||
      value.ownerId !== owner ||
      value.targetId !== target ||
      !["playlist", "routines"].includes(String(value.origin)) ||
      typeof value.expiresAt !== "number" ||
      value.expiresAt <= now ||
      value.expiresAt > now + 1800000 ||
      !(
        (value.scheduleId === null && value.revision === null) ||
        (id(value.scheduleId) && digest(value.revision))
      ) ||
      !record(value.draft) ||
      !Object.hasOwn(labels, String(value.draft.job_type)) ||
      !record(value.draft.algorithm_params) ||
      !record(value.draft.timing) ||
      Object.keys(value).some(
        (key) =>
          ![
            "v",
            "ownerId",
            "targetId",
            "scheduleId",
            "revision",
            "origin",
            "draft",
            "expiresAt",
          ].includes(key),
      )
    )
      return null;
    return value as unknown as Continuation;
  } catch {
    return null;
  }
}
export function parseHistory(value: unknown): JobExecution[] {
  if (!record(value) || value.success !== true || !Array.isArray(value.history))
    throw new ProtocolError("This routine history could not be loaded.");
  return value.history.map((row) => {
    if (
      !record(row) ||
      !id(row.id) ||
      !record(row.result_detail) ||
      typeof row.result_detail.detail_status !== "string"
    )
      throw new ProtocolError("This run detail is incomplete.");
    return row as unknown as JobExecution;
  });
}

/** A transport/protocol failure cannot prove a mutation was refused. */
export function unconfirmedMutation(problem: unknown): boolean {
  return !(problem instanceof ApiRequestError) || problem.status >= 500;
}
export function listDeepLink(search: string, hash: string): number | null {
  const query = new URLSearchParams(search);
  const raw = query.get("routine_id") ?? /^#routine-([0-9]+)$/u.exec(hash)?.[1];
  if (raw === null || raw === undefined) return null;
  if (!/^[0-9]{1,18}$/u.test(raw) || !id(Number(raw)))
    throw new TypeError("This exact routine link is unavailable.");
  return Number(raw);
}
export function safeAvailableDetail(value: unknown, executionId: number) {
  if (
    !record(value) ||
    value.detail_status !== "available" ||
    value.version !== 1 ||
    value.execution_id !== executionId ||
    !Array.isArray(value.steps) ||
    !value.steps.length ||
    value.steps.length > 4 ||
    !value.steps.every(
      (step) =>
        record(step) &&
        typeof step.step === "string" &&
        typeof step.status === "string" &&
        Array.isArray(step.resources) &&
        step.resources.every(
          (fact) => record(fact) && typeof fact.status === "string",
        ) &&
        Array.isArray(step.bookkeeping) &&
        step.bookkeeping.every(
          (fact) =>
            record(fact) &&
            typeof fact.kind === "string" &&
            typeof fact.status === "string",
        ) &&
        (step.counts === undefined || record(step.counts)),
    )
  )
    return null;
  return value as unknown as import("../../api/contracts.ts").AvailableExecutionDetail;
}
