import type { ApiClient } from "../../api/client.ts";
import type { JsonRecord, LibraryContext } from "../../api/contracts.ts";
import type { SessionStore } from "../../auth/session-store.ts";
import {
  ApiRequestError,
  MissingCsrfTokenError,
  NetworkError,
  ProtocolError,
  RateLimitError,
} from "../../api/errors.ts";
import { getOperationStore } from "../../operations/store.ts";
import type { OperationStore } from "../../operations/store.ts";
import {
  inspectCurrent,
  loadLibrary,
  loadReview,
  preference,
  record,
  sendReviewed,
} from "./api.ts";
import type { Intent, Review, Inspection, InspectionIntent } from "./api.ts";
import {
  createShuffleDraft,
  findRow,
  moveRow,
  orderedIds,
  placeRow,
  selectMethod,
  setParameter,
  shuffleForm,
} from "./model.ts";
import type { ShuffleDraft } from "./model.ts";
export interface Receipt {
  id: number;
  playlistId: string;
  name: string;
  intent: Intent;
  revision: number;
  key: string;
  status:
    | "loading"
    | "ready"
    | "sending"
    | "accepted"
    | "unknown"
    | "refused"
    | "cancelled";
  review: Review | null;
  rejection: JsonRecord | null;
  inspecting: boolean;
  inspections: Inspection[];
  inspectionError: string;
  inspectionRetryUntil: number;
  error: string;
  retryUntil: number;
  operationId: number | null;
  busyId: number | null;
}
export interface LibraryState {
  context: LibraryContext | null;
  loading: boolean;
  error: string;
  retryUntil: number;
  drafts: ReadonlyMap<string, ShuffleDraft>;
  receipts: ReadonlyMap<number, Receipt>;
  preferenceBusy: boolean;
  preferenceError: string;
  preferenceUnknown: boolean;
  preferenceRetryUntil: number;
  orderDirty: boolean;
  notice: string;
}
export interface ControllerOptions {
  key?: () => string;
  operations?: OperationStore;
}
export function createLibraryController(
  api: ApiClient,
  session: SessionStore,
  actor: string,
  options: ControllerOptions = {},
) {
  const operations = options.operations ?? getOperationStore(api, actor);
  let state: LibraryState = {
    context: null,
    loading: false,
    error: "",
    retryUntil: 0,
    drafts: new Map(),
    receipts: new Map(),
    preferenceBusy: false,
    preferenceError: "",
    preferenceUnknown: false,
    preferenceRetryUntil: 0,
    orderDirty: false,
    notice: "",
  };
  const listeners = new Set<() => void>();
  const update = (patch: Partial<LibraryState>) => {
    state = { ...state, ...patch };
    listeners.forEach((fn) => fn());
  };
  const changeReceipt = (id: number, patch: Partial<Receipt>) => {
    const old = state.receipts.get(id);
    if (old)
      update({
        receipts: new Map(state.receipts).set(id, { ...old, ...patch }),
      });
  };
  const sameActor = () => {
    const snapshot = session.getSnapshot();
    return (
      snapshot.status === "ready" &&
      snapshot.session.signed_in &&
      snapshot.session.user.id === actor
    );
  };
  const authorize = () => {
    if (!sameActor()) throw new MissingCsrfTokenError();
  };
  let pendingRead: Promise<void> | null = null,
    sequence = 0,
    projectionRevision = 0,
    orderWanted = false,
    savingOrder: Promise<void> | null = null;
  const message = (e: unknown) =>
    e instanceof ApiRequestError
      ? e.message
      : "The response could not be confirmed. Check your connection and try again.";
  async function load(refresh = false) {
    if (pendingRead) return pendingRead;
    if (state.context && !refresh) return;
    const readRevision = projectionRevision;
    update({ loading: true, error: "" });
    pendingRead = Promise.resolve().then(async () => {
      try {
        authorize();
        const context = await loadLibrary(api, refresh);
        if (!sameActor()) return;
        if (readRevision !== projectionRevision) {
          update({
            error:
              "The playlist changed while refreshing. Refresh again to load current Library details.",
          });
          return;
        }
        const drafts = new Map(state.drafts);
        for (const row of Object.values(context.sections).flat())
          if (!drafts.has(row.id))
            drafts.set(
              row.id,
              createShuffleDraft(
                row,
                context.algorithms,
                context.shuffle_defaults,
              ),
            );
        update({
          context,
          drafts,
          error: "",
          preferenceError: "",
          preferenceUnknown: false,
          orderDirty: false,
          notice: refresh ? "Library refreshed." : "",
          retryUntil: 0,
        });
      } catch (e) {
        update({
          error: message(e),
          retryUntil:
            e instanceof RateLimitError
              ? Date.now() + (e.retryAfterMs ?? 1000)
              : 0,
        });
      } finally {
        update({ loading: false });
        pendingRead = null;
      }
    });
    return pendingRead;
  }
  function editMethod(id: string, method: string) {
    const c = state.context,
      row = c && findRow(c.sections, id),
      draft = state.drafts.get(id);
    if (c && row && draft)
      update({
        drafts: new Map(state.drafts).set(
          id,
          selectMethod(draft, method, row, c.algorithms, c.shuffle_defaults),
        ),
      });
  }
  function editParameter(id: string, name: string, value: string) {
    const draft = state.drafts.get(id);
    if (draft)
      update({
        drafts: new Map(state.drafts).set(id, setParameter(draft, name, value)),
      });
  }
  function blocked(): boolean {
    return [...state.receipts.values()].some((r) => {
      if (r.status === "sending" || r.status === "unknown") return true;
      const id = r.operationId ?? r.busyId;
      if (id === null) return false;
      const entry = operations.getSnapshot().get(id),
        op = entry?.operation;
      if (
        op &&
        record(op.result?.operation) &&
        ["partial", "unknown"].includes(
          String(op.result.operation.aggregate_status),
        )
      )
        return true;
      return !op || !["succeeded", "failed", "rolled_back"].includes(op.state);
    });
  }
  async function begin(
    id: string,
    kind: Intent["kind"],
    publicValue?: boolean,
  ): Promise<number> {
    authorize();
    if (state.loading)
      throw new Error(
        "Wait for the Library refresh before reviewing a new action.",
      );
    if (blocked())
      throw new Error(
        "Reconcile the retained operation before starting another Spotify change.",
      );
    const c = state.context,
      row = c && findRow(c.sections, id),
      draft = state.drafts.get(id);
    if (!c || !row || !draft)
      throw new Error("Reload this playlist before reviewing.");
    const intent: Intent =
      kind === "shuffle"
        ? { kind, form: shuffleForm(draft, row, c.algorithms) }
        : kind === "privacy"
          ? { kind, public: publicValue ?? !row.public }
          : { kind };
    const rid = ++sequence,
      receipt: Receipt = {
        id: rid,
        playlistId: id,
        name: row.name,
        intent,
        revision: draft.revision,
        key: (options.key ?? (() => crypto.randomUUID()))(),
        status: "loading",
        review: null,
        rejection: null,
        inspecting: false,
        inspections: [],
        inspectionError: "",
        inspectionRetryUntil: 0,
        error: "",
        retryUntil: 0,
        operationId: null,
        busyId: null,
      };
    update({ receipts: new Map(state.receipts).set(rid, receipt) });
    void reviewAgain(rid);
    return rid;
  }
  async function reviewAgain(id: number) {
    const receipt = state.receipts.get(id);
    if (
      !receipt ||
      ["sending", "accepted", "unknown"].includes(receipt.status) ||
      Date.now() < receipt.retryUntil
    )
      return;
    changeReceipt(id, { status: "loading", error: "", rejection: null });
    try {
      authorize();
      const review = await loadReview(api, receipt.playlistId, receipt.intent);
      if (state.receipts.get(id)?.status !== "loading") return;
      changeReceipt(id, { review, status: "ready", retryUntil: 0 });
    } catch (e) {
      if (state.receipts.get(id)?.status !== "loading") return;
      changeReceipt(id, {
        status: "refused",
        rejection:
          e instanceof ApiRequestError
            ? (JSON.parse(JSON.stringify(e.error)) as JsonRecord)
            : null,
        error: message(e),
        retryUntil:
          e instanceof RateLimitError
            ? Date.now() + (e.retryAfterMs ?? 1000)
            : 0,
      });
    }
  }
  const stale = (receipt: Receipt) =>
    receipt.intent.kind === "shuffle" &&
    state.drafts.get(receipt.playlistId)?.revision !== receipt.revision;
  async function dispatch(id: number, exactRetry = false) {
    const r = state.receipts.get(id);
    if (
      !r ||
      !r.review ||
      !sameActor() ||
      state.loading ||
      Date.now() < r.retryUntil
    )
      return;
    if (
      exactRetry
        ? r.status !== "unknown"
        : r.status !== "ready" || stale(r) || blocked()
    )
      return;
    // Freeze intent and key before the first network boundary; never generate a retry key here.
    changeReceipt(id, { status: "sending", error: "" });
    try {
      authorize();
      const accepted = await sendReviewed(
        api,
        r.playlistId,
        r.intent,
        r.review,
        r.key,
      );
      if (accepted.status !== "accepted")
        throw new ProtocolError(
          "A Spotify command completed without an operation receipt.",
        );
      changeReceipt(id, {
        status: "accepted",
        operationId: accepted.operation.id,
        retryUntil: 0,
      });
      operations.accept(accepted.operation);
    } catch (e) {
      const known =
        e instanceof ApiRequestError || e instanceof MissingCsrfTokenError;
      const busy =
        e instanceof ApiRequestError ? (e.error.operation_id ?? null) : null;
      changeReceipt(id, {
        status: known ? "refused" : "unknown",
        rejection:
          e instanceof ApiRequestError
            ? (JSON.parse(JSON.stringify(e.error)) as JsonRecord)
            : null,
        error: known
          ? message(e)
          : "The request may have been accepted. Its exact request and key are retained. New Spotify changes are blocked until this is reconciled.",
        retryUntil:
          e instanceof RateLimitError
            ? Date.now() + (e.retryAfterMs ?? 1000)
            : 0,
        busyId: busy,
      });
      if (busy !== null) operations.observe(busy);
    }
  }
  async function inspect(id: number) {
    const r = state.receipts.get(id);
    if (
      !r ||
      r.inspecting ||
      !sameActor() ||
      Date.now() < r.inspectionRetryUntil
    )
      return;
    const operationId = r.operationId ?? r.busyId,
      op =
        operationId === null
          ? null
          : operations.getSnapshot().get(operationId)?.operation;
    let desired: string[] | null = null;
    if (
      record(op?.result?.operation) &&
      Array.isArray(op.result.operation.resources)
    ) {
      const resource = op.result.operation.resources.findLast(
        (value) =>
          record(value) &&
          value.resource === r.playlistId &&
          Array.isArray(value.desired_track_uris) &&
          value.desired_track_uris.every((uri) => typeof uri === "string"),
      );
      if (record(resource) && Array.isArray(resource.desired_track_uris))
        desired = resource.desired_track_uris.filter(
          (uri): uri is string => typeof uri === "string",
        );
    }
    if (desired === null && r.review?.kind === "undo")
      desired = [...r.review.action.desired_track_uris];
    const intent: InspectionIntent =
      r.intent.kind === "privacy"
        ? { kind: "privacy", desired_public: r.intent.public }
        : { kind: "tracks", desired_track_uris: desired };
    changeReceipt(id, { inspecting: true, inspectionError: "" });
    try {
      authorize();
      const inspection = await inspectCurrent(api, r.playlistId, intent);
      changeReceipt(id, {
        inspections: [
          ...(state.receipts.get(id)?.inspections ?? []),
          inspection,
        ],
        inspectionRetryUntil: 0,
      });
      if (inspection.status === "match" && r.intent.kind === "privacy")
        patchPlaylist(r.playlistId, { public: r.intent.public });
    } catch (e) {
      changeReceipt(id, {
        inspectionError: message(e),
        inspectionRetryUntil:
          e instanceof RateLimitError
            ? Date.now() + (e.retryAfterMs ?? 1000)
            : 0,
      });
    } finally {
      changeReceipt(id, { inspecting: false });
    }
  }
  function closeReview(id: number) {
    const r = state.receipts.get(id);
    if (r && (r.status === "loading" || r.status === "ready"))
      changeReceipt(id, { status: "cancelled" });
  }
  function patchPlaylist(
    id: string,
    patch: Partial<
      Pick<
        import("../../api/contracts.ts").LibraryRow,
        "track_count" | "can_undo" | "public"
      >
    >,
  ) {
    const c = state.context;
    if (!c) return;
    projectionRevision++;
    update({
      context: {
        ...c,
        sections: Object.fromEntries(
          Object.entries(c.sections).map(([k, rows]) => [
            k,
            rows.map((row) => (row.id === id ? { ...row, ...patch } : row)),
          ]),
        ) as LibraryContext["sections"],
      },
    });
  }
  const applied = new Set<number>();
  operations.subscribe(() => {
    for (const r of state.receipts.values()) {
      if (r.operationId === null || applied.has(r.operationId)) continue;
      const op = operations.getSnapshot().get(r.operationId)?.operation;
      if (
        !op ||
        !["succeeded", "failed", "rolled_back", "needs_attention"].includes(
          op.state,
        )
      )
        continue;
      applied.add(op.id);
      // Only verified terminal success may update the Library projection.
      if (op.state === "succeeded" && op.result?.success === true) {
        const result = op.result;
        if (r.intent.kind === "privacy" && typeof result.public === "boolean")
          patchPlaylist(r.playlistId, { public: result.public });
        if (record(result.playlist) && result.playlist.id === r.playlistId) {
          const patch: Partial<
            Pick<
              import("../../api/contracts.ts").LibraryRow,
              "track_count" | "can_undo"
            >
          > = {};
          if (
            Number.isSafeInteger(result.playlist.total_tracks) &&
            Number(result.playlist.total_tracks) >= 0
          )
            patch.track_count = Number(result.playlist.total_tracks);
          if (
            record(result.playlist_state) &&
            Number.isSafeInteger(result.playlist_state.current_index)
          )
            patch.can_undo = Number(result.playlist_state.current_index) > 0;
          patchPlaylist(r.playlistId, patch);
        }
      }
    }
    update({});
  });
  const preferenceCooling = () => Date.now() < state.preferenceRetryUntil;
  const preferenceRetry = (e: unknown) =>
    e instanceof RateLimitError ? Date.now() + (e.retryAfterMs ?? 1000) : 0;
  async function toggle(id: string, flag: "pinned" | "hidden") {
    if (
      state.loading ||
      state.preferenceBusy ||
      state.preferenceUnknown ||
      preferenceCooling()
    )
      return false;
    update({ preferenceBusy: true, preferenceError: "", notice: "" });
    try {
      authorize();
      const response = await preference(
          api,
          `${encodeURIComponent(id)}/toggle-${flag}`,
        ),
        value = response[flag === "pinned" ? "is_pinned" : "is_hidden"];
      if (typeof value !== "boolean")
        throw new ProtocolError("The saved preference was not confirmed.");
      const c = state.context;
      if (c)
        update({
          context: {
            ...c,
            sections: placeRow(c.sections, id, { [flag]: value }),
          },
          notice:
            flag === "pinned"
              ? value
                ? "Added to Favorites."
                : "Removed from Favorites."
              : value
                ? "Playlist hidden."
                : "Playlist shown.",
        });
      return true;
    } catch (e) {
      update({
        preferenceError: message(e),
        preferenceRetryUntil: preferenceRetry(e),
        preferenceUnknown:
          e instanceof NetworkError || e instanceof ProtocolError,
      });
      return false;
    } finally {
      update({ preferenceBusy: false });
    }
  }
  async function saveOrder() {
    if (savingOrder) return savingOrder;
    savingOrder = (async () => {
      while (orderWanted && !state.preferenceUnknown) {
        orderWanted = false;
        const c = state.context;
        if (!c) return;
        const ids = orderedIds(c.sections);
        update({ preferenceBusy: true, preferenceError: "" });
        try {
          authorize();
          const result = await preference(api, "order", { playlist_ids: ids });
          if (result.count !== ids.length)
            throw new ProtocolError("The playlist order was not confirmed.");
          if (!orderWanted)
            update({ orderDirty: false, notice: "Order saved." });
        } catch (e) {
          orderWanted = false;
          update({
            orderDirty: true,
            preferenceError: message(e),
            preferenceRetryUntil: preferenceRetry(e),
            preferenceUnknown:
              e instanceof NetworkError || e instanceof ProtocolError,
          });
          break;
        } finally {
          update({ preferenceBusy: false });
        }
      }
    })().finally(() => {
      savingOrder = null;
    });
    return savingOrder;
  }
  function move(id: string, position: number) {
    if (
      state.loading ||
      state.preferenceUnknown ||
      preferenceCooling() ||
      (state.preferenceBusy && !savingOrder)
    )
      return false;
    const c = state.context;
    if (!c) return false;
    const sections = moveRow(c.sections, id, position);
    if (sections === c.sections) {
      const section = Object.values(c.sections).find((rows) =>
        rows.some((row) => row.id === id),
      );
      update({
        notice: `Already at position ${(section?.findIndex((row) => row.id === id) ?? 0) + 1}.`,
      });
      return false;
    }
    update({ context: { ...c, sections }, orderDirty: true, notice: "" });
    orderWanted = true;
    void saveOrder();
    return true;
  }
  async function retryOrder() {
    if (
      state.preferenceBusy ||
      state.loading ||
      state.preferenceUnknown ||
      preferenceCooling() ||
      !state.orderDirty
    )
      return;
    orderWanted = true;
    await saveOrder();
  }
  async function reset() {
    if (state.loading || state.preferenceUnknown || preferenceCooling())
      return false;
    if (savingOrder) await savingOrder;
    if (
      state.loading ||
      state.preferenceBusy ||
      state.preferenceUnknown ||
      preferenceCooling()
    )
      return false;
    update({ preferenceBusy: true, preferenceError: "" });
    try {
      authorize();
      await preference(api, "reset");
      await load(true);
      return !state.error;
    } catch (e) {
      update({
        preferenceError: message(e),
        preferenceRetryUntil: preferenceRetry(e),
        preferenceUnknown:
          e instanceof NetworkError || e instanceof ProtocolError,
      });
      return false;
    } finally {
      update({ preferenceBusy: false });
    }
  }
  return {
    getSnapshot: () => state,
    subscribe: (fn: () => void) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    operations,
    actor,
    sameActor,
    load,
    editMethod,
    editParameter,
    begin,
    reviewAgain,
    dispatch,
    closeReview,
    inspect,
    stale,
    blocked,
    toggle,
    move,
    retryOrder,
    reset,
  };
}
export type LibraryController = ReturnType<typeof createLibraryController>;
const controllers = new WeakMap<ApiClient, Map<string, LibraryController>>();
export function getLibraryController(
  api: ApiClient,
  session: SessionStore,
  actor: string,
) {
  let actors = controllers.get(api);
  if (!actors) {
    actors = new Map();
    controllers.set(api, actors);
  }
  let controller = actors.get(actor);
  if (!controller) {
    controller = createLibraryController(api, session, actor);
    actors.set(actor, controller);
  }
  return controller;
}
