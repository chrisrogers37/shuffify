import type { ApiClient, ApiResult } from "../../api/client.ts";
import type { JsonRecord, RoutinesContext } from "../../api/contracts.ts";
import { ProtocolError } from "../../api/errors.ts";
import {
  activationBody,
  digest,
  normalizeRow,
  parseGuided,
  parseHistory,
  parseRoutines,
  parseSetup,
  playlistPath,
  record,
} from "./model.ts";
import type {
  ActivationReply,
  ReviewRequest,
  RoutineReview,
  RunReview,
  SetupChange,
  SetupReview,
} from "./model.ts";
export function createRoutineClient(api: ApiClient) {
  async function completed<T>(
    path: string,
    init: Parameters<ApiClient["request"]>[1] = {},
  ): Promise<T> {
    const reply = await api.request<T>(path, { cache: "no-store", ...init });
    if (reply.status !== "completed")
      throw new ProtocolError(
        "This request requires a completed local response.",
      );
    return reply.data;
  }
  const base = (target: string) => "/api" + playlistPath(target);
  return {
    list: async () =>
      parseRoutines(await completed<RoutinesContext>("/api/routines")),
    guided: async (target: string, query: string) => {
      const value = parseGuided(
        await completed(base(target) + "/routines/context" + query),
      );
      if (value.target.id !== target)
        throw new ProtocolError("The response names a different playlist.");
      return value;
    },
    state: async (id: number, enabled: boolean, revision: string) =>
      normalizeRow(
        await completed(`/api/routines/${id}/state`, {
          method: "PUT",
          json: { desired_enabled: enabled, expected_revision: revision },
        }),
      ),
    remove: (id: number) =>
      completed(`/api/schedules/${id}`, { method: "DELETE" }),
    history: async (id: number) =>
      parseHistory(await completed(`/api/schedules/${id}/history`)),
    latest: () =>
      completed<{ success: true; results: Record<string, unknown> }>(
        "/api/schedules/latest-results",
      ),
    runReview: async (id: number): Promise<RunReview> => {
      const value = await completed<{ success: true; context: RunReview }>(
        `/api/schedules/${id}/run-context`,
      );
      if (
        !record(value.context) ||
        !record(value.context.review_binding) ||
        !digest(value.context.review_binding.scope) ||
        !Array.isArray(value.context.resources)
      )
        throw new ProtocolError("This run cannot be reviewed.");
      return value.context;
    },
    run: (id: number, scope: string, key: string) =>
      api.request(`/api/schedules/${id}/run`, {
        method: "POST",
        headers: { "Idempotency-Key": key },
        json: { reviewed_scope: scope },
      }),
    review: async (
      target: string,
      body: ReviewRequest,
    ): Promise<RoutineReview> => {
      const value = await completed<RoutineReview>(
        base(target) + "/routines/review",
        { method: "POST", json: body },
      );
      if (
        !record(value.definition) ||
        !record(value.intent) ||
        !digest(value.dependency_revision) ||
        !Array.isArray(value.candidate_schedule_ids)
      )
        throw new ProtocolError("The review response is incomplete.");
      return value;
    },
    activate: async (
      target: string,
      body: ReturnType<typeof activationBody>,
    ): Promise<ActivationReply> => {
      const value = await completed<ActivationReply>(
        base(target) + "/routines/activate",
        { method: "POST", json: body },
      );
      if (
        value.accepted !== true ||
        !Number.isSafeInteger(value.schedule_id) ||
        (body.schedule_id && value.schedule_id !== body.schedule_id) ||
        typeof value.replayed !== "boolean" ||
        !value.accepted_at ||
        (value.schedule !== null && value.schedule.id !== value.schedule_id)
      )
        throw new ProtocolError(
          "Activation may already be saved. Check the same request.",
        );
      return value;
    },
    setup: async (target: string, returnTarget: string) => {
      const value = await completed<{ success: true; context: unknown }>(
        base(target) + "/setup/context?return_target=" + returnTarget,
      );
      const context = parseSetup(value.context);
      if (context.target.id !== target)
        throw new ProtocolError("The response names a different playlist.");
      return context;
    },
    owned: (target: string, refresh = false) =>
      completed<{
        success: true;
        playlists: {
          id: string;
          name: string;
          owned: boolean;
          image_url: string | null;
        }[];
      }>(base(target) + "/setup/playlists" + (refresh ? "?refresh=1" : "")),
    impact: (
      target: string,
      body: {
        configuration_revision: string;
        changes: SetupChange[];
        section_digests: Record<string, string>;
      },
    ) =>
      completed<SetupReview>(base(target) + "/setup/impact", {
        method: "POST",
        json: body,
      }),
    save: (
      target: string,
      body: {
        configuration_revision: string;
        changes: SetupChange[];
        acknowledgment: JsonRecord;
        section_digests: Record<string, string>;
      },
    ) =>
      completed<{ success: true; saved: true; context: unknown }>(
        base(target) + "/setup/settings",
        { method: "PUT", json: body },
      ),
    create: (
      target: string,
      purpose: "collection" | "archive",
      name: string,
      key: string,
    ): Promise<ApiResult<JsonRecord>> =>
      api.request(base(target) + "/setup/spotify-playlists", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        json: { purpose, name },
      }),
  };
}
