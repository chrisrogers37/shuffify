import initial from "../../fixtures/library/initial.json" with { type: "json" };
import signedIn from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import signedOut from "../../fixtures/valid/session-signed-out.json" with { type: "json" };
import type { LibraryContext, Operation } from "../api/contracts";
import type { Intent, Review } from "../pages/library/api";
import type { ShuffleForm } from "../pages/library/model.ts";
import { findRow, placeRow } from "../pages/library/model.ts";
export const libraryScenarios = [
  "library-empty",
  "library-no-default",
  "library-read-failure",
  "library-rate-limit",
  "library-auth-loss",
  "library-busy",
  "library-queue-failure",
  "library-unknown",
  "library-status-failure",
  "library-partial",
  "library-recovery",
  "library-preference-failure",
  "library-preference-unknown",
  "library-loading",
] as const;
const fieldsOf = (form: FormData): Record<string, FormDataEntryValue> => {
  const fields: Record<string, FormDataEntryValue> = {};
  form.forEach((v, k) => {
    fields[k] = v;
  });
  return fields;
};
const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
const fail = (message: string, extra: object = {}) => ({
  success: false,
  error_code: "library_error",
  category: "error",
  message,
  ...extra,
});
export function fixtureReview(id: string, intent: Intent): Review {
  const row = findRow(initial.sections, id)!;
  const common = {
    version: 1 as const,
    label:
      intent.kind === "shuffle"
        ? "Shuffle now"
        : intent.kind === "undo"
          ? "Restore previous saved version now"
          : "Change playlist visibility",
    target: { id, name: row.name },
    resources: [
      {
        resource: id,
        resource_name: row.name,
        role: "target" as const,
        domain: "spotify" as const,
        access: "write" as const,
        mutation_type:
          intent.kind === "privacy"
            ? ("metadata" as const)
            : ("track_order" as const),
        field: intent.kind === "privacy" ? ("public" as const) : null,
      },
    ],
    required_bookkeeping: intent.kind === "privacy" ? [] : ["session_history"],
    review_binding: {
      version: 1 as const,
      scope: "a".repeat(64),
      before_order: intent.kind === "undo" ? "b".repeat(64) : null,
      history: intent.kind === "undo" ? "c".repeat(64) : null,
    },
  };
  if (intent.kind === "privacy")
    return {
      ...common,
      kind: "privacy",
      current: {
        public: row.public ?? false,
        loaded_at: "2026-01-01T12:00:00Z",
      },
      action: { public: intent.public },
    };
  if (intent.kind === "undo")
    return {
      ...common,
      kind: "undo",
      current: {
        track_count: 3,
        track_uris: [
          "spotify:track:alice",
          "spotify:track:bob",
          "spotify:track:alice",
        ],
        loaded_at: "2026-01-01T12:00:00Z",
      },
      action: {
        current_index: 1,
        selected_index: 0,
        desired_track_uris: [
          "spotify:track:alice",
          "spotify:track:alice",
          "spotify:track:bob",
        ],
        summary: { added_count: 0, removed_count: 0, order_changed: true },
      },
    };
  return {
    ...common,
    kind: "shuffle",
    current: {
      track_count: row.track_count,
      loaded_at: "2026-01-01T12:00:00Z",
    },
    action: {
      algorithm: {
        id: intent.form.algorithm,
        name:
          initial.algorithms.find((a) => a.class_name === intent.form.algorithm)
            ?.name ?? intent.form.algorithm,
      },
      params: Object.fromEntries(
        Object.entries(intent.form)
          .filter(([k]) => k !== "algorithm")
          .map(([k, v]) => [k, /^-?\d+(?:\.\d+)?$/u.test(v) ? Number(v) : v]),
      ),
    },
  };
}
export function createLibraryFixtures(scenario: string) {
  let value = structuredClone(initial) as LibraryContext,
    reads = 0,
    writes = 0,
    preferences = 0,
    signed = true,
    nextId = 101;
  const receipts = new Map<
    string,
    { fingerprint: string; operation: Operation; intent: Intent; polls: number }
  >();
  if (scenario === "library-empty")
    value.sections = { favorites: [], playlists: [], hidden: [] };
  if (scenario === "library-no-default")
    value.shuffle_defaults = { algorithm: null, params: {} };
  const original = structuredClone(value);
  return async (
    path: string,
    method: string,
    init?: RequestInit,
  ): Promise<Response | null> => {
    if (
      path === "/api/session" &&
      method === "GET" &&
      scenario === "library-auth-loss"
    )
      return json(signed ? signedIn : signedOut);
    if (
      (path === "/api/library" || path === "/api/library?refresh=1") &&
      method === "GET"
    ) {
      reads++;
      if (scenario === "library-loading")
        await new Promise((resolve) => setTimeout(resolve, 900));
      if (scenario === "library-read-failure" && reads === 1)
        return json(fail("Playlists could not be read."), 503);
      if (scenario === "library-rate-limit" && reads === 1)
        return json(
          fail("Spotify requested a pause.", { retry_after: 2 }),
          429,
          { "Retry-After": "2" },
        );
      return json(value);
    }
    if (path.startsWith("/api/playlist-preferences/") && method === "POST") {
      if (new Headers(init?.headers).get("X-CSRFToken") !== signedIn.csrf_token)
        return json(fail("Missing CSRF token."), 400);
      preferences++;
      if (scenario === "library-preference-failure" && preferences === 1)
        return json(fail("Preferences were not saved."), 503);
      const suffix = path.slice("/api/playlist-preferences/".length),
        match = /^([^/]+)\/toggle-(pinned|hidden)$/u.exec(suffix);
      if (match) {
        const id = decodeURIComponent(match[1]!),
          flag = match[2] as "pinned" | "hidden",
          row = findRow(value.sections, id);
        if (!row) return json(fail("Playlist unavailable."), 404);
        const enabled = !row[flag];
        value.sections = placeRow(value.sections, id, { [flag]: enabled });
        if (scenario === "library-preference-unknown" && preferences === 1)
          throw Error("Lost preference receipt");
        return json({
          success: true,
          [flag === "pinned" ? "is_pinned" : "is_hidden"]: enabled,
          is_favorited: flag === "pinned" ? enabled : row.pinned,
        });
      }
      if (suffix === "order") {
        const body = JSON.parse(String(init?.body)) as {
          playlist_ids: string[];
        };
        for (const section of ["favorites", "playlists"] as const)
          value.sections[section].sort(
            (a, b) =>
              body.playlist_ids.indexOf(a.id) - body.playlist_ids.indexOf(b.id),
          );
        return json({ success: true, count: body.playlist_ids.length });
      }
      if (suffix === "reset") {
        const all = Object.values(original.sections)
          .flat()
          .map((row) => ({ ...row, pinned: false, hidden: false }));
        value.sections = { favorites: [], playlists: all, hidden: [] };
        return json({ success: true, count: all.length });
      }
    }
    const inspection = /^\/api\/playlist\/([^/]+)\/inspect-live$/u.exec(path);
    if (inspection && method === "POST") {
      const id = decodeURIComponent(inspection[1]!),
        body = JSON.parse(String(init?.body)) as {
          kind: string;
          desired_public?: boolean | null;
          desired_track_uris?: string[] | null;
        },
        row = findRow(value.sections, id)!;
      const current = [
        "spotify:track:alice",
        "spotify:track:bob",
        "spotify:track:alice",
      ];
      const desired = body.desired_track_uris ?? null,
        match =
          body.kind === "privacy"
            ? body.desired_public === row.public
            : desired !== null &&
              JSON.stringify(desired) === JSON.stringify(current);
      return json(
        {
          success: match,
          inspection_only: true,
          category: match ? "success" : "error",
          message: match
            ? "Current state matches the retained intent."
            : "Current contents do not establish the earlier action outcome.",
          ...(match ? {} : { error_code: "upstream_error" }),
          ...(body.kind === "privacy" && match ? { public: row.public } : {}),
          operation: {
            kind: body.kind === "privacy" ? "privacy" : "track_inspection",
            aggregate_status: match ? "complete" : "unknown",
            resources: [
              {
                resource: id,
                status: match ? "not_required" : "unknown",
                ...(body.kind === "privacy"
                  ? { current_metadata: { public: row.public } }
                  : { current_track_uris: current }),
              },
            ],
            error_code: match
              ? null
              : body.kind === "tracks" && desired === null
                ? "intent_unavailable"
                : body.kind === "privacy"
                  ? "verification_failed"
                  : "intent_not_observed",
          },
        },
        match ? 200 : 502,
      );
    }
    const context = /^\/api\/playlist\/([^/]+)\/live-context$/u.exec(path);
    if (context && method === "POST")
      return json({
        success: true,
        context: fixtureReview(
          decodeURIComponent(context[1]!),
          JSON.parse(String(init?.body)) as Intent,
        ),
      });
    const command = /^\/api\/(shuffle|undo)\/([^/]+)$/u.exec(path),
      privacy = /^\/api\/playlist\/([^/]+)\/toggle-visibility$/u.exec(path);
    if ((command || privacy) && method === "POST") {
      if (new Headers(init?.headers).get("X-CSRFToken") !== signedIn.csrf_token)
        return json(fail("Missing CSRF token."), 400);
      writes++;
      if (scenario === "library-auth-loss") {
        signed = false;
        return json(fail("Your session has ended."), 401);
      }
      if (scenario === "library-busy")
        return json(
          fail("This playlist has an operation in progress.", {
            operation_id: 99,
          }),
          409,
        );
      if (scenario === "library-queue-failure")
        return json(fail("Queue admission was refused."), 503);
      const id = decodeURIComponent(command?.[2] ?? privacy![1]!),
        kind = command?.[1] ?? "privacy";
      const fields =
        init?.body instanceof FormData
          ? fieldsOf(init.body)
          : (JSON.parse(String(init?.body)) as Record<string, unknown>);
      const intent: Intent =
        kind === "privacy"
          ? { kind: "privacy", public: fields.public === true }
          : kind === "undo"
            ? { kind: "undo" }
            : {
                kind: "shuffle",
                form: {
                  algorithm: String(fields.algorithm),
                  ...Object.fromEntries(
                    Object.entries(fields).filter(
                      ([k, v]) =>
                        !k.startsWith("reviewed_") && typeof v === "string",
                    ),
                  ),
                } as ShuffleForm,
              };
      const key = new Headers(init?.headers).get("Idempotency-Key");
      if (!key) return json(fail("Missing request key."), 400);
      const fingerprint = JSON.stringify({ id, fields }),
        old = receipts.get(key);
      if (old && old.fingerprint !== fingerprint)
        return json(fail("Request key changed intent."), 409);
      const operation: Operation = old?.operation ?? {
        id: nextId++,
        kind,
        state: "queued",
        targets: [id],
        started_at: null,
        completed_at: null,
        progress: { batches_done: 0, total_batches: 1, phase: "forward" },
        result: null,
        recovery: { available: false, action: null, reason: null },
      };
      if (!old) receipts.set(key, { fingerprint, operation, intent, polls: 0 });
      if (scenario === "library-unknown" && writes === 1)
        throw Error("Lost admission response");
      return json(
        { success: true, operation_id: operation.id, operation },
        202,
      );
    }
    const operation = /^\/api\/operations\/([1-9]\d*)$/u.exec(path);
    if (operation && method === "GET") {
      const entry = [...receipts.values()].find(
        (r) => r.operation.id === Number(operation[1]),
      );
      if (Number(operation[1]) === 99 && scenario === "library-busy")
        return json({
          success: true,
          operation: {
            id: 99,
            kind: "shuffle",
            state: "running",
            targets: ["alice-mix"],
            started_at: "2026-01-01T12:00:00Z",
            completed_at: null,
            progress: { batches_done: 0, total_batches: 1 },
            result: null,
            recovery: { available: false, action: null, reason: null },
          },
        });
      if (!entry) return null;
      if (scenario === "library-status-failure" && entry.polls++ < 4)
        return json(fail("Status could not be read."), 503);
      if (entry.polls++ < 2)
        return json({
          success: true,
          operation: {
            ...entry.operation,
            state: "running",
            started_at: "2026-01-01T12:00:00Z",
          },
        });
      const state =
          scenario === "library-partial"
            ? "failed"
            : scenario === "library-recovery"
              ? "needs_attention"
              : "succeeded",
        target = entry.operation.targets[0]!,
        row = findRow(value.sections, target)!;
      const terminal: Operation = {
        ...entry.operation,
        state,
        completed_at: "2026-01-01T12:01:00Z",
        progress: { batches_done: 1, total_batches: 1, phase: "forward" },
        result: {
          success: state === "succeeded",
          message:
            state === "succeeded"
              ? "The playlist change was verified."
              : "One resource needs review. Saved history was not confirmed.",
          ...(entry.intent.kind === "privacy"
            ? { public: entry.intent.public }
            : {
                playlist: { id: target, total_tracks: row.track_count },
                playlist_state: {
                  current_index: entry.intent.kind === "undo" ? 0 : 1,
                },
              }),
          operation: {
            kind: entry.intent.kind,
            aggregate_status: state === "succeeded" ? "complete" : "unknown",
            resources: [
              {
                resource: target,
                resource_name: row.name,
                status: state === "succeeded" ? "complete" : "unknown",
                verification:
                  entry.intent.kind === "privacy" ? "metadata" : "ordered",
                affected_count: state === "succeeded" ? 3 : null,
                error_code:
                  state === "succeeded" ? null : "verification_failed",
              },
            ],
            bookkeeping:
              entry.intent.kind === "privacy"
                ? []
                : [
                    {
                      kind: "session_history",
                      status: state === "succeeded" ? "complete" : "failed",
                    },
                  ],
            error_code:
              state === "succeeded" ? null : "history_confirmation_failed",
            retryable: false,
          },
        },
        recovery: {
          available: state === "needs_attention",
          action: state === "needs_attention" ? "restore" : null,
          reason:
            state === "needs_attention"
              ? "Restore needs a fresh inspection before any further write."
              : null,
        },
      };
      entry.operation = terminal;
      return json({ success: true, operation: terminal });
    }
    return null;
  };
}
