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
  readonly feature: "library" | "routines" | "setup" | "workshop";
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
export interface Admission {
  readonly request: ReadonlyDeep<AdmissionRequest>;
  readonly status:
    "submitting" | "unknown" | "accepted" | "attention" | "settled" | "refused";
  readonly operationId: number | null;
  readonly retryUntil: number;
  readonly relation: "original" | "blocking" | null;
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
const held = (a: Admission) => !["settled", "refused"].includes(a.status);
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
  function reconcile(key: string, operation: Operation) {
    const current = snapshot.get(key);
    if (
      !current ||
      current.operationId !== operation.id ||
      !operation.targets.includes(current.request.expected.target)
    )
      return false;
    const settled = safelySettled(operation);
    update(key, {
      ...withKnownResources(current, operation),
      status: settled
        ? "settled"
        : terminalOperationStates.has(operation.state)
          ? "attention"
          : "accepted",
    });
    return settled;
  }
  function accept(key: string, operation: Operation, observe = true) {
    const a = snapshot.get(key);
    if (!a) throw new TypeError("The original admission must be retained.");
    if (
      operation.kind !== a.request.expected.kind ||
      !operation.targets.includes(a.request.expected.target)
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
      const op = operations.getSnapshot().get(a.operationId)?.operation;
      if (op) reconcile(key, op);
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
        retryUntil:
          error instanceof ApiRequestError && error.retryAfterMs !== null
            ? Date.now() + error.retryAfterMs
            : a.retryUntil,
      });
      if (busy) operations.observe(busy);
      throw error;
    }
  }
  return {
    actor,
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
