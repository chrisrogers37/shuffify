import type { ApiClient } from "../../api/client.ts";
import type {
  AlgorithmParameter,
  JsonValue,
  SettingsContext,
  SettingsUpdateRequest,
  UserSettings,
} from "../../api/contracts.ts";
import {
  ApiRequestError,
  NetworkError,
  ProtocolError,
  RateLimitError,
} from "../../api/errors.ts";
export interface SettingsDraft {
  default_algorithm: string;
  auto_snapshot_enabled: boolean;
  max_snapshots_per_playlist: string;
  dashboard_show_recent_activity: boolean;
}
export type FieldErrors = Partial<Record<keyof SettingsDraft, string>>;
export interface SettingsProblem {
  message: string;
  fields: FieldErrors;
  retryAfterMs: number | null;
}
export function draftFromSettings(settings: UserSettings): SettingsDraft {
  return {
    default_algorithm: settings.default_algorithm ?? "",
    auto_snapshot_enabled: settings.auto_snapshot_enabled,
    max_snapshots_per_playlist: String(settings.max_snapshots_per_playlist),
    dashboard_show_recent_activity: settings.dashboard_show_recent_activity,
  };
}
export function validateDraft(draft: SettingsDraft): FieldErrors {
  return /^\d+$/u.test(draft.max_snapshots_per_playlist) &&
    Number(draft.max_snapshots_per_playlist) >= 1 &&
    Number(draft.max_snapshots_per_playlist) <= 50
    ? {}
    : { max_snapshots_per_playlist: "Choose a whole number from 1 to 50." };
}
export function updateFromDraft(draft: SettingsDraft): SettingsUpdateRequest {
  if (Object.keys(validateDraft(draft)).length)
    throw new TypeError("Invalid settings draft.");
  // Omitted stored JSON and unavailable preferences remain untouched by the API.
  return {
    default_algorithm: draft.default_algorithm || null,
    auto_snapshot_enabled: draft.auto_snapshot_enabled,
    max_snapshots_per_playlist: Number(draft.max_snapshots_per_playlist),
    dashboard_show_recent_activity: draft.dashboard_show_recent_activity,
  };
}
export function isDirty(draft: SettingsDraft, settings: UserSettings): boolean {
  const saved = draftFromSettings(settings);
  return (Object.keys(saved) as (keyof SettingsDraft)[]).some(
    (key) => draft[key] !== saved[key],
  );
}
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const nullableString = (value: unknown) =>
  value === null || typeof value === "string";
const jsonValue = (value: unknown): value is JsonValue =>
  value === null ||
  typeof value === "string" ||
  typeof value === "boolean" ||
  (typeof value === "number" && Number.isFinite(value)) ||
  (Array.isArray(value) && value.every(jsonValue)) ||
  (record(value) && Object.values(value).every(jsonValue));
function parameter(value: unknown): value is AlgorithmParameter {
  if (!record(value) || typeof value.description !== "string") return false;
  if (value.type === "string")
    return (
      typeof value.default === "string" &&
      Array.isArray(value.options) &&
      value.options.every((item) => typeof item === "string")
    );
  return (
    (value.type === "integer" || value.type === "float") &&
    typeof value.default === "number" &&
    Number.isFinite(value.default) &&
    (value.min === undefined || typeof value.min === "number") &&
    (value.max === undefined || typeof value.max === "number")
  );
}
function assertContext(value: unknown): asserts value is SettingsContext {
  if (
    !record(value) ||
    value.success !== true ||
    (value.message !== undefined && typeof value.message !== "string") ||
    (value.category !== undefined && value.category !== "success") ||
    !record(value.settings) ||
    !Array.isArray(value.algorithm_options) ||
    !value.algorithm_options.every(
      (option) =>
        record(option) &&
        typeof option.value === "string" &&
        typeof option.label === "string",
    ) ||
    !Array.isArray(value.algorithms) ||
    !value.algorithms.every(
      (algorithm) =>
        record(algorithm) &&
        typeof algorithm.name === "string" &&
        typeof algorithm.class_name === "string" &&
        typeof algorithm.description === "string" &&
        record(algorithm.parameters) &&
        Object.values(algorithm.parameters).every(parameter),
    )
  )
    throw new ProtocolError("The settings response is incomplete.", 200);
  const s = value.settings;
  if (
    !Number.isSafeInteger(s.id) ||
    Number(s.id) <= 0 ||
    !Number.isSafeInteger(s.user_id) ||
    Number(s.user_id) <= 0 ||
    !nullableString(s.default_algorithm) ||
    !record(s.default_algorithm_params) ||
    !jsonValue(s.default_algorithm_params) ||
    !["light", "dark", "system"].includes(String(s.theme)) ||
    typeof s.notifications_enabled !== "boolean" ||
    typeof s.auto_snapshot_enabled !== "boolean" ||
    typeof s.dashboard_show_recent_activity !== "boolean" ||
    !Number.isSafeInteger(s.max_snapshots_per_playlist) ||
    Number(s.max_snapshots_per_playlist) < 1 ||
    Number(s.max_snapshots_per_playlist) > 50 ||
    !record(s.extra) ||
    !jsonValue(s.extra) ||
    !nullableString(s.created_at) ||
    !nullableString(s.updated_at)
  )
    throw new ProtocolError("The settings response is incomplete.", 200);
}
export async function loadSettings(
  api: ApiClient,
  signal?: AbortSignal,
): Promise<SettingsContext> {
  const result = await api.request<unknown>(
    "/api/settings",
    signal ? { signal } : {},
  );
  if (result.status !== "completed")
    throw new ProtocolError("Settings reads cannot accept operations.", 202);
  assertContext(result.data);
  return result.data;
}
export async function saveSettings(
  api: ApiClient,
  draft: SettingsDraft,
): Promise<SettingsContext> {
  const result = await api.request<unknown>("/api/settings", {
    method: "POST",
    json: updateFromDraft(draft),
  });
  if (result.status !== "completed")
    throw new ProtocolError(
      "The settings save returned an unexpected operation. Check your settings before trying again.",
      202,
    );
  assertContext(result.data);
  return result.data;
}
export function settingsProblem(error: unknown): SettingsProblem {
  const fields: FieldErrors = {};
  if (error instanceof ApiRequestError) {
    for (const item of error.error.field_errors ?? []) {
      const key = item.path.replace(/^body\./u, "");
      if (
        [
          "default_algorithm",
          "auto_snapshot_enabled",
          "max_snapshots_per_playlist",
          "dashboard_show_recent_activity",
        ].includes(key)
      )
        fields[key as keyof SettingsDraft] = item.message;
    }
    return {
      message: error.message,
      fields,
      retryAfterMs:
        error instanceof RateLimitError ? (error.retryAfterMs ?? 1000) : null,
    };
  }
  return {
    message:
      error instanceof NetworkError || error instanceof ProtocolError
        ? "Could not confirm your settings were saved. Check your connection and try again."
        : "Your settings could not be saved. Please try again.",
    fields,
    retryAfterMs: null,
  };
}
