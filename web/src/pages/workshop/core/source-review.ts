import type {
  AdmissionRequest,
  ReviewedContinuation,
} from "../../../operations/admissions.ts";
import { ProtocolError } from "../../../api/errors.ts";
import { sameOrder, freeze } from "./occurrences.ts";
import type { SourcePrepareInput, SourceReviewedWork } from "./source-types.ts";
const equal = (
  a: readonly (string | number)[],
  b: readonly (string | number)[],
) => a.length === b.length && a.every((v, i) => v === b[i]);
/** Source controllers may propose only the exact captured staging and fresh identities. */
export function validateSourceReview(
  input: SourcePrepareInput,
  work: SourceReviewedWork,
): SourceReviewedWork {
  const no = () => {
    throw new ProtocolError(
      "The source review does not match its selected tasks, source identities or saved order.",
    );
  };
  if (!work || !Array.isArray(work.tasks)) return no();
  const stages = input.staging.filter((s) =>
    s.kind === "raid_promotion" ? s.pending_rows.length : s.track_uris.length,
  );
  if (work.tasks.length !== stages.length) return no();
  const matched = new Set<number>(),
    ids = new Set<string>(),
    keys = new Set<string>();
  for (const [at, task] of work.tasks.entries()) {
    if (
      !task ||
      !["raid_promotion", "archive_restore", "archive_append"].includes(
        task.kind,
      ) ||
      !task.id ||
      !task.key ||
      !task.label.trim() ||
      ids.has(task.id) ||
      keys.has(task.key) ||
      !Array.isArray(task.dependsOn) ||
      new Set(task.dependsOn).size !== task.dependsOn.length ||
      task.dependsOn.some((id: string) => !ids.has(id)) ||
      !task.body ||
      !Array.isArray(task.body.target_track_uris) ||
      !sameOrder(task.body.target_track_uris, input.desired)
    )
      return no();
    const expected =
      task.kind === "raid_promotion"
        ? input.sourceContext.raid
        : input.sourceContext.archive;
    if (
      (task.source?.id ?? null) !== (expected?.id ?? null) ||
      (task.source?.name ?? null) !== (expected?.name ?? null) ||
      task.body.source_playlist_id !== (expected?.id ?? null)
    )
      return no();
    const index = stages.findIndex(
      (stage, i) =>
        !matched.has(i) &&
        stage.kind === task.kind &&
        stage.source_playlist_id === task.body.source_playlist_id &&
        (stage.kind === "raid_promotion" && task.kind === "raid_promotion"
          ? Array.isArray(task.body.track_ids) &&
            equal(
              stage.pending_rows.map((r) => r.id),
              task.body.track_ids,
            )
          : stage.kind !== "raid_promotion" &&
            task.kind !== "raid_promotion" &&
            Array.isArray(task.body.track_uris) &&
            sameOrder(stage.track_uris, task.body.track_uris)),
    );
    const stage = stages[index];
    if (
      !stage ||
      task.selectedCount !==
        (stage.kind === "raid_promotion"
          ? stage.pending_rows.length
          : stage.track_uris.length)
    )
      return no();
    const selected =
      stage.kind === "raid_promotion"
        ? stage.pending_rows.map((r) => r.track_uri)
        : stage.track_uris;
    if (
      task.kind !== "archive_append" &&
      selected.some((u) => !input.desired.includes(u))
    )
      return no();
    if (
      task.kind === "raid_promotion" &&
      (new Set(task.body.track_ids).size !== task.body.track_ids.length ||
        task.body.track_ids.some(
          (id: number) => !Number.isSafeInteger(id) || id < 1,
        ))
    )
      return no();
    if (task.kind === "archive_append") {
      if (!input.sourceContext.archive) return no();
      const earlier = work.tasks
        .slice(0, at)
        .filter(
          (t) => t.source?.id === expected?.id && t.kind !== "archive_append",
        );
      if (
        !equal(
          task.dependsOn,
          earlier.map((t) => t.id),
        )
      )
        return no();
      const removed = new Set(
        earlier.flatMap((t) =>
          t.kind === "raid_promotion"
            ? stages
                .filter((s) => s.kind === "raid_promotion")
                .flatMap((s) => s.pending_rows.map((r) => r.track_uri))
            : t.body.track_uris,
        ),
      );
      const baseline = input.sourceContext.archive.baseline_track_uris.filter(
        (u) => !removed.has(u),
      );
      if (
        !Array.isArray(task.body.baseline_track_uris) ||
        !sameOrder(task.body.baseline_track_uris, baseline)
      )
        return no();
    } else if (task.dependsOn.length) return no();
    matched.add(index);
    ids.add(task.id);
    keys.add(task.key);
  }
  return freeze(structuredClone(work));
}

export function continuationRequests(
  target: string,
  work: SourceReviewedWork,
): ReviewedContinuation[] {
  const paths = {
    raid_promotion: "/pending-raids/finalize",
    archive_restore: "/pair/finalize-restore",
    archive_append: "/pair/archive",
  };
  const kinds = {
    raid_promotion: "workshop_finalize_raids",
    archive_restore: "workshop_finalize_restore",
    archive_append: "workshop_archive_append",
  };
  return work.tasks.map((task) => ({
    dependsOn: task.dependsOn.map(
      (id) => work.tasks.find((t) => t.id === id)!.key,
    ),
    request: {
      key: task.key,
      feature: "workshop",
      label: task.label,
      resources: [
        { resource: `playlist:${target}`, access: "read" },
        ...(task.source
          ? [
              {
                resource: `playlist:${task.source.id}`,
                access: "write" as const,
              },
            ]
          : []),
      ],
      path: `/api/playlist/${encodeURIComponent(target)}${paths[task.kind]}`,
      body: {
        encoding: "json",
        value: structuredClone(
          task.body,
        ) as unknown as import("../../../api/contracts.ts").JsonValue,
      },
      expected: { kind: kinds[task.kind], target },
    } satisfies AdmissionRequest,
  }));
}
