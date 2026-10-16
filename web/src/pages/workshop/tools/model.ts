import type { Algorithm } from "../../../api/contracts.ts";
import type { ExternalIntent, Snapshot, TrackLock } from "./types.ts";
export const trackURI = (v: unknown): v is string =>
  typeof v === "string" && /^spotify:track:[A-Za-z0-9]{22}$/u.test(v);
export function parameterDefaults(
  algorithm: Algorithm,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(algorithm.parameters).map(([key, p]) => [
      key,
      String(p.default),
    ]),
  );
}
export function parameterErrors(
  algorithm: Algorithm,
  values: Readonly<Record<string, string>>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [key, p] of Object.entries(algorithm.parameters)) {
    const value = values[key] ?? "";
    if (p.type === "string") {
      if (!p.options.includes(value))
        errors[key] = "Choose one of the listed options.";
      continue;
    }
    const number = Number(value);
    if (!value.trim() || !Number.isFinite(number))
      errors[key] = "Enter a number.";
    else if (p.type === "integer" && !Number.isInteger(number))
      errors[key] = "Enter a whole number.";
    else if (p.min !== undefined && number < p.min)
      errors[key] = `Enter at least ${p.min}.`;
    else if (p.max !== undefined && number > p.max)
      errors[key] = `Enter at most ${p.max}.`;
  }
  return errors;
}
export function activeLockMap(
  locks: readonly Pick<TrackLock, "track_uri" | "lock_tier" | "expires_at">[],
  now = Date.now(),
): Record<string, boolean> {
  return Object.fromEntries(
    locks
      .filter(
        (l) =>
          l.lock_tier === "super" ||
          l.expires_at === null ||
          (snapshotDate(l.expires_at)?.getTime() ?? 0) > now,
      )
      .map((l) => [l.track_uri, true]),
  );
}
export function lockLabel(
  lock: Pick<TrackLock, "lock_tier" | "expires_at">,
): string {
  return lock.lock_tier === "super"
    ? "Super-locked"
    : lock.expires_at && Number.isFinite(Date.parse(lock.expires_at))
      ? `Locked until ${snapshotDate(lock.expires_at)!.toLocaleDateString()}`
      : "Locked";
}
export function lockAction(
  lock: Pick<TrackLock, "lock_tier"> | undefined,
): string {
  return !lock
    ? "Lock for 30 days"
    : lock.lock_tier === "standard"
      ? "Lock permanently"
      : "Unlock";
}
export function externalIntent(value: string): ExternalIntent {
  const input = value.trim();
  if (!input) throw Error("Enter a playlist link or a playlist name.");
  if (input.length > 200) throw Error("Use at most 200 characters.");
  return /^(?:https?:\/\/|spotify:playlist:)/iu.test(input) ||
    /^[A-Za-z0-9]{22}$/u.test(input)
    ? { url: input }
    : { query: input };
}
export function duration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return "Duration unavailable";
  const seconds = Math.floor(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
export function snapshotType(type: string): string {
  return (
    (
      {
        auto_pre_shuffle: "Pre-shuffle",
        auto_pre_raid: "Pre-Collect",
        auto_pre_commit: "Workshop backup",
        auto_pre_rotate: "Before rotation",
        auto_pre_drip: "Before Drip",
        scheduled_pre_execution: "Auto-backup",
        manual: "Manual",
      } as Record<string, string>
    )[type] ?? "Snapshot"
  );
}
export function snapshotDate(value: string | null): Date | null {
  if (!value) return null;
  const d = new Date(
    /(?:Z|[+-]\d{2}:\d{2})$/iu.test(value) ? value : `${value}Z`,
  );
  return Number.isFinite(d.getTime()) ? d : null;
}
export function groupSnapshots<T extends Pick<Snapshot, "created_at">>(
  snapshots: readonly T[],
  now = Date.now(),
): { label: string; snapshots: T[] }[] {
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const groups = [
    { label: "Today", snapshots: [] as T[] },
    { label: "Yesterday", snapshots: [] as T[] },
    { label: "Older", snapshots: [] as T[] },
  ];
  for (const snapshot of snapshots) {
    const date = snapshotDate(snapshot.created_at);
    const i = date && date >= today ? 0 : date && date >= yesterday ? 1 : 2;
    groups[i]!.snapshots.push(snapshot);
  }
  return groups.filter((g) => g.snapshots.length > 0);
}
export function trackDifference(count: number, current: number): string {
  const delta = count - current;
  return delta === 0
    ? "Same track count"
    : `${delta > 0 ? "+" : "−"}${Math.abs(delta)} track${Math.abs(delta) === 1 ? "" : "s"}`;
}
export function relativeTime(value: string | null, now = Date.now()): string {
  const date = snapshotDate(value);
  if (!date) return "Date unavailable";
  const age = Math.max(0, now - date.getTime());
  if (age < 60000) return "Just now";
  if (age < 3600000) {
    const n = Math.floor(age / 60000);
    return `${n} minute${n === 1 ? "" : "s"} ago`;
  }
  if (age < 86400000) {
    const n = Math.floor(age / 3600000);
    return `${n} hour${n === 1 ? "" : "s"} ago`;
  }
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
