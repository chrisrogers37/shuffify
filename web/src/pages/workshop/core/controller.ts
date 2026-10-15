import type { ApiClient } from "../../../api/client.ts";
import type { WorkshopTrack } from "../../../api/contracts.ts";
import type { SessionStore } from "../../../auth/session-store.ts";
import {
  ApiRequestError,
  ConflictError,
  NetworkError,
  ProtocolError,
  RateLimitError,
} from "../../../api/errors.ts";
import {
  getOperationStore,
  terminalOperationStates,
} from "../../../operations/store.ts";
import type { OperationStore } from "../../../operations/store.ts";
import {
  createWorkshopClient,
  targetResult,
  trackURI,
  verifiedRestore,
} from "./client.ts";
import {
  freeze,
  moveOccurrence,
  occurrences,
  observedOccurrences,
  remap,
  removeOccurrence,
  sameOrder,
  summarize,
  uris,
} from "./occurrences.ts";
import { workshopPermission } from "./permissions.ts";
import type {
  DeepReadonly,
  Occurrence,
  PermissionGroup,
  PreviewInput,
  ResourceClaim,
  ReviewedAction,
  SourceCoordinator,
  ToolId,
  WorkshopState,
} from "./types.ts";
let reviewIssued = 0,
  observationIssued = 0;
const asError = (e: unknown) =>
  e instanceof Error ? e : Error("This action could not be completed.");
export function createWorkshopController(
  api: ApiClient,
  session: SessionStore,
  actor: string,
  playlistId: string,
  options: { operations?: OperationStore; key?: () => string } = {},
) {
  const client = createWorkshopClient(api, playlistId),
    operations = options.operations ?? getOperationStore(api, actor),
    listeners = new Set<() => void>(),
    tools = new Map<ToolId | "named-drafts", object>();
  let state: WorkshopState = {
    phase: "idle",
    context: null,
    error: null,
    baseline: [],
    working: [],
    revision: 0,
    configRevision: 0,
    lockRevision: 0,
    sourceRevision: 0,
    locks: {},
    staging: [],
    claims: [],
    reviewing: false,
    review: null,
    receipt: null,
    observation: null,
    checking: false,
    notice: "",
    retryUntil: 0,
    drafts: [],
    draftsLoaded: false,
    draftsBusy: false,
    draftsUnknown: false,
    draftsError: "",
  };
  let loading: Promise<void> | null = null,
    source: SourceCoordinator | null = null,
    sourceOff: (() => void) | null = null,
    draftTicket = 0;
  let pendingDraft: {
    kind: "save" | "delete";
    id?: number;
    name: string;
    uris: string[];
    beforeIds: number[];
  } | null = null;
  const update = (patch: Partial<WorkshopState>) => {
    state = freeze({ ...state, ...patch }) as WorkshopState;
    listeners.forEach((fn) => fn());
  };
  const authenticated = () => {
    const s = session.getSnapshot();
    return (
      s.status === "ready" && s.session.signed_in && s.session.user.id === actor
    );
  };
  const permission = (group: PermissionGroup, subject?: string) =>
    workshopPermission(state, group, playlistId, authenticated(), subject);
  const refuse = (group: PermissionGroup) => {
    const p = permission(group);
    if (!p.allowed) update({ notice: p.reason });
    return !p.allowed;
  };
  const retryHint = (e: unknown) =>
    e instanceof RateLimitError
      ? Date.now() + (e.retryAfterMs ?? 1000)
      : state.retryUntil;
  const cooling = () => Date.now() < state.retryUntil;
  const hasDraft = () =>
    !sameOrder(uris(state.baseline), uris(state.working)) ||
    state.staging.some((s) =>
      s.kind === "raid_promotion"
        ? s.pending_rows.length > 0
        : s.track_uris.length > 0,
    );
  const changed = (rows: readonly Occurrence[]) =>
    update({
      working: [...rows],
      revision: state.revision + 1,
      notice: "Draft changes are local until you review and save.",
    });
  function refreshStaging() {
    update({
      staging: source ? structuredClone(source.snapshot()) : [],
      sourceRevision: state.sourceRevision + 1,
    });
  }
  function discardStaging() {
    source?.discard();
    refreshStaging();
  }
  async function load() {
    if (loading) return loading;
    if (state.phase === "ready") return;
    update({ phase: "loading", error: null });
    loading = (async () => {
      try {
        if (!authenticated())
          throw Error("Confirm your session before loading this Workshop.");
        const context = await client.context();
        if (!authenticated()) throw Error("The signed-in account changed.");
        const rows = occurrences(context.tracks);
        update({
          context,
          baseline: rows,
          working: rows,
          phase: "ready",
          error: null,
        });
      } catch (e) {
        update({ phase: "error", error: asError(e), retryUntil: retryHint(e) });
      } finally {
        loading = null;
      }
    })();
    return loading;
  }
  function move(key: string, position: number) {
    if (refuse("draft")) return false;
    const rows = state.working,
      from = rows.findIndex((r) => r.key === key),
      to = Math.max(0, Math.min(rows.length - 1, Math.trunc(position) - 1));
    if (from < 0 || !Number.isFinite(position)) return false;
    if (state.locks[rows[from]!.uri] || state.locks[rows[to]!.uri]) {
      update({
        notice:
          "Held tracks cannot be moved directly or used as a destination. Choose an unheld row and destination.",
      });
      return false;
    }
    const next = moveOccurrence(rows, key, position);
    changed(next);
    return true;
  }
  function remove(key: string) {
    if (refuse("draft") || !state.working.some((r) => r.key === key))
      return false;
    changed(removeOccurrence(state.working, key));
    return true;
  }
  function replace(
    next: readonly string[],
    args: {
      expectedRevision: number;
      expectedLockRevision?: number;
      tracks?: readonly DeepReadonly<WorkshopTrack>[];
      discardStaging?: boolean;
    },
  ) {
    if (refuse("draft")) return false;
    if (
      args.expectedRevision !== state.revision ||
      (args.expectedLockRevision !== undefined &&
        args.expectedLockRevision !== state.lockRevision)
    ) {
      update({
        notice:
          "The draft changed while this read was running. Its result was not applied.",
      });
      return false;
    }
    if (!next.every(trackURI)) {
      update({
        notice: "This order contains unsupported tracks and cannot be applied.",
      });
      return false;
    }
    if (args.discardStaging) discardStaging();
    changed(remap(state.working, next, args.tracks));
    return true;
  }
  function append(
    tracks: readonly DeepReadonly<WorkshopTrack>[],
    args: { mode: "append" | "append-missing"; origin: string },
  ) {
    if (refuse("draft")) return false;
    const next = [...state.working],
      seen = new Set(uris(next));
    for (const track of tracks) {
      if (!trackURI(track.uri) || track.is_local) continue;
      if (args.mode === "append-missing" && seen.has(track.uri)) continue;
      next.push(...occurrences([track]));
      seen.add(track.uri);
    }
    changed(next);
    return true;
  }
  function getPreviewInput(): PreviewInput {
    return freeze({
      draftRevision: state.revision,
      lockRevision: state.lockRevision,
      track_uris: uris(state.working),
      locked_positions: Object.fromEntries(
        state.working.flatMap((r, i) =>
          state.locks[r.uri] ? [[i, r.uri]] : [],
        ),
      ),
    });
  }
  function applyPreview(input: PreviewInput, next: readonly string[]) {
    if (refuse("preview")) return false;
    const a = [...input.track_uris].sort(),
      b = [...next].sort();
    if (
      !sameOrder(a, b) ||
      Object.entries(input.locked_positions).some(
        ([i, uri]) => next[Number(i)] !== uri,
      )
    ) {
      update({
        notice:
          "The preview did not preserve the complete order and held positions.",
      });
      return false;
    }
    return replace(next, {
      expectedRevision: input.draftRevision,
      expectedLockRevision: input.lockRevision,
    });
  }
  function setLocks(
    locks: Readonly<Record<string, boolean>>,
    expectedRevision?: number,
  ) {
    if (
      refuse("locks") ||
      (expectedRevision !== undefined &&
        expectedRevision !== state.lockRevision)
    )
      return false;
    update({ locks: { ...locks }, lockRevision: state.lockRevision + 1 });
    return true;
  }
  const configurationChanged = () =>
    update({ configRevision: state.configRevision + 1 });
  function validateReview(review: ReviewedAction) {
    return (
      review.revision === state.revision &&
      review.configRevision === state.configRevision &&
      review.lockRevision === state.lockRevision &&
      review.sourceRevision === state.sourceRevision
    );
  }
  function snapshotReviewBase() {
    return {
      id: ++reviewIssued,
      revision: state.revision,
      configRevision: state.configRevision,
      lockRevision: state.lockRevision,
      sourceRevision: state.sourceRevision,
      key: options.key?.() ?? crypto.randomUUID(),
      target: { id: playlistId, name: state.context!.playlist.name },
      baseline: [...state.baseline],
      desired: [...state.working],
      staging: structuredClone(state.staging),
      summary: summarize(uris(state.baseline), uris(state.working)),
    };
  }
  async function prepareReview() {
    if (refuse("review")) throw Error(state.notice);
    if (state.reviewing) throw Error("A review is already being prepared.");
    if (
      state.staging.some((s) =>
        s.kind === "raid_promotion"
          ? s.pending_rows.length > 0
          : s.track_uris.length > 0,
      )
    ) {
      const message =
        "Source staging needs its source coordinator review before it can be saved.";
      update({ notice: message });
      throw Error(message);
    }
    const captured = freeze({
      ...snapshotReviewBase(),
      kind: "commit" as const,
      body: {
        baseline_track_uris: uris(state.baseline),
        track_uris: uris(state.working),
      },
    });
    update({ reviewing: true, notice: "" });
    try {
      const scope = await client.sourceContext(false, false);
      if (!authenticated() || !validateReview(captured))
        throw Error("The draft or configuration changed. Review it again.");
      const reviewed = freeze({ ...captured, target: scope.target });
      update({ review: reviewed });
      return reviewed;
    } catch (error) {
      update({ retryUntil: retryHint(error), notice: asError(error).message });
      throw error;
    } finally {
      update({ reviewing: false });
    }
  }
  async function prepareSnapshotRestore(snapshotId: number) {
    if (refuse("snapshot")) throw Error(state.notice);
    if (hasDraft())
      throw Error(
        "Resolve the independent local draft and source staging before restoring a snapshot.",
      );
    if (state.reviewing) throw Error("A review is already being prepared.");
    const base = freeze(snapshotReviewBase());
    update({ reviewing: true });
    try {
      const context = await client.snapshotReview(snapshotId);
      if (!authenticated() || !validateReview(base as ReviewedAction))
        throw Error("The draft changed. Review the snapshot again.");
      const review = freeze({
        ...base,
        kind: "snapshot_restore" as const,
        snapshotId,
        context,
        desired: remap(state.working, context.action.desired_track_uris),
        summary: context.action.summary,
      });
      update({ review });
      return review;
    } catch (error) {
      update({ retryUntil: retryHint(error), notice: asError(error).message });
      throw error;
    } finally {
      update({ reviewing: false });
    }
  }
  async function prepareVisibility(publicValue: boolean) {
    if (refuse("metadata")) throw Error(state.notice);
    if (state.reviewing) throw Error("A review is already being prepared.");
    const base = freeze(snapshotReviewBase());
    update({ reviewing: true });
    try {
      const context = await client.visibilityReview(publicValue);
      if (!authenticated()) throw Error("The signed-in account changed.");
      const review = freeze({
        ...base,
        kind: "privacy" as const,
        public: publicValue,
        context,
      });
      update({ review });
      return review;
    } catch (error) {
      update({ retryUntil: retryHint(error), notice: asError(error).message });
      throw error;
    } finally {
      update({ reviewing: false });
    }
  }
  async function dispatch(review: ReviewedAction) {
    update({
      receipt: {
        review,
        status: "submitting",
        operationId: null,
        operation: null,
        result: null,
        message: "Submitting this exact reviewed request…",
        retryUntil: 0,
      },
    });
    try {
      const op =
        review.kind === "commit"
          ? await client.commit(review.body, review.key)
          : review.kind === "snapshot_restore"
            ? await client.restore(
                review.snapshotId,
                review.context.review_binding,
                review.key,
              )
            : await client.visibility(
                review.public,
                review.context.review_binding.scope,
                review.key,
              );
      const expected =
        review.kind === "commit"
          ? "workshop_commit"
          : review.kind === "privacy"
            ? "visibility"
            : review.kind;
      if (op.kind !== expected || !op.targets.includes(playlistId))
        throw new ProtocolError(
          "The accepted operation names a different action or target.",
        );
      update({
        receipt: {
          review,
          status: "accepted",
          operationId: op.id,
          operation: op,
          result: null,
          message: `Operation ${op.id} was accepted. Closing this panel does not cancel it.`,
          retryUntil: 0,
        },
      });
      operations.accept(op);
    } catch (e) {
      const error = asError(e),
        unknown =
          e instanceof NetworkError ||
          e instanceof ProtocolError ||
          (e instanceof ApiRequestError && e.status >= 500),
        until =
          e instanceof RateLimitError
            ? Date.now() + (e.retryAfterMs ?? 1000)
            : 0;
      const busy = e instanceof ConflictError ? e.error.operation_id : null;
      update({
        retryUntil: until,
        receipt: {
          review,
          status: busy ? "accepted" : unknown ? "unknown" : "refused",
          operationId: busy ?? null,
          operation: null,
          result:
            e instanceof ApiRequestError
              ? targetResult(
                  e.error as unknown as import("../../../api/contracts.ts").JsonRecord,
                )
              : null,
          message: busy
            ? `${error.message} Operation ${busy} retains this target.`
            : unknown
              ? `${error.message} Keep this exact request; no new save is safe until it is reconciled.`
              : error.message,
          retryUntil: until,
        },
      });
      if (busy) operations.observe(busy);
    }
  }
  async function submitReviewed(id: number) {
    const r = state.review;
    if (!r || r.id !== id) return false;
    if (refuse(r.kind === "privacy" ? "metadata" : "save")) return false;
    if (!validateReview(r)) {
      update({
        notice: "This review is out of date. Review the current draft again.",
      });
      return false;
    }
    await dispatch(r);
    return true;
  }
  async function retryExactRequest() {
    const r = state.receipt;
    if (
      !r ||
      r.status !== "unknown" ||
      !authenticated() ||
      Date.now() < r.retryUntil
    )
      return false;
    await dispatch(r.review);
    return true;
  }
  const observeReceipt = () => {
    const id = state.receipt?.operationId;
    if (id) operations.observe(id);
  };
  operations.subscribe(() => {
    const receipt = state.receipt;
    if (!receipt?.operationId) return;
    const observed = operations.getSnapshot().get(receipt.operationId);
    if (!observed?.operation) return;
    const op = observed.operation;
    if (receipt.operation?.state === op.state && receipt.operation === op)
      return;
    const terminal = terminalOperationStates.has(op.state),
      result = targetResult(op.result);
    let patch: Partial<WorkshopState> = {
      receipt: {
        ...receipt,
        status: terminal ? "settled" : "accepted",
        operation: op,
        result,
        message:
          typeof op.result?.message === "string"
            ? op.result.message
            : `Operation ${op.id}: ${op.state.replaceAll("_", " ")}.`,
      },
    };
    if (terminal && op.state === "succeeded" && op.result?.success === true) {
      if (
        receipt.review.kind === "privacy" &&
        typeof op.result.public === "boolean" &&
        state.context
      )
        patch = {
          ...patch,
          context: {
            ...state.context,
            playlist: { ...state.context.playlist, public: op.result.public },
          },
        };
      else if (receipt.review.kind === "snapshot_restore") {
        const verified = verifiedRestore(op, receipt.review);
        if (
          verified &&
          state.revision === receipt.review.revision &&
          state.sourceRevision === receipt.review.sourceRevision
        ) {
          const rows = observedOccurrences(
            receipt.review.desired,
            verified.tracks,
          );
          patch = {
            ...patch,
            baseline: rows,
            working: rows,
            revision: state.revision + 1,
            review: null,
            observation: null,
            notice: "Snapshot restored to Spotify.",
          };
        }
      } else if (
        result &&
        ["saved", "already_applied"].includes(result.target_status) &&
        result.saved_track_uris &&
        sameOrder(result.saved_track_uris, uris(receipt.review.desired)) &&
        state.revision === receipt.review.revision &&
        state.sourceRevision === receipt.review.sourceRevision
      ) {
        const rows = remap(receipt.review.desired, result.saved_track_uris);
        patch = {
          ...patch,
          baseline: rows,
          working: rows,
          review: null,
          observation: null,
          notice: "Playlist saved to Spotify.",
        };
      }
    }
    update(patch);
  });
  async function checkCurrent() {
    if (state.checking || !authenticated() || cooling()) return false;
    update({ checking: true, observation: null, notice: "" });
    try {
      const value = await client.current();
      if (!authenticated()) throw Error("The signed-in account changed.");
      update({
        observation: freeze({ ...value, id: ++observationIssued }),
        notice:
          "Current Spotify order loaded. This read does not settle a queued operation or confirm history.",
      });
      return true;
    } catch (e) {
      update({
        observation: null,
        notice: asError(e).message,
        retryUntil: retryHint(e),
      });
      return false;
    } finally {
      update({ checking: false });
    }
  }
  function adoptCurrent(id: number) {
    const o = state.observation;
    if (!o || o.id !== id || refuse("history")) return false;
    discardStaging();
    const rows = observedOccurrences(state.working, o.tracks);
    update({
      baseline: rows,
      working: rows,
      revision: state.revision + 1,
      review: null,
      notice:
        "This view now uses the inspected Spotify order. No Spotify write or history reset was made.",
    });
    return true;
  }
  function discardLocal() {
    if (!authenticated()) return false;
    if (
      state.receipt?.review.kind !== "privacy" &&
      (state.receipt?.status === "submitting" ||
        state.receipt?.status === "accepted")
    ) {
      update({
        notice:
          "An accepted Spotify operation cannot be cancelled by discarding the draft.",
      });
      return false;
    }
    discardStaging();
    update({
      working: [...state.baseline],
      revision: state.revision + 1,
      review: null,
      notice: "Local draft changes discarded.",
    });
    return true;
  }
  async function loadDrafts() {
    if (state.draftsBusy || !authenticated() || cooling()) return false;
    update({ draftsBusy: true, draftsError: "" });
    try {
      const drafts = await client.drafts();
      if (!authenticated()) return false;
      let unknown = state.draftsUnknown;
      if (pendingDraft) {
        const p = pendingDraft;
        const match =
          p.kind === "delete"
            ? !drafts.some((d) => d.id === p.id)
            : drafts.some(
                (d) =>
                  (p.id ? d.id === p.id : !p.beforeIds.includes(d.id)) &&
                  d.session_name === p.name &&
                  sameOrder(d.track_uris, p.uris),
              );
        if (match) {
          unknown = false;
          pendingDraft = null;
        }
      }
      update({
        drafts,
        draftsLoaded: true,
        draftsUnknown: unknown,
        draftsError: unknown
          ? "The previous local change is still unconfirmed. Inspect the saved drafts before repeating it."
          : "",
      });
      return true;
    } catch (e) {
      update({ retryUntil: retryHint(e), draftsError: asError(e).message });
      return false;
    } finally {
      update({ draftsBusy: false });
    }
  }
  async function saveDraft(name: string, id?: number) {
    if (
      state.draftsBusy ||
      state.draftsUnknown ||
      cooling() ||
      refuse("history")
    )
      return false;
    name = name.trim();
    if (!name) {
      update({ draftsError: "Give this draft a name." });
      return false;
    }
    const order = uris(state.working);
    update({ draftsBusy: true, draftsError: "" });
    try {
      const saved = await client.saveDraft(name, order, id);
      if (!authenticated()) return false;
      update({
        drafts: [saved, ...state.drafts.filter((d) => d.id !== saved.id)],
        draftsLoaded: true,
        notice: `Draft “${saved.session_name}” saved. Spotify was not changed.`,
      });
      return true;
    } catch (e) {
      const unknown =
        e instanceof NetworkError ||
        e instanceof ProtocolError ||
        (e instanceof ApiRequestError && e.status >= 500);
      if (unknown)
        pendingDraft = {
          kind: "save",
          ...(id ? { id } : {}),
          name,
          uris: order,
          beforeIds: state.drafts.map((d) => d.id),
        };
      update({
        draftsUnknown: unknown,
        retryUntil: retryHint(e),
        draftsError: asError(e).message,
      });
      return false;
    } finally {
      update({ draftsBusy: false });
    }
  }
  async function deleteDraft(id: number) {
    if (
      state.draftsBusy ||
      state.draftsUnknown ||
      cooling() ||
      refuse("history")
    )
      return false;
    update({ draftsBusy: true, draftsError: "" });
    try {
      await client.deleteDraft(id);
      if (!authenticated()) return false;
      update({
        drafts: state.drafts.filter((d) => d.id !== id),
        notice: "Saved draft deleted. Spotify was not changed.",
      });
      return true;
    } catch (e) {
      const unknown =
        e instanceof NetworkError ||
        e instanceof ProtocolError ||
        (e instanceof ApiRequestError && e.status >= 500);
      if (unknown)
        pendingDraft = {
          kind: "delete",
          id,
          name: "",
          uris: [],
          beforeIds: [],
        };
      update({
        draftsUnknown: unknown,
        retryUntil: retryHint(e),
        draftsError: asError(e).message,
      });
      return false;
    } finally {
      update({ draftsBusy: false });
    }
  }
  async function loadNamedDraft(id: number, allowReplace = false) {
    if (cooling() || refuse("draft")) return false;
    const ticket = ++draftTicket,
      revision = state.revision,
      sourceRevision = state.sourceRevision;
    try {
      const saved = await client.draft(id);
      if (
        ticket !== draftTicket ||
        sourceRevision !== state.sourceRevision ||
        revision !== state.revision ||
        refuse("draft")
      )
        return false;
      if (hasDraft() && !allowReplace) {
        update({
          draftsError:
            "Confirm replacing the local draft before loading this saved draft.",
        });
        return false;
      }
      return replace(saved.track_uris, {
        expectedRevision: revision,
        discardStaging: true,
      });
    } catch (e) {
      update({ retryUntil: retryHint(e), draftsError: asError(e).message });
      return false;
    }
  }
  const registerSourceCoordinator = (coordinator: SourceCoordinator) => {
    if (source && source !== coordinator)
      throw Error("Only one source coordinator may own this target.");
    source = coordinator;
    sourceOff?.();
    sourceOff = coordinator.subscribe(refreshStaging);
    refreshStaging();
    return () => {
      sourceOff?.();
      sourceOff = null;
      source =
        null; /* Keep staging until an explicit coordinator discard/reconciliation. */
    };
  };
  const claim = (c: ResourceClaim) => {
    update({
      claims: [
        ...state.claims.filter(
          (x) => x.owner !== c.owner || x.resource !== c.resource,
        ),
        c,
      ],
    });
    return () => update({ claims: state.claims.filter((x) => x !== c) });
  };
  session.subscribe(() => update({}));
  return {
    api,
    client,
    operations,
    actor,
    playlistId,
    subscribe: (fn: () => void) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    getSnapshot: () => state,
    load,
    permission,
    hasDraft,
    moveOccurrence: move,
    removeOccurrence: remove,
    appendTracks: append,
    replaceDraftOrder: replace,
    getPreviewInput,
    applyPreview,
    setLocks,
    configurationChanged,
    prepareReview,
    prepareSnapshotRestore,
    prepareVisibility,
    submitReviewed,
    retryExactRequest,
    observeReceipt,
    checkCurrent,
    adoptCurrent,
    discardLocal,
    registerSourceCoordinator,
    claim,
    loadDrafts,
    saveDraft,
    deleteDraft,
    loadNamedDraft,
    reviewIsCurrent: () => !!state.review && validateReview(state.review),
    retainTool: <T extends object>(
      id: ToolId | "named-drafts",
      initial: () => T,
    ): T => {
      if (!tools.has(id)) tools.set(id, initial());
      return tools.get(id) as T;
    },
  };
}
export type WorkshopController = ReturnType<typeof createWorkshopController>;
const controllers = new WeakMap<ApiClient, Map<string, WorkshopController>>();
export function getWorkshopController(
  api: ApiClient,
  session: SessionStore,
  actor: string,
  playlistId: string,
) {
  let cache = controllers.get(api);
  if (!cache) {
    cache = new Map();
    controllers.set(api, cache);
  }
  const key = JSON.stringify([actor, playlistId]);
  let c = cache.get(key);
  if (!c) {
    c = createWorkshopController(api, session, actor, playlistId);
    cache.set(key, c);
  }
  return c;
}
