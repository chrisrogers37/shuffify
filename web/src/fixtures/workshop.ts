import type {
  WorkshopContext,
  WorkshopTrack,
  Operation,
} from "../api/contracts";
import type { NamedDraft } from "../pages/workshop/core/types";
export const workshopScenarios = [
  "workshop-happy",
  "workshop-empty",
  "workshop-long",
  "workshop-read-error",
  "workshop-not-found",
  "workshop-lost",
  "workshop-poll-error",
  "workshop-busy",
  "workshop-rate",
  "workshop-failed",
  "workshop-partial",
  "workshop-history-failure",
  "workshop-current-error",
  "workshop-named-error",
  "workshop-named-lost",
  "workshop-auth-loss",
  "workshop-admission-error",
] as const;
export const workshopURI = (n: number) =>
  `spotify:track:${String(n).padStart(22, "0")}`;
export function workshopTrack(
  n: number,
  added = "2026-01-01T12:00:00Z",
): WorkshopTrack {
  return {
    id: String(n).padStart(22, "0"),
    uri: workshopURI(n),
    name:
      n === 1 ? "First Light" : n === 2 ? "Night Drive" : `Example Track ${n}`,
    artists: ["Example Artist"],
    artist_urls: [null],
    duration_ms: 183000,
    is_local: false,
    added_at: added,
    album_name: "After Hours",
    album_image_url: null,
    track_url: `https://open.spotify.com/track/${String(n).padStart(22, "0")}`,
  };
}
export function workshopContext(
  id = "target",
  scenario = "workshop-happy",
): WorkshopContext {
  return {
    success: true,
    playlist: {
      id,
      name: id === "target" ? "Evening Collection" : "Morning Collection",
      owner_id: "alice",
      description: "A place for the next arrangement.",
      total_tracks: scenario === "workshop-empty" ? 0 : 3,
      public: false,
      audio_features: {},
    },
    tracks:
      scenario === "workshop-empty"
        ? []
        : scenario === "workshop-long"
          ? Array.from({ length: 60 }, (_, i) => workshopTrack(i + 1))
          : [
              workshopTrack(1),
              workshopTrack(2),
              workshopTrack(1, "2026-02-01T12:00:00Z"),
            ],
    algorithms: [],
    upstream_sources: {},
    previous:
      id === "target" ? null : { id: "target", name: "Evening Collection" },
    next:
      id === "target" ? { id: "morning", name: "Morning Collection" } : null,
    recently_loaded: [],
  };
}
const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
const failure = (
  code: string,
  message: string,
  status = 503,
  extra: Record<string, unknown> = {},
) =>
  json(
    { success: false, error_code: code, message, category: "error", ...extra },
    status,
  );
export function createWorkshopFixtures(scenario: string) {
  if (!scenario.startsWith("workshop-"))
    return async (
      _path: string,
      _method: string,
      _init?: RequestInit,
    ): Promise<Response | null> => null;
  let authLost = false;
  let desired: string[] | null = null,
    baseline: string[] = [],
    reads = 0,
    postCount = 0,
    opReads = 0,
    draftId = 1;
  let sessions: NamedDraft[] = [];
  const keys = new Map<string, number>();
  const operation = (state: Operation["state"] = "queued"): Operation => ({
    id: 2001,
    kind: "workshop_commit",
    state,
    targets: ["target"],
    started_at: null,
    completed_at: ["queued", "running"].includes(state)
      ? null
      : "2026-01-01T12:01:00Z",
    progress: {
      batches_done: ["queued", "running"].includes(state) ? 0 : 1,
      total_batches: 1,
    },
    result: ["queued", "running"].includes(state)
      ? null
      : {
          success: state === "succeeded",
          target_status:
            scenario === "workshop-partial"
              ? "partial"
              : scenario === "workshop-failed"
                ? "not_written"
                : "saved",
          message:
            scenario === "workshop-history-failure"
              ? "The target was saved, but history confirmation failed."
              : state === "succeeded"
                ? "Playlist saved to Spotify."
                : "The save needs attention.",
          error_code:
            state === "succeeded" ? null : "history_confirmation_failed",
          requires_refresh: state !== "succeeded",
          retryable: false,
          confirmed_batches: 1,
          total_batches: 1,
          saved_track_uris: desired ?? [],
          bookkeeping: {
            history:
              scenario === "workshop-history-failure" ? "failed" : "confirmed",
          },
        },
    recovery: { available: false, action: null, reason: null },
  });
  return async (
    path: string,
    method: string,
    init?: RequestInit,
  ): Promise<Response | null> => {
    const parsed = new URL(path, "https://fixture.invalid"),
      url = parsed.pathname;
    if(method === "GET" && /^\/api\/playlist\/[^/]+\/pair$/.test(url)) return json({success:true,paired:false});
    if(method === "GET" && url.endsWith("/raid-status")) return json({success:true,raid_status:{sources:[],raid_link:null,source_count:0,max_sources:10}});
    if(method === "GET" && url.endsWith("/pending-raids")) return json({success:true,tracks:[]});

    if (url === "/api/session" && scenario === "workshop-auth-loss")
      return json(
        authLost
          ? { success: true, signed_in: false, user: null, notice: null }
          : {
              success: true,
              signed_in: true,
              user: { id: "alice", display_name: "Alice", image_url: null },
              csrf_token: "fixture-csrf-token",
              notice: null,
            },
      );
    if (url === "/api/user-playlists" && method === "GET")
      return json({
        success: true,
        playlists: [
          { id: "target", name: "Evening Collection" },
          { id: "morning", name: "Morning Collection" },
        ],
      });
    if (/^\/api\/workshop\/[^/]+\/locks$/u.test(url) && method === "GET")
      return json({ success: true, locks: [] });
    if (/^\/api\/workshop\/(target|morning)$/u.test(url) && method === "GET") {
      reads++;
      if (scenario === "workshop-auth-loss" && reads > 1)
        return failure("authentication_required", "Your session ended.", 401);
      if (scenario === "workshop-not-found")
        return failure("not_found", "Playlist cannot be opened.", 404);
      if (scenario === "workshop-read-error" && reads === 1)
        return failure(
          "upstream_unavailable",
          "The playlist could not be read.",
        );
      return json(workshopContext(url.split("/")[3], scenario));
    }
    if (url.endsWith("/source-context") && url.includes("/workshop/"))
      return json({
        success: true,
        target: { id: url.split("/")[3], name: "Evening Collection" },
        raid: null,
        archive: null,
      });
    if (url.endsWith("/current-state") && url.includes("/workshop/")) {
      if (scenario === "workshop-current-error")
        return failure(
          "upstream_unavailable",
          "The current order could not be read.",
        );
      const order =
        desired ?? workshopContext("target", scenario).tracks.map((t) => t.uri);
      return json({
        success: true,
        representable: true,
        current_track_uris: order,
        current_tracks: order.map((uri) => {
          const { artist_urls: _urls, ...track } = workshopTrack(
            Number(uri.split(":")[2]),
          );
          return track;
        }),
        loaded_at: "2026-01-01T12:02:00Z",
      });
    }
    if (url.endsWith("/commit") && method === "POST") {
      postCount++;
      if (scenario === "workshop-admission-error")
        return failure(
          "internal_error",
          "The save admission reply is unavailable.",
          500,
        );
      if (scenario === "workshop-auth-loss") {
        authLost = true;
        return failure("authentication_required", "Your session ended.", 401);
      }
      const body = JSON.parse(String(init?.body ?? "{}")) as {
        baseline_track_uris: string[];
        track_uris: string[];
      };
      const key = new Headers(init?.headers).get("Idempotency-Key");
      if (!key)
        return failure("invalid_request", "A request key is required.", 400);
      if (scenario === "workshop-rate")
        return failure(
          "rate_limited",
          "Wait before saving this reviewed draft.",
          429,
          { retry_after: 2 },
        );
      if (scenario === "workshop-busy")
        return failure(
          "playlist_busy",
          "This playlist has an operation in progress.",
          409,
          { operation_id: 2001 },
        );
      if (!keys.has(key)) {
        keys.set(key, 2001);
        desired = [...body.track_uris];
        baseline = [...body.baseline_track_uris];
      }
      if (scenario === "workshop-lost" && postCount === 1)
        return new Response("lost admission reply", {
          headers: { "Content-Type": "application/json" },
        });
      return json(
        {
          success: true,
          category: "info",
          message: "Accepted.",
          operation_id: 2001,
          operation: operation(),
        },
        202,
      );
    }
    if (url === "/api/operations/2001" && method === "GET") {
      opReads++;
      if (scenario === "workshop-poll-error")
        return failure(
          "upstream_unavailable",
          "Status is temporarily unavailable.",
        );
      return json({
        success: true,
        operation: operation(
          opReads < 2
            ? "running"
            : scenario === "workshop-history-failure" ||
                scenario === "workshop-partial"
              ? "needs_attention"
              : scenario === "workshop-failed"
                ? "failed"
                : "succeeded",
        ),
      });
    }
    if (url === "/api/workshop/target/sessions") {
      if (method === "GET")
        return scenario === "workshop-named-error"
          ? failure("database_unavailable", "Saved drafts could not be loaded.")
          : json({ success: true, sessions });
      if (method === "POST") {
        const body = JSON.parse(String(init?.body)) as {
          session_name: string;
          track_uris: string[];
        };
        const d: NamedDraft = {
          id: draftId++,
          user_id: 1,
          playlist_id: "target",
          session_name: body.session_name,
          track_uris: body.track_uris,
          track_count: body.track_uris.length,
          created_at: "2026-01-01T12:00:00Z",
          updated_at: "2026-01-01T12:00:00Z",
        };
        sessions = [d, ...sessions];
        if (scenario === "workshop-named-lost")
          return new Response("lost local save reply");
        return json({ success: true, session: d });
      }
    }
    const match = /^\/api\/workshop\/sessions\/(\d+)$/u.exec(url);
    if (match) {
      const id = Number(match[1]),
        d = sessions.find((d) => d.id === id);
      if (!d) return failure("not_found", "Saved draft not found.", 404);
      if (method === "GET") return json({ success: true, session: d });
      if (method === "DELETE") {
        sessions = sessions.filter((d) => d.id !== id);
        return json({ success: true });
      }
      if (method === "PUT") {
        const body = JSON.parse(String(init?.body)) as {
          session_name: string;
          track_uris: string[];
        };
        const saved = { ...d, ...body, track_count: body.track_uris.length };
        sessions = sessions.map((d) => (d.id === id ? saved : d));
        return json({ success: true, session: saved });
      }
    }
    return null;
  };
}
