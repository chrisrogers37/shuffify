import type { RoutineRow } from "../../api/contracts";

type Timing = RoutineRow["timing"];

/** The timing text with its zone, naming the zone only once. */
export function timingLine(timing: Timing, missingZone?: string): string {
  const zone = timing.effective_timezone ?? missingZone;
  return zone && !timing.text.includes(zone)
    ? `${timing.text} · ${zone}`
    : timing.text;
}

/** Plain explanation for a routine that runs on the scheduler's own clock. */
export function legacyTimingNote(timing: Timing): string {
  const zone = timing.effective_timezone
    ? ` (${timing.effective_timezone})`
    : "";
  return `This routine runs on the scheduler's clock${zone}. Change its timing to pick a timezone.`;
}

/** A moment in the viewer's local time, with the zone name. */
export function formatNextRun(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}
