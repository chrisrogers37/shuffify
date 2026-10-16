import algorithms from "../../fixtures/workshop-tools/algorithms.json" with { type: "json" };
import routineSeed from "../../fixtures/routines/initial.json" with { type: "json" };
import {
  workshopContext,
  workshopTrack,
  workshopURI as uri,
} from "./workshop.ts";
import type { JsonRecord, Operation } from "../api/contracts";
import type {
  ArchivePair,
  PendingTrack,
  RaidLink,
  UpstreamSource,
} from "../pages/workshop/sources/types";
export const workshopSourcesScenarios = [
  "workshop-sources",
  "workshop-sources-pair-error",
  "workshop-sources-dismiss-lost",
  "workshop-sources-dismiss-cleanup-failed",
  "workshop-sources-finalize-failed",
  "workshop-sources-finalize-refused",
  "workshop-sources-append-unknown",
  "workshop-sources-relink",
  "workshop-sources-create-unknown",
  "workshop-sources-unlinked",
  "workshop-sources-run-partial",
  "workshop-sources-rate",
  "workshop-sources-read-rate",
  "workshop-sources-running-collect",
] as const;
const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      ...headers,
    },
  });
const error = (
  message: string,
  status = 409,
  extra: Record<string, unknown> = {},
) =>
  json(
    {
      success: false,
      error_code: "source_unavailable",
      message,
      category: "error",
      retryable: false,
      ...extra,
    },
    status,
  );
export function createWorkshopSourcesFixtures(scenario: string) {
  const enabled = scenario.startsWith("workshop-sources");
  let pair: ArchivePair | null = {
    id: 9,
    production_playlist_id: "target",
    archive_playlist_id: "archive",
    archive_playlist_name: "Evening archive",
    auto_archive_on_remove: true,
  };
  let link: RaidLink | null = {
    id: 8,
    target_playlist_id: "target",
    raid_playlist_id: "collection",
    raid_playlist_name: "Collected discoveries",
    drip_count: 3,
    drip_enabled: false,
  };
  let sources: UpstreamSource[] = [
    {
      id: 5,
      target_playlist_id: "target",
      source_type: "external",
      source_playlist_id: "watched",
      source_name: "Night discoveries",
      source_url: "https://open.spotify.com/playlist/watched",
      search_query: null,
      raid_count: 5,
      last_track_count: null,
      last_resolve_status: "restricted",
    },
    {
      id: 6,
      target_playlist_id: "target",
      source_type: "search_query",
      source_playlist_id: null,
      source_name: null,
      source_url: null,
      search_query: "new instrumental",
      raid_count: 3,
      last_track_count: 40,
      last_resolve_status: "success",
    },
  ];
  let pending: PendingTrack[] = Array.from({ length: 3 }, (_, i) => ({
    id: i + 11,
    target_playlist_id: "target",
    track_uri: uri(i + 3),
    track_name: `Discovery ${i + 3}`,
    track_artists: "Night Artist",
    track_album: "Quiet places",
    track_image_url: null,
    track_duration_ms: 187000,
    source_playlist_id: "watched",
    source_name: "Night discoveries",
    status: "pending",
    created_at: "2026-01-01T12:00:00Z",
    resolved_at: null,
  }));
  let archive = [uri(6), uri(7), uri(6)],
    current = [uri(1), uri(2), uri(1)],
    pairReads = 0,
    nextId = 6000,
    lost = false,
    limited = false,
    finalizerResults = 0,
    refused = false,
    pendingReadLimited = false;
  const operations = new Map<
      number,
      {
        operation: Operation;
        body: JsonRecord;
        polls: number;
        applied: boolean;
      }
    >(),
    keys = new Map<string, number>();
  const routines = JSON.parse(
    JSON.stringify(routineSeed).replaceAll("target1", "target"),
  ) as typeof routineSeed;
  if (
    ["workshop-sources-create-unknown", "workshop-sources-unlinked"].includes(
      scenario,
    )
  ) {
    pair = null;
    link = null;
  }
  const result = (
    kind: string,
    body: JsonRecord,
  ): { state: Operation["state"]; facts: JsonRecord } => {
    if (kind === "workshop_commit") {
      current = body.track_uris as string[];
      return {
        state: "succeeded",
        facts: {
          success: true,
          target_status: "saved",
          message: "Playlist saved to Spotify.",
          requires_refresh: false,
          retryable: false,
          error_code: null,
          confirmed_batches: 1,
          total_batches: 1,
          saved_track_uris: current,
          bookkeeping: { history: "confirmed" },
        },
      };
    }
    if (kind === "pending_dismiss") {
      const selected = (body.track_ids as number[]).filter((id) =>
          pending.some((x) => x.id === id),
        ),
        tracks = pending.filter((x) => selected.includes(x.id));
      pending = pending.filter((x) => !selected.includes(x.id));
      const failed = scenario === "workshop-sources-dismiss-cleanup-failed";
      return {
        state: failed ? "rolled_back" : "succeeded",
        facts: {
          success: !failed,
          message: failed
            ? "Inbox dismissal confirmed; collection cleanup was restored."
            : "Pending tracks dismissed.",
          database_status: "complete",
          source_status: failed ? "restored" : "complete",
          dismissed_ids: selected,
          changed_ids: selected,
          dismissed_count: selected.length,
          source_removed_count: failed ? 0 : tracks.length,
          source_playlist_id: link?.raid_playlist_id ?? null,
          error_code: failed ? "write_rejected" : null,
          retryable: false,
        },
      };
    }
    if (
      kind === "workshop_finalize_raids" ||
      kind === "workshop_finalize_restore" ||
      kind === "workshop_archive_append"
    ) {
      const append = kind === "workshop_archive_append",
        promote = kind === "workshop_finalize_raids",
        resource = body.source_playlist_id as string | null;
      const failed =
          scenario === "workshop-sources-finalize-failed" &&
          finalizerResults++ === 0,
        unknown = append && scenario === "workshop-sources-append-unknown";
      if (!failed && !unknown) {
        if (promote)
          pending = pending.filter(
            (x) => !(body.track_ids as number[]).includes(x.id),
          );
        else if (append) archive.push(...(body.track_uris as string[]));
        else
          archive = archive.filter(
            (u) => !(body.track_uris as string[]).includes(u),
          );
      }
      const count = promote
        ? (body.track_ids as number[]).length
        : (body.track_uris as string[]).length;
      return {
        state: unknown
          ? "needs_attention"
          : failed
            ? "rolled_back"
            : "succeeded",
        facts: {
          success: !failed && !unknown,
          resource,
          resource_name: promote ? "Collected discoveries" : "Evening archive",
          status: unknown ? "unknown" : failed ? "failed" : "complete",
          source_status: unknown ? "unknown" : failed ? "restored" : "complete",
          ...(promote
            ? {
                database_status: failed ? "not_started" : "complete",
                promoted_count: failed ? 0 : count,
              }
            : append
              ? { archived_count: unknown ? null : failed ? 0 : count }
              : { restored_count: failed ? 0 : count }),
          affected_count: unknown ? null : failed ? 0 : count,
          error_code: unknown
            ? "needs_attention"
            : failed
              ? "write_rejected"
              : null,
          retryable: failed,
          message: unknown
            ? "Archive append outcome is unknown. Inspect the original source before another action."
            : failed
              ? "Source refused the change; target save remains confirmed."
              : "Source task completed.",
          aggregate_status: unknown
            ? "unknown"
            : failed
              ? "partial"
              : "complete",
        },
      };
    }
    if (kind === "pair_create" || kind === "raid_link_create") {
      if (scenario === "workshop-sources-create-unknown")
        return {
          state: "needs_attention",
          facts: {
            success: false,
            outcome: "unknown",
            creation_unknown: true,
            privacy_confirmed: false,
            association_status: "not_started",
            error_code: "creation_unknown",
            retryable: false,
            message:
              "Creation could not be confirmed. Inspect the original attempt before creating another.",
          },
        };
      const id = body.create_new
          ? "newasset"
          : String(body.raid_playlist_id ?? body.archive_playlist_id),
        name = body.create_new
          ? "Created private playlist"
          : String(body.raid_playlist_name ?? body.archive_playlist_name);
      if (kind === "pair_create")
        pair = {
          id: 10,
          production_playlist_id: "target",
          archive_playlist_id: id,
          archive_playlist_name: name,
          auto_archive_on_remove: true,
        };
      else
        link = {
          id: 10,
          target_playlist_id: "target",
          raid_playlist_id: id,
          raid_playlist_name: name,
          drip_count: 3,
          drip_enabled: false,
        };
      return {
        state: "succeeded",
        facts: {
          success: true,
          outcome: body.create_new ? "created" : "linked",
          creation_unknown: false,
          privacy_confirmed: true,
          association_status: "complete",
          playlist: { id, name },
          ...(kind === "pair_create"
            ? { pair: pair ? { ...pair } : null }
            : { raid_link: link ? { ...link } : null }),
          message: "Playlist association confirmed.",
        },
      };
    }
    const partial = scenario === "workshop-sources-run-partial";
    if (kind === "raid_now" || kind === "raid_and_drip")
      pending.push({
        ...pending[0]!,
        id: 30,
        track_uri: uri(8),
        track_name: "New discovery",
      });
    return {
      state: partial ? "failed" : "succeeded",
      facts: {
        success: !partial,
        status: partial ? "partial" : "success",
        message: partial
          ? "Collect completed. Add failed; collected tracks remain."
          : "The reviewed action completed.",
        tracks_added: partial ? 1 : 3,
        tracks_total: current.length,
        result: {
          success: !partial,
          status: partial ? "partial" : "success",
          tracks_added: partial ? 1 : 3,
          tracks_total: current.length,
        },
        result_detail: {
          detail_status: "available",
          version: 1,
          operation:
            kind === "schedule_run"
              ? "shuffle"
              : kind === "raid_and_drip"
                ? "raid_and_drip"
                : kind === "drip_now"
                  ? "drip"
                  : "raid",
          execution_id: nextId,
          outcome: partial ? "partial" : "complete",
          steps: [
            {
              step: "raid",
              status: "complete",
              counts: { tracks_collected: 1 },
              resources: [
                {
                  resource: "collection",
                  kind: "collection",
                  status: "complete",
                  dispatched: true,
                },
              ],
              bookkeeping: [],
            },
            ...(partial
              ? [
                  {
                    step: "drip",
                    status: "failed",
                    resources: [
                      {
                        resource: "target",
                        kind: "target",
                        status: "failed",
                        dispatched: false,
                      },
                    ],
                    bookkeeping: [],
                  },
                ]
              : []),
          ],
        },
      },
    };
  };
  return async (
    path: string,
    method = "GET",
    init?: RequestInit,
  ): Promise<Response | null> => {
    const url = new URL(path, "http://fixture.invalid");
    if (!enabled) {
      if (
        method === "GET" &&
        /^\/api\/playlist\/[^/]+\/pair$/.test(url.pathname)
      )
        return json({ success: true, paired: false });
      if (method === "GET" && path.endsWith("/raid-status"))
        return json({
          success: true,
          raid_status: {
            sources: [],
            raid_link: null,
            source_count: 0,
            max_sources: 10,
          },
        });
      if (method === "GET" && path.endsWith("/pending-raids"))
        return json({ success: true, tracks: [] });
      return null;
    }
    const body =
      method === "GET" || !init?.body
        ? {}
        : (JSON.parse(String(init.body)) as JsonRecord);
    if (path === "/api/workshop/target" && method === "GET")
      return json({
        ...workshopContext("target", "workshop-happy"),
        algorithms,
      });
    if (path === "/api/playlist/target/pair" && method === "GET") {
      if (scenario === "workshop-sources-pair-error" && pairReads++ === 0)
        return error("Archive settings could not be read.", 503);
      if (scenario === "workshop-sources-relink" && pairReads++ > 1)
        pair = {
          ...pair!,
          archive_playlist_id: "replacement",
          archive_playlist_name: "Replacement archive",
        };
      return json({ success: true, paired: !!pair, pair });
    }
    if (path === "/api/playlist/target/raid-status" && method === "GET")
      return json({
        success: true,
        raid_status: {
          sources,
          raid_link: link,
          source_count: sources.length,
          max_sources: 10,
        },
      });
    if (path === "/api/playlist/target/pending-raids" && method === "GET") {
      if (scenario === "workshop-sources-read-rate" && !pendingReadLimited) {
        pendingReadLimited = true;
        return error("Wait before reading the inbox again.", 429, {
          retry_after: 1,
        });
      }
      return json({ success: true, tracks: pending });
    }
    if (path === "/api/playlist/target/pair/archive-tracks")
      return json({
        success: true,
        tracks: archive.map((x) => workshopTrack(Number(x.split(":")[2]))),
        total: archive.length,
      });
    if (url.pathname === "/api/user-playlists")
      return json({
        success: true,
        playlists: [
          {
            id: "chooseA",
            name: "Evening collection",
            owned: true,
            image_url: null,
            total_tracks: null,
          },
          {
            id: "chooseB",
            name: "Evening collection",
            owned: true,
            image_url: null,
            total_tracks: 12,
          },
        ],
      });
    if (url.pathname === "/api/workshop/target/source-context")
      return json({
        success: true,
        target: { id: "target", name: "Evening Collection" },
        raid:
          url.searchParams.get("raid") === "1" && link
            ? { id: link.raid_playlist_id, name: link.raid_playlist_name }
            : null,
        archive:
          url.searchParams.get("archive") === "1" && pair
            ? {
                id: pair.archive_playlist_id,
                name: pair.archive_playlist_name,
                baseline_track_uris: archive,
              }
            : null,
      });
    if (path === "/api/workshop/target/current-state")
      return json({
        success: true,
        representable: true,
        current_track_uris: current,
        current_tracks: current.map((x) =>
          workshopTrack(Number(x.split(":")[2])),
        ),
        loaded_at: "2026-01-01T12:00:00Z",
      });
    if (url.pathname === "/api/playlist/target/routines/context")
      return json(routines.guided);
    if (url.pathname === "/api/schedules/latest-results")
      return json({ success: true, results: {} });
    if (/^\/api\/schedules\/\d+\/history$/.test(path))
      return json({ success: true, history: [] });
    if (/^\/api\/routines\/\d+\/state$/.test(path)) {
      const row = routines.guided.routines.find(
        (x) => x.schedule_id === Number(path.split("/")[3]),
      );
      if (!row) return error("Routine missing", 404);
      row.schedule.is_enabled = body.desired_enabled === true;
      row.state = row.schedule.is_enabled ? "active" : "paused";
      row.state_label = row.schedule.is_enabled ? "Active" : "Paused";
      row.schedule_revision = "b".repeat(64);
      return json({ success: true, ...row });
    }
    if (/^\/api\/schedules\/\d+$/.test(path) && method === "DELETE") {
      routines.guided.routines = routines.guided.routines.filter(
        (x) => x.schedule_id !== Number(path.split("/")[3]),
      );
      return json({ success: true });
    }
    if (
      path === "/api/playlist/target/live-context" ||
      /^\/api\/schedules\/\d+\/run-context$/.test(path)
    ) {
      const kind = body.kind ?? "schedule";
      return json({
        success: true,
        context: {
          version: 1,
          kind,
          target: { id: "target", name: "Evening Collection" },
          label: "Reviewed action",
          resources: [
            {
              resource: "target",
              resource_name: "Evening Collection",
              role: "target",
              domain: "spotify",
              access: kind === "raid" ? "read" : "write",
            },
            {
              resource: "collection",
              resource_name: "Collected discoveries",
              role: "collection",
              domain: "spotify",
              access: "write",
            },
          ],
          steps:
            kind === "raid_and_drip"
              ? ["raid", "drip"]
              : [kind === "schedule" ? "shuffle" : kind],
          required_bookkeeping: ["execution_record"],
          review_binding: {
            version: 1,
            scope: "a".repeat(64),
            before_order: null,
            history: null,
          },
          current: { drip_enabled: false },
          action: {
            count: 3,
            sources: sources.map((x) => ({
              id: x.id,
              label: x.source_name ?? x.search_query,
            })),
          },
        },
      });
    }
    if (path === "/api/playlist/target/raid-add-url") {
      sources.push({
        ...sources[0]!,
        id: 99,
        source_name: "New watched source",
        source_playlist_id: "newWatched",
      });
      return json({
        success: true,
        source: sources.at(-1),
        schedule: null,
        track_count: null,
      });
    }
    if (path === "/api/playlist/target/raid-unwatch") {
      sources = sources.filter((x) => x.id !== body.source_id);
      return json({ success: true });
    }
    if (path === "/api/playlist/target/raid-source-count") {
      sources = sources.map((x) =>
        x.id === body.source_id
          ? { ...x, raid_count: Number(body.raid_count) }
          : x,
      );
      return json({ success: true });
    }
    if (path === "/api/playlist/target/raid-link" && method === "PUT") {
      link = {
        ...link!,
        ...(typeof body.drip_count === "number"
          ? { drip_count: body.drip_count }
          : {}),
        ...(typeof body.drip_enabled === "boolean"
          ? { drip_enabled: body.drip_enabled }
          : {}),
      };
      return json({ success: true, raid_link: link });
    }
    if (path === "/api/playlist/target/raid-link" && method === "DELETE") {
      link = null;
      return json({ success: true });
    }
    if (path === "/api/playlist/target/pair" && method === "PATCH") {
      pair = {
        ...pair!,
        auto_archive_on_remove: body.auto_archive_on_remove === true,
      };
      return json({ success: true, pair });
    }
    if (path === "/api/playlist/target/pair" && method === "DELETE") {
      pair = null;
      return json({ success: true });
    }
    if (body.inspect_only === true)
      return error(
        "This observation cannot establish the previous source outcome.",
        409,
        {
          resource: body.source_playlist_id,
          resource_name: "Original source",
          status: "unknown",
          affected_count: null,
          source_status: "unknown",
        },
      );
    const match = /^\/api\/operations\/(\d+)$/.exec(path);
    if (match) {
      const entry = operations.get(Number(match[1]));
      if (!entry) return null;
      if (
        entry.polls++ === 0 ||
        (scenario === "workshop-sources-running-collect" &&
          entry.operation.kind === "raid_now" &&
          entry.polls < 30)
      )
        return json({
          success: true,
          operation: {
            ...entry.operation,
            state: "running",
            started_at: "2026-01-01T12:00:00Z",
          },
        });
      if (scenario === "workshop-sources-rate" && !limited) {
        limited = true;
        return error("Wait before checking the same operation.", 429, {
          retry_after: 1,
        });
      }
      if (!entry.applied) {
        const terminal = result(entry.operation.kind, entry.body);
        entry.operation = {
          ...entry.operation,
          state: terminal.state,
          started_at: "2026-01-01T12:00:00Z",
          completed_at: "2026-01-01T12:01:00Z",
          result: terminal.facts,
        };
        entry.applied = true;
      }
      return json({ success: true, operation: entry.operation });
    }
    if (method === "POST") {
      const kind =
        path === "/api/workshop/target/commit"
          ? "workshop_commit"
          : path.endsWith("/pending-raids/dismiss")
            ? "pending_dismiss"
            : path.endsWith("/pending-raids/finalize")
              ? "workshop_finalize_raids"
              : path.endsWith("/pair/finalize-restore")
                ? "workshop_finalize_restore"
                : path.endsWith("/pair/archive")
                  ? "workshop_archive_append"
                  : path.endsWith("/raid-link")
                    ? "raid_link_create"
                    : path.endsWith("/pair")
                      ? "pair_create"
                      : path.endsWith("/raid-now")
                        ? "raid_now"
                        : path.endsWith("/drip-now")
                          ? "drip_now"
                          : path.endsWith("/raid-and-drip")
                            ? "raid_and_drip"
                            : /^\/api\/schedules\/\d+\/run$/.test(path)
                              ? "schedule_run"
                              : null;
      if (kind) {
        if (
          scenario === "workshop-sources-finalize-refused" &&
          kind === "workshop_finalize_raids" &&
          !refused
        ) {
          refused = true;
          return error("Source access was refused before admission.", 403);
        }
        const key = new Headers(init?.headers).get("Idempotency-Key");
        if (!key) return error("Request key required", 400);
        let id = keys.get(key);
        if (!id) {
          id = ++nextId;
          keys.set(key, id);
          const targets = [
            "target",
            ...(kind === "workshop_commit" ||
            ((kind === "pair_create" || kind === "raid_link_create") &&
              body.create_new)
              ? []
              : body.source_playlist_id
                ? [String(body.source_playlist_id)]
                : ["collection", "archive"]),
          ];
          operations.set(id, {
            operation: {
              id,
              kind,
              state: "queued",
              targets,
              started_at: null,
              completed_at: null,
              progress: {},
              result: null,
              recovery: { available: false, action: null, reason: null },
            },
            body,
            polls: 0,
            applied: false,
          });
        }
        if (
          scenario === "workshop-sources-dismiss-lost" &&
          kind === "pending_dismiss" &&
          !lost
        ) {
          lost = true;
          return new Response("lost initial response");
        }
        return json(
          {
            success: true,
            operation_id: id,
            operation: operations.get(id)!.operation,
          },
          202,
          { Location: `/api/operations/${id}` },
        );
      }
    }
    return null;
  };
}
