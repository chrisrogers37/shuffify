import type { JsonRecord, WorkshopTrack } from "../../../api/contracts.ts";
import { ProtocolError } from "../../../api/errors.ts";
import type {
  SourceContext,
  SourceResult,
  SourceTask,
  SourceWork,
  Staging,
  PendingTrack,
} from "./types.ts";
export const record = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
export const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter(record) : [];
export const positive = (v: unknown): v is number =>
  Number.isSafeInteger(v) && (v as number) > 0;
export const count = (v: unknown): v is number =>
  Number.isSafeInteger(v) && (v as number) >= 0;
export const uri = (v: unknown): v is string =>
  typeof v === "string" && /^spotify:track:[A-Za-z0-9]{22}$/.test(v);
export const playlistId = (v: unknown): v is string =>
  typeof v === "string" && /^[A-Za-z0-9]+$/.test(v) && v.length <= 255;
export function freeze<T>(v: T): T {
  const value = structuredClone(v);
  const visit = (x: unknown) => {
    if (x && typeof x === "object") {
      Object.values(x).forEach(visit);
      Object.freeze(x);
    }
  };
  visit(value);
  return value;
}
export function prepareTasks(
  staging: Staging,
  desired: readonly string[],
  context: SourceContext,
  key: () => string = () => crypto.randomUUID(),
): SourceWork {
  const tasks: SourceTask[] = [];
  const ordered = [...staging].sort(
    (a, b) =>
      ["raid_promotion", "archive_restore", "archive_append"].indexOf(a.kind) -
      ["raid_promotion", "archive_restore", "archive_append"].indexOf(b.kind),
  );
  for (const entry of ordered) {
    const source =
      entry.kind === "raid_promotion" ? context.raid : context.archive;
    if ((source?.id ?? null) !== entry.source_playlist_id)
      throw Error(
        "The linked source changed. Review the new destination or keep the original staging.",
      );
    const selected =
      entry.kind === "raid_promotion"
        ? entry.pending_rows.map((x) => x.track_uri)
        : entry.track_uris;
    if (!selected.length) continue;
    if (
      entry.kind !== "archive_append" &&
      selected.some((x) => !desired.includes(x))
    )
      throw Error(
        "Some staged tracks are missing from the draft. Add them back or discard staging.",
      );
    const token = key();
    const base = {
      id: token,
      key: token,
      source: source ? { id: source.id, name: source.name } : null,
      dependsOn: [] as string[],
      selectedCount: selected.length,
    };
    const body = {
      source_playlist_id: source?.id ?? null,
      target_track_uris: [...desired],
    };
    if (entry.kind === "raid_promotion")
      tasks.push({
        ...base,
        kind: entry.kind,
        label: "Finish pending tracks",
        body: { ...body, track_ids: entry.pending_rows.map((x) => x.id) },
      });
    else if (entry.kind === "archive_restore")
      tasks.push({
        ...base,
        kind: entry.kind,
        label: "Finish archive restorations",
        body: {
          ...body,
          source_playlist_id: source!.id,
          track_uris: [...selected],
        },
      });
    else {
      if (!context.archive)
        throw Error("Link an archive before reviewing these tracks.");
      const earlier = tasks.filter(
        (x) => x.source?.id === source!.id && x.kind !== "archive_append",
      );
      const removed = new Set(
        earlier.flatMap((x) =>
          x.kind === "raid_promotion"
            ? ordered
                .filter((y) => y.kind === "raid_promotion")
                .flatMap((y) => y.pending_rows.map((z) => z.track_uri))
            : x.body.track_uris,
        ),
      );
      tasks.push({
        ...base,
        kind: entry.kind,
        label: "Finish archiving removed tracks",
        dependsOn: earlier.map((x) => x.id),
        body: {
          ...body,
          source_playlist_id: source!.id,
          track_uris: [...selected],
          baseline_track_uris: context.archive.baseline_track_uris.filter(
            (x) => !removed.has(x),
          ),
        },
      });
    }
  }
  return freeze({ tasks });
}
export function removeArchiveCopy(
  queue: readonly string[],
  track: string,
  afterCount: number,
  baselineCount: number,
) {
  return afterCount < baselineCount &&
    queue.filter((x) => x === track).length < baselineCount - afterCount
    ? [...queue, track]
    : [...queue];
}
export function cancelArchiveCopies(
  queue: readonly string[],
  track: string,
  afterCount: number,
  baselineCount: number,
) {
  const result = [...queue];
  let extra =
    result.filter((x) => x === track).length -
    Math.max(0, baselineCount - afterCount);
  while (extra-- > 0) result.splice(result.lastIndexOf(track), 1);
  return result;
}
export function confirmedDismissal(
  facts: JsonRecord,
  requested: readonly number[],
): number[] | null {
  if (facts.database_status !== "complete") return null;
  if (
    !Array.isArray(facts.dismissed_ids) ||
    !facts.dismissed_ids.every(positive) ||
    !count(facts.dismissed_count) ||
    new Set(facts.dismissed_ids).size !== facts.dismissed_ids.length ||
    facts.dismissed_count !== facts.dismissed_ids.length ||
    facts.dismissed_ids.some((x) => !requested.includes(x))
  )
    throw new ProtocolError(
      "The dismissal receipt names an unconfirmed identity.",
    );
  return facts.dismissed_ids;
}
export function sourceResult(
  task: Pick<SourceTask, "source">,
  facts: unknown,
): SourceResult {
  if (
    !record(facts) ||
    facts.resource !== (task.source?.id ?? null) ||
    !["complete", "not_required", "failed", "unknown"].includes(
      String(facts.status),
    ) ||
    (["complete", "not_required"].includes(String(facts.status)) &&
      (facts.success !== true ||
        facts.source_status === "unknown" ||
        ["unknown", "failed", "pending"].includes(
          String(facts.database_status),
        )))
  )
    return {
      status: "unknown",
      retryable: false,
      message:
        "This source result could not be verified. Keep the original task and check its status.",
      facts: record(facts) ? (facts as JsonRecord) : {},
    };
  return {
    status: facts.status as SourceResult["status"],
    retryable:
      facts.retryable === true &&
      facts.source_status !== "unknown" &&
      facts.database_status !== "unknown" &&
      facts.status === "failed",
    message:
      typeof facts.message === "string"
        ? facts.message
        : "Source outcome requires attention.",
    facts: facts as JsonRecord,
  };
}
export function pendingTrack(track: PendingTrack): WorkshopTrack {
  return {
    id: track.track_uri.split(":").pop()!,
    uri: track.track_uri,
    name: track.track_name,
    artists: (track.track_artists ?? "").split(", ").filter(Boolean),
    album_name: track.track_album ?? "",
    album_image_url: track.track_image_url,
    duration_ms: track.track_duration_ms ?? 0,
    is_local: false,
    added_at: null,
    artist_urls: [],
    track_url: `https://open.spotify.com/track/${track.track_uri.split(":").pop()}`,
  } as WorkshopTrack;
}
export const taskPath = (target: string, kind: SourceTask["kind"]) =>
  `/api/playlist/${encodeURIComponent(target)}` +
  {
    raid_promotion: "/pending-raids/finalize",
    archive_restore: "/pair/finalize-restore",
    archive_append: "/pair/archive",
  }[kind];
export const taskOperationKind = (kind: SourceTask["kind"]) =>
  ({
    raid_promotion: "workshop_finalize_raids",
    archive_restore: "workshop_finalize_restore",
    archive_append: "workshop_archive_append",
  })[kind];
