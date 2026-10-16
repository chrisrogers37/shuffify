import algorithms from "../../fixtures/workshop-tools/algorithms.json" with { type: "json" };
import {
  createWorkshopFixtures,
  workshopTrack,
  workshopURI,
} from "./workshop.ts";
import type { Algorithm, WorkshopContext } from "../api/contracts";
import type { TrackLock, Snapshot } from "../pages/workshop/tools/types";
export const workshopToolsScenarios = [
  "workshop-tools",
  "workshop-tools-lock-error",
  "workshop-tools-lock-lost",
  "workshop-tools-preview-stale",
  "workshop-tools-preview-invalid",
  "workshop-tools-snapshot-error",
  "workshop-tools-snapshot-lost",
  "workshop-tools-search-error",
  "workshop-tools-auth-loss",
  "workshop-tools-lock-rate",
  "workshop-tools-restore-lost",
  "workshop-tools-restore-partial",
] as const;
const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
const error = (
  message: string,
  code = "upstream_unavailable",
  status = 503,
  extra: Record<string, unknown> = {},
) =>
  json(
    { success: false, error_code: code, message, category: "error", ...extra },
    status,
  );
export function createWorkshopToolsFixtures(
  scenario: string,
  base = createWorkshopFixtures(scenario),
) {
  let queued: {
    id: number;
    kind: "snapshot_restore" | "visibility";
    desired: string[];
    publicValue: boolean;
    reads: number;
  } | null = null;
  let current = [workshopURI(1), workshopURI(2), workshopURI(1)],
    isPublic = false,
    queuePosts = 0;
  const keys = new Map<string, number>();
  const operation = (
    state: "queued" | "running" | "succeeded" | "needs_attention" = "queued",
  ) => {
    if (!queued) throw Error("No queued fixture operation.");
    const finished = state === "succeeded" || state === "needs_attention";
    const partial = state === "needs_attention";
    return {
      id: queued.id,
      kind: queued.kind,
      state,
      targets: ["target"],
      started_at: state === "queued" ? null : "2026-01-01T12:00:00Z",
      completed_at: finished ? "2026-01-01T12:01:00Z" : null,
      progress: {},
      recovery: { available: false, action: null, reason: null },
      result: !finished
        ? null
        : queued.kind === "visibility"
          ? {
              success: true,
              public: queued.publicValue,
              message: "Playlist visibility updated.",
              operation: { kind: "privacy", resources: [] },
            }
          : {
              success: !partial,
              message: partial
                ? "The restore could not be confirmed. Check the current playlist."
                : "Snapshot restored. Undo history confirmed.",
              error_code: partial ? "needs_attention" : null,
              requires_refresh: partial,
              retryable: false,
              playlist: partial
                ? null
                : {
                    id: "target",
                    name: "Evening Collection",
                    tracks: queued.desired.map((uri) => {
                      const { artist_urls, ...t } = workshopTrack(
                        Number(uri.split(":")[2]),
                      );
                      return t;
                    }),
                  },
              operation: {
                kind: "snapshot_restore",
                aggregate_status: partial ? "unknown" : "complete",
                resources: [
                  {
                    resource: "target",
                    resource_name: "Evening Collection",
                    role: "target",
                    domain: "spotify",
                    access: "write",
                    mutation_type: "track_membership",
                    status: partial ? "unknown" : "complete",
                    affected_count: partial ? null : queued.desired.length,
                    ...(partial ? {} : { current_track_uris: queued.desired }),
                  },
                ],
                bookkeeping: [
                  {
                    kind: "session_history",
                    status: partial ? "failed" : "complete",
                  },
                ],
              },
            },
    };
  };
  let locks: TrackLock[] = [],
    lockRead = 0,
    lockWrite = 0,
    previewCount = 0,
    snapshotRead = 0,
    snapshotCreates = 0,
    searchCalls = 0;
  const snapshots: Snapshot[] = [
    {
      id: 31,
      user_id: 1,
      playlist_id: "target",
      playlist_name: "Evening Collection",
      track_uris: [workshopURI(1), workshopURI(1), workshopURI(2)],
      track_count: 3,
      snapshot_type: "manual",
      trigger_description: "Repeated first light",
      created_at: new Date().toISOString(),
    },
    {
      id: 30,
      user_id: 1,
      playlist_id: "target",
      playlist_name: "Evening Collection",
      track_uris: [workshopURI(2), workshopURI(9)],
      track_count: 2,
      snapshot_type: "auto_pre_commit",
      trigger_description: "Workshop backup",
      created_at: new Date(Date.now() - 86400000).toISOString(),
    },
    {
      id: 29,
      user_id: 1,
      playlist_id: "target",
      playlist_name: "Evening Collection",
      track_uris: [],
      track_count: 0,
      snapshot_type: "manual",
      trigger_description: "Empty order",
      created_at: "2026-01-01T12:00:00Z",
    },
  ];
  return async (
    path: string,
    method: string,
    init?: RequestInit,
  ): Promise<Response | null> => {
    const url = new URL(path, "https://fixture.invalid"),
      body = () =>
        JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
    const restore =
      /^\/api\/snapshots\/(\d+)\/(restore-context|restore)$/u.exec(
        url.pathname,
      );
    if (restore) {
      const id = Number(restore[1]),
        snapshot = snapshots.find((s) => s.id === id);
      if (!snapshot)
        return error("This snapshot is no longer available.", "not_found", 404);
      if (restore[2] === "restore-context" && method === "GET")
        return json({
          success: true,
          context: {
            version: 1,
            kind: "snapshot_restore",
            label: "Restore saved snapshot now",
            target: { id: "target", name: "Evening Collection" },
            snapshot: { id, created_at: snapshot.created_at },
            resources: [
              {
                resource: "target",
                resource_name: "Evening Collection",
                role: "target",
                domain: "spotify",
                access: "write",
                mutation_type: "track_membership",
                field: null,
              },
            ],
            steps: [],
            required_bookkeeping: ["session_history"],
            review_binding: {
              version: 1,
              scope: "a".repeat(64),
              before_order: "b".repeat(64),
              history: null,
            },
            current: {
              track_count: current.length,
              track_uris: current,
              loaded_at: "2026-01-01T12:00:00Z",
            },
            action: {
              snapshot_id: id,
              desired_track_uris: snapshot.track_uris,
              summary: {
                added_count: 0,
                removed_count: Math.max(
                  0,
                  current.length - snapshot.track_count,
                ),
                order_changed: true,
              },
            },
          },
        });
      if (restore[2] === "restore" && method === "POST") {
        queuePosts++;
        const key = new Headers(init?.headers).get("Idempotency-Key");
        if (!key)
          return error("A request key is required.", "invalid_request", 400);
        if (!keys.has(key)) {
          keys.set(key, 3001);
          queued = {
            id: 3001,
            kind: "snapshot_restore",
            desired: [...snapshot.track_uris],
            publicValue: isPublic,
            reads: 0,
          };
        }
        if (scenario === "workshop-tools-restore-lost" && queuePosts === 1)
          return new Response("lost acceptance reply");
        return json(
          { success: true, operation_id: 3001, operation: operation() },
          202,
          { Location: "/api/operations/3001" },
        );
      }
    }
    if (
      url.pathname === "/api/playlist/target/live-context" &&
      method === "POST"
    ) {
      const b = body();
      if (b.kind === "privacy")
        return json({
          success: true,
          context: {
            version: 1,
            kind: "privacy",
            label: "Change playlist visibility",
            target: { id: "target", name: "Evening Collection" },
            resources: [
              {
                resource: "target",
                resource_name: "Evening Collection",
                role: "target",
                domain: "spotify",
                access: "write",
                mutation_type: "metadata",
                field: "public",
              },
            ],
            required_bookkeeping: [],
            review_binding: {
              version: 1,
              scope: "a".repeat(64),
              before_order: null,
              history: null,
            },
            current: { public: isPublic, loaded_at: "2026-01-01T12:00:00Z" },
            action: { public: b.public },
          },
        });
    }
    if (
      url.pathname === "/api/playlist/target/toggle-visibility" &&
      method === "POST"
    ) {
      const b = body(),
        key = new Headers(init?.headers).get("Idempotency-Key");
      if (!key)
        return error("A request key is required.", "invalid_request", 400);
      if (!keys.has(key)) {
        keys.set(key, 3002);
        queued = {
          id: 3002,
          kind: "visibility",
          desired: current,
          publicValue: b.public === true,
          reads: 0,
        };
      }
      return json(
        { success: true, operation_id: 3002, operation: operation() },
        202,
        { Location: "/api/operations/3002" },
      );
    }
    if (
      /^\/api\/operations\/(3001|3002)$/u.test(url.pathname) &&
      method === "GET"
    ) {
      if (!queued)
        return error("No accepted fixture operation.", "not_found", 404);
      queued.reads++;
      const state =
        queued.reads < 2
          ? "running"
          : scenario === "workshop-tools-restore-partial"
            ? "needs_attention"
            : "succeeded";
      if (state === "succeeded") {
        if (queued.kind === "visibility") isPublic = queued.publicValue;
        else current = [...queued.desired];
      }
      return json({ success: true, operation: operation(state) });
    }
    if (
      /^\/api\/workshop\/(target|morning)$/u.test(url.pathname) &&
      method === "GET"
    ) {
      const response = await base(path, method, init);
      if (!response || response.status !== 200) return response;
      const context = (await response.json()) as WorkshopContext;
      context.playlist.public = isPublic;
      context.algorithms = algorithms as Algorithm[];
      context.recently_loaded = [
        {
          id: "sourceA",
          name: "Night Signals",
          owner_id: "curator-a",
          track_count: null,
        },
        {
          id: "sourceB",
          name: "Night Signals",
          owner_id: "curator-b",
          track_count: 3,
        },
      ];
      return json(context);
    }
    if (
      /^\/api\/workshop\/[^/]+\/locks$/u.test(url.pathname) &&
      method === "GET"
    ) {
      lockRead++;
      if (scenario === "workshop-tools-lock-error" && lockRead === 1)
        return error("The active locks could not be loaded.");
      return json({ success: true, locks });
    }
    if (url.pathname.endsWith("/locks/toggle") && method === "POST") {
      lockWrite++;
      if (scenario === "workshop-tools-lock-rate" && lockWrite === 1)
        return error(
          "Wait before changing this track lock.",
          "rate_limited",
          429,
          { retry_after: 2 },
        );
      const b = body(),
        uri = String(b.track_uri),
        previous = locks.find((l) => l.track_uri === uri);
      locks = locks.filter((l) => l.track_uri !== uri);
      const lock: TrackLock | null =
        previous?.lock_tier === "super"
          ? null
          : {
              id: 1,
              user_id: 1,
              spotify_playlist_id: "target",
              track_uri: uri,
              position: Number(b.position),
              lock_tier: previous ? "super" : "standard",
              created_at: new Date().toISOString(),
              expires_at: previous
                ? null
                : new Date(Date.now() + 30 * 86400000).toISOString(),
            };
      if (lock) locks.push(lock);
      if (scenario === "workshop-tools-lock-lost" && lockWrite === 1)
        return new Response("lost lock reply");
      return json({ success: true, lock });
    }
    if (url.pathname.endsWith("/locks/unlock-all") && method === "POST") {
      const count = locks.length;
      locks = [];
      return json({ success: true, count });
    }
    if (url.pathname.endsWith("/preview-shuffle") && method === "POST") {
      previewCount++;
      const b = body(),
        tracks = b.tracks as { uri: string }[],
        held = b.locked_positions as Record<string, string> | undefined;
      const order = tracks.map((t) => t.uri),
        available = order.filter((_, i) => !held?.[i]).reverse();
      const next = order.map((uri, i) =>
        held?.[i] ? uri : available.shift()!,
      );
      if (scenario === "workshop-tools-preview-stale")
        await new Promise((resolve) => setTimeout(resolve, 700));
      return json({
        success: true,
        shuffled_uris:
          scenario === "workshop-tools-preview-invalid"
            ? [workshopURI(9)]
            : next,
        algorithm_name:
          algorithms.find((a) => a.class_name === b.algorithm)?.name ?? "Basic",
      });
    }
    if (url.pathname === "/api/workshop/search" && method === "POST") {
      searchCalls++;
      const b = body();
      if (scenario === "workshop-tools-search-error" && searchCalls > 1)
        return error("The next search page could not be loaded.");
      const query = String(b.query),
        offset = Number(b.offset),
        limit = Number(b.limit);
      return json({
        success: true,
        query,
        offset,
        limit,
        tracks: Array.from({ length: offset === 0 ? 10 : 3 }, (_, i) => {
          const t = workshopTrack(offset + i + 3);
          return {
            id: t.id,
            uri: t.uri,
            name: t.name,
            artists: t.artists,
            album_name: t.album_name,
            album_image_url:
              i === 0 ? "/images/favicon.svg" : t.album_image_url,
            duration_ms: i === 1 ? null : t.duration_ms,
          };
        }),
      });
    }
    if (
      url.pathname === "/api/workshop/load-external-playlist" &&
      method === "POST"
    ) {
      const b = body();
      if (b.query)
        return json({
          success: true,
          mode: "search",
          playlists: [
            {
              id: "sourceA",
              name: "Night Signals",
              owner_display_name: "Curator A",
              image_url: null,
              total_tracks: null,
            },
            {
              id: "sourceB",
              name: "Night Signals",
              owner_display_name: "Curator B",
              image_url: null,
              total_tracks: 3,
            },
          ],
        });
      const id = String(b.url).includes("sourceA") ? "sourceA" : "sourceB",
        playlist = {
          id,
          name: "Night Signals",
          owner_id: id === "sourceA" ? "curator-a" : "curator-b",
          description: "A source playlist. Importing leaves it unchanged.",
          track_count: id === "sourceA" ? null : 3,
        };
      return id === "sourceA"
        ? json({
            success: true,
            mode: "restricted",
            playlist,
            tracks: [],
            message:
              "This playlist is visible, but its tracks cannot be read here.",
            suggested_search: "Night Signals",
          })
        : json({
            success: true,
            mode: "tracks",
            playlist,
            tracks: [workshopTrack(1), workshopTrack(8), workshopTrack(8)],
          });
    }
    if (url.pathname === "/api/playlist/target/snapshots") {
      if (method === "GET") return json({ success: true, snapshots });
      if (method === "POST") {
        snapshotCreates++;
        const b = body(),
          order = b.track_uris as string[];
        const s: Snapshot = {
          id: 31 + snapshotCreates,
          user_id: 1,
          playlist_id: "target",
          playlist_name: String(b.playlist_name),
          track_uris: order,
          track_count: order.length,
          snapshot_type: "manual",
          trigger_description: String(b.trigger_description),
          created_at: new Date().toISOString(),
        };
        snapshots.unshift(s);
        if (
          scenario === "workshop-tools-snapshot-lost" &&
          snapshotCreates === 1
        )
          return new Response("lost snapshot reply");
        return json({ success: true, snapshot: s });
      }
    }
    const match = /^\/api\/snapshots\/(\d+)$/u.exec(url.pathname);
    if (match) {
      const id = Number(match[1]),
        snapshot = snapshots.find((s) => s.id === id);
      if (!snapshot)
        return error("This snapshot is no longer available.", "not_found", 404);
      if (method === "GET") {
        snapshotRead++;
        if (scenario === "workshop-tools-snapshot-error" && snapshotRead === 1)
          return error("Snapshot tracks could not be loaded.");
        return json({ success: true, snapshot });
      }
      if (method === "DELETE") {
        snapshots.splice(snapshots.indexOf(snapshot), 1);
        return json({ success: true });
      }
    }
    return null;
  };
}
