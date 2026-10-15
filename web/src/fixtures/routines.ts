import initial from "../../fixtures/routines/initial.json" with { type: "json" };
import activity from "../../fixtures/activity/initial.json" with { type: "json" };
import type { Operation, RoutineDefinition } from "../api/contracts.ts";
import { parseRoutines, parseSetup } from "../pages/routines/model.ts";
export const routinesScenarios = [
  "routines-happy",
  "routines-empty",
  "routines-failure",
  "routines-partial",
  "routines-attention",
  "routines-setup-empty",
  "routines-creation-unknown",
  "routines-creation-admission-unknown",
  "routines-activation-unknown",
  "routines-conflict",
] as const;
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
const error = (message: string, code = "fixture_error") =>
  json(
    {
      success: false,
      category: "error",
      message,
      error_code: code,
      retryable: false,
    },
    409,
  );
export function createRoutinesFixtures(scenario: string) {
  const seed = structuredClone(initial),
    list = parseRoutines(seed.list),
    setup = parseSetup(seed.setup);
  let reads = 0,
    nextId = 1000,
    created = 0,
    lostActivation = false;
  const operations = new Map<
      number,
      {
        operation: Operation;
        polls: number;
        creation: boolean;
        unknown: boolean;
        name: string;
        playlistId: string;
      }
    >(),
    receipts = new Map<string, unknown>(),
    admissionReceipts = new Map<
      string,
      { success: true; operation_id: number; operation: Operation }
    >();
  const owned = [
    { id: "choose1", name: "Evening collection", owned: true, image_url: null },
    { id: "choose2", name: "Evening collection", owned: true, image_url: null },
  ];
  if (scenario === "routines-empty") list.routines = [];
  if (["routines-partial", "routines-attention"].includes(scenario)) {
    const kind =
      scenario === "routines-partial" ? "raid_and_shuffle" : "rotate";
    list.routines[0]!.schedule.job_type = kind;
    list.routines[0]!.definition.job_type = kind;
  }
  if (
    [
      "routines-setup-empty",
      "routines-creation-unknown",
      "routines-creation-admission-unknown",
    ].includes(scenario)
  ) {
    setup.collection = null as unknown as typeof setup.collection;
    setup.archive = null as unknown as typeof setup.archive;
  }
  function admit(target: string, kind: string, creation = false, name = "") {
    const id = ++nextId,
      operation: Operation = {
        id,
        kind,
        state: "queued",
        targets: [target],
        started_at: null,
        completed_at: null,
        progress: {},
        result: null,
        recovery: { available: false, action: null, reason: null },
      };
    operations.set(id, {
      operation,
      polls: 0,
      creation,
      unknown:
        creation &&
        created === 1 &&
        scenario.includes("creation-") &&
        scenario.includes("unknown"),
      name,
      playlistId: `created${created}`,
    });
    return json({ success: true, operation_id: id, operation }, 202);
  }
  return async (
    path: string,
    method: string,
    init?: RequestInit,
  ): Promise<Response | null> => {
    const url = new URL(path, "https://fixture.invalid"),
      route = url.pathname,
      body = typeof init?.body === "string"
        ? (JSON.parse(init.body) as Record<string, unknown>)
        : {};
    const requestKey = new Headers(init?.headers).get("Idempotency-Key");
    if (method === "POST" && requestKey && admissionReceipts.has(requestKey))
      return json(admissionReceipts.get(requestKey), 202);
    if (route === "/api/routines") {
      if (scenario === "routines-failure" && reads++ === 0)
        return json(
          {
            success: false,
            error_code: "unavailable",
            category: "error",
            message: "Routines are temporarily unavailable.",
            retryable: true,
          },
          503,
        );
      return json(list);
    }
    if (route === "/api/schedules/latest-results")
      return json({
        success: true,
        results: Object.fromEntries(
          list.routines.map((row) => [row.schedule_id, null]),
        ),
      });
    if (route.endsWith("/routines/context")) {
      const requested = url.searchParams.get("schedule_id"),
        id = requested ? Number(requested) : null;
      if (id && !list.routines.some((row) => row.schedule_id === id))
        return json(
          {
            success: false,
            category: "error",
            error_code: "not_found",
            message: "This exact routine is unavailable.",
          },
          404,
        );
      return json({
        ...seed.guided,
        routines: list.routines,
        prerequisites: setup,
        dependency_revision: setup.configuration_revision,
        requested_schedule_id: id,
        origin: url.searchParams.get("origin") ?? "playlist",
      });
    }
    const state = /^\/api\/routines\/(\d+)\/state$/u.exec(route);
    if (state) {
      const row = list.routines.find(
        (value) => value.schedule_id === Number(state[1]),
      );
      if (!row || body.expected_revision !== row.schedule_revision)
        return error(
          "This routine changed. Review it again.",
          "routine_changed",
        );
      row.schedule.is_enabled = body.desired_enabled === true;
      row.state = row.schedule.is_enabled ? "active" : "paused";
      row.state_label = row.schedule.is_enabled ? "Active" : "Paused";
      row.schedule_revision = "e".repeat(64);
      return json({ success: true, ...row });
    }
    const schedule =
      /^\/api\/schedules\/(\d+)(?:\/(history|run-context|run))?$/u.exec(route);
    if (schedule) {
      const id = Number(schedule[1]),
        row = list.routines.find((value) => value.schedule_id === id);
      if (!row) return error("This exact routine is unavailable.");
      if (schedule[2] === "history")
        return json({
          success: true,
          history: activity.executions
            .slice(0, 2)
            .map((value) => ({ ...value, schedule_id: id })),
        });
      if (schedule[2] === "run-context")
        return json({
          success: true,
          context: {
            kind: "schedule",
            review_binding: { scope: "a".repeat(64) },
            resources: [
              {
                resource: row.schedule.target_playlist_id,
                resource_name: row.schedule.target_playlist_name,
                access: "write",
                domain: "spotify",
                role: "target",
              },
            ],
          },
        });
      if (schedule[2] === "run") {
        const response = admit(row.schedule.target_playlist_id, "schedule_run");
        if (requestKey)
          admissionReceipts.set(requestKey, await response.clone().json());
        return response;
      }
      if (method === "DELETE") {
        list.routines = list.routines.filter(
          (value) => value.schedule_id !== id,
        );
        return json({ success: true, message: "Schedule deleted." });
      }
    }
    const routine =
      /^\/api\/playlist\/([^/]+)\/routines\/(review|activate)$/u.exec(route);
    if (routine) {
      const selected =
        typeof body.schedule_id === "number"
          ? list.routines.find((row) => row.schedule_id === body.schedule_id)
          : null;
      if (routine[2] === "review") {
        const supplied = body.definition as Partial<RoutineDefinition> & {
            timing?: Record<string, string>;
          },
          definition: RoutineDefinition = {
            job_type: supplied.job_type ?? "shuffle",
            target_playlist_id: routine[1]!,
            target_playlist_name: setup.target.name,
            source_playlist_ids: selected?.definition.source_playlist_ids ?? [],
            algorithm_name: supplied.algorithm_name ?? null,
            algorithm_params: supplied.algorithm_params ?? {},
            schedule_type: selected?.definition.schedule_type ?? "interval",
            schedule_value:
              supplied.timing?.interval ??
              selected?.definition.schedule_value ??
              "daily",
            timezone:
              supplied.timing?.timezone ??
              selected?.definition.timezone ??
              "UTC",
          };
        return json({
          success: true,
          definition,
          intent: { signature: "fixture-reviewed-intent" },
          dependency_revision: setup.configuration_revision,
          schedule_revision: selected?.schedule_revision ?? null,
          candidate_schedule_ids: selected
            ? []
            : list.routines
                .filter(
                  (row) => row.definition.job_type === definition.job_type,
                )
                .map((row) => row.schedule_id),
          warnings: [],
          resource_scope: { target_id: routine[1] },
        });
      }
      const key = String(body.request_key),
        prior = receipts.get(key);
      if (prior) return json({ ...(prior as object), replayed: true });
      if (scenario === "routines-conflict")
        return error(
          "Playlist settings changed. Review again.",
          "prerequisites_changed",
        );
      const definition = body.definition as RoutineDefinition,
        id =
          selected?.schedule_id ??
          Math.max(0, ...list.routines.map((row) => row.schedule_id)) + 1,
        template = selected ?? parseRoutines(seed.list).routines[0]!;
      const row = {
        ...structuredClone(template),
        schedule_id: id,
        schedule: { ...template.schedule, ...definition, id, is_enabled: true },
        definition,
        schedule_revision: "f".repeat(64),
        state: "active" as const,
        state_label: "Active" as const,
      };
      list.routines = selected
        ? list.routines.map((value) => (value.schedule_id === id ? row : value))
        : [...list.routines, row];
      const answer = {
        success: true,
        accepted: true,
        replayed: false,
        accepted_at: "2026-10-15T19:00:00Z",
        dependency_revision: setup.configuration_revision,
        ...row,
      };
      receipts.set(key, answer);
      if (scenario === "routines-activation-unknown" && !lostActivation) {
        lostActivation = true;
        throw Error("Fixture lost activation response.");
      }
      return json(answer);
    }
    const settings =
      /^\/api\/playlist\/([^/]+)\/setup\/(context|playlists|impact|settings|spotify-playlists)$/u.exec(
        route,
      );
    if (settings) {
      const target = settings[1]!,
        action = settings[2];
      if (action === "context")
        return json({
          success: true,
          context: setup,
          return_target: url.searchParams.get("return_target") ?? "playlist",
        });
      if (action === "playlists")
        return json({ success: true, playlists: owned });
      if (action === "impact")
        return json({
          success: true,
          configuration_revision: setup.configuration_revision,
          changes: body.changes,
          affected_routines: setup.routines.map((row) => ({
            ...row,
            effects: ["Used on the next run."],
          })),
          unaffected_routines: [],
          notes: ["Settings apply after Save."],
          expected_state_digest: "9".repeat(64),
          acknowledgment: { signature: "fixture-settings-review" },
        });
      if (action === "settings") {
        if (body.configuration_revision !== setup.configuration_revision)
          return error(
            "Playlist settings changed. Reload and review.",
            "configuration_changed",
          );
        for (const change of body.changes as Record<string, unknown>[]) {
          if (
            change.op === "add_playlist_source" ||
            change.op === "add_search_source"
          )
            setup.sources.push({
              id: 50 + setup.sources.length,
              type: change.op === "add_search_source" ? "search_query" : "own",
              playlist_id:
                typeof change.playlist_id === "string"
                  ? change.playlist_id
                  : null,
              search_query:
                typeof change.search_query === "string"
                  ? change.search_query
                  : null,
              name: String(change.playlist_id ?? change.search_query),
              raid_count: 5,
            });
          if (change.op === "remove_source")
            setup.sources = setup.sources.filter(
              (row) => row.id !== change.source_id,
            );
          if (change.op === "set_source_count") {
            const row = setup.sources.find(
              (value) => value.id === change.source_id,
            );
            if (row) row.raid_count = Number(change.raid_count);
          }
          if (
            change.op === "link_collection" ||
            change.op === "replace_collection"
          )
            setup.collection = {
              playlist_id: String(change.playlist_id),
              name:
                owned.find((row) => row.id === change.playlist_id)?.name ??
                "Created playlist",
              drip_enabled: false,
              drip_count: 3,
            };
          if (change.op === "pair_archive" || change.op === "replace_archive")
            setup.archive = {
              playlist_id: String(change.playlist_id),
              name:
                owned.find((row) => row.id === change.playlist_id)?.name ??
                "Created playlist",
              auto_archive_on_remove: true,
            };
          if (change.op === "unlink_collection")
            setup.collection = null as unknown as typeof setup.collection;
          if (change.op === "unpair_archive")
            setup.archive = null as unknown as typeof setup.archive;
          if (change.op === "set_drip" && setup.collection) {
            if (change.drip_count !== undefined)
              setup.collection.drip_count = Number(change.drip_count);
            if (change.drip_enabled !== undefined)
              setup.collection.drip_enabled = Boolean(change.drip_enabled);
          }
          if (change.op === "set_auto_archive" && setup.archive)
            setup.archive.auto_archive_on_remove = Boolean(change.value);
        }
        setup.configuration_revision = "d".repeat(64);
        setup.state_digest = "9".repeat(64);
        return json({ success: true, saved: true, context: setup });
      }
      if (action === "spotify-playlists") {
        created++;
        const name = String(body.name);
        owned.push({
          id: `created${created}`,
          name,
          owned: true,
          image_url: null,
        });
        const reply = admit(target, "setup_playlist_create", true, name);
        if (scenario === "routines-creation-admission-unknown" && created === 1)
          throw Error("Fixture lost creation response.");
        return reply;
      }
    }
    const poll = /^\/api\/operations\/(\d+)$/u.exec(route);
    if (poll) {
      const value = operations.get(Number(poll[1]));
      if (!value) return null;
      const operation = structuredClone(value.operation);
      if (++value.polls === 1) operation.state = "running";
      else {
        operation.state =
          value.unknown || scenario === "routines-attention"
            ? "needs_attention"
            : scenario === "routines-partial"
              ? "failed"
              : "succeeded";
        operation.completed_at = "2026-10-15T19:00:01Z";
        operation.result = value.creation
          ? value.unknown
            ? {
                success: false,
                outcome: "unknown",
                message: "Creation could not be confirmed.",
              }
            : {
                success: true,
                outcome: "created",
                message: "Playlist created.",
                playlist: { id: value.playlistId, name: value.name },
                privacy_confirmed: true,
              }
          : {
              success: operation.state === "succeeded",
              message:
                operation.state === "succeeded"
                  ? "The run completed."
                  : scenario === "routines-partial"
                    ? "Collect was retained. Review the incomplete result."
                    : "The run needs review. Check the current playlist before another run.",
              status:
                operation.state === "succeeded"
                  ? "success"
                  : operation.state === "needs_attention"
                    ? "unknown"
                    : "failed",
              execution_id: operation.id,
              result_detail: {
                ...activity.executions[
                  scenario === "routines-partial"
                    ? 1
                    : scenario === "routines-attention"
                      ? 2
                      : 6
                ]!.result_detail,
                execution_id: operation.id,
              },
            };
      }
      return json({ success: true, operation });
    }
    return null;
  };
}
