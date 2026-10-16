import { sourceSettlement, unidentifiedCreation } from "./domain-evidence.ts";
import type {
  UnidentifiedCreationAcknowledgement,
  UnidentifiedCreatorKind,
} from "./domain-evidence.ts";
import type { ApiClient, ApiResult } from "../api/client.ts";
import type { JsonValue, Operation } from "../api/contracts.ts";
import {
  ApiRequestError,
  ConflictError,
  MissingCsrfTokenError,
  ProtocolError,
  RateLimitError,
} from "../api/errors.ts";
import { getOperationStore, terminalOperationStates } from "./store.ts";
import type { OperationStore } from "./store.ts";
export interface AdmissionResource {
  readonly resource: string;
  readonly access: "read" | "write";
}
export interface AdmissionRequest {
  readonly key: string;
  readonly feature:
    | "library"
    | "routines"
    | "setup"
    | "workshop"
    | "restoration";
  readonly label: string;
  readonly resources: readonly AdmissionResource[];
  readonly path: string;
  readonly body:
    | { readonly encoding: "json"; readonly value: JsonValue }
    | {
        readonly encoding: "form";
        readonly value: Readonly<Record<string, string>>;
      };
  readonly expected: { readonly kind: string; readonly target: string };
}
type ReadonlyDeep<T> = T extends readonly (infer E)[]
  ? readonly ReadonlyDeep<E>[]
  : T extends object
    ? { readonly [K in keyof T]: ReadonlyDeep<T[K]> }
    : T;
export interface RestorationAdmissionContext {
  readonly kind: "operation_restore";
  readonly operation_id: number;
  readonly available: true;
  readonly reviewed: {
    readonly version: 1;
    readonly reviewed_scope: string;
    readonly reviewed_resources: readonly {
      readonly playlist_id: string;
      readonly digest: string;
    }[];
  };
}
export interface RestorationAdmission {
  readonly parent: Operation;
  readonly context: RestorationAdmissionContext;
  readonly request: AdmissionRequest;
}
export interface ContinuationRetryReview {
  readonly failedKey: string;
  readonly request: AdmissionRequest;
  readonly currentTarget: {
    readonly id: string;
    readonly track_uris: readonly string[];
  };
  readonly sourceContext: {
    readonly target: { readonly id: string; readonly name: string };
    readonly raid: { readonly id: string; readonly name: string } | null;
    readonly archive: { readonly id: string; readonly name: string } | null;
  };
}
export interface ReviewedContinuation {
  readonly request: AdmissionRequest;
  readonly dependsOn: readonly string[];
}
export interface Admission {
  readonly request: ReadonlyDeep<AdmissionRequest>;
  readonly status:
    | "reserved"
    | "submitting"
    | "unknown"
    | "accepted"
    | "attention"
    | "settled"
    | "refused";
  readonly operationId: number | null;
  readonly retryUntil: number;
  readonly relation: "original" | "blocking" | null;
  readonly conclusiveRefusal?: 400 | 401 | 403;
  readonly resolution?: {
    readonly kind: "restored";
    readonly operationId: number;
  };
  readonly restoration?: {
    readonly parentId: number;
    readonly parentTargets: readonly string[];
    readonly resolved: boolean;
  };
  readonly acknowledgedOrphan?: UnidentifiedCreationAcknowledgement;
  readonly continuation?: {
    readonly parentKey: string;
    readonly activated: boolean;
    readonly dependsOn: readonly string[];
  };
}
const record = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
function frozen<T>(value: T): ReadonlyDeep<T> {
  const copy = structuredClone(value);
  const freeze = (v: unknown) => {
    if (v && typeof v === "object") {
      Object.values(v).forEach(freeze);
      Object.freeze(v);
    }
  };
  freeze(copy);
  return copy as ReadonlyDeep<T>;
}
const held = (a: Admission) =>
  !a.acknowledgedOrphan &&
  ((!!a.restoration && !a.restoration.resolved) ||
    !["settled", "refused"].includes(a.status));
const sameTargets = (a: readonly string[], b: readonly string[]) =>
  a.length > 0 &&
  new Set(a).size === a.length &&
  new Set(b).size === b.length &&
  JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
export class AdmissionBlockedError extends ApiRequestError {
  constructor(admission: Admission) {
    super(409, {
      success: false,
      error_code: "local_admission_conflict",
      category: "error",
      message: `${admission.request.label} is not reconciled. Open its original review before starting another Spotify change.`,
    });
    this.name = "AdmissionBlockedError";
  }
}
/** Spotify read claims matter too: a later write cannot invalidate a pending source read. */
export function spotifyResources(
  target: string,
  reviewed: readonly {
    resource: string;
    domain?: string;
    access: string;
  }[] = [],
  sources: readonly (string | null)[] = [],
): AdmissionResource[] {
  const values = new Map<string, "read" | "write">([
    [`playlist:${target}`, "write"],
  ]);
  for (const r of reviewed)
    if (
      r.domain === "spotify" &&
      r.resource &&
      ["read", "write"].includes(r.access)
    ) {
      const id = `playlist:${r.resource}`;
      values.set(
        id,
        values.get(id) === "write" ? "write" : (r.access as "read" | "write"),
      );
    }
  for (const id of sources) if (id) values.set(`playlist:${id}`, "write");
  return [...values].map(([resource, access]) => ({ resource, access }));
}
/** A terminal error is not evidence that Spotify effects or history were undone. */
export function safelySettled(operation: Operation): boolean {
  const facts = operation.result,
    detail = record(facts?.operation) ? facts.operation : null;
  if (
    operation.state === "needs_attention" ||
    !terminalOperationStates.has(operation.state)
  )
    return false;
  if (
    [
      facts?.source_status,
      facts?.database_status,
      facts?.aggregate_status,
    ].some((s) => ["unknown", "needs_attention"].includes(String(s)))
  )
    return false;
  const bookkeepingFailure = (value: unknown): boolean => {
    if (Array.isArray(value))
      return value.some(
        (item) =>
          record(item) &&
          item.required !== false &&
          ["failed", "partial", "unknown", "unconfirmed"].includes(
            String(item.status),
          ),
      );
    return (
      record(value) &&
      (["failed", "partial", "unknown", "unconfirmed"].includes(
        String(value.aggregate_status),
      ) ||
        Object.values(value).some((v) =>
          ["failed", "unknown", "unconfirmed"].includes(String(v)),
        ))
    );
  };
  if (
    bookkeepingFailure(facts?.bookkeeping) ||
    bookkeepingFailure(detail?.bookkeeping)
  )
    return false;
  if (
    ["partial", "unknown"].includes(String(facts?.target_status)) ||
    ["unknown", "needs_attention"].includes(String(detail?.aggregate_status))
  )
    return false;
  const source = sourceSettlement(operation);
  if (source !== null) return source;
  if (operation.state === "rolled_back") {
    // Worker publishes this state only after verified compensation and history staging.
    // A routine's retained earlier steps/restored current step make aggregate partial.
    if (
      Array.isArray(detail?.resources) &&
      detail.resources.some(
        (r) =>
          record(r) &&
          ["unknown", "partial", "failed"].includes(String(r.status)),
      )
    )
      return false;
    return true;
  }
  if (
    operation.state === "failed" &&
    facts?.success === false &&
    Array.isArray(detail?.resources) &&
    detail.resources.length === 0
  )
    return true;
  if (detail?.aggregate_status === "partial") return false;
  if (
    typeof facts?.error_code === "string" &&
    /history|confirmation|partial|unknown|ambiguous/u.test(facts.error_code)
  )
    return false;
  if (operation.state === "succeeded") return facts?.success === true;
  return (
    facts?.target_status === "not_written" ||
    ["refused", "rate_limited", "not_written"].includes(
      String(facts?.outcome),
    ) ||
    ["refused", "not_written"].includes(String(detail?.aggregate_status))
  );
}
export function createAdmissionRegistry(
  api: ApiClient,
  actor: string,
  operations: OperationStore,
) {
  if (!actor.trim()) throw new TypeError("An authenticated actor is required.");
  let snapshot: ReadonlyMap<string, Admission> = new Map();
  const listeners = new Set<() => void>();
  const publishMany = (values: readonly [string, Admission][]) => {
    const next = new Map(snapshot);
    for (const [key, value] of values) next.set(key, Object.freeze(value));
    snapshot = next;
    listeners.forEach((fn) => fn());
  };
  const update = (key: string, value: Admission) => {
    snapshot = new Map(snapshot).set(key, Object.freeze(value));
    listeners.forEach((fn) => fn());
  };
  function conflict(
    resources: readonly AdmissionResource[],
    exceptKey?: string,
  ): Admission | null {
    for (const [key, a] of snapshot)
      if (
        key !== exceptKey &&
        held(a) &&
        resources.some((r) =>
          a.request.resources.some(
            (c) =>
              r.resource === c.resource &&
              (r.access === "write" || c.access === "write"),
          ),
        )
      )
        return a;
    return null;
  }
  function withKnownResources(a: Admission, operation: Operation): Admission {
    const resources = new Map(
      a.request.resources.map((r) => [r.resource, r.access]),
    );
    for (const id of operation.targets)
      if (id && !resources.has(`playlist:${id}`))
        resources.set(`playlist:${id}`, "write");
    const facts = operation.result,
      detail = record(facts?.operation) ? facts.operation : null;
    if (
      record(facts?.playlist) &&
      typeof facts.playlist.id === "string" &&
      facts.playlist.id
    )
      resources.set(`playlist:${facts.playlist.id}`, "write");
    if (Array.isArray(detail?.resources))
      for (const r of detail.resources)
        if (record(r) && typeof r.resource === "string" && r.resource)
          resources.set(`playlist:${r.resource}`, "write");
    return {
      ...a,
      request: frozen({
        ...a.request,
        resources: [...resources].map(([resource, access]) => ({
          resource,
          access,
        })),
      }),
    };
  }
  function refuseUnstartedContinuations(parentKey: string) {
    const values: [string, Admission][] = [];
    for (const [key, a] of snapshot)
      if (
        a.status === "reserved" &&
        a.continuation?.parentKey === parentKey &&
        !a.continuation.activated
      )
        values.push([key, { ...a, status: "refused" }]);
    if (values.length) publishMany(values);
  }
  function reconcile(key: string, operation: Operation) {
    const current = snapshot.get(key);
    if (
      !current ||
      current.acknowledgedOrphan ||
      current.operationId !== operation.id
    )
      return false;
    if (current.relation === "blocking") {
      const refused = terminalOperationStates.has(operation.state);
      update(key, { ...current, status: refused ? "refused" : "accepted" });
      return refused;
    }
    if (
      (current.relation === "original" &&
        operation.kind !== current.request.expected.kind) ||
      !operation.targets.includes(current.request.expected.target)
    )
      return false;
    const settled = !current.restoration && safelySettled(operation);
    update(key, {
      ...withKnownResources(current, operation),
      status: settled
        ? "settled"
        : terminalOperationStates.has(operation.state)
          ? "attention"
          : "accepted",
    });
    if (settled && operation.state !== "succeeded")
      refuseUnstartedContinuations(key);
    return settled;
  }
  function accept(key: string, operation: Operation, observe = true) {
    const a = snapshot.get(key);
    if (!a || a.acknowledgedOrphan)
      throw new TypeError(
        "The original admission must be retained and unresolved.",
      );
    if (
      operation.kind !== a.request.expected.kind ||
      !operation.targets.includes(a.request.expected.target) ||
      (a.restoration &&
        (!sameTargets(a.restoration.parentTargets, operation.targets) ||
          operation.id === a.restoration.parentId))
    )
      throw new ProtocolError(
        "The accepted operation names a different action or target.",
      );
    update(key, {
      ...withKnownResources(a, operation),
      status: "accepted",
      operationId: operation.id,
      relation: "original",
    });
    if (observe) operations.accept(operation);
    reconcile(key, operation);
  }
  operations.subscribe(() => {
    for (const [key, a] of snapshot) {
      if (a.operationId === null || !held(a)) continue;
      const observed = operations.getSnapshot().get(a.operationId);
      if (
        a.relation === "blocking" &&
        observed?.observation === "unavailable"
      ) {
        update(key, { ...a, status: "refused" });
      } else if (observed?.operation) reconcile(key, observed.operation);
    }
  });
  async function perform(
    a: Admission,
    confirmedActor: string | null,
    wasUnknown: boolean,
  ): Promise<ApiResult<never>> {
    if (confirmedActor !== actor) throw new MissingCsrfTokenError();
    const r = a.request;
    update(r.key, { ...a, status: "submitting" });
    try {
      const body = r.body.encoding === "form" ? new FormData() : null;
      if (body && r.body.encoding === "form")
        for (const [k, v] of Object.entries(r.body.value)) body.set(k, v);
      const reply = await api.request<never>(r.path, {
        method: "POST",
        headers: { "Idempotency-Key": r.key },
        ...(body ? { body } : { json: r.body.value }),
      });
      if (reply.status !== "accepted")
        throw new ProtocolError(
          "This Spotify change did not identify an accepted operation.",
        );
      accept(r.key, reply.operation, false);
      return reply;
    } catch (error) {
      const busy =
        error instanceof ConflictError &&
        Number.isSafeInteger(error.error.operation_id)
          ? error.error.operation_id
          : null;
      const refused =
        !wasUnknown &&
        (error instanceof MissingCsrfTokenError ||
          (error instanceof ApiRequestError && error.status < 500));
      update(r.key, {
        ...a,
        status: wasUnknown
          ? "unknown"
          : busy
            ? "accepted"
            : refused
              ? "refused"
              : "unknown",
        operationId: wasUnknown ? null : (busy ?? null),
        relation: !wasUnknown && busy ? "blocking" : null,
        ...(!wasUnknown &&
        error instanceof ApiRequestError &&
        [400, 401, 403].includes(error.status)
          ? { conclusiveRefusal: error.status as 400 | 401 | 403 }
          : {}),
        retryUntil:
          error instanceof ApiRequestError && error.retryAfterMs !== null
            ? Date.now() + error.retryAfterMs
            : a.retryUntil,
      });
      if (refused) refuseUnstartedContinuations(r.key);
      if (busy) operations.observe(busy);
      throw error;
    }
  }
  function originalParent(a: Admission, parent: Operation) {
    return (
      a.relation === "original" &&
      a.operationId === parent.id &&
      a.request.expected.kind === parent.kind &&
      parent.targets.includes(a.request.expected.target)
    );
  }
  function knownBlockingParent(a: Admission, parent: Operation) {
    return (
      a.relation === "blocking" &&
      a.operationId === parent.id &&
      parent.targets.includes(a.request.expected.target)
    );
  }
  function relatedParent(a: Admission, parent: Operation) {
    return originalParent(a, parent) || knownBlockingParent(a, parent);
  }
  function unstartedParentSource(a: Admission, parent: Operation) {
    const origin = a.continuation
      ? snapshot.get(a.continuation.parentKey)
      : null;
    return (
      a.status === "reserved" &&
      !a.continuation?.activated &&
      !!origin &&
      originalParent(origin, parent)
    );
  }
  async function submitRestoration(
    input: RestorationAdmission,
    confirmedActor: string | null,
  ) {
    if (confirmedActor !== actor) throw new MissingCsrfTokenError();
    const { parent, context, request } = input;
    const digest = (v: unknown) =>
      typeof v === "string" && /^[0-9a-f]{64}$/u.test(v);
    if (
      !Number.isSafeInteger(parent.id) ||
      parent.id < 1 ||
      !terminalOperationStates.has(parent.state) ||
      parent.kind === "operation_restore" ||
      !parent.recovery.available ||
      parent.recovery.action !== "restore" ||
      context.kind !== "operation_restore" ||
      context.operation_id !== parent.id ||
      context.available !== true ||
      context.reviewed.version !== 1 ||
      !digest(context.reviewed.reviewed_scope) ||
      !sameTargets(
        context.reviewed.reviewed_resources.map((r) => r.playlist_id),
        parent.targets,
      ) ||
      context.reviewed.reviewed_resources.some((r) => !digest(r.digest)) ||
      request.feature !== "restoration" ||
      request.expected.kind !== "operation_restore" ||
      !parent.targets.includes(request.expected.target) ||
      request.path !== `/api/operations/${parent.id}/restore` ||
      request.body.encoding !== "json" ||
      JSON.stringify(request.body.value) !==
        JSON.stringify({ reviewed: context.reviewed }) ||
      !request.key ||
      !request.label.trim() ||
      parent.targets.some(
        (id) => !request.resources.some((r) => r.resource === `playlist:${id}`),
      )
    )
      throw new TypeError(
        "The restoration must name its fresh owned parent, exact reviewed scope and every original target.",
      );
    const existing = snapshot.get(request.key);
    if (existing && held(existing)) throw new AdmissionBlockedError(existing);
    const resources = new Map(
      request.resources.map((r) => [r.resource, r.access]),
    );
    for (const id of parent.targets) resources.set(`playlist:${id}`, "write");
    for (const a of snapshot.values()) {
      if (!held(a)) continue;
      const related =
        relatedParent(a, parent) || unstartedParentSource(a, parent);
      const prior = a.restoration?.parentId === parent.id;
      const child = a.operationId
        ? operations.getSnapshot().get(a.operationId)?.operation
        : null;
      // Only a fresh successful server review may follow a known failed/no-admission child.
      const freshRefusal =
        prior &&
        ((a.status === "refused" &&
          a.operationId === null &&
          a.relation === null) ||
          (a.relation === "original" &&
            child?.kind === "operation_restore" &&
            child.state === "failed"));
      if (prior && !freshRefusal) throw new AdmissionBlockedError(a);
      if (related || freshRefusal)
        for (const r of a.request.resources)
          resources.set(
            r.resource,
            resources.get(r.resource) === "write" ? "write" : r.access,
          );
    }
    const effective = frozen({
      ...request,
      resources: [...resources].map(([resource, access]) => ({
        resource,
        access,
      })),
    });
    for (const a of snapshot.values()) {
      if (
        !held(a) ||
        relatedParent(a, parent) ||
        unstartedParentSource(a, parent) ||
        a.restoration?.parentId === parent.id
      )
        continue;
      if (
        effective.resources.some((r) =>
          a.request.resources.some(
            (c) =>
              r.resource === c.resource &&
              (r.access === "write" || c.access === "write"),
          ),
        )
      )
        throw new AdmissionBlockedError(a);
    }
    const a: Admission = {
      request: effective,
      status: "submitting",
      operationId: null,
      retryUntil: 0,
      relation: null,
      restoration: frozen({
        parentId: parent.id,
        parentTargets: parent.targets,
        resolved: false,
      }),
    };
    return perform(a, confirmedActor, false);
  }
  function reconcileRestoration(
    key: string,
    evidence: { readonly child: Operation; readonly parent: Operation },
    confirmedActor: string | null,
  ) {
    const a = snapshot.get(key),
      { child, parent } = evidence;
    const recovery = parent.recovery as Operation["recovery"] & {
      resolved_operation_id?: number;
    };
    if (
      confirmedActor !== actor ||
      !a?.restoration ||
      a.restoration.resolved ||
      a.relation !== "original" ||
      a.operationId !== child.id ||
      child.id === parent.id ||
      a.restoration.parentId !== parent.id ||
      child.kind !== "operation_restore" ||
      !sameTargets(child.targets, a.restoration.parentTargets) ||
      !sameTargets(parent.targets, a.restoration.parentTargets) ||
      child.state !== "succeeded" ||
      !safelySettled(child) ||
      child.result?.parent_operation_id !== parent.id ||
      child.result.parent_resolution !== "complete" ||
      recovery.available ||
      recovery.reason !== "operation_already_restored" ||
      recovery.resolved_operation_id !== child.id
    )
      return false;
    for (const other of snapshot.values()) {
      if (
        !held(other) ||
        other.request.key === key ||
        other.restoration?.parentId !== parent.id
      )
        continue;
      const op = other.operationId
        ? operations.getSnapshot().get(other.operationId)?.operation
        : null;
      if (
        !(other.status === "refused" && !other.operationId) &&
        !(
          other.relation === "original" &&
          op?.kind === "operation_restore" &&
          op.state === "failed"
        )
      )
        return false;
    }
    const values: [string, Admission][] = [];
    for (const [k, other] of snapshot)
      if (
        relatedParent(other, parent) ||
        unstartedParentSource(other, parent) ||
        other.restoration?.parentId === parent.id
      )
        values.push([
          k,
          {
            ...other,
            status: other.relation === "blocking" ? "refused" : "settled",
            resolution: { kind: "restored", operationId: child.id },
            ...(other.restoration
              ? {
                  restoration: frozen({ ...other.restoration, resolved: true }),
                }
              : {}),
          },
        ]);
    publishMany(values);
    return true;
  }
  function unidentifiedCreationAcknowledgement(
    key: string,
    confirmedActor: string | null,
  ): UnidentifiedCreationAcknowledgement | null {
    const a = snapshot.get(key);
    const op = a?.operationId
      ? operations.getSnapshot().get(a.operationId)?.operation
      : null;
    if (
      confirmedActor !== actor ||
      !a ||
      a.acknowledgedOrphan ||
      a.relation !== "original" ||
      !op ||
      a.operationId !== op.id ||
      a.request.expected.kind !== op.kind ||
      a.request.expected.target !== op.targets[0] ||
      !unidentifiedCreation(op) ||
      a.request.resources.some(
        (r) =>
          r.resource !== `playlist:${a.request.expected.target}` &&
          r.resource !== "collection:spotify-playlists",
      )
    )
      return null;
    return frozen({
      key,
      operationId: op.id,
      label: a.request.label,
      kind: op.kind as UnidentifiedCreatorKind,
      target: a.request.expected.target,
    });
  }
  function acknowledgeUnidentifiedCreation(
    acknowledgement: UnidentifiedCreationAcknowledgement,
    confirmedActor: string | null,
  ) {
    const proof = unidentifiedCreationAcknowledgement(
      acknowledgement.key,
      confirmedActor,
    );
    if (
      !proof ||
      proof.operationId !== acknowledgement.operationId ||
      proof.label !== acknowledgement.label ||
      proof.kind !== acknowledgement.kind ||
      proof.target !== acknowledgement.target
    )
      return false;
    const a = snapshot.get(proof.key)!;
    update(proof.key, { ...a, status: "attention", acknowledgedOrphan: proof });
    return true;
  }
  function reserveReviewedContinuations(
    parent: AdmissionRequest,
    continuations: readonly ReviewedContinuation[],
    confirmedActor: string | null,
  ) {
    if (confirmedActor !== actor) throw new MissingCsrfTokenError();
    const no = () => {
      throw new TypeError(
        "The reviewed source continuation does not match its target, body or dependencies.",
      );
    };
    const target = parent.expected.target,
      body = parent.body.value;
    if (
      parent.expected.kind !== "workshop_commit" ||
      parent.feature !== "workshop" ||
      parent.path !== `/api/workshop/${encodeURIComponent(target)}/commit` ||
      parent.body.encoding !== "json" ||
      !record(body) ||
      !Array.isArray(body.track_uris) ||
      !body.track_uris.every((u) => typeof u === "string") ||
      !parent.key ||
      !parent.label.trim() ||
      !parent.resources.some(
        (r) => r.resource === `playlist:${target}` && r.access === "write",
      )
    )
      no();
    const keys = new Set([parent.key]);
    const paths: Record<string, string> = {
      workshop_finalize_raids: "/pending-raids/finalize",
      workshop_finalize_restore: "/pair/finalize-restore",
      workshop_archive_append: "/pair/archive",
    };
    for (const { request: r, dependsOn } of continuations) {
      const value = r.body.value;
      if (
        !r.key ||
        keys.has(r.key) ||
        !r.label.trim() ||
        r.feature !== "workshop" ||
        r.expected.target !== target ||
        !paths[r.expected.kind] ||
        r.path !==
          `/api/playlist/${encodeURIComponent(target)}${paths[r.expected.kind]}` ||
        r.body.encoding !== "json" ||
        !record(value) ||
        !Array.isArray(value.target_track_uris) ||
        JSON.stringify(value.target_track_uris) !==
          JSON.stringify((body as Record<string, unknown>).track_uris) ||
        !(
          value.source_playlist_id === null ||
          typeof value.source_playlist_id === "string"
        ) ||
        !r.resources.some(
          (x) => x.resource === `playlist:${target}` && x.access === "read",
        ) ||
        (typeof value.source_playlist_id === "string" &&
          !r.resources.some(
            (x) =>
              x.resource === `playlist:${value.source_playlist_id}` &&
              x.access === "write",
          )) ||
        dependsOn.some((k) => k === parent.key || !keys.has(k)) ||
        new Set(dependsOn).size !== dependsOn.length
      )
        no();
      keys.add(r.key);
    }
    const requests = [parent, ...continuations.map((c) => c.request)];
    for (const r of requests) {
      const existing = snapshot.get(r.key);
      if (existing && held(existing)) throw new AdmissionBlockedError(existing);
      const other = conflict(r.resources);
      if (other) throw new AdmissionBlockedError(other);
    }
    const values: [string, Admission][] = requests.map((r) => [
      r.key,
      {
        request: frozen(r),
        status: "reserved",
        operationId: null,
        relation: null,
        retryUntil: 0,
        ...(r.key === parent.key
          ? {}
          : {
              continuation: frozen({
                parentKey: parent.key,
                activated: false,
                dependsOn: continuations.find((c) => c.request.key === r.key)!
                  .dependsOn,
              }),
            }),
      },
    ]);
    publishMany(values);
    let dispatched = false;
    return Object.freeze({
      parentKey: parent.key,
      taskKeys: Object.freeze(continuations.map((c) => c.request.key)),
      submitTarget(confirmed: string | null) {
        if (confirmed !== actor) throw new MissingCsrfTokenError();
        if (dispatched)
          throw new TypeError(
            "This exact target reservation was already submitted.",
          );
        dispatched = true;
        return perform(snapshot.get(parent.key)!, confirmed, false);
      },
    });
  }
  function activateReviewedContinuations(
    parentKey: string,
    operation: Operation,
    confirmedActor: string | null,
  ) {
    const parent = snapshot.get(parentKey),
      body = parent?.request.body.value;
    if (
      confirmedActor !== actor ||
      !parent ||
      parent.relation !== "original" ||
      parent.operationId !== operation.id ||
      operation.kind !== "workshop_commit" ||
      !operation.targets.includes(parent.request.expected.target) ||
      operation.state !== "succeeded" ||
      !safelySettled(operation) ||
      !["saved", "already_applied"].includes(
        String(operation.result?.target_status),
      ) ||
      !record(body) ||
      !Array.isArray(body.track_uris) ||
      JSON.stringify(operation.result?.saved_track_uris) !==
        JSON.stringify(body.track_uris)
    )
      return false;
    const values: [string, Admission][] = [];
    for (const [key, a] of snapshot)
      if (a.status === "reserved" && a.continuation?.parentKey === parentKey)
        values.push([
          key,
          {
            ...a,
            continuation: frozen({ ...a.continuation, activated: true }),
          },
        ]);
    if (values.length) publishMany(values);
    return true;
  }
  function reserveContinuationRetry(
    review: ContinuationRetryReview,
    confirmedActor: string | null,
  ) {
    const old = snapshot.get(review.failedKey),
      replacement = review.request;
    const operation = old?.operationId
      ? operations.getSnapshot().get(old.operationId)?.operation
      : null;
    const body = old?.request.body.value;
    const source =
      old?.request.expected.kind === "workshop_finalize_raids"
        ? review.sourceContext.raid
        : review.sourceContext.archive;
    if (
      confirmedActor !== actor ||
      !old?.continuation ||
      !(
        (old.relation === "original" &&
          operation &&
          operation.kind === old.request.expected.kind &&
          operation.targets.includes(old.request.expected.target) &&
          ["failed", "rolled_back"].includes(operation.state) &&
          safelySettled(operation)) ||
        (old.status === "refused" &&
          ((old.operationId === null &&
            old.relation === null &&
            old.conclusiveRefusal !== undefined) ||
            (old.relation === "blocking" && old.operationId !== null)))
      ) ||
      !record(body) ||
      review.currentTarget.id !== old.request.expected.target ||
      review.sourceContext.target.id !== old.request.expected.target ||
      JSON.stringify(review.currentTarget.track_uris) !==
        JSON.stringify(body.target_track_uris) ||
      (source?.id ?? null) !== body.source_playlist_id ||
      !replacement.key ||
      replacement.key === review.failedKey ||
      JSON.stringify({ ...replacement, key: old.request.key }) !==
        JSON.stringify(old.request)
    )
      return false;
    if (snapshot.has(replacement.key)) return false;
    const dependents: [string, Admission][] = [];
    for (const [key, a] of snapshot) {
      if (
        a.continuation?.parentKey !== old.continuation.parentKey ||
        !a.continuation.dependsOn.includes(review.failedKey)
      )
        continue;
      if (
        a.status !== "reserved" ||
        a.operationId !== null ||
        a.relation !== null
      )
        return false;
      dependents.push([
        key,
        {
          ...a,
          continuation: frozen({
            ...a.continuation,
            dependsOn: a.continuation.dependsOn.map((k) =>
              k === review.failedKey ? replacement.key : k,
            ),
          }),
        },
      ]);
    }
    for (const a of snapshot.values()) {
      const sameFamily =
        a.request.key === old.continuation.parentKey ||
        a.continuation?.parentKey === old.continuation.parentKey;
      if (
        !sameFamily &&
        held(a) &&
        replacement.resources.some((r) =>
          a.request.resources.some(
            (c) =>
              r.resource === c.resource &&
              (r.access === "write" || c.access === "write"),
          ),
        )
      )
        throw new AdmissionBlockedError(a);
    }
    const next: Admission = {
      request: frozen(replacement),
      status: "reserved",
      operationId: null,
      relation: null,
      retryUntil: 0,
      continuation: frozen({ ...old.continuation, activated: true }),
    };
    publishMany([...dependents, [replacement.key, next]]);
    return true;
  }
  async function submitContinuation(
    key: string,
    confirmedActor: string | null,
  ) {
    if (confirmedActor !== actor) throw new MissingCsrfTokenError();
    const a = snapshot.get(key);
    if (!a || a.status !== "reserved" || !a.continuation?.activated)
      throw new TypeError(
        "Confirm the original saved target before submitting its reviewed source task.",
      );
    for (const dep of a.continuation.dependsOn) {
      const dependency = snapshot.get(dep),
        op = dependency?.operationId
          ? operations.getSnapshot().get(dependency.operationId)?.operation
          : null;
      if (
        !dependency ||
        dependency.status !== "settled" ||
        !op ||
        !originalParent(dependency, op) ||
        op.state !== "succeeded" ||
        op.result?.success !== true ||
        !["complete", "not_required"].includes(String(op.result.status))
      )
        throw new TypeError(
          "Finish the earlier reviewed source task before this dependency.",
        );
    }
    for (const other of snapshot.values()) {
      const sameFamily =
        other.request.key === a.continuation.parentKey ||
        other.continuation?.parentKey === a.continuation.parentKey;
      if (
        !sameFamily &&
        held(other) &&
        a.request.resources.some((r) =>
          other.request.resources.some(
            (c) =>
              r.resource === c.resource &&
              (r.access === "write" || c.access === "write"),
          ),
        )
      )
        throw new AdmissionBlockedError(other);
    }
    return perform(a, confirmedActor, false);
  }
  return {
    actor,
    submitRestoration,
    reconcileRestoration,
    unidentifiedCreationAcknowledgement,
    acknowledgeUnidentifiedCreation,
    reserveReviewedContinuations,
    activateReviewedContinuations,
    submitContinuation,
    reserveContinuationRetry,
    subscribe(fn: () => void) {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    getSnapshot: () => snapshot,
    conflict,
    accept,
    reconcile,
    async submit(request: AdmissionRequest, confirmedActor: string | null) {
      if (confirmedActor !== actor) throw new MissingCsrfTokenError();
      if (request.expected.kind === "operation_restore")
        throw new TypeError(
          "Use the explicit owned restoration review for this admission.",
        );
      if (!request.key || !request.label || !request.resources.length)
        throw new TypeError(
          "An exact named request and resources are required.",
        );
      const existing = snapshot.get(request.key);
      if (existing && held(existing)) throw new AdmissionBlockedError(existing);
      const other = conflict(request.resources);
      if (other) throw new AdmissionBlockedError(other);
      const admission: Admission = {
        request: frozen(request),
        status: "submitting",
        operationId: null,
        relation: null,
        retryUntil: 0,
      };
      return perform(admission, confirmedActor, false);
    },
    async retry(key: string, confirmedActor: string | null) {
      if (confirmedActor !== actor) throw new MissingCsrfTokenError();
      const a = snapshot.get(key);
      if (!a || a.status !== "unknown")
        throw new TypeError(
          "Only an unknown original admission can be retried explicitly.",
        );
      if (Date.now() < a.retryUntil)
        throw new RateLimitError(
          {
            success: false,
            error_code: "rate_limited",
            category: "error",
            message: `Wait ${Math.ceil((a.retryUntil - Date.now()) / 1000)} seconds before retrying this exact request.`,
          },
          a.retryUntil - Date.now(),
        );
      return perform(a, confirmedActor, true);
    },
  };
}
export type AdmissionRegistry = ReturnType<typeof createAdmissionRegistry>;
const registries = new WeakMap<ApiClient, Map<string, AdmissionRegistry>>();
export function getAdmissionRegistry(
  api: ApiClient,
  actor: string,
  operations: OperationStore = getOperationStore(api, actor),
): AdmissionRegistry {
  let actors = registries.get(api);
  if (!actors) {
    actors = new Map();
    registries.set(api, actors);
  }
  let registry = actors.get(actor);
  if (!registry) {
    registry = createAdmissionRegistry(api, actor, operations);
    actors.set(actor, registry);
  }
  return registry;
}
export function admissionUnknown(
  registry: AdmissionRegistry,
  key: string,
  error: unknown,
): boolean {
  const a = registry.getSnapshot().get(key);
  if (a) return a.status === "unknown";
  return (
    !(error instanceof MissingCsrfTokenError) &&
    (!(error instanceof ApiRequestError) || error.status >= 500)
  );
}
