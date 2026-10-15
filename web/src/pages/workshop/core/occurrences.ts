import type { WorkshopTrack } from "../../../api/contracts.ts";
import type { DeepReadonly, Occurrence, OrderSummary } from "./types.ts";
let issued = 0;
export const sameOrder = (a: readonly string[], b: readonly string[]) =>
  a.length === b.length && a.every((u, i) => u === b[i]);
export const uris = (rows: readonly Occurrence[]) => rows.map((r) => r.uri);
export function freeze<T>(value: T): DeepReadonly<T> {
  if (value && typeof value === "object") {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value as DeepReadonly<T>;
}
export function placeholder(uri: string): WorkshopTrack {
  const id = uri.split(":")[2] ?? "";
  return {
    id,
    uri,
    name: "Saved track",
    artists: [],
    artist_urls: [],
    duration_ms: null,
    is_local: false,
    added_at: null,
    album_name: null,
    album_image_url: null,
    track_url: /^[A-Za-z0-9]{22}$/u.test(id)
      ? `https://open.spotify.com/track/${id}`
      : null,
  };
}
function occurrence(track: DeepReadonly<WorkshopTrack>): Occurrence {
  return freeze({
    key: `o${++issued}`,
    uri: track.uri,
    track: {
      ...track,
      artists: [...track.artists],
      artist_urls: [...track.artist_urls],
    },
  });
}
export const occurrences = (tracks: readonly DeepReadonly<WorkshopTrack>[]) =>
  tracks.map(occurrence);
/** Reuse distinct occurrences FIFO; never collapse duplicate metadata into a URI map. */
export function remap(
  previous: readonly Occurrence[],
  next: readonly string[],
  tracks: readonly DeepReadonly<WorkshopTrack>[] = [],
): Occurrence[] {
  const queues = new Map<string, Occurrence[]>();
  previous.forEach((r) => queues.set(r.uri, [...(queues.get(r.uri) ?? []), r]));
  const supplied = new Map<string, DeepReadonly<WorkshopTrack>[]>();
  tracks.forEach((t) =>
    supplied.set(t.uri, [...(supplied.get(t.uri) ?? []), t]),
  );
  return next.map((uri) => {
    const old = queues.get(uri)?.shift();
    const suppliedTrack = supplied.get(uri)?.shift();
    return (
      old ??
      occurrence(
        suppliedTrack ??
          previous.find((r) => r.uri === uri)?.track ??
          placeholder(uri),
      )
    );
  });
}
export function moveOccurrence(
  rows: readonly Occurrence[],
  key: string,
  position: number,
): Occurrence[] {
  const from = rows.findIndex((r) => r.key === key);
  if (from < 0 || !Number.isFinite(position)) return [...rows];
  const to = Math.max(0, Math.min(rows.length - 1, Math.trunc(position) - 1));
  const copy = [...rows];
  const [r] = copy.splice(from, 1);
  copy.splice(to, 0, r!);
  return copy;
}
export const removeOccurrence = (rows: readonly Occurrence[], key: string) =>
  rows.filter((r) => r.key !== key);
export function summarize(
  before: readonly string[],
  after: readonly string[],
): OrderSummary {
  const counts = (list: readonly string[]) => {
    const m = new Map<string, number>();
    list.forEach((u) => m.set(u, (m.get(u) ?? 0) + 1));
    return m;
  };
  const a = counts(before),
    b = counts(after);
  let added_count = 0,
    removed_count = 0;
  for (const u of new Set([...a.keys(), ...b.keys()])) {
    added_count += Math.max(0, (b.get(u) ?? 0) - (a.get(u) ?? 0));
    removed_count += Math.max(0, (a.get(u) ?? 0) - (b.get(u) ?? 0));
  }
  const shared = (list: readonly string[], other: Map<string, number>) => {
    const seen = new Map<string, number>();
    return list.filter((u) => {
      const n = (seen.get(u) ?? 0) + 1;
      seen.set(u, n);
      return n <= (other.get(u) ?? 0);
    });
  };
  return {
    added_count,
    removed_count,
    order_changed: !sameOrder(shared(before, b), shared(after, a)),
  };
}
/** Current-state observations replace presentation per occurrence, preserving only the UI key FIFO. */
export function observedOccurrences(
  previous: readonly Occurrence[],
  tracks: readonly DeepReadonly<WorkshopTrack>[],
): Occurrence[] {
  const rows = remap(
    previous,
    tracks.map((t) => t.uri),
    tracks,
  );
  return rows.map((row, i) =>
    freeze({ ...row, track: structuredClone(tracks[i]!) }),
  );
}
