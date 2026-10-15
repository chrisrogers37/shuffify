import { verifiedRestore } from "./client.ts";
import type {
  Permission,
  PermissionGroup,
  WorkshopState,
  Receipt,
} from "./types.ts";
export function receiptNeedsAttention(receipt: Receipt | null): boolean {
  if (!receipt) return false;
  if (
    receipt.status === "unknown" ||
    receipt.operation?.state === "needs_attention" ||
    ["partial", "unknown"].includes(receipt.result?.target_status ?? "")
  )
    return true;
  if (receipt.status !== "settled") return false;
  if (receipt.review.kind === "privacy")
    return (
      receipt.operation?.state !== "succeeded" ||
      receipt.operation.result?.success !== true ||
      typeof receipt.operation.result.public !== "boolean"
    );
  if (receipt.review.kind === "snapshot_restore")
    return (
      !receipt.operation || !verifiedRestore(receipt.operation, receipt.review)
    );
  const result = receipt.result;
  if (!result) return true;
  if (["saved", "already_applied"].includes(result.target_status))
    return (
      receipt.operation?.state !== "succeeded" ||
      receipt.operation.result?.success !== true ||
      !result.saved_track_uris ||
      result.saved_track_uris.length !== receipt.review.desired.length ||
      result.saved_track_uris.some(
        (u, i) => u !== receipt.review.desired[i]?.uri,
      )
    );
  return false;
}
export function workshopPermission(
  state: WorkshopState,
  group: PermissionGroup,
  target: string,
  authenticated: boolean,
  subject?: string,
): Permission {
  const no = (reason: string) => ({ allowed: false, reason });
  if (!authenticated)
    return no(
      "Confirm the original signed-in account before changing this Workshop.",
    );
  if (state.phase !== "ready")
    return no("Load this playlist before continuing.");
  if (group === "pause") return { allowed: true, reason: "" };
  const receipt = state.receipt;
  const unresolved =
    receipt &&
    (["submitting", "accepted"].includes(receipt.status) ||
      receiptNeedsAttention(receipt));
  if (unresolved) {
    const kind = receipt.review.kind;
    const blocked =
      kind === "privacy"
        ? ["metadata", "save", "review", "snapshot"].includes(group)
        : kind === "snapshot_restore"
          ? ![
              "collect",
              "routine",
              "snapshot-read",
              "snapshot-create",
              "snapshot-delete",
            ].includes(group) ||
            (group === "snapshot-delete" &&
              subject ===
                String(
                  receipt.review.kind === "snapshot_restore"
                    ? receipt.review.snapshotId
                    : "",
                ))
          : group !== "snapshot-read";
    if (blocked)
      return no(
        "Reconcile the retained operation and its result before changing this target.",
      );
  }
  if (["snapshot-read", "snapshot-create", "snapshot-delete"].includes(group)) {
    const scoped = state.claims.find(
      (c) => c.access === "write" && c.resource === `snapshot:${subject ?? ""}`,
    );
    return scoped ? no(scoped.reason) : { allowed: true, reason: "" };
  }
  if (
    ["save", "review", "snapshot", "metadata", "preview"].includes(group) &&
    Date.now() < state.retryUntil
  )
    return no(
      "Wait for the full server retry interval before submitting again.",
    );
  const resources =
    group === "collect"
      ? [`inbox:${target}`]
      : group === "routine"
        ? [`routine:${subject ?? target}`]
        : group === "metadata"
          ? [`metadata:${target}`]
          : [`tracks:${target}`, `history:${target}`];
  const claim = state.claims.find(
    (c) => c.access === "write" && resources.includes(c.resource),
  );
  return claim ? no(claim.reason) : { allowed: true, reason: "" };
}
