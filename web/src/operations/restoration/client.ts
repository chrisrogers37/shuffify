import { isOperation } from "../../api/client.ts";
import type { ApiClient } from "../../api/client.ts";
import type {
  Operation,
  OperationRestoreContext,
  OperationRestoreRequest,
} from "../../api/contracts.ts";
import { ProtocolError } from "../../api/errors.ts";

export type RestoreContext = OperationRestoreContext["context"];
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const id = (value: unknown): value is number =>
  Number.isSafeInteger(value) && Number(value) > 0;
const count = (value: unknown): value is number =>
  Number.isSafeInteger(value) && Number(value) >= 0;
const digest = (value: unknown) =>
  typeof value === "string" && /^[0-9a-f]{64}$/u.test(value);
const playlist = (value: unknown): value is string =>
  typeof value === "string" && /^[A-Za-z0-9_-]{1,255}$/u.test(value);
const track = (value: unknown) =>
  typeof value === "string" && /^spotify:track:[A-Za-z0-9]{22}$/u.test(value);
const same = (left: readonly string[], right: readonly string[]) => {
  const ordered = [...right].sort();
  return (
    left.length === right.length &&
    [...left].sort().every((value, index) => value === ordered[index])
  );
};
const keys = (value: Record<string, unknown>, allowed: readonly string[]) =>
  Object.keys(value).every((key) => allowed.includes(key));
function fail(): never {
  throw new ProtocolError(
    "The restoration review is incomplete or names another resource. Load it again.",
  );
}

export function freeze<T>(value: T): T {
  const copy = structuredClone(value);
  function visit(item: unknown) {
    if (item !== null && typeof item === "object") {
      Object.values(item).forEach(visit);
      Object.freeze(item);
    }
  }
  visit(copy);
  return copy;
}

export function assertRestoreContext(
  value: unknown,
  parent: Operation,
): asserts value is RestoreContext {
  if (
    !isOperation(parent) ||
    !id(parent.id) ||
    parent.kind === "operation_restore" ||
    parent.state !== "needs_attention" ||
    parent.recovery.available !== true ||
    parent.recovery.action !== "restore" ||
    !parent.targets.length ||
    parent.targets.length > 100 ||
    !parent.targets.every(playlist) ||
    new Set(parent.targets).size !== parent.targets.length ||
    !record(value) ||
    !keys(value, [
      "kind",
      "operation_id",
      "available",
      "reviewed",
      "resources",
    ]) ||
    value.kind !== "operation_restore" ||
    value.operation_id !== parent.id ||
    value.available !== true ||
    !record(value.reviewed) ||
    !keys(value.reviewed, [
      "version",
      "reviewed_scope",
      "reviewed_resources",
    ]) ||
    value.reviewed.version !== 1 ||
    !digest(value.reviewed.reviewed_scope) ||
    !Array.isArray(value.reviewed.reviewed_resources) ||
    !Array.isArray(value.resources) ||
    !value.resources.length ||
    value.resources.length > 100 ||
    value.resources.length !== value.reviewed.reviewed_resources.length
  )
    fail();
  const review = value.reviewed.reviewed_resources as unknown[];
  const resourceIds: string[] = [];
  for (const [index, resource] of (value.resources as unknown[]).entries()) {
    const binding = review[index];
    if (
      !record(resource) ||
      !keys(resource, [
        "playlist_id",
        "name",
        "role",
        "scope",
        "write_fields",
        "current_count",
        "desired_count",
        "current_preview",
        "desired_preview",
        "current_metadata",
        "desired_metadata",
        "metadata_truncated",
      ]) ||
      !playlist(resource.playlist_id) ||
      !record(binding) ||
      !keys(binding, ["playlist_id", "digest"]) ||
      binding.playlist_id !== resource.playlist_id ||
      !digest(binding.digest) ||
      !(
        resource.name === null ||
        (typeof resource.name === "string" && resource.name.length <= 255)
      ) ||
      !["restore", "guard"].includes(String(resource.role)) ||
      !["tracks", "metadata"].includes(String(resource.scope)) ||
      !Array.isArray(resource.write_fields) ||
      resource.write_fields.length > 4 ||
      new Set(resource.write_fields).size !== resource.write_fields.length ||
      !resource.write_fields.every((field) =>
        ["tracks", "name", "public", "description"].includes(String(field)),
      ) ||
      !record(resource.current_metadata) ||
      !record(resource.desired_metadata) ||
      typeof resource.metadata_truncated !== "boolean"
    )
      fail();
    const fields = resource.write_fields as string[];
    if (
      (resource.role === "guard" ? fields.length !== 0 : fields.length === 0) ||
      (resource.scope === "metadata" && fields.includes("tracks"))
    )
      fail();
    for (const [side, preview, total] of [
      ["current", resource.current_preview, resource.current_count],
      ["desired", resource.desired_preview, resource.desired_count],
    ] as const) {
      if (
        !Array.isArray(preview) ||
        preview.length > 20 ||
        !preview.every(track) ||
        !(total === null || count(total)) ||
        (total !== null && total < preview.length) ||
        (resource.scope === "metadata" &&
          (total !== null || preview.length !== 0)) ||
        (resource.scope === "tracks" && total === null)
      )
        fail();
      const metadata = resource[`${side}_metadata`] as Record<string, unknown>;
      const metadataFields = fields.filter((field) => field !== "tracks");
      if (!same(Object.keys(metadata), metadataFields)) fail();
      for (const [field, item] of Object.entries(metadata)) {
        if (item === null && side === "current") continue;
        if (
          field === "public"
            ? typeof item !== "boolean"
            : typeof item !== "string" || item.length > 1024
        )
          fail();
      }
    }
    resourceIds.push(resource.playlist_id as string);
  }
  if (
    !same(resourceIds, parent.targets) ||
    new Set(resourceIds).size !== resourceIds.length ||
    resourceIds.some((value, index) => value !== [...resourceIds].sort()[index])
  )
    fail();
}

export async function loadRestoreReview(
  api: ApiClient,
  parent: Operation,
): Promise<RestoreContext> {
  if (!id(parent.id))
    throw new TypeError("A known original operation ID is required.");
  const reply = await api.request<OperationRestoreContext>(
    `/api/operations/${parent.id}/restore-context`,
  );
  if (reply.status !== "completed") fail();
  assertRestoreContext(reply.data.context, parent);
  return freeze(reply.data.context);
}

export function restoreRequest(
  context: RestoreContext,
): OperationRestoreRequest {
  return freeze({ reviewed: context.reviewed });
}

export function assertRestoreChild(
  value: unknown,
  parent: Operation,
  context: RestoreContext,
): asserts value is Operation {
  if (
    !isOperation(value) ||
    value.id === parent.id ||
    value.kind !== "operation_restore" ||
    !same(value.targets, parent.targets) ||
    new Set(value.targets).size !== value.targets.length ||
    !same(
      value.targets,
      context.reviewed.reviewed_resources.map(
        (resource) => resource.playlist_id,
      ),
    )
  ) {
    throw new ProtocolError(
      "The response did not identify the reviewed restoration operation. Keep this exact request.",
    );
  }
}

export function restorationResolved(
  original: Operation,
  child: Operation,
  parent: Operation,
): boolean {
  return (
    isOperation(child) &&
    isOperation(parent) &&
    child.kind === "operation_restore" &&
    child.id !== original.id &&
    parent.id === original.id &&
    child.state === "succeeded" &&
    child.result?.success === true &&
    child.result.parent_operation_id === original.id &&
    child.result.parent_resolution === "complete" &&
    id(parent.recovery.resolved_operation_id) &&
    parent.recovery.resolved_operation_id === child.id &&
    parent.recovery.reason === "operation_already_restored" &&
    same(parent.targets, original.targets) &&
    same(child.targets, original.targets) &&
    new Set(child.targets).size === child.targets.length
  );
}
