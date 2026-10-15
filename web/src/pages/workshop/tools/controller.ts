import type { Algorithm, WorkshopTrack } from "../../../api/contracts.ts";
import {
  ApiRequestError,
  ConflictError,
  NetworkError,
  ProtocolError,
} from "../../../api/errors.ts";
import type { WorkshopController } from "../core/controller.ts";
import { freeze } from "../core/occurrences.ts";
import type { DeepReadonly } from "../core/types.ts";
import { createToolsClient } from "./client.ts";
import type { ToolsClient } from "./client.ts";
import {
  activeLockMap,
  externalIntent,
  parameterDefaults,
  parameterErrors,
} from "./model.ts";
import type { ExternalResult, Snapshot, TrackLock } from "./types.ts";
interface Action {
  busy: boolean;
  error: string;
  message: string;
  retryUntil: number;
  operationId: number | null;
}
interface ToolsState {
  algorithm: Action & {
    selected: string;
    values: Record<string, string>;
    errors: Record<string, string>;
  };
  locks: Action & {
    phase: "idle" | "loading" | "ready" | "error" | "installing";
    items: TrackLock[];
    unknown: boolean;
  };
  catalog: Action & {
    query: string;
    loadedQuery: string;
    tracks: WorkshopTrack[];
    offset: number;
    more: boolean;
  };
  external: Action & { input: string; result: ExternalResult | null };
  snapshots: Action & {
    loaded: boolean;
    items: Snapshot[];
    form: boolean;
    description: string;
    createUnknown: boolean;
    deleteUnknown: number | null;
    confirming: number | null;
    details: Record<
      number,
      { busy: boolean; open: boolean; error: string; value: Snapshot | null }
    >;
  };
}
const action = (): Action => ({
  busy: false,
  error: "",
  message: "",
  retryUntil: 0,
  operationId: null,
});
const message = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "This request could not be confirmed.";
const uncertain = (error: unknown) =>
  error instanceof NetworkError ||
  error instanceof ProtocolError ||
  (error instanceof ApiRequestError && error.status >= 500);
export function createToolsController(
  core: WorkshopController,
  client: ToolsClient = createToolsClient(core.api, core.playlistId),
) {
  let state: DeepReadonly<ToolsState> = freeze({
    algorithm: { ...action(), selected: "", values: {}, errors: {} },
    locks: { ...action(), phase: "idle", items: [], unknown: false },
    catalog: {
      ...action(),
      query: "",
      loadedQuery: "",
      tracks: [],
      offset: 0,
      more: false,
    },
    external: { ...action(), input: "", result: null },
    snapshots: {
      ...action(),
      loaded: false,
      items: [],
      form: false,
      description: "",
      createUnknown: false,
      deleteUnknown: null,
      confirming: null,
      details: {},
    },
  });
  const listeners = new Set<() => void>();
  const update = <K extends keyof ToolsState>(
    key: K,
    patch: Partial<ToolsState[K]>,
  ) => {
    state = freeze({
      ...state,
      [key]: {
        ...state[key],
        ...("error" in patch && patch.error === ""
          ? { operationId: null }
          : {}),
        ...patch,
      },
    }) as DeepReadonly<ToolsState>;
    listeners.forEach((fn) => fn());
  };
  const canRead = () => core.permission("snapshot-read").allowed;
  const error = (key: keyof ToolsState, e: unknown) => {
    if (e instanceof ConflictError && e.error.operation_id)
      core.operations.observe(e.error.operation_id);
    update(key, {
      error: message(e),
      message: "",
      operationId:
        e instanceof ConflictError ? (e.error.operation_id ?? null) : null,
      retryUntil:
        e instanceof ApiRequestError && e.retryAfterMs !== null
          ? Date.now() + e.retryAfterMs
          : 0,
    } as Partial<ToolsState[typeof key]>);
  };
  const waiting = (key: keyof ToolsState) => Date.now() < state[key].retryUntil;
  let algorithmRevision = 0,
    previewTicket = 0,
    queryTicket = 0,
    externalTicket = 0,
    listTicket = 0;
  let lockPending: Promise<void> | null = null,
    releaseLocks: (() => void) | null = null,
    confirmedLocks: TrackLock[] | null = null,
    installing = false,
    initialized = false,
    claimingLocks = false;
  let deferredList: Snapshot[] | null = null;
  const lockClaim = () => {
    if (releaseLocks || claimingLocks) return;
    claimingLocks = true;
    releaseLocks = core.claim({
      owner: "workshop-tools-locks",
      resource: `tracks:${core.playlistId}`,
      access: "write",
      reason: "Confirm the active track locks before changing this draft.",
    });
    claimingLocks = false;
  };
  const release = () => {
    const fn = releaseLocks;
    releaseLocks = null;
    fn?.();
  };
  function installLocks() {
    if (!confirmedLocks || installing || !canRead()) return;
    installing = true;
    const items = confirmedLocks;
    release();
    if (core.setLocks(activeLockMap(items))) {
      confirmedLocks = null;
      update("locks", {
        items,
        phase: "ready",
        unknown: false,
        busy: false,
        error: "",
        message: "Track locks are up to date.",
      });
    } else {
      lockClaim();
      update("locks", {
        phase: "installing",
        busy: false,
        message:
          "Lock confirmation is retained until the other target change settles.",
      });
    }
    installing = false;
  }
  core.subscribe(() => {
    configure();
    if (confirmedLocks) installLocks();
    if (
      initialized &&
      !claimingLocks &&
      state.locks.phase === "idle" &&
      core.getSnapshot().phase === "ready"
    )
      void loadLocks();
  });
  function configure() {
    if (!state.algorithm.selected) {
      const first = core.getSnapshot().context?.algorithms[0];
      if (first)
        update("algorithm", {
          selected: first.class_name,
          values: parameterDefaults(first as Algorithm),
        });
    }
  }
  async function loadLocks() {
    if (lockPending || !canRead() || waiting("locks"))
      return lockPending ?? Promise.resolve();
    lockClaim();
    update("locks", { busy: true, phase: "loading", error: "", message: "" });
    lockPending = (async () => {
      try {
        const items = await client.locks();
        if (!canRead()) return;
        confirmedLocks = items;
        installLocks();
      } catch (e) {
        error("locks", e);
        update("locks", { phase: "error", busy: false });
      } finally {
        lockPending = null;
      }
    })();
    return lockPending;
  }
  async function initialize() {
    initialized = true;
    lockClaim();
    await core.load();
    configure();
    if (lockPending) await lockPending;
    else if (state.locks.phase === "idle") await loadLocks();
  }
  function selectAlgorithm(name: string) {
    const a = core
      .getSnapshot()
      .context?.algorithms.find((a) => a.class_name === name);
    if (!a) return;
    algorithmRevision++;
    core.configurationChanged();
    update("algorithm", {
      selected: name,
      values: parameterDefaults(a as Algorithm),
      errors: {},
      error: "",
      message: "",
    });
  }
  function setParameter(name: string, value: string) {
    algorithmRevision++;
    core.configurationChanged();
    update("algorithm", {
      values: { ...state.algorithm.values, [name]: value },
      errors: {},
      error: "",
      message: "",
    });
  }
  async function preview() {
    if (
      state.algorithm.busy ||
      waiting("algorithm") ||
      !core.getSnapshot().working.length ||
      !core.permission("preview").allowed
    )
      return false;
    const a = core
      .getSnapshot()
      .context?.algorithms.find(
        (a) => a.class_name === state.algorithm.selected,
      );
    if (!a) return false;
    const errors = parameterErrors(a as Algorithm, state.algorithm.values);
    if (Object.keys(errors).length) {
      update("algorithm", { errors });
      return false;
    }
    const input = core.getPreviewInput(),
      ticket = ++previewTicket,
      revision = algorithmRevision;
    const body = {
      algorithm: a.class_name,
      tracks: core.getSnapshot().working.map((o) => o.track),
      ...state.algorithm.values,
      ...(Object.keys(input.locked_positions).length
        ? { locked_positions: input.locked_positions }
        : {}),
    };
    update("algorithm", { busy: true, error: "", message: "", errors: {} });
    try {
      const result = await client.preview(body);
      if (ticket !== previewTicket) return false;
      if (
        revision !== algorithmRevision ||
        !core.applyPreview(input, result.uris)
      ) {
        update("algorithm", {
          message:
            "The draft, locks or options changed. Preview again to use the current version.",
        });
        return false;
      }
      update("algorithm", {
        message: `${result.algorithm} preview applied to this local draft. Review and save when ready.`,
      });
      return true;
    } catch (e) {
      error("algorithm", e);
      return false;
    } finally {
      if (ticket === previewTicket) update("algorithm", { busy: false });
    }
  }
  async function toggleLock(key: string) {
    if (
      state.locks.busy ||
      state.locks.unknown ||
      state.locks.phase !== "ready" ||
      waiting("locks") ||
      !core.permission("locks").allowed
    )
      return;
    const rows = core.getSnapshot().working,
      index = rows.findIndex((o) => o.key === key),
      row = rows[index];
    if (!row) return;
    lockClaim();
    update("locks", { busy: true, error: "", message: "" });
    try {
      const value = await client.toggleLock(row.uri, index);
      if (!canRead()) return;
      const items = state.locks.items.filter(
        (l) => l.track_uri !== row.uri,
      ) as TrackLock[];
      if (value) items.push(value);
      confirmedLocks = items;
      installLocks();
    } catch (e) {
      error("locks", e);
      if (uncertain(e))
        update("locks", {
          unknown: true,
          message:
            "The lock change may have completed. Refresh locks to confirm its current tier.",
        });
      else release();
    } finally {
      update("locks", { busy: false });
    }
  }
  async function unlockAll() {
    if (
      state.locks.busy ||
      state.locks.unknown ||
      state.locks.phase !== "ready" ||
      waiting("locks") ||
      !core.permission("locks").allowed
    )
      return;
    lockClaim();
    update("locks", { busy: true, error: "", message: "" });
    try {
      await client.unlockAll();
      if (!canRead()) return;
      confirmedLocks = [];
      installLocks();
    } catch (e) {
      error("locks", e);
      if (uncertain(e))
        update("locks", {
          unknown: true,
          message: "Unlocking may have completed. Refresh locks to confirm.",
        });
      else release();
    } finally {
      update("locks", { busy: false });
    }
  }
  function setQuery(query: string) {
    queryTicket++;
    update("catalog", { query, busy: false, error: "", message: "" });
  }
  async function search(more = false) {
    if (!canRead() || waiting("catalog") || (more && state.catalog.busy))
      return;
    const query = state.catalog.query.trim(),
      offset = more ? state.catalog.offset : 0;
    if (!query || query.length > 200) {
      update("catalog", { error: "Enter a search of at most 200 characters." });
      return;
    }
    if (more && query !== state.catalog.loadedQuery) return;
    const ticket = ++queryTicket;
    update("catalog", { busy: true, error: "", message: "" });
    try {
      const result = await client.search(query, offset);
      if (ticket !== queryTicket || !canRead()) return;
      const previous = more ? state.catalog.tracks : [];
      update("catalog", {
        tracks: [...previous, ...result.tracks] as WorkshopTrack[],
        loadedQuery: query,
        offset: offset + result.limit,
        more: result.tracks.length === result.limit,
        message: result.tracks.length ? "" : "No tracks found.",
      });
    } catch (e) {
      if (ticket === queryTicket) error("catalog", e);
    } finally {
      if (ticket === queryTicket) update("catalog", { busy: false });
    }
  }
  function allTracks(): readonly DeepReadonly<WorkshopTrack>[] {
    return [
      ...core.getSnapshot().baseline.map((o) => o.track),
      ...core.getSnapshot().working.map((o) => o.track),
      ...state.catalog.tracks,
      ...(state.external.result?.mode === "tracks"
        ? state.external.result.tracks
        : []),
    ];
  }
  function addTrack(uri: string, origin = "catalog") {
    const t = allTracks().find((t) => t.uri === uri);
    if (!t || core.getSnapshot().working.some((o) => o.uri === uri))
      return false;
    return core.appendTracks([t], { mode: "append-missing", origin });
  }
  function setExternalInput(input: string) {
    externalTicket++;
    update("external", { input, busy: false, error: "", message: "" });
  }
  async function loadExternal(id?: string) {
    if (!canRead() || waiting("external")) return;
    let intent;
    try {
      intent = id ? { url: id } : externalIntent(state.external.input);
    } catch (e) {
      error("external", e);
      return;
    }
    const ticket = ++externalTicket;
    update("external", { busy: true, error: "", message: "" });
    try {
      const result = await client.external(intent);
      if (ticket !== externalTicket || !canRead()) return;
      update("external", {
        result,
        message:
          result.mode === "tracks"
            ? "Source tracks loaded. Add missing tracks to this draft; the source playlist stays unchanged."
            : "",
      });
    } catch (e) {
      if (ticket === externalTicket) error("external", e);
    } finally {
      if (ticket === externalTicket) update("external", { busy: false });
    }
  }
  function appendExternal() {
    const result = state.external.result;
    if (result?.mode !== "tracks") return false;
    const ok = core.appendTracks(result.tracks, {
      mode: "append-missing",
      origin: "external",
    });
    if (ok)
      update("external", {
        message:
          "Missing source tracks added to this draft. The source playlist stays unchanged.",
      });
    return ok;
  }
  function applyList(items: Snapshot[]) {
    if (state.snapshots.form || state.snapshots.confirming !== null)
      deferredList = items;
    else update("snapshots", { items, loaded: true });
  }
  function flushList() {
    if (
      deferredList &&
      !state.snapshots.form &&
      state.snapshots.confirming === null
    ) {
      const items = deferredList;
      deferredList = null;
      update("snapshots", { items, loaded: true });
    }
  }
  async function loadSnapshots() {
    if (!canRead() || waiting("snapshots")) return;
    const ticket = ++listTicket;
    update("snapshots", { busy: true, error: "", message: "" });
    try {
      const items = await client.snapshots();
      if (ticket !== listTicket || !canRead()) return;
      applyList(items);
      if (state.snapshots.deleteUnknown !== null)
        update("snapshots", {
          deleteUnknown: null,
          message: items.some((s) => s.id === state.snapshots.deleteUnknown)
            ? "This snapshot is still listed. You may explicitly choose Delete again."
            : "The snapshot is no longer in this timeline.",
        });
    } catch (e) {
      if (ticket === listTicket) error("snapshots", e);
    } finally {
      if (ticket === listTicket) update("snapshots", { busy: false });
    }
  }
  function showSnapshotForm(form: boolean) {
    update("snapshots", { form, error: "", message: "" });
    if (!form) flushList();
  }
  function setSnapshotDescription(description: string) {
    update("snapshots", { description });
  }
  async function createSnapshot() {
    if (
      state.snapshots.busy ||
      state.snapshots.createUnknown ||
      waiting("snapshots") ||
      !core.permission("snapshot-create").allowed
    )
      return;
    const context = core.getSnapshot().context;
    if (!context) return;
    update("snapshots", { busy: true, error: "", message: "" });
    try {
      const s = await client.createSnapshot(
        context.playlist.name,
        core.getSnapshot().working.map((o) => o.uri),
        state.snapshots.description,
      );
      if (!canRead()) return;
      update("snapshots", {
        form: false,
        description: "",
        message: "Snapshot saved. Spotify is unchanged.",
      });
      const items = [
        s,
        ...state.snapshots.items.filter((x) => x.id !== s.id),
      ] as Snapshot[];
      deferredList = null;
      update("snapshots", { items, loaded: true });
    } catch (e) {
      error("snapshots", e);
      if (uncertain(e))
        update("snapshots", {
          createUnknown: true,
          message:
            "This snapshot may have been saved. Refresh the timeline to inspect it, or explicitly choose to create another snapshot.",
        });
    } finally {
      update("snapshots", { busy: false });
    }
  }
  function allowAnotherSnapshot() {
    if (!core.permission("snapshot-create").allowed) return;
    update("snapshots", {
      createUnknown: false,
      message: "A new snapshot will be created only when you submit again.",
      error: "",
    });
  }
  async function viewSnapshot(id: number) {
    if (!canRead()) return;
    const existing = state.snapshots.details[id];
    if (existing?.busy) return;
    if (existing?.value) {
      update("snapshots", {
        details: {
          ...state.snapshots.details,
          [id]: { ...existing, open: true },
        } as ToolsState["snapshots"]["details"],
      });
      return;
    }
    update("snapshots", {
      details: {
        ...state.snapshots.details,
        [id]: { busy: true, open: true, error: "", value: null },
      } as ToolsState["snapshots"]["details"],
    });
    try {
      const value = await client.snapshot(id);
      if (!canRead()) return;
      update("snapshots", {
        details: {
          ...state.snapshots.details,
          [id]: { busy: false, open: true, error: "", value },
        } as ToolsState["snapshots"]["details"],
      });
    } catch (e) {
      update("snapshots", {
        details: {
          ...state.snapshots.details,
          [id]: { busy: false, open: true, error: message(e), value: null },
        } as ToolsState["snapshots"]["details"],
      });
    }
  }
  function closeSnapshot(id: number) {
    const d = state.snapshots.details[id];
    if (d)
      update("snapshots", {
        details: {
          ...state.snapshots.details,
          [id]: { ...d, open: false },
        } as ToolsState["snapshots"]["details"],
      });
  }
  function confirmingSnapshot(id: number | null) {
    update("snapshots", { confirming: id });
    if (id === null) flushList();
  }
  async function deleteSnapshot(id: number) {
    if (
      state.snapshots.busy ||
      state.snapshots.deleteUnknown === id ||
      waiting("snapshots") ||
      !core.permission("snapshot-delete", String(id)).allowed
    )
      return false;
    update("snapshots", { busy: true, error: "", message: "" });
    try {
      await client.deleteSnapshot(id);
      if (!canRead()) return false;
      deferredList = null;
      update("snapshots", {
        items: state.snapshots.items.filter((s) => s.id !== id) as Snapshot[],
        message: "Snapshot deleted. Spotify is unchanged.",
      });
      return true;
    } catch (e) {
      error("snapshots", e);
      if (uncertain(e))
        update("snapshots", {
          deleteUnknown: id,
          message:
            "The deletion could not be confirmed. Refresh the timeline before doing anything else with this snapshot.",
        });
      return false;
    } finally {
      update("snapshots", { busy: false });
    }
  }
  return {
    core,
    subscribe: (fn: () => void) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    getSnapshot: () => state,
    initialize,
    loadLocks,
    selectAlgorithm,
    setParameter,
    preview,
    toggleLock,
    unlockAll,
    setQuery,
    search,
    addTrack,
    setExternalInput,
    loadExternal,
    appendExternal,
    loadSnapshots,
    showSnapshotForm,
    setSnapshotDescription,
    createSnapshot,
    allowAnotherSnapshot,
    viewSnapshot,
    closeSnapshot,
    confirmingSnapshot,
    deleteSnapshot,
    knownTrack: (uri: string) => allTracks().find((t) => t.uri === uri),
  };
}
export type ToolsController = ReturnType<typeof createToolsController>;
const controllers = new WeakMap<WorkshopController, ToolsController>();
export function getToolsController(core: WorkshopController) {
  let c = controllers.get(core);
  if (!c) {
    c = createToolsController(core);
    controllers.set(core, c);
  }
  return c;
}
