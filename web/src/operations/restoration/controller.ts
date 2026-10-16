import type { ApiClient } from "../../api/client.ts";
import type { JsonValue, Operation } from "../../api/contracts.ts";
import { ApiRequestError } from "../../api/errors.ts";
import type { SessionStore } from "../../auth/session-store.ts";
import { getAdmissionRegistry } from "../admissions.ts";
import type { AdmissionRegistry, AdmissionRequest } from "../admissions.ts";
import { getOperationStore, terminalOperationStates } from "../store.ts";
import type { OperationStore } from "../store.ts";
import {
  assertRestoreChild,
  freeze,
  loadRestoreReview,
  restorationResolved,
  restoreRequest,
} from "./client.ts";
import type { RestorationAttempt, RestorationState } from "./types.ts";

interface Options {
  operations?: OperationStore;
  admissions?: AdmissionRegistry;
  key?: () => string;
}

export function createRestorationController(
  api: ApiClient,
  session: SessionStore,
  actor: string,
  parentId: number,
  options: Options = {},
) {
  if (!actor.trim() || !Number.isSafeInteger(parentId) || parentId <= 0)
    throw new TypeError(
      "An original actor and known operation ID are required.",
    );
  const operations = options.operations ?? getOperationStore(api, actor);
  const admissions =
    options.admissions ?? getAdmissionRegistry(api, actor, operations);
  const listeners = new Set<() => void>();
  let state: RestorationState = {
    actor,
    parentId,
    parent: null,
    context: null,
    phase: "idle",
    attempts: [],
    child: null,
    relatedChildId: null,
    error: "",
    message: "",
    retryUntil: 0,
  };
  let sending = false,
    reading = false,
    resolving = false;
  const update = (patch: Partial<RestorationState>) => {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  const current = () => state.attempts.at(-1);
  const confirmedActor = () => {
    const value = session.getSnapshot();
    return value.status === "ready" && value.session.signed_in
      ? value.session.user.id
      : null;
  };
  function authorize() {
    if (confirmedActor() === actor) return true;
    update({
      error: "Confirm the original account before continuing this restoration.",
    });
    return false;
  }
  function attempt(patch: Partial<RestorationAttempt>) {
    const latest = current();
    if (!latest) return;
    update({
      attempts: [
        ...state.attempts.slice(0, -1),
        freeze({ ...latest, ...patch }),
      ],
    });
  }
  function failure(error: unknown, phase: RestorationState["phase"]) {
    update({
      phase,
      error:
        error instanceof Error
          ? error.message
          : "This restoration could not be confirmed.",
      retryUntil:
        error instanceof ApiRequestError && error.retryAfterMs !== null
          ? Math.max(state.retryUntil, Date.now() + error.retryAfterMs)
          : state.retryUntil,
      relatedChildId:
        error instanceof ApiRequestError &&
        Number.isSafeInteger(error.error.operation_id)
          ? error.error.operation_id!
          : state.relatedChildId,
    });
  }
  function canReview() {
    if (
      sending ||
      reading ||
      resolving ||
      ["unknown", "submitting", "observing", "resolved"].includes(state.phase)
    )
      return false;
    const child = state.child;
    return child === null || child.state === "failed";
  }
  async function loadReview() {
    if (!authorize() || !canReview() || Date.now() < state.retryUntil) return;
    reading = true;
    update({
      phase: "loading",
      error: "",
      message: "Loading the current restoration review…",
    });
    try {
      const parent = await api.getOperation(parentId);
      const context = await loadRestoreReview(api, parent);
      if (!authorize()) return;
      update({
        parent: freeze(parent),
        context,
        phase: "ready",
        error: "",
        retryUntil: 0,
        relatedChildId: null,
        message: "Nothing changes until you confirm this reviewed restoration.",
      });
    } catch (error) {
      failure(error, "refused");
    } finally {
      reading = false;
    }
  }
  async function checkResolution() {
    const child = state.child,
      original = state.parent;
    if (
      !authorize() ||
      resolving ||
      state.phase === "resolved" ||
      !child ||
      !original ||
      child.state !== "succeeded"
    )
      return;
    resolving = true;
    try {
      const [latestChild, parent] = await Promise.all([
        api.getOperation(child.id),
        api.getOperation(parentId),
      ]);
      if (!authorize()) return;
      update({ child: freeze(latestChild) });
      attempt({ operation: latestChild });
      const latest = current();
      const resolved =
        !!latest &&
        restorationResolved(original, latestChild, parent) &&
        admissions.reconcileRestoration(
          latest.key,
          { child: latestChild, parent },
          actor,
        );
      update({
        parent: freeze(parent),
        phase: resolved ? "resolved" : "attention",
        error: "",
        message: resolved
          ? "The restoration and its original operation record are confirmed."
          : "The restoration result does not confirm its original operation record. Keep these operation IDs for review.",
      });
    } catch (error) {
      failure(error, "unavailable");
    } finally {
      resolving = false;
    }
  }
  function observeChild() {
    const child = state.child;
    if (!authorize() || !child) return;
    if (child.state === "succeeded") {
      void checkResolution();
      return;
    }
    operations.observe(child.id);
  }
  operations.subscribe(() => {
    if (state.phase === "resolved") return;
    const latest = current();
    if (!latest?.operationId) return;
    const observed = operations.getSnapshot().get(latest.operationId);
    if (!observed) return;
    if (observed.operation) {
      const child = observed.operation;
      if (child.id !== latest.operationId || !state.parent || !state.context)
        return;
      try {
        assertRestoreChild(child, state.parent, state.context);
      } catch (error) {
        failure(error, "attention");
        return;
      }
      update({ child: freeze(child) });
      attempt({ operation: child });
      if (child.state === "succeeded") {
        void checkResolution();
      } else
        update({
          phase: terminalOperationStates.has(child.state)
            ? "attention"
            : "observing",
          message:
            typeof child.result?.message === "string"
              ? child.result.message
              : "Observing the accepted restoration.",
          error: "",
        });
    }
    if (observed.observation === "unavailable") {
      failure(observed.error, "unavailable");
    }
  });
  async function send(retry: boolean) {
    if (!authorize() || sending || Date.now() < state.retryUntil) return;
    const parent = state.parent,
      context = state.context;
    if (
      !parent ||
      !context ||
      (retry ? state.phase !== "unknown" : state.phase !== "ready")
    )
      return;
    let latest = current();
    if (!retry) {
      latest = freeze({
        key: (options.key ?? (() => crypto.randomUUID()))(),
        body: restoreRequest(context),
        context,
        operationId: null,
        operation: null,
      });
      update({ attempts: [...state.attempts, latest], child: null });
    }
    if (!latest) return;
    const request: AdmissionRequest = {
      key: latest.key,
      feature: "restoration",
      label: `Restore operation #${parentId}`,
      resources: context.resources.map((resource) => ({
        resource: `playlist:${resource.playlist_id}`,
        access: resource.role === "restore" ? "write" : "read",
      })),
      path: `/api/operations/${parentId}/restore`,
      body: { encoding: "json", value: latest.body as unknown as JsonValue },
      expected: { kind: "operation_restore", target: parent.targets[0]! },
    };
    sending = true;
    update({
      phase: "submitting",
      error: "",
      message: "Submitting this exact reviewed restoration…",
    });
    try {
      const reply = retry
        ? await admissions.retry(latest.key, actor)
        : await admissions.submitRestoration(
            { parent, context, request },
            actor,
          );
      if (reply.status !== "accepted")
        throw new Error(
          "The restoration response did not identify an accepted child.",
        );
      assertRestoreChild(reply.operation, parent, context);
      attempt({ operationId: reply.operation.id, operation: reply.operation });
      update({
        child: freeze(reply.operation),
        phase: "observing",
        retryUntil: 0,
        message: `Restoration #${reply.operation.id} was accepted for original operation #${parentId}.`,
      });
      operations.accept(reply.operation);
    } catch (error) {
      const entry = admissions.getSnapshot().get(latest.key);
      failure(
        error,
        entry?.status === "unknown" || retry ? "unknown" : "refused",
      );
    } finally {
      sending = false;
    }
  }
  session.subscribe(() => listeners.forEach((listener) => listener()));
  return {
    actor,
    parentId,
    operations,
    admissions,
    getSnapshot: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    loadReview,
    checkResolution,
    observeChild,
    confirm: () => send(false),
    retryExact: () => send(true),
    canReview,
  };
}
export type RestorationController = ReturnType<
  typeof createRestorationController
>;
function createWorkspace(api: ApiClient, session: SessionStore, actor: string) {
  const parents = new Map<number, RestorationController>();
  const listeners = new Set<() => void>();
  let snapshot: readonly RestorationController[] = [];
  const publish = () => {
    snapshot = [...parents.values()];
    listeners.forEach((listener) => listener());
  };
  return {
    get(parentId: number) {
      let controller = parents.get(parentId);
      if (!controller) {
        controller = createRestorationController(api, session, actor, parentId);
        parents.set(parentId, controller);
        controller.subscribe(publish);
        publish();
      }
      return controller;
    },
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
const workspaces = new WeakMap<
  ApiClient,
  Map<string, ReturnType<typeof createWorkspace>>
>();
export function getRestorationWorkspace(
  api: ApiClient,
  session: SessionStore,
  actor: string,
) {
  if (!actor.trim()) throw new TypeError("An original actor is required.");
  let actors = workspaces.get(api);
  if (!actors) {
    actors = new Map();
    workspaces.set(api, actors);
  }
  let workspace = actors.get(actor);
  if (!workspace) {
    workspace = createWorkspace(api, session, actor);
    actors.set(actor, workspace);
  }
  return workspace;
}
export function getRestorationController(
  api: ApiClient,
  session: SessionStore,
  actor: string,
  parentId: number,
) {
  return getRestorationWorkspace(api, session, actor).get(parentId);
}
