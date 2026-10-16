import type {
  JsonRecord,
  Operation,
  WorkshopTrack,
} from "../../../api/contracts.ts";
import { ApiRequestError, ProtocolError } from "../../../api/errors.ts";
import type { AdmissionRequest } from "../../../operations/admissions.ts";
import { getAdmissionRegistry } from "../../../operations/admissions.ts";
import { terminalOperationStates } from "../../../operations/store.ts";
import type { OperationStore } from "../../../operations/store.ts";
import type { WorkshopController } from "../core/controller.ts";
import type {
  SourcePrepareInput,
  SourceTargetConfirmation,
  ManualDraftMutation,
} from "../core/source-types.ts";

import type { SourceStagingSnapshot } from "../core/types.ts";
import { createRoutineClient } from "../../routines/client.ts";
import { createSourcesClient } from "./client.ts";
import {
  cancelArchiveCopies,
  confirmedDismissal,
  freeze,
  pendingTrack,
  prepareTasks,
  record,
  records,
  removeArchiveCopy,
  sourceResult,
  taskOperationKind,
  taskPath,
} from "./model.ts";
import type {
  ArchivePair,
  PendingTrack,
  QueuedAction,
  ReadState,
  RetainedTask,
  SourceState,
  SourceTask,
  SourceWork,
  Staging,
} from "./types.ts";
const initial = <T>(value: T): ReadState<T> => ({
  phase: "idle",
  value,
  error: "",
  retryUntil: 0,
});
const message = (e: unknown) =>
  e instanceof Error ? e.message : "This action could not be confirmed.";
export function createSourcesController(
  c: WorkshopController,
  options: {
    operations?: OperationStore;
    key?: () => string;
  } = {},
) {
  const client = createSourcesClient(c.api, c.playlistId),
    operations = options.operations ?? c.operations,
    admissions = getAdmissionRegistry(c.api, c.actor, operations),
    listeners = new Set<() => void>(),
    stagingListeners = new Set<() => void>(),
    key = options.key ?? (() => crypto.randomUUID());
  const routineClient = createRoutineClient(c.api);
  let state: SourceState = {
    routines: initial([]),
    latest: {},
    histories: {},
    runReview: null,
    pair: initial(null),
    status: initial(null),
    pending: initial([]),
    archive: initial({ tracks: [], total: 0 }),
    owned: initial([]),
    stagedPending: [],
    stagedRestore: [],
    archiveQueue: [],
    pendingSource: null,
    archiveSource: null,
    retained: [],
    actions: [],
    url: "",
    dripInput: "",
    sourceCountInputs: {},
    selectedCollection: "",
    selectedArchive: "",
    message: "",
    error: "",
    busy: false,
    retryUntil: 0,
    configurationUnknown: false,
  };
  let syncActionClaims = () => {};
  const update = (patch: Partial<SourceState>) => {
    state = freeze({ ...state, ...patch });
    syncActionClaims();
    listeners.forEach((fn) => fn());
  };
  const stageUpdate = (patch: Partial<SourceState>) => {
    update(patch);
    stagingListeners.forEach((fn) => fn());
  };
  const active = () => c.permission("pause").allowed;
  const assertActor = () => {
    if (!active())
      throw Error("Confirm the original signed-in account before continuing.");
  };
  const errorPatch = (e: unknown) => ({
    error: message(e),
    retryUntil:
      e instanceof ApiRequestError && e.retryAfterMs !== null
        ? Date.now() + e.retryAfterMs
        : state.retryUntil,
  });
  const cooling = () => Date.now() < state.retryUntil;
  let bootRelease: (() => void) | null = null,
    pairPromise: Promise<void> | null = null,
    booted = false,
    reading = new Map<string, number>();
  function bootClaim() {
    bootRelease ??= c.claim({
      owner: "workshop-sources-pair-bootstrap",
      resource: `tracks:${c.playlistId}`,
      access: "write",
      reason:
        "Confirm archive settings before editing tracks. Retry the archive settings read if it failed.",
    });
  }
  async function read<
    K extends "pair" | "status" | "pending" | "archive" | "owned",
  >(name: K, load: () => Promise<SourceState[K]["value"]>) {
    if (!active() || cooling()) return;
    const ticket = (reading.get(name) ?? 0) + 1;
    reading.set(name, ticket);
    update({
      [name]: { ...state[name], phase: "loading", error: "" },
    } as Partial<SourceState>);
    try {
      const value = await load();
      assertActor();
      if (reading.get(name) !== ticket) return;
      const pairingChanged =
        name === "pair" &&
        (state.pair.value?.archive_playlist_id ?? null) !==
          ((value as ArchivePair | null)?.archive_playlist_id ?? null);
      if (pairingChanged)
        reading.set("archive", (reading.get("archive") ?? 0) + 1);
      update({
        [name]: { phase: "ready", value, error: "", retryUntil: 0 },
        ...(pairingChanged
          ? { archive: initial({ tracks: [], total: 0 }) }
          : {}),
      } as Partial<SourceState>);
    } catch (e) {
      if (reading.get(name) !== ticket) return;
      const retryUntil =
        e instanceof ApiRequestError && e.retryAfterMs !== null
          ? Date.now() + e.retryAfterMs
          : 0;
      update({
        [name]: {
          ...state[name],
          phase: "error",
          error: message(e),
          retryUntil,
        },
        retryUntil: Math.max(state.retryUntil, retryUntil),
      } as Partial<SourceState>);
    }
  }
  async function loadPair() {
    if (pairPromise) return pairPromise;
    bootClaim();
    pairPromise = (async () => {
      await read("pair", client.pair);
      if (state.pair.phase === "ready") {
        bootRelease?.();
        bootRelease = null;
      }
    })();
    try {
      await pairPromise;
    } finally {
      pairPromise = null;
    }
  }
  async function loadStatus() {
    const previous = state.status.value?.raid_link;
    await read("status", client.status);
    const link = state.status.value?.raid_link;
    if (
      state.status.phase === "ready" &&
      (state.dripInput === "" ||
        state.dripInput === String(previous?.drip_count ?? ""))
    )
      update({ dripInput: link ? String(link.drip_count) : "" });
  }
  const loadPending = () => read("pending", client.pending);
  const loadArchive = () =>
    state.pair.value ? read("archive", client.archive) : Promise.resolve();
  const loadOwned = (refresh = false) =>
    read("owned", () => client.owned(refresh));
  c.subscribe(() => {
    if (
      booted &&
      c.getSnapshot().phase === "ready" &&
      state.pair.phase === "idle"
    )
      queueMicrotask(() => {
        if (state.pair.phase === "idle") void loadPair();
      });
  });
  async function initialize() {
    if (!booted) {
      booted = true;
      bootClaim();
    }
    await c.load();
    if (active()) await Promise.all([loadPair(), loadStatus(), loadPending()]);
  }
  const heldPending = () =>
    new Set(
      state.actions
        .filter((a) => a.kind === "pending_dismiss" && a.phase !== "refused")
        .flatMap((a) => {
          if (a.result) {
            try {
              const confirmed = confirmedDismissal(a.result, a.requestedIds);
              if (confirmed !== null) return confirmed;
            } catch {
              return a.requestedIds;
            }
          }
          return a.phase === "complete" ? [] : a.requestedIds;
        }),
    );
  const visiblePending = () => {
    const held = heldPending();
    state.stagedPending.forEach((x) => held.add(x.id));
    state.retained
      .filter((x) => x.task.kind === "raid_promotion")
      .forEach((x) => {
        if (x.task.kind === "raid_promotion")
          x.task.body.track_ids.forEach((id) => held.add(id));
      });
    return state.pending.value.filter((x) => !held.has(x.id));
  };
  const visibleArchive = () => {
    const held = new Set(state.stagedRestore.map((x) => x.uri));
    state.retained
      .filter(
        (x) =>
          (x.task.kind === "archive_restore" ||
            x.task.kind === "archive_append") &&
          x.phase !== "complete",
      )
      .forEach((x) => {
        if (x.task.kind !== "raid_promotion")
          x.task.body.track_uris.forEach((id) => held.add(id));
      });
    return state.archive.value.tracks.filter((x) => !held.has(x.uri));
  };
  function snapshot(): Staging {
    const staging: SourceStagingSnapshot[] = [];
    if (state.stagedPending.length)
      staging.push({
        kind: "raid_promotion",
        source_playlist_id: state.pendingSource?.id ?? null,
        pending_rows: state.stagedPending.map((x) => ({
          id: x.id,
          track_uri: x.track_uri,
        })),
      });
    if (state.stagedRestore.length)
      staging.push({
        kind: "archive_restore",
        source_playlist_id: state.archiveSource!.id,
        track_uris: state.stagedRestore.map((x) => x.uri),
      });
    if (state.archiveQueue.length)
      staging.push({
        kind: "archive_append",
        source_playlist_id: state.archiveSource!.id,
        track_uris: [...state.archiveQueue],
        baseline_track_uris: [],
      });
    return freeze(staging);
  }
  function stagePending(ids: readonly number[]) {
    if (
      !c.permission("draft").allowed ||
      state.status.phase !== "ready" ||
      state.configurationUnknown
    )
      return false;
    const ready = visiblePending().filter((x) => ids.includes(x.id));
    if (ready.length !== new Set(ids).size) {
      update({
        error:
          "One of these rows is held by another action or no longer pending.",
      });
      return false;
    }
    const link = state.status.value?.raid_link;
    const binding = link
      ? {
          id: link.raid_playlist_id,
          name: link.raid_playlist_name ?? link.raid_playlist_id,
        }
      : null;
    if (
      state.stagedPending.length &&
      (state.pendingSource?.id ?? null) !== (binding?.id ?? null)
    ) {
      update({
        error:
          "The collection changed. Review the new collection before staging more tracks.",
      });
      return false;
    }
    if (
      !c.appendTracks(ready.map(pendingTrack), {
        mode: "append-missing",
        origin: "raid",
      })
    )
      return false;
    stageUpdate({
      stagedPending: [...state.stagedPending, ...ready],
      pendingSource: state.stagedPending.length ? state.pendingSource : binding,
      message: `${ready.length} pending tracks staged for the next save.`,
      error: "",
    });
    return true;
  }
  function stageArchive(all = false, trackUri?: string) {
    if (
      !c.permission("draft").allowed ||
      !state.pair.value ||
      state.archive.phase !== "ready" ||
      state.configurationUnknown
    )
      return false;
    const ready = visibleArchive().filter((x) => all || x.uri === trackUri);
    if (!ready.length) return false;
    const present = new Set(c.getSnapshot().working.map((x) => x.uri));
    if (!all && present.has(ready[0]!.uri)) {
      update({ message: "Track already in playlist.", error: "" });
      return false;
    }
    const pair = state.pair.value;
    if (
      (state.stagedRestore.length || state.archiveQueue.length) &&
      state.archiveSource?.id !== pair.archive_playlist_id
    ) {
      update({
        error:
          "The archive changed. Review the new archive before staging more tracks.",
      });
      return false;
    }
    if (!c.appendTracks(ready, { mode: "append-missing", origin: "archive" }))
      return false;
    stageUpdate({
      stagedRestore: [...state.stagedRestore, ...(all ? ready : [ready[0]!])],
      archiveSource: state.archiveSource ?? {
        id: pair.archive_playlist_id,
        name: pair.archive_playlist_name ?? pair.archive_playlist_id,
      },
      message: "Archive tracks staged for restore after saving.",
      error: "",
    });
    return true;
  }
  function discard() {
    stageUpdate({
      stagedPending: [],
      stagedRestore: [],
      archiveQueue: [],
      pendingSource: null,
      archiveSource: null,
      message:
        "Uncommitted source staging returned. Saved or uncertain work is retained.",
    });
  }
  function manualDraftMutation(event: ManualDraftMutation) {
    if (event.kind === "remove") {
      const u = event.occurrence.uri,
        restoration = state.stagedRestore.findIndex((x) => x.uri === u);
      let queue = [...state.archiveQueue],
        restored = [...state.stagedRestore],
        source = state.archiveSource;
      if (restoration >= 0) restored.splice(restoration, 1);
      else if (state.pair.value?.auto_archive_on_remove) {
        if (source && source.id !== state.pair.value.archive_playlist_id) {
          update({
            error:
              "The archive changed. Existing staging was kept; review its destination.",
          });
        }
        queue = removeArchiveCopy(
          queue,
          u,
          event.afterCount,
          event.baselineCount,
        );
        if (queue.length)
          source ??= {
            id: state.pair.value.archive_playlist_id,
            name:
              state.pair.value.archive_playlist_name ??
              state.pair.value.archive_playlist_id,
          };
      }
      stageUpdate({
        stagedRestore: restored,
        archiveQueue: queue,
        archiveSource: queue.length || restored.length ? source : null,
        stagedPending:
          event.afterCount === 0
            ? state.stagedPending.filter((x) => x.track_uri !== u)
            : state.stagedPending,
      });
    } else {
      let queue = [...state.archiveQueue];
      for (const u of new Set(event.occurrences.map((x) => x.uri)))
        queue = cancelArchiveCopies(
          queue,
          u,
          event.afterCounts[u] ?? 0,
          event.baselineCounts[u] ?? 0,
        );
      if (queue.length !== state.archiveQueue.length)
        stageUpdate({
          archiveQueue: queue,
          archiveSource:
            queue.length || state.stagedRestore.length
              ? state.archiveSource
              : null,
        });
    }
  }
  async function prepareReview(input: SourcePrepareInput) {
    if (state.busy || state.configurationUnknown)
      throw Error(
        "Confirm pending source settings before reviewing this save.",
      );
    return prepareTasks(input.staging, input.desired, input.sourceContext, key);
  }
  const actionFor = (id: string) => state.actions.find((x) => x.id === id);
  const patchAction = (id: string, patch: Partial<QueuedAction>) => {
    update({
      actions: state.actions.map((x) => (x.id === id ? { ...x, ...patch } : x)),
    });
    stagingListeners.forEach((fn) => fn());
  };
  const patchTask = (id: string, patch: Partial<RetainedTask>) => {
    update({
      retained: state.retained.map((x) =>
        x.task.id === id ? { ...x, ...patch } : x,
      ),
    });
    stagingListeners.forEach((fn) => fn());
  };
  function actionResources(
    source: string | null,
    targetAccess: "read" | "write" = "write",
  ) {
    const values = [
      { resource: `playlist:${c.playlistId}`, access: targetAccess },
    ];
    if (source && source !== c.playlistId)
      values.push({ resource: `playlist:${source}`, access: "write" });
    return values;
  }
  const newAction = (
    values: Pick<
      QueuedAction,
      "label" | "kind" | "path" | "body" | "resources"
    > & {
      key?: string;
      id?: string;
      requestedIds?: readonly number[];
      declaredSteps?: readonly string[];
    },
  ): QueuedAction => {
    const token = values.key ?? key();
    return freeze({
      ...values,
      id: values.id ?? token,
      key: token,
      phase: "ready",
      operationId: null,
      blockingOperationId: null,
      operation: null,
      error: "",
      retryUntil: 0,
      result: null,
      requestedIds: values.requestedIds ?? [],
      declaredSteps: values.declaredSteps ?? [],
    });
  };
  function retain(action: QueuedAction) {
    if (!actionFor(action.id)) update({ actions: [...state.actions, action] });
    stagingListeners.forEach((fn) => fn());
    return action;
  }
  async function submit(id: string, retry = false) {
    const action = actionFor(id);
    if (
      !action ||
      !active() ||
      Date.now() < action.retryUntil ||
      cooling() ||
      !["ready", "unknown"].includes(action.phase)
    )
      return false;
    patchAction(id, { phase: "submitting", error: "" });
    try {
      const reply = retry
        ? await admissions.retry(action.key, c.actor)
        : admissions.getSnapshot().get(action.key)?.status === "reserved"
          ? await admissions.submitContinuation(action.key, c.actor)
          : await admissions.submit(
              {
                key: action.key,
                feature: "workshop",
                label: action.label,
                resources: action.resources,
                path: action.path,
                body: { encoding: "json", value: action.body },
                expected: { kind: action.kind, target: c.playlistId },
              },
              c.actor,
            );
      assertActor();
      if (reply.status !== "accepted")
        throw new ProtocolError(
          "This change did not identify an accepted operation.",
        );
      patchAction(id, {
        phase: "accepted",
        operationId: reply.operation.id,
        operation: reply.operation,
        blockingOperationId: null,
      });
      operations.accept(reply.operation);
      reconcile();
      return true;
    } catch (e) {
      const a = admissions.getSnapshot().get(action.key);
      const original = a?.relation === "original" ? a.operationId : null;
      patchAction(id, {
        phase:
          a?.status === "unknown"
            ? "unknown"
            : a?.status === "refused"
              ? "refused"
              : original
                ? "accepted"
                : "failed",
        operationId: original ?? null,
        blockingOperationId: a?.relation === "blocking" ? a.operationId : null,
        error: message(e),
        ...(e instanceof ApiRequestError
          ? { result: JSON.parse(JSON.stringify(e.error)) as JsonRecord }
          : {}),
        retryUntil:
          a?.retryUntil ??
          (e instanceof ApiRequestError && e.retryAfterMs !== null
            ? Date.now() + e.retryAfterMs
            : 0),
      });
      update({
        retryUntil: Math.max(
          state.retryUntil,
          a?.retryUntil ?? 0,
          e instanceof ApiRequestError && e.retryAfterMs !== null
            ? Date.now() + e.retryAfterMs
            : 0,
        ),
      });
      return false;
    }
  }
  const seenOperations = new Map<number, Operation>();
  const liveTargetBases = new Map<
    string,
    { revision: number; configRevision: number; sourceRevision: number; lockRevision: number }
  >();
  const refreshedLiveTargets = new Set<number>();
  async function refreshLiveTarget(action: QueuedAction) {
    const original = liveTargetBases.get(action.id),
      op = action.operation,
      admission = admissions.getSnapshot().get(action.key);
    if (
      !original || !op || refreshedLiveTargets.has(op.id) || action.phase !== "complete" ||
      op.state !== "succeeded" || op.result?.success !== true || !op.targets.includes(c.playlistId) ||
      admission?.relation !== "original" || admission.operationId !== op.id ||
      !record(op.result.operation) || op.result.operation.aggregate_status !== "complete"
    ) return;
    const facts = records(op.result.operation.resources),
      target = facts.findLast((resource) => resource.resource === c.playlistId);
    if (
      !target || !["complete", "not_required"].includes(String(target.status)) ||
      target.verification !== "ordered" || !Array.isArray(target.current_track_uris) ||
      !target.current_track_uris.every((uri) => typeof uri === "string") ||
      !Array.isArray(target.desired_track_uris) ||
      JSON.stringify(target.current_track_uris) !== JSON.stringify(target.desired_track_uris)
    ) return;
    refreshedLiveTargets.add(op.id);
    const unchanged = () => {
      const current = c.getSnapshot();
      return active() && !c.hasDraft() && current.revision === original.revision &&
        current.configRevision === original.configRevision && current.sourceRevision === original.sourceRevision &&
        current.lockRevision === original.lockRevision;
    };
    if (!unchanged()) return;
    if (!(await c.checkCurrent())) {
      update({
        error: "The live action completed, but its current target could not be read. Compare Spotify before editing.",
      });
      return;
    }
    const observation = c.getSnapshot().observation;
    if (!unchanged() || !observation) return;
    if (JSON.stringify(observation.track_uris) !== JSON.stringify(target.current_track_uris)) {
      update({
        error: "The live action completed, but Spotify has changed again. Compare its current order before editing.",
      });
      return;
    }
    c.adoptCurrent(observation.id);
  }
  function reconcile() {
    let changed = false;
    const changedActions = new Set<string>();
    const actions = state.actions.map((a) => {
      if (!a.operationId) return a;
      const observed = operations.getSnapshot().get(a.operationId);
      if (
        !observed?.operation ||
        seenOperations.get(a.operationId) === observed.operation
      )
        return a;
      seenOperations.set(a.operationId, observed.operation);
      changed = true;
      changedActions.add(a.id);
      const op = observed.operation;
      if (op.kind !== a.kind || !op.targets.includes(c.playlistId))
        return {
          ...a,
          phase: "failed" as const,
          error: "The operation identity does not match this request.",
        };
      let receiptError = "";
      if (
        terminalOperationStates.has(op.state) &&
        op.state === "succeeded" &&
        op.result?.success === true
      ) {
        if (a.kind === "pending_dismiss") {
          try {
            if (confirmedDismissal(op.result, a.requestedIds) === null)
              receiptError = "The required inbox receipt remains unconfirmed.";
          } catch (error) {
            receiptError = message(error);
          }
        }
        const task = state.retained.find((x) => x.task.id === a.id)?.task;
        if (
          task &&
          !["complete", "not_required"].includes(
            sourceResult(task, op.result).status,
          )
        )
          receiptError =
            "The source result could not be verified for its original resource.";
      }
      return {
        ...a,
        operation: op,
        phase: terminalOperationStates.has(op.state)
          ? op.state === "succeeded" &&
            op.result?.success === true &&
            !receiptError
            ? ("complete" as const)
            : ("failed" as const)
          : ("accepted" as const),
        result: op.result,
        error:
          receiptError ||
          (typeof op.result?.message === "string" && op.result?.success !== true
            ? op.result.message
            : ""),
      };
    });
    if (changed) {
      update({ actions });
      for (const a of actions) {
        if (changedActions.has(a.id) && a.phase === "complete") {
          queueMicrotask(() => {
            void refreshLiveTarget(a);
          });
          if (a.kind === "pair_create")
            queueMicrotask(() => {
              void loadPair();
            });
          else if (a.kind === "raid_link_create")
            queueMicrotask(() => {
              void loadStatus();
            });
        }
      }
    }
    let taskChanged = false;
    const retained = state.retained.map((t) => {
      const a = actionFor(t.task.id);
      if (!a || a.phase === "ready" || !changedActions.has(a.id)) return t;
      taskChanged = true;
      return {
        ...t,
        phase: a.phase,
        result: a.result ? sourceResult(t.task, a.result) : t.result,
      };
    });
    if (taskChanged) {
      const archiveConfirmed = retained.some(
        (x) =>
          changedActions.has(x.task.id) &&
          x.phase === "complete" &&
          x.task.kind !== "raid_promotion" &&
          x.task.source?.id === state.pair.value?.archive_playlist_id,
      );
      update({
        retained,
        ...(archiveConfirmed
          ? { archive: initial({ tracks: [], total: 0 }) }
          : {}),
      });
      if (archiveConfirmed)
        queueMicrotask(() => {
          void loadArchive();
        });
    }
    if (changed || taskChanged) stagingListeners.forEach((fn) => fn());
  }
  const actionClaims = new Map<string, () => void>();
  const liveKinds = new Set([
    "raid_now",
    "drip_now",
    "raid_and_drip",
    "schedule_run",
  ]);
  const actionHeld = (a: QueuedAction) => {
    const admission = admissions.getSnapshot().get(a.key);
    return admission
      ? !["settled", "refused"].includes(admission.status) &&
          !admission.acknowledgedOrphan
      : ["ready", "submitting", "unknown", "accepted"].includes(a.phase);
  };
  syncActionClaims = () => {
    const desired = new Map<string, { resource: string; reason: string }>();
    for (const a of state.actions.filter(
      (a) => liveKinds.has(a.kind) && actionHeld(a),
    )) {
      const name = `source-action:${a.key}`;
      if (
        a.resources.some(
          (r) =>
            r.resource === `playlist:${c.playlistId}` && r.access === "write",
        )
      )
        desired.set(`${name}:tracks`, {
          resource: `tracks:${c.playlistId}`,
          reason:
            "The retained live action may change target tracks. Inspect its original operation before editing.",
        });
      if (a.declaredSteps.includes("raid"))
        desired.set(`${name}:inbox`, {
          resource: `inbox:${c.playlistId}`,
          reason:
            "A retained live action is using the inbox and source configuration.",
        });
      const schedule = /\/schedules\/(\d+)\/run$/.exec(a.path)?.[1];
      if (schedule)
        desired.set(`${name}:routine`, {
          resource: `routine:${schedule}`,
          reason: "This exact routine has a retained live operation.",
        });
    }
    for (const [name, release] of actionClaims)
      if (!desired.has(name)) {
        actionClaims.delete(name);
        release();
      }
    for (const [name, value] of desired)
      if (!actionClaims.has(name))
        actionClaims.set(
          name,
          c.claim({
            owner: name,
            resource: value.resource,
            access: "write",
            reason: value.reason,
          }),
        );
  };
  admissions.subscribe(() => syncActionClaims());
  operations.subscribe(reconcile);
  function sourceConfigurationAllowed() {
    if (
      state.actions.some(
        (a) => actionHeld(a) && a.declaredSteps.includes("raid"),
      )
    ) {
      update({
        error:
          "A retained Collect is using these source settings. Pause or inspect its original operation before changing shared configuration.",
      });
      return false;
    }
    return true;
  }
  function associationAllowed(source: string | null) {
    if (
      source &&
      state.actions.some(
        (a) =>
          actionHeld(a) &&
          a.resources.some((r) => r.resource === `playlist:${source}`),
      )
    ) {
      update({
        error:
          "A retained operation still uses this exact source association. Inspect its original receipt before changing the link.",
      });
      return false;
    }
    return true;
  }
  async function wait(id: string) {
    const a = actionFor(id);
    if (!a?.operationId) return false;
    const opId = a.operationId;
    const done = () => {
      const observed = operations.getSnapshot().get(opId);
      return observed?.observation === "unavailable"
        ? false
        : observed?.operation &&
            terminalOperationStates.has(observed.operation.state)
          ? actionFor(id)?.phase === "complete"
          : null;
    };
    const ready = done();
    if (ready !== null) return ready;
    return new Promise<boolean>((resolve) => {
      const off = operations.subscribe(() => {
        const value = done();
        if (value !== null) {
          off();
          resolve(value);
        }
      });
    });
  }
  async function runTask(id: string) {
    const t = state.retained.find((x) => x.task.id === id);
    if (!t || !active() || cooling() || !["ready", "failed"].includes(t.phase))
      return false;
    if (
      t.task.dependsOn.some(
        (dep) =>
          !state.retained.some(
            (x) =>
              x.task.id === dep &&
              x.result &&
              ["complete", "not_required"].includes(x.result.status),
          ),
      )
    ) {
      update({
        error: "Finish the earlier removal on this source before appending.",
      });
      return false;
    }
    const old = actionFor(id);
    if (old) {
      if (old.phase === "unknown") return false;
      if (old.phase === "accepted") {
        operations.observe(old.operationId!);
        return false;
      }
      if (old.phase === "failed" || old.phase === "refused") {
        update({
          error:
            "This exact operation already finished. Inspect it or prepare a fresh explicit source review before another mutation.",
        });
        return false;
      }
    }
    retain(
      newAction({
        id,
        key: t.task.key,
        label: t.task.label,
        kind: taskOperationKind(t.task.kind),
        path: taskPath(c.playlistId, t.task.kind),
        body: structuredClone(t.task.body) as unknown as JsonRecord,
        resources: actionResources(t.task.source?.id ?? null, "read"),
      }),
    );
    patchTask(id, { phase: "submitting" });
    const accepted = await submit(id);
    const action = actionFor(id)!;
    patchTask(id, { phase: action.phase });
    return accepted && (await wait(id));
  }
  async function continueInitial(ids: readonly string[]) {
    for (const id of ids) {
      const t = state.retained.find((x) => x.task.id === id);
      if (!t || t.phase !== "ready") continue;
      if (
        t.task.dependsOn.some(
          (dep) =>
            !state.retained.some(
              (x) =>
                x.task.id === dep &&
                x.result &&
                ["complete", "not_required"].includes(x.result.status),
            ),
        )
      )
        continue;
      await runTask(id);
    }
  }
  const confirmedTargets = new Set<number>();
  function targetConfirmed(proof: SourceTargetConfirmation) {
    if (confirmedTargets.has(proof.operationId)) return;
    const tasks = proof.sourceReview.tasks;
    if (
      tasks.some(
        (t) =>
          t.body.target_track_uris.length !== proof.savedTrackUris.length ||
          t.body.target_track_uris.some(
            (u, i) => u !== proof.savedTrackUris[i],
          ),
      )
    )
      throw Error("The source handoff does not match the saved target.");
    confirmedTargets.add(proof.operationId);
    const ids = new Set(
      proof.staging.flatMap((x) =>
        x.kind === "raid_promotion" ? x.pending_rows.map((r) => r.id) : [],
      ),
    );
    const subtract = (
      items: readonly string[],
      selected: readonly string[],
    ) => {
      const copy = [...items];
      selected.forEach((u) => {
        const at = copy.indexOf(u);
        if (at >= 0) copy.splice(at, 1);
      });
      return copy;
    };
    const restore = proof.staging.flatMap((x) =>
      x.kind === "archive_restore" ? x.track_uris : [],
    );
    const append = proof.staging.flatMap((x) =>
      x.kind === "archive_append" ? x.track_uris : [],
    );
    let remaining = [...restore];
    stageUpdate({
      stagedPending: state.stagedPending.filter((x) => !ids.has(x.id)),
      stagedRestore: state.stagedRestore.filter((x) => {
        const at = remaining.indexOf(x.uri);
        if (at < 0) return true;
        remaining.splice(at, 1);
        return false;
      }),
      archiveQueue: subtract(state.archiveQueue, append),
      retained: [
        ...state.retained,
        ...tasks.map((task) => ({
          task,
          targetOperationId: proof.operationId,
          phase: "ready" as const,
          result: null,
          inspection: null,
        })),
      ],
      message: "Target saved. Finishing its reviewed source tasks.",
    });
    queueMicrotask(() => {
      void continueInitial(tasks.map((t) => t.id));
    });
  }
  function canReviewRetry(id: string) {
    const retained = state.retained.find((x) => x.task.id === id),
      action = actionFor(id),
      admission = action ? admissions.getSnapshot().get(action.key) : null;
    return (
      !!retained &&
      (retained.result?.retryable === true ||
        (admission?.status === "refused" &&
          admission.operationId === null &&
          admission.relation === null &&
          admission.conclusiveRefusal !== undefined))
    );
  }
  async function reviewRetry(id: string) {
    const retained = state.retained.find((x) => x.task.id === id);
    if (
      !retained ||
      !canReviewRetry(id) ||
      !active() ||
      state.busy ||
      cooling()
    )
      return false;
    update({ busy: true, error: "" });
    try {
      const context = await client.sourceContext(
        retained.task.kind === "raid_promotion",
        retained.task.kind !== "raid_promotion",
      );
      assertActor();
      const bound =
        retained.task.kind === "raid_promotion"
          ? context.raid
          : context.archive;
      if ((bound?.id ?? null) !== (retained.task.source?.id ?? null))
        throw Error(
          "The original source is no longer linked. Saved work cannot follow a replacement source.",
        );
      const current = await c.client.current();
      assertActor();
      if (
        current.track_uris.length !==
          retained.task.body.target_track_uris.length ||
        current.track_uris.some(
          (u, i) => u !== retained.task.body.target_track_uris[i],
        )
      )
        throw Error(
          "The saved target changed. Inspect the original source task before retrying.",
        );
      const original = admissions.getSnapshot().get(retained.task.key);
      if (!original)
        throw Error(
          "The original reviewed task is unavailable. Keep its saved receipt.",
        );
      const newKey = key(),
        request: AdmissionRequest = {
          ...(JSON.parse(JSON.stringify(original.request)) as AdmissionRequest),
          key: newKey,
        },
        task = freeze({ ...retained.task, id: newKey, key: newKey });
      if (
        !admissions.reserveContinuationRetry(
          {
            failedKey: retained.task.key,
            request,
            currentTarget: { id: c.playlistId, track_uris: current.track_uris },
            sourceContext: context,
          },
          c.actor,
        )
      )
        throw Error(
          "This task or a dependent action is still uncertain. Keep the original receipt and inspect its status before another review.",
        );
      update({
        retained: state.retained.map((x) =>
          x.task.id === id
            ? { ...x, task, phase: "ready", result: null, inspection: null }
            : {
                ...x,
                task: {
                  ...x.task,
                  dependsOn: x.task.dependsOn.map((dep) =>
                    dep === id ? newKey : dep,
                  ),
                },
              },
        ),
        message:
          "The original source task was reviewed again. Run this exact reviewed cleanup when ready.",
      });
      stagingListeners.forEach((fn) => fn());
      return true;
    } catch (e) {
      update(errorPatch(e));
      return false;
    } finally {
      update({ busy: false });
    }
  }
  function retainedWork() {
    return [
      ...state.retained
        .filter(
          (x) =>
            !x.result ||
            !["complete", "not_required"].includes(x.result.status),
        )
        .map((x) => ({
          id: x.task.id,
          kind: x.task.kind,
          label: x.task.label,
          source: x.task.source,
          operationId: actionFor(x.task.id)?.operationId ?? null,
          status: x.phase === "refused" ? ("failed" as const) : x.phase,
        })),
      ...state.actions
        .filter(
          (x) =>
            !state.retained.some((t) => t.task.id === x.id) &&
            ![
              "workshop_finalize_raids",
              "workshop_finalize_restore",
              "workshop_archive_append",
            ].includes(x.kind) &&
            !admissions.getSnapshot().get(x.key)?.acknowledgedOrphan &&
            !["complete", "refused"].includes(x.phase),
        )
        .map((x) => ({
          id: x.id,
          kind: x.kind,
          label: x.label,
          source: null,
          operationId: x.operationId,
          status: x.phase === "refused" ? ("failed" as const) : x.phase,
        })),
    ];
  }
  const coordinator = {
    snapshot,
    discard,
    subscribe(fn: () => void) {
      stagingListeners.add(fn);
      return () => stagingListeners.delete(fn);
    },
    prepareReview,
    targetConfirmed,
    retainedWork,
    manualDraftMutation,
  };
  c.registerSourceCoordinator(coordinator);
  async function local(
    run: () => Promise<unknown>,
    refresh: () => Promise<void>,
  ) {
    if (state.busy || state.configurationUnknown || !active() || cooling())
      return false;
    update({ busy: true, error: "" });
    c.configurationChanged();
    try {
      await run();
      assertActor();
      await refresh();
      update({
        message: "Playlist settings saved.",
        configurationUnknown: false,
      });
      return true;
    } catch (e) {
      const unknown = !(e instanceof ApiRequestError && e.status < 500);
      update({ ...errorPatch(e), configurationUnknown: unknown });
      return false;
    } finally {
      update({ busy: false });
      c.configurationChanged();
    }
  }
  async function dismiss(ids: readonly number[]) {
    const visible = new Set(visiblePending().map((x) => x.id)),
      selected = [...new Set(ids)];
    if (
      !active() ||
      cooling() ||
      state.status.phase !== "ready" ||
      !selected.length ||
      selected.length > 200 ||
      selected.some((id) => !visible.has(id))
    ) {
      update({ error: "Choose at most 200 currently shown pending rows." });
      return false;
    }
    const action = retain(
      newAction({
        label: `Dismiss ${selected.length} pending tracks`,
        kind: "pending_dismiss",
        path: `/api/playlist/${c.playlistId}/pending-raids/dismiss`,
        body: { track_ids: selected },
        requestedIds: selected,
        resources: actionResources(
          state.status.value?.raid_link?.raid_playlist_id ?? null,
          "read",
        ),
      }),
    );
    return submit(action.id);
  }
  async function dismissMany(ids: readonly number[]) {
    const visible = new Set(visiblePending().map((x) => x.id)),
      selected = [...new Set(ids)];
    if (
      !active() ||
      cooling() ||
      state.status.phase !== "ready" ||
      !selected.length ||
      selected.some((id) => !visible.has(id))
    )
      return false;
    const batch: QueuedAction[] = [];
    for (let offset = 0; offset < selected.length; offset += 200) {
      const rows = selected.slice(offset, offset + 200);
      batch.push(
        newAction({
          label: `Dismiss ${rows.length} pending tracks`,
          kind: "pending_dismiss",
          path: `/api/playlist/${c.playlistId}/pending-raids/dismiss`,
          body: { track_ids: rows },
          requestedIds: rows,
          resources: actionResources(
            state.status.value?.raid_link?.raid_playlist_id ?? null,
            "read",
          ),
        }),
      );
    }
    batch.forEach(retain);
    for (let i = 0; i < batch.length; i++) {
      const action = batch[i]!;
      if (!(await submit(action.id)) || !(await wait(action.id))) {
        for (const rest of batch.slice(i + 1))
          patchAction(rest.id, {
            phase: "refused",
            error: "Not submitted because an earlier batch needs attention.",
          });
        return false;
      }
    }
    return true;
  }
  async function createAsset(
    purpose: "collection" | "archive",
    createNew: boolean,
    choiceId?: string,
  ) {
    if (!active() || cooling() || state.busy || state.configurationUnknown)
      return false;
    if (
      (purpose === "collection" &&
        (!sourceConfigurationAllowed() ||
          state.stagedPending.length > 0 ||
          !associationAllowed(
            state.status.value?.raid_link?.raid_playlist_id ?? null,
          ))) ||
      (purpose === "archive" &&
        (state.stagedRestore.length > 0 ||
          state.archiveQueue.length > 0 ||
          !associationAllowed(state.pair.value?.archive_playlist_id ?? null)))
    ) {
      update({
        error:
          "Keep the captured source association until its staged or retained work is resolved.",
      });
      return false;
    }
    const existing = state.actions.find(
      (a) =>
        ["raid_link_create", "pair_create"].includes(a.kind) &&
        !["complete", "refused"].includes(a.phase) &&
        !admissions.getSnapshot().get(a.key)?.acknowledgedOrphan,
    );
    if (existing) {
      update({
        error:
          "Resolve the retained playlist creation or association before creating another.",
      });
      return false;
    }
    const choice = state.owned.value.find((x) => x.id === choiceId);
    if (!createNew && !choice) return false;
    const name = c.getSnapshot().context?.playlist.name ?? c.playlistId;
    const body: JsonRecord =
      purpose === "collection"
        ? {
            create_new: createNew,
            target_playlist_name: name,
            ...(createNew
              ? { drip_count: 3, drip_enabled: false }
              : {
                  raid_playlist_id: choice!.id,
                  raid_playlist_name: choice!.name,
                }),
          }
        : {
            create_new: createNew,
            production_playlist_name: name,
            ...(createNew
              ? {}
              : {
                  archive_playlist_id: choice!.id,
                  archive_playlist_name: choice!.name,
                }),
          };
    const a = retain(
      newAction({
        label: createNew
          ? `Create ${purpose} playlist`
          : `Link ${purpose} playlist #${choice!.id}`,
        kind: purpose === "collection" ? "raid_link_create" : "pair_create",
        path: `/api/playlist/${c.playlistId}/${purpose === "collection" ? "raid-link" : "pair"}`,
        body,
        resources: actionResources(choice?.id ?? null),
      }),
    );
    return submit(a.id);
  }
  async function inspect(id: string) {
    const t = state.retained.find((x) => x.task.id === id);
    if (!t || !active() || cooling()) return;
    try {
      const result = await client.inspect(t.task);
      assertActor();
      patchTask(id, { inspection: result });
      update({ message: result.message, error: "" });
    } catch (e) {
      update(errorPatch(e));
    }
  }
  async function loadRoutines() {
    await readRoutines();
    try {
      const data = await routineClient.latest();
      assertActor();
      const latest: SourceState["latest"] = {};
      for (const row of state.routines.value) {
        const value = data.results[String(row.schedule_id)];
        latest[row.schedule_id] = record(value)
          ? (value as unknown as SourceState["latest"][number])
          : null;
      }
      update({ latest });
    } catch (e) {
      update(errorPatch(e));
    }
  }
  async function readRoutines() {
    if (!active() || cooling()) return;
    update({ routines: { ...state.routines, phase: "loading", error: "" } });
    try {
      const value = await routineClient.guided(c.playlistId, "?from=playlist");
      assertActor();
      update({
        routines: {
          phase: "ready",
          value: value.routines,
          error: "",
          retryUntil: 0,
        },
      });
    } catch (e) {
      update({
        routines: {
          ...state.routines,
          phase: "error",
          error: message(e),
          retryUntil: 0,
        },
      });
    }
  }
  async function history(id: number) {
    if (!active()) return;
    update({
      histories: {
        ...state.histories,
        [id]: { ...initial([]), phase: "loading" },
      },
    });
    try {
      const value = await routineClient.history(id);
      assertActor();
      update({
        histories: {
          ...state.histories,
          [id]: { phase: "ready", value, error: "", retryUntil: 0 },
        },
      });
    } catch (e) {
      update({
        histories: {
          ...state.histories,
          [id]: { ...initial([]), phase: "error", error: message(e) },
        },
      });
    }
  }
  async function routineState(id: number) {
    const row = state.routines.value.find((x) => x.schedule_id === id);
    if (!row) return false;
    return local(async () => {
      await routineClient.state(
        id,
        !row.schedule.is_enabled,
        row.schedule_revision,
      );
    }, loadRoutines);
  }
  async function deleteRoutine(id: number) {
    if (!c.permission("routine", String(id)).allowed) return false;
    return local(() => routineClient.remove(id), loadRoutines);
  }
  async function prepareRun(
    kind: "raid" | "drip" | "raid_and_drip" | "schedule",
    scheduleId?: number,
  ) {
    if (!active() || cooling() || state.busy) return false;
    const before = c.getSnapshot();
    update({ busy: true, error: "" });
    try {
      const context =
        kind === "schedule"
          ? await routineClient.runReview(scheduleId!)
          : await client.runReview(kind);
      assertActor();
      const after = c.getSnapshot();
      if (
        before.configRevision !== after.configRevision ||
        before.sourceRevision !== after.sourceRevision ||
        before.revision !== after.revision
      )
        throw Error(
          "The draft or configuration changed while this review loaded. Review again.",
        );
      update({
        runReview: {
          id: key(),
          label:
            kind === "schedule"
              ? `Routine #${scheduleId}`
              : {
                  raid: "Collect for review",
                  drip: "Add collected tracks",
                  raid_and_drip: "Collect and add tracks",
                }[kind],
          context: context as JsonRecord,
          revision: after.revision,
          configRevision: after.configRevision,
          sourceRevision: after.sourceRevision,
          actionId: null,
          kind,
          scheduleId: scheduleId ?? null,
        },
      });
      return true;
    } catch (e) {
      update(errorPatch(e));
      return false;
    } finally {
      update({ busy: false });
    }
  }
  async function runReviewed() {
    const r = state.runReview;
    if (!r || r.actionId || !active() || cooling()) return false;
    const current = c.getSnapshot();
    if (
      current.configRevision !== r.configRevision ||
      current.sourceRevision !== r.sourceRevision ||
      current.revision !== r.revision
    ) {
      update({
        error:
          "This run review is stale. Review the current configuration again.",
      });
      return false;
    }
    const binding = r.context.review_binding;
    if (!record(binding) || typeof binding.scope !== "string") return false;
    const resources = Array.isArray(r.context.resources)
      ? records(r.context.resources)
          .filter(
            (x) => x.domain === "spotify" && typeof x.resource === "string",
          )
          .map((x) => ({
            resource: `playlist:${x.resource}`,
            access:
              x.access === "write" ? ("write" as const) : ("read" as const),
          }))
      : [];
    if (!resources.some((x) => x.resource === `playlist:${c.playlistId}`))
      resources.push({ resource: `playlist:${c.playlistId}`, access: "read" });
    const path =
      r.kind === "schedule"
        ? `/api/schedules/${r.scheduleId}/run`
        : `/api/playlist/${c.playlistId}/` +
          {
            raid: "raid-now",
            drip: "drip-now",
            raid_and_drip: "raid-and-drip",
          }[r.kind as "raid" | "drip" | "raid_and_drip"];
    const action = retain(
      newAction({
        id: r.id,
        key: r.id,
        label: r.label,
        kind:
          r.kind === "schedule"
            ? "schedule_run"
            : {
                raid: "raid_now",
                drip: "drip_now",
                raid_and_drip: "raid_and_drip",
              }[r.kind as "raid" | "drip" | "raid_and_drip"],
        path,
        body: { reviewed_scope: binding.scope },
        declaredSteps: Array.isArray(r.context.steps)
          ? r.context.steps.filter((x): x is string => typeof x === "string")
          : [],
        resources,
      }),
    );
    if (resources.some((resource) =>
      resource.resource === `playlist:${c.playlistId}` && resource.access === "write",
    )) {
      liveTargetBases.set(action.id, {
        revision: current.revision,
        configRevision: current.configRevision,
        sourceRevision: current.sourceRevision,
        lockRevision: current.lockRevision,
      });
    }
    update({ runReview: { ...r, actionId: action.id } });
    return submit(action.id);
  }
  async function retarget(
    kind: "collection" | "archive",
    expectedOld: string | null,
    expectedNew: string | null,
  ) {
    if (!active() || state.busy || state.configurationUnknown) return false;
    await (kind === "collection" ? loadStatus() : loadPair());
    if (
      (kind === "collection" ? state.status.phase : state.pair.phase) !==
      "ready"
    )
      return false;
    const current =
      kind === "collection"
        ? (state.status.value?.raid_link?.raid_playlist_id ?? null)
        : (state.pair.value?.archive_playlist_id ?? null);
    const old =
      kind === "collection"
        ? (state.pendingSource?.id ?? null)
        : (state.archiveSource?.id ?? null);
    if (
      current !== expectedNew ||
      old !== expectedOld ||
      (kind === "archive" && !current)
    ) {
      update({
        error:
          "The source changed again. Review its exact identity before retargeting.",
      });
      return false;
    }
    if (kind === "collection") {
      const link = state.status.value?.raid_link;
      stageUpdate({
        pendingSource: link
          ? {
              id: link.raid_playlist_id,
              name: link.raid_playlist_name ?? link.raid_playlist_id,
            }
          : null,
      });
    } else {
      const pair = state.pair.value!;
      stageUpdate({
        archiveSource: {
          id: pair.archive_playlist_id,
          name: pair.archive_playlist_name ?? pair.archive_playlist_id,
        },
      });
    }
    c.configurationChanged();
    update({
      message:
        "Unsubmitted staging now names the explicitly reviewed source. Review & save again.",
    });
    return true;
  }
  return {
    client,
    creationAcknowledged: (id: string) => {
      const action = actionFor(id);
      return (
        !!action &&
        !!admissions.getSnapshot().get(action.key)?.acknowledgedOrphan
      );
    },
    creationAcknowledgement: (id: string) => {
      const action = actionFor(id);
      return action && active()
        ? admissions.unidentifiedCreationAcknowledgement(action.key, c.actor)
        : null;
    },
    acknowledgeCreation: (
      id: string,
      proof: import("../../../operations/domain-evidence.ts").UnidentifiedCreationAcknowledgement,
    ) => {
      const action = actionFor(id);
      if (
        !active() ||
        !action ||
        action.key !== proof.key ||
        !admissions.acknowledgeUnidentifiedCreation(proof, c.actor)
      )
        return false;
      update({
        message: `Unidentified creation #${proof.operationId} remains recorded. Choose an exact playlist or explicitly create another.`,
        error: "",
      });
      stagingListeners.forEach((fn) => fn());
      return true;
    },
    initialize,
    loadRoutines,
    history,
    routineState,
    deleteRoutine,
    prepareRun,
    runReviewed,
    retarget,
    loadPair,
    loadStatus,
    loadPending,
    loadArchive,
    loadOwned,
    snapshot,
    visiblePending,
    visibleArchive,
    stagePending,
    stageArchive,
    discard,
    manualDraftMutation,
    prepareReview,
    targetConfirmed,
    retainedWork,
    dismiss,
    dismissMany,
    createAsset,
    runTask,
    reviewRetry,
    canReviewRetry,
    inspect,
    submit,
    actionFor,
    subscribe(fn: () => void) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    getSnapshot: () => state,
    setURL: (url: string) => update({ url }),
    setDripInput: (dripInput: string) => update({ dripInput }),
    setSourceCountInput: (id: number, value: string) =>
      update({
        sourceCountInputs: { ...state.sourceCountInputs, [id]: value },
      }),
    selectCollection: (selectedCollection: string) =>
      update({ selectedCollection }),
    selectArchive: (selectedArchive: string) => update({ selectedArchive }),
    addURL: () =>
      sourceConfigurationAllowed()
        ? local(
            () =>
              client.addURL(
                state.url,
                c.getSnapshot().context?.playlist.name ?? c.playlistId,
              ),
            async () => {
              update({ url: "" });
              await loadStatus();
            },
          )
        : Promise.resolve(false),
    saveCount: () => {
      const n = Number(state.dripInput);
      if (!Number.isInteger(n) || n < 1 || n > 50) {
        update({ error: "Tracks per run must be 1–50." });
        return Promise.resolve(false);
      }
      return local(() => client.updateLink({ drip_count: n }), loadStatus);
    },
    toggleDrip: () =>
      local(
        () =>
          client.updateLink({
            drip_enabled: !state.status.value?.raid_link?.drip_enabled,
          }),
        loadStatus,
      ),
    async updateSourceCount(id: number, n: number) {
      const row = state.status.value?.sources.find((x) => x.id === id);
      if (
        !sourceConfigurationAllowed() ||
        !row ||
        state.busy ||
        state.configurationUnknown ||
        !active()
      )
        return false;
      if (!Number.isInteger(n) || n < 1 || n > 100 || n === row.raid_count) {
        update({
          sourceCountInputs: {
            ...state.sourceCountInputs,
            [id]: String(row.raid_count),
          },
        });
        return false;
      }
      const ok = await local(() => client.sourceCount(id, n), loadStatus);
      if (ok || !state.configurationUnknown)
        update({
          sourceCountInputs: {
            ...state.sourceCountInputs,
            [id]: String(
              state.status.value?.sources.find((x) => x.id === id)
                ?.raid_count ?? row.raid_count,
            ),
          },
        });
      return ok;
    },
    unwatch: (id: number) =>
      sourceConfigurationAllowed()
        ? local(
            () => client.unwatch(id),
            async () => {
              await loadStatus();
              if (state.routines.phase !== "idle") await loadRoutines();
            },
          )
        : Promise.resolve(false),
    toggleArchive: () =>
      local(
        () => client.autoArchive(!state.pair.value?.auto_archive_on_remove),
        loadPair,
      ),
    unlinkCollection: () =>
      !associationAllowed(
        state.status.value?.raid_link?.raid_playlist_id ?? null,
      ) ||
      state.stagedPending.length ||
      state.retained.some(
        (x) => x.task.kind === "raid_promotion" && x.phase !== "complete",
      )
        ? Promise.resolve(false)
        : local(client.unlinkCollection, loadStatus),
    unlinkArchive: () =>
      !associationAllowed(state.pair.value?.archive_playlist_id ?? null) ||
      state.stagedRestore.length ||
      state.archiveQueue.length ||
      state.retained.some(
        (x) => x.task.kind !== "raid_promotion" && x.phase !== "complete",
      )
        ? Promise.resolve(false)
        : local(client.unlinkArchive, async () => {
            await loadPair();
            update({ archive: initial({ tracks: [], total: 0 }) });
          }),
    async refreshConfiguration() {
      const routinesLoaded = state.routines.phase !== "idle";
      await Promise.all([
        loadPair(),
        loadStatus(),
        ...(routinesLoaded ? [loadRoutines()] : []),
      ]);
      if (
        state.pair.phase === "ready" &&
        state.status.phase === "ready" &&
        (!routinesLoaded || state.routines.phase === "ready")
      )
        update({
          configurationUnknown: false,
          error: "",
          message: "Current settings read. Review them before another change.",
        });
    },
    retryStatus: (id: number) => {
      if (active()) operations.observe(id);
    },
  };
}
export type SourcesController = ReturnType<typeof createSourcesController>;
const cache = new WeakMap<WorkshopController, SourcesController>();
export function getSourcesController(c: WorkshopController) {
  let value = cache.get(c);
  if (!value) {
    value = createSourcesController(c);
    cache.set(c, value);
  }
  return value;
}
