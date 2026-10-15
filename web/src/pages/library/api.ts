import { spotifyResources } from "../../operations/admissions.ts";
import type { AdmissionRequest } from "../../operations/admissions.ts";
import type { ApiClient, ApiResult } from "../../api/client.ts";
import type { JsonRecord, LibraryContext } from "../../api/contracts.ts";
import { ApiRequestError, ProtocolError } from "../../api/errors.ts";
import type { ShuffleForm } from "./model.ts";
export type Intent =
  | { kind: "shuffle"; form: ShuffleForm }
  | { kind: "undo" }
  | { kind: "privacy"; public: boolean };
export interface Binding {
  version: 1;
  scope: string;
  before_order: string | null;
  history: string | null;
}
export interface ReviewResource {
  resource: string;
  resource_name: string;
  role: "target";
  domain: "spotify";
  access: "write";
  mutation_type: "track_order" | "track_membership" | "metadata";
  field: "public" | null;
}
interface ReviewBase {
  version: 1;
  label: string;
  target: { id: string; name: string };
  resources: ReviewResource[];
  required_bookkeeping: string[];
  review_binding: Binding;
}
export type Review = ReviewBase &
  (
    | {
        kind: "shuffle";
        current: { track_count: number; loaded_at: string };
        action: {
          algorithm: { id: string; name: string };
          params: Record<string, string | number>;
        };
      }
    | {
        kind: "undo";
        current: {
          track_count: number;
          track_uris: string[];
          loaded_at: string;
        };
        action: {
          current_index: number;
          selected_index: number;
          desired_track_uris: string[];
          summary: {
            added_count: number;
            removed_count: number;
            order_changed: boolean;
          };
        };
      }
    | {
        kind: "privacy";
        current: { public: boolean; loaded_at: string };
        action: { public: boolean };
      }
  );
export const record = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const strings = (v: unknown): v is string[] =>
  Array.isArray(v) && v.every((x) => typeof x === "string");
const count = (v: unknown): v is number =>
  Number.isSafeInteger(v) && Number(v) >= 0;
const digest = (v: unknown) =>
  typeof v === "string" && /^[a-f0-9]{64}$/u.test(v);
export async function loadLibrary(
  api: ApiClient,
  refresh = false,
): Promise<LibraryContext> {
  const result = await api.request<LibraryContext>(
    `/api/library${refresh ? "?refresh=1" : ""}`,
    { cache: "no-store" },
  );
  if (result.status !== "completed")
    throw new ProtocolError("Library reads cannot accept operations.");
  const v = result.data;
  if (
    !record(v.sections) ||
    !(["favorites", "playlists", "hidden"] as const).every(
      (k) =>
        Array.isArray(v.sections[k]) &&
        (v.sections[k] as unknown[]).every(
          (row) =>
            record(row) &&
            typeof row.id === "string" &&
            typeof row.name === "string" &&
            count(row.track_count) &&
            (row.image_url === null || typeof row.image_url === "string") &&
            typeof row.spotify_url === "string" &&
            (row.public === null || typeof row.public === "boolean") &&
            ["pinned", "hidden", "can_undo"].every(
              (key) => typeof row[key] === "boolean",
            ),
        ),
    ) ||
    !Array.isArray(v.algorithms) ||
    !record(v.shuffle_defaults) ||
    !record(v.greeting)
  )
    throw new ProtocolError("The Library response is incomplete.");
  return v;
}
export function assertReview(
  value: unknown,
  id: string,
  intent: Intent,
): asserts value is Review {
  if (
    !record(value) ||
    value.version !== 1 ||
    value.kind !== intent.kind ||
    typeof value.label !== "string" ||
    !record(value.target) ||
    value.target.id !== id ||
    typeof value.target.name !== "string" ||
    !Array.isArray(value.resources) ||
    value.resources.length !== 1 ||
    !value.resources.every(
      (r) =>
        record(r) &&
        r.resource === id &&
        typeof r.resource_name === "string" &&
        r.role === "target" &&
        r.domain === "spotify" &&
        r.access === "write" &&
        ["track_order", "track_membership", "metadata"].includes(
          String(r.mutation_type),
        ) &&
        (intent.kind === "privacy"
          ? r.mutation_type === "metadata" && r.field === "public"
          : r.field === null &&
            (intent.kind === "shuffle"
              ? r.mutation_type === "track_order"
              : ["track_order", "track_membership"].includes(
                  String(r.mutation_type),
                ))),
    ) ||
    !strings(value.required_bookkeeping) ||
    JSON.stringify(value.required_bookkeeping) !==
      JSON.stringify(intent.kind === "privacy" ? [] : ["session_history"]) ||
    !record(value.review_binding) ||
    value.review_binding.version !== 1 ||
    !digest(value.review_binding.scope) ||
    !(
      value.review_binding.before_order === null ||
      digest(value.review_binding.before_order)
    ) ||
    !(
      value.review_binding.history === null ||
      digest(value.review_binding.history)
    ) ||
    !record(value.current) ||
    typeof value.current.loaded_at !== "string" ||
    !Number.isFinite(Date.parse(value.current.loaded_at)) ||
    !record(value.action)
  )
    throw new ProtocolError(
      "The live review does not match this playlist and action.",
    );
  const { current, action } = value;
  if (intent.kind === "shuffle") {
    if (
      !count(current.track_count) ||
      !record(action.algorithm) ||
      action.algorithm.id !== intent.form.algorithm ||
      typeof action.algorithm.name !== "string" ||
      !record(action.params) ||
      !Object.values(action.params).every(
        (v) =>
          typeof v === "string" ||
          (typeof v === "number" && Number.isFinite(v)),
      ) ||
      !Object.entries(intent.form)
        .filter(([k]) => k !== "algorithm")
        .every(
          ([k, v]) =>
            String((action.params as Record<string, unknown>)[k]) === v,
        ) ||
      Object.keys(action.params).some((k) => !(k in intent.form))
    )
      throw new ProtocolError(
        "The reviewed shuffle options changed. Review again.",
      );
  } else if (intent.kind === "privacy") {
    if (typeof current.public !== "boolean" || action.public !== intent.public)
      throw new ProtocolError(
        "The visibility review does not match this action.",
      );
  } else if (
    !count(current.track_count) ||
    !strings(current.track_uris) ||
    current.track_count !== current.track_uris.length ||
    !count(action.current_index) ||
    Number(action.current_index) < 1 ||
    action.selected_index !== Number(action.current_index) - 1 ||
    !strings(action.desired_track_uris) ||
    !record(action.summary) ||
    !count(action.summary.added_count) ||
    !count(action.summary.removed_count) ||
    typeof action.summary.order_changed !== "boolean" ||
    !digest(value.review_binding.before_order) ||
    !digest(value.review_binding.history)
  )
    throw new ProtocolError("The saved-version review is incomplete.");
}
export async function loadReview(
  api: ApiClient,
  id: string,
  intent: Intent,
): Promise<Review> {
  const result = await api.request<{ success: true; context: Review }>(
    `/api/playlist/${encodeURIComponent(id)}/live-context`,
    { method: "POST", json: intent },
  );
  if (result.status !== "completed")
    throw new ProtocolError("A live review cannot accept a write.");
  assertReview(result.data.context, id, intent);
  return result.data.context;
}
export function libraryAdmission(
  id: string,
  intent: Intent,
  review: Review,
  key: string,
): AdmissionRequest {
  const binding = {
    reviewed_scope: review.review_binding.scope,
    ...(intent.kind === "undo"
      ? {
          reviewed_before_order: review.review_binding.before_order!,
          reviewed_history: review.review_binding.history!,
        }
      : {}),
  };
  return {
    key,
    feature: "library",
    label: `${review.label} · ${review.target.name}`,
    resources: spotifyResources(id, review.resources),
    expected: {
      kind: intent.kind === "privacy" ? "visibility" : intent.kind,
      target: id,
    },
    path:
      intent.kind === "privacy"
        ? `/api/playlist/${encodeURIComponent(id)}/toggle-visibility`
        : `/api/${intent.kind === "shuffle" ? "shuffle" : "undo"}/${encodeURIComponent(id)}`,
    body:
      intent.kind === "privacy"
        ? { encoding: "json", value: { public: intent.public, ...binding } }
        : {
            encoding: "form",
            value: {
              ...(intent.kind === "shuffle" ? intent.form : {}),
              ...binding,
            },
          },
  };
}
export function sendReviewed(
  api: ApiClient,
  id: string,
  intent: Intent,
  review: Review,
  key: string,
): Promise<ApiResult<never>> {
  const binding = {
    reviewed_scope: review.review_binding.scope,
    ...(intent.kind === "undo"
      ? {
          reviewed_before_order: review.review_binding.before_order!,
          reviewed_history: review.review_binding.history!,
        }
      : {}),
  };
  const headers = { "Idempotency-Key": key };
  if (intent.kind === "privacy")
    return api.request(
      `/api/playlist/${encodeURIComponent(id)}/toggle-visibility`,
      { method: "POST", headers, json: { public: intent.public, ...binding } },
    );
  const body = new FormData();
  if (intent.kind === "shuffle")
    for (const [name, value] of Object.entries(intent.form))
      body.set(name, value);
  for (const [name, value] of Object.entries(binding)) body.set(name, value);
  return api.request(
    `/api/${intent.kind === "shuffle" ? "shuffle" : "undo"}/${encodeURIComponent(id)}`,
    { method: "POST", headers, body },
  );
}
export async function preference(
  api: ApiClient,
  path: string,
  json?: unknown,
): Promise<Record<string, unknown>> {
  const result = await api.request<Record<string, unknown>>(
    `/api/playlist-preferences/${path}`,
    { method: "POST", ...(json === undefined ? {} : { json }) },
  );
  if (result.status !== "completed")
    throw new ProtocolError(
      "Library preferences must finish without a Spotify operation.",
    );
  return result.data;
}
export interface ResultFact {
  name: string;
  status: string;
  verification: string | null;
  count: number | null;
  error: string | null;
}
/** Project display facts only; the complete raw result remains on the retained operation. */
export function resultFacts(result: JsonRecord | null): {
  message: string;
  resources: ResultFact[];
  bookkeeping: ResultFact[];
} {
  const op = result && record(result.operation) ? result.operation : {};
  const project = (value: unknown, book: boolean): ResultFact[] =>
    !Array.isArray(value)
      ? []
      : value.filter(record).map((r) => ({
          name:
            typeof r.resource_name === "string"
              ? r.resource_name
              : typeof r.resource === "string"
                ? r.resource
                : book && r.kind === "session_history"
                  ? "Saved playlist history"
                  : typeof r.kind === "string"
                    ? r.kind.replaceAll("_", " ")
                    : "Playlist",
          status: typeof r.status === "string" ? r.status : "unknown",
          verification:
            typeof r.verification === "string" ? r.verification : null,
          count: count(r.affected_count) ? r.affected_count : null,
          error: typeof r.error_code === "string" ? r.error_code : null,
        }));
  return {
    message:
      typeof result?.message === "string"
        ? result.message
        : "Read the operation details before trying another change.",
    resources: project(op.resources, false),
    bookkeeping: project(op.bookkeeping, true),
  };
}
export type InspectionIntent =
  | { kind: "privacy"; desired_public: boolean | null }
  | { kind: "tracks"; desired_track_uris: string[] | null };
export interface Inspection {
  status: "match" | "mismatch" | "unavailable" | "failed";
  message: string;
  checkedAt: string;
  raw: JsonRecord;
}
/** Observe present state only. This never confirms queue admission, causation or saved history. */
export async function inspectCurrent(
  api: ApiClient,
  id: string,
  intent: InspectionIntent,
): Promise<Inspection> {
  let data: unknown;
  try {
    const result = await api.request<JsonRecord>(
      `/api/playlist/${encodeURIComponent(id)}/inspect-live`,
      { method: "POST", json: intent },
    );
    if (result.status !== "completed")
      throw new ProtocolError("An inspection cannot accept a write.");
    data = result.data;
  } catch (e) {
    if (
      !(e instanceof ApiRequestError) ||
      !record(e.error) ||
      e.error.inspection_only !== true
    )
      throw e;
    data = e.error;
  }
  if (
    !record(data) ||
    data.inspection_only !== true ||
    typeof data.message !== "string" ||
    !record(data.operation) ||
    data.operation.kind !==
      (intent.kind === "privacy" ? "privacy" : "track_inspection") ||
    !Array.isArray(data.operation.resources) ||
    data.operation.resources.length > 1 ||
    data.operation.resources.some((r) => !record(r) || r.resource !== id)
  )
    throw new ProtocolError(
      "The inspection response does not match this playlist.",
    );
  const op = data.operation,
    resource = (op.resources as unknown[])[0];
  let status: Inspection["status"] = "failed";
  if (
    data.success === true &&
    op.aggregate_status === "complete" &&
    record(resource) &&
    resource.status === "not_required"
  ) {
    const same =
      intent.kind === "privacy"
        ? typeof intent.desired_public === "boolean" &&
          record(resource.current_metadata) &&
          resource.current_metadata.public === intent.desired_public &&
          data.public === intent.desired_public
        : strings(resource.current_track_uris) &&
          intent.desired_track_uris !== null &&
          JSON.stringify(resource.current_track_uris) ===
            JSON.stringify(intent.desired_track_uris);
    if (same) status = "match";
  } else if (op.error_code === "intent_unavailable") status = "unavailable";
  else if (
    op.error_code === "intent_not_observed" ||
    op.error_code === "verification_failed"
  )
    status = "mismatch";
  return {
    status,
    message: data.message,
    checkedAt: new Date().toISOString(),
    raw: JSON.parse(JSON.stringify(data)) as JsonRecord,
  };
}
