import {
  getAdmissionRegistry,
  spotifyResources,
  admissionUnknown,
} from "../../operations/admissions";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Link, useLocation } from "react-router";
import type {
  JobExecution,
  RoutineRow,
  RoutinesContext,
} from "../../api/contracts";
import { useServices, useSession } from "../../auth/SessionProvider";
import { useAnnouncer } from "../../a11y/Announcer";
import { Button, Select } from "../../components/ui/controls";
import { State } from "../../components/ui/States";
import { useConfirm } from "../../components/tasks/ConfirmDialog";
import { useTasks, Workspace } from "../../components/tasks/TaskPanel";
import { useNavigationBlocker } from "../../navigation/NavigationGuard";
import { ExecutionDetails } from "../../components/results/ExecutionDetails";
import { getOperationStore } from "../../operations/store";
import { originLabel } from "../activity/model";
import { createRoutineClient } from "./client";
import {
  labels,
  operationLabel,
  playlistPath,
  listDeepLink,
  safeAvailableDetail,
} from "./model";
import type { RunReview } from "./model";
import { OperationView } from "./OperationView";
import "./routines.css";
const intents = new WeakMap<
  object,
  Map<
    string,
    {
      key: string;
      scope: string;
      review: RunReview;
      unknown: boolean;
      operationId: number | null;
    }
  >
>();
function RunTask({
  row,
  actor,
  onIntentChange,
}: {
  row: RoutineRow;
  actor: string;
  onIntentChange: () => void;
}) {
  const { api } = useServices(),
    session = useSession(),
    client = useMemo(() => createRoutineClient(api), [api]),
    store = getOperationStore(api, actor),
    admissions = getAdmissionRegistry(api, actor, store),
    entries = useSyncExternalStore(store.subscribe, store.getSnapshot),
    [review, setReview] = useState<RunReview | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [operationId, setId] = useState<number | null>(null),
    locked = useRef(false),
    read = useRef<Promise<RunReview> | null>(null);
  useSyncExternalStore(admissions.subscribe, admissions.getSnapshot);
  let records = intents.get(api);
  if (!records) {
    records = new Map();
    intents.set(api, records);
  }
  const key = `${actor}:${row.schedule_id}`,
    remembered = records.get(key),
    recordRef = useRef(remembered);
  useEffect(() => {
    if (recordRef.current?.operationId) {
      setId(recordRef.current.operationId);
      return;
    }
    if (recordRef.current?.unknown) {
      setReview(recordRef.current.review);
      return;
    }
    let active = true;
    read.current ??= client.runReview(row.schedule_id);
    void read.current.then(
      (value) => {
        if (active) setReview(value);
      },
      (problem) => {
        if (active) setError(problem.message);
      },
    );
    return () => {
      active = false;
    };
  }, [client, row.schedule_id]);
  const entry = operationId ? entries.get(operationId) : undefined;
  const claim = admissions.conflict(
    spotifyResources(row.schedule.target_playlist_id, review?.resources ?? []),
    recordRef.current?.unknown ? recordRef.current.key : undefined,
  );
  const submit = async () => {
    if (
      locked.current ||
      entry?.operation ||
      session.status !== "ready" ||
      !session.session.signed_in ||
      session.session.user.id !== actor
    )
      return;
    const scope = recordRef.current?.scope ?? review?.review_binding.scope;
    if (!scope || !review) return;
    locked.current = true;
    setBusy(true);
    setError("");
    const intent = recordRef.current ?? {
      key: crypto.randomUUID(),
      scope,
      review: structuredClone(review),
      unknown: false,
      operationId: null,
    };
    recordRef.current = intent;
    records.set(key, intent);
    try {
      const reply = intent.unknown
        ? await admissions.retry(intent.key, actor)
        : await admissions.submit(
            {
              key: intent.key,
              feature: "routines",
              label: `Run routine #${row.schedule_id} · ${row.schedule.target_playlist_name ?? row.schedule.target_playlist_id}`,
              resources: spotifyResources(
                row.schedule.target_playlist_id,
                intent.review.resources,
              ),
              path: `/api/schedules/${row.schedule_id}/run`,
              body: {
                encoding: "json",
                value: { reviewed_scope: intent.scope },
              },
              expected: {
                kind: "schedule_run",
                target: row.schedule.target_playlist_id,
              },
            },
            actor,
          );
      if (reply.status !== "accepted")
        throw Error("The run response did not identify an accepted operation.");
      intent.operationId = reply.operation.id;
      intent.unknown = false;
      setId(reply.operation.id);
      store.accept(reply.operation);
    } catch (problem) {
      intent.unknown = admissionUnknown(admissions, intent.key, problem);
      const retained = admissions.getSnapshot().get(intent.key);
      if (retained?.operationId) {
        intent.operationId = retained.operationId;
        setId(retained.operationId);
      }
      setError(
        problem instanceof Error
          ? problem.message
          : "Run could not be accepted.",
      );
    } finally {
      onIntentChange();
      locked.current = false;
      setBusy(false);
    }
  };
  if (
    session.status !== "ready" ||
    !session.session.signed_in ||
    session.session.user.id !== actor
  )
    return (
      <State kind="error" title="Your account changed">
        Close this review and reopen it for the current account.
      </State>
    );
  return (
    <div className="sh-routine-stack">
      <p>
        {operationLabel(row.schedule.job_type)} · Routine #{row.schedule_id}
      </p>
      {error && (
        <State kind="error" title="The run needs attention">
          {error}
        </State>
      )}
      {!entry && review && (
        <>
          <p>
            Review the resources for this exact saved routine. This review
            changes nothing.
          </p>
          <ul>
            {review.resources.map((resource, index) => (
              <li key={index}>
                {resource.resource_name ?? "Playlist name unavailable"} ·{" "}
                {resource.access === "write" ? "May change" : "Read only"}
              </li>
            ))}
          </ul>
          {claim && (
            <p role="status">
              {claim.request.label} is awaiting reconciliation. Open its
              original review.
            </p>
          )}
          <Button busy={busy} disabled={!!claim} onClick={() => void submit()}>
            {recordRef.current?.unknown
              ? "Check the same run request"
              : "Run reviewed routine"}
          </Button>
        </>
      )}
      {recordRef.current?.unknown && (
        <p>
          The request may already be accepted. Checking uses the same request
          key; do not start another run.
        </p>
      )}
      {entry?.operation && <OperationView operation={entry.operation} />}{" "}
      {entry?.observation === "unavailable" && (
        <>
          <p>
            Observation stopped. The accepted operation may still be running.
          </p>
          <Button variant="secondary" onClick={() => store.observe(entry.id)}>
            Check operation #{entry.id}
          </Button>
        </>
      )}
      {!review && !entry && !error && <p role="status">Loading run review…</p>}
    </div>
  );
}
export function RoutinesPage() {
  const { api } = useServices(),
    session = useSession(),
    actor =
      session.status === "ready" && session.session.signed_in
        ? session.session.user.id
        : "",
    client = useMemo(() => createRoutineClient(api), [api]),
    store = getOperationStore(api, actor),
    admissions = getAdmissionRegistry(api, actor, store),
    entries = useSyncExternalStore(store.subscribe, store.getSnapshot),
    tasks = useTasks(),
    { confirm } = useConfirm(),
    announce = useAnnouncer(),
    location = useLocation();
  useSyncExternalStore(admissions.subscribe, admissions.getSnapshot);
  const [, intentsChanged] = useState(0);
  const loadedActor = useRef(actor);
  const [context, setContext] = useState<RoutinesContext | null>(null),
    [error, setError] = useState(""),
    [playlist, setPlaylist] = useState(""),
    [kind, setKind] = useState(""),
    [state, setState] = useState(""),
    [createTarget, setCreateTarget] = useState(""),
    [pending, setPending] = useState(new Set<number>()),
    [histories, setHistories] = useState<Record<number, JobExecution[] | null>>(
      {},
    ),
    [openHistory, setOpenHistory] = useState(new Set<number>()),
    [latest, setLatest] = useState<Record<string, unknown>>({});
  const read = useRef<Promise<RoutinesContext> | null>(null),
    inflight = useRef(new Set<number>()),
    heading = useRef<HTMLHeadingElement>(null);
  const unconfirmedRun = [...(intents.get(api)?.entries() ?? [])].some(
    ([key, intent]) => key.startsWith(`${actor}:`) && intent.unknown,
  );
  useNavigationBlocker(
    "routines-unconfirmed-run",
    !!actor && unconfirmedRun,
    "A run request is unconfirmed. Its original request and result are retained only in this document. Leave this page?",
  );
  async function refresh() {
    try {
      const value = await client.list();
      setContext(value);
      setCreateTarget((old) => old || value.playlists[0]?.id || "");
      setHistories({});
      setOpenHistory(new Set());
      setError("");
    } catch (problem) {
      setError(
        problem instanceof Error
          ? problem.message
          : "Routines could not be loaded.",
      );
    }
  }
  useEffect(() => {
    let active = true;
    read.current ??= client.list();
    void read.current.then(
      (value) => {
        if (active) {
          setContext(value);
          setCreateTarget(value.playlists[0]?.id ?? "");
        }
      },
      (problem) => {
        if (active) setError(problem.message);
      },
    );
    void client.latest().then(
      (reply) => {
        if (active) setLatest(reply.results);
      },
      () => {},
    );
    return () => {
      active = false;
    };
  }, [client]);
  useEffect(() => {
    document.title = "Routines · Shuffify";
    heading.current?.focus();
  }, []);
  useEffect(() => {
    if (!context) return;
    try {
      const wanted = listDeepLink(location.search, location.hash);
      if (!wanted) return;
      const row = context.routines.find(
        (value) => value.schedule_id === wanted,
      );
      if (!row) {
        setError("This exact routine is unavailable.");
        return;
      }
      document.getElementById(`routine-${wanted}-title`)?.focus();
      if (new URLSearchParams(location.search).get("view") === "history") {
        setOpenHistory((old) => new Set([...old, wanted]));
        void client.history(wanted).then(
          (values) => setHistories((old) => ({ ...old, [wanted]: values })),
          () => setHistories((old) => ({ ...old, [wanted]: null })),
        );
      }
    } catch (problem) {
      setError((problem as Error).message);
    }
  }, [client, context, location.hash, location.search]);
  const rows =
    context?.routines.filter(
      (row) =>
        (!playlist || row.schedule.target_playlist_id === playlist) &&
        (!kind || row.schedule.job_type === kind) &&
        (!state || row.state === state),
    ) ?? [];
  useEffect(() => {
    if (context)
      announce(
        `${rows.length} of ${context.routines.length} routines shown.`,
        `routine-filter-${playlist}-${kind}-${state}-${context.routines.length}`,
      );
  }, [playlist, kind, state, context, rows.length, announce]);
  async function stateAction(row: RoutineRow) {
    if (inflight.current.has(row.schedule_id)) return;
    inflight.current.add(row.schedule_id);
    setPending(new Set(inflight.current));
    try {
      const changed = await client.state(
        row.schedule_id,
        !row.schedule.is_enabled,
        row.schedule_revision,
      );
      if (changed.schedule_id !== row.schedule_id)
        throw Error("The response names a different routine.");
      setContext((old) =>
        old
          ? {
              ...old,
              routines: old.routines.map((item) =>
                item.schedule_id === row.schedule_id ? changed : item,
              ),
            }
          : old,
      );
      announce(
        `${changed.state_label} routine #${changed.schedule_id}.`,
        crypto.randomUUID(),
      );
    } catch (problem) {
      setError(
        problem instanceof Error
          ? problem.message
          : "The state change could not be confirmed. Reload routines.",
      );
    } finally {
      inflight.current.delete(row.schedule_id);
      setPending(new Set(inflight.current));
    }
  }
  async function remove(row: RoutineRow) {
    if (
      inflight.current.has(row.schedule_id) ||
      !(await confirm({
        title: `Delete routine #${row.schedule_id}?`,
        message:
          "This removes the saved routine. It does not delete the Spotify playlist.",
        confirmLabel: "Delete routine",
        danger: true,
      }))
    )
      return;
    inflight.current.add(row.schedule_id);
    setPending(new Set(inflight.current));
    try {
      await client.remove(row.schedule_id);
      setContext((old) =>
        old
          ? {
              ...old,
              routines: old.routines.filter(
                (item) => item.schedule_id !== row.schedule_id,
              ),
            }
          : old,
      );
      heading.current?.focus();
      announce(`Routine #${row.schedule_id} deleted.`, crypto.randomUUID());
    } catch (problem) {
      setError(
        problem instanceof Error
          ? problem.message
          : "Deletion could not be confirmed. Reload routines.",
      );
    } finally {
      inflight.current.delete(row.schedule_id);
      setPending(new Set(inflight.current));
    }
  }
  async function history(row: RoutineRow) {
    const id = row.schedule_id;
    setOpenHistory((old) => {
      const value = new Set(old);
      value.has(id) ? value.delete(id) : value.add(id);
      return value;
    });
    if (histories[id] !== undefined) return;
    try {
      const values = await client.history(id);
      setHistories((old) => ({ ...old, [id]: values }));
    } catch {
      setHistories((old) => ({ ...old, [id]: null }));
    }
  }
  if (actor !== loadedActor.current)
    return (
      <State kind="error" title="Your account changed">
        <a href="/routines">Reload routines for your current account</a>
      </State>
    );
  return (
    <div className="sh-container sh-routines-page">
      <header className="sh-routine-header">
        <div>
          <p className="sh-eyebrow">Your listening, on a rhythm</p>
          <h1 className="sh-heading" tabIndex={-1} ref={heading}>
            Routines
          </h1>
          <p className="sh-text-muted">
            Exact routines. Shared playlist settings. Every run accounted for.
          </p>
        </div>
        <Button variant="secondary" onClick={() => void refresh()}>
          Reload routines
        </Button>
      </header>
      <Workspace>
        {error && (
          <State kind="error" title="Routines need attention">
            {error}
          </State>
        )}
        {!context && !error ? (
          <State kind="loading" title="Loading routines" />
        ) : (
          context && (
            <>
              <section
                className="sh-routine-create"
                aria-label="Create a routine"
              >
                <div>
                  <label htmlFor="routine-new-playlist">Playlist</label>
                  <Select
                    id="routine-new-playlist"
                    value={createTarget}
                    onChange={(event) =>
                      setCreateTarget(event.currentTarget.value)
                    }
                  >
                    {context.playlists.map((value) => (
                      <option value={value.id} key={value.id}>
                        {value.name}
                      </option>
                    ))}
                  </Select>
                </div>
                {createTarget ? (
                  <Link
                    className="sh-button"
                    to={`${playlistPath(createTarget)}/routines/new?origin=routines`}
                  >
                    Create routine
                  </Link>
                ) : (
                  <Link className="sh-button-secondary" to="/library">
                    Choose a playlist in Library
                  </Link>
                )}
              </section>
              <div className="sh-routine-filters">
                <div>
                  <label htmlFor="routine-playlist-filter">Playlist</label>
                  <Select
                    id="routine-playlist-filter"
                    value={playlist}
                    onChange={(event) => setPlaylist(event.currentTarget.value)}
                  >
                    <option value="">All playlists</option>
                    {context.playlists.map((value) => (
                      <option value={value.id} key={value.id}>
                        {value.name}
                      </option>
                    ))}
                  </Select>
                </div>
                <div>
                  <label htmlFor="routine-type-filter">Operation</label>
                  <Select
                    id="routine-type-filter"
                    value={kind}
                    onChange={(event) => setKind(event.currentTarget.value)}
                  >
                    <option value="">All operations</option>
                    {Object.entries(labels).map(([value, label]) => (
                      <option value={value} key={value}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </div>
                <div>
                  <label htmlFor="routine-state-filter">State</label>
                  <Select
                    id="routine-state-filter"
                    value={state}
                    onChange={(event) => setState(event.currentTarget.value)}
                  >
                    <option value="">All states</option>
                    <option value="active">Active</option>
                    <option value="paused">Paused</option>
                    <option value="needs_attention">Needs attention</option>
                  </Select>
                </div>
              </div>
              <p className="sh-caption sh-text-muted">
                {rows.length} of {context.routines.length} routines shown
              </p>
              {!rows.length && (
                <State
                  kind="empty"
                  title={
                    context.routines.length
                      ? "No routines match these filters"
                      : "No routines yet"
                  }
                >
                  Choose a playlist to create your first routine.
                </State>
              )}
              <div className="sh-routine-list">
                {rows.map((row) => {
                  const current = [...entries.values()]
                      .filter((value) =>
                        value.operation?.targets.includes(
                          row.schedule.target_playlist_id,
                        ),
                      )
                      .sort((a, b) => b.id - a.id)[0],
                    blocked =
                      current?.operation &&
                      [
                        "queued",
                        "running",
                        "compensating",
                        "needs_attention",
                      ].includes(current.operation.state),
                    sharedClaim = admissions.conflict(
                      spotifyResources(row.schedule.target_playlist_id),
                      intents.get(api)?.get(`${actor}:${row.schedule_id}`)
                        ?.unknown
                        ? intents.get(api)?.get(`${actor}:${row.schedule_id}`)
                            ?.key
                        : undefined,
                    ),
                    ownUnknown = intents
                      .get(api)
                      ?.get(`${actor}:${row.schedule_id}`)?.unknown,
                    uncertain = context.routines.some(
                      (candidate) =>
                        candidate.schedule.target_playlist_id ===
                          row.schedule.target_playlist_id &&
                        intents
                          .get(api)
                          ?.get(`${actor}:${candidate.schedule_id}`)?.unknown,
                    ),
                    latestRecord = latest[String(row.schedule_id)],
                    description = `routine-${row.schedule_id}-title`;
                  return (
                    <article
                      className="sh-routine-card"
                      id={`routine-${row.schedule_id}`}
                      key={row.schedule_id}
                    >
                      <div className="sh-routine-card-heading">
                        <h2
                          className="sh-routine-title"
                          id={description}
                          tabIndex={-1}
                        >
                          {row.schedule.target_playlist_name ??
                            "Playlist name unavailable"}
                        </h2>
                        <span className="sh-badge">{row.state_label}</span>
                      </div>
                      <p>
                        {operationLabel(row.definition.job_type)} · Routine #
                        {row.schedule_id}
                      </p>
                      <p className="sh-text-muted">
                        {row.timing.text}
                        {row.timing.effective_timezone
                          ? ` · ${row.timing.effective_timezone}`
                          : ""}
                      </p>
                      {row.timing.legacy && (
                        <p>
                          Legacy timing uses the scheduler timezone. Editing
                          options preserves it until you change timing.
                        </p>
                      )}
                      <p className="sh-caption">
                        Registered next run:{" "}
                        {row.registration.next_run_at
                          ? new Date(
                              row.registration.next_run_at,
                            ).toLocaleString()
                          : "Unavailable"}
                      </p>
                      {latestRecord === null ? (
                        <p>Never run.</p>
                      ) : latestRecord && typeof latestRecord === "object" ? (
                        <p>
                          Last run:{" "}
                          {"status" in latestRecord
                            ? String(latestRecord.status)
                            : "Status unavailable"}
                        </p>
                      ) : (
                        <p>Latest result unavailable.</p>
                      )}
                      {uncertain && (
                        <p className="sh-routine-warning">
                          A run request for this playlist is unconfirmed. Check
                          that exact request before editing, deleting or
                          starting another run.
                        </p>
                      )}
                      {blocked && (
                        <p className="sh-routine-warning">
                          Operation #{current.id}{" "}
                          {current.operation?.state.replaceAll("_", " ")}. Run,
                          edit and delete require its result to be resolved.
                          Pause and Resume remain available.
                        </p>
                      )}
                      <div className="sh-routine-actions">
                        {!blocked && !uncertain ? (
                          <Link
                            aria-describedby={description}
                            className="sh-button-secondary"
                            to={`${playlistPath(row.schedule.target_playlist_id)}/routines/new?schedule_id=${row.schedule_id}&origin=routines`}
                          >
                            Edit this routine
                          </Link>
                        ) : (
                          <Button
                            variant="secondary"
                            disabled
                            aria-describedby={description}
                          >
                            Edit this routine
                          </Button>
                        )}
                        <Button
                          variant="secondary"
                          aria-describedby={description}
                          busy={pending.has(row.schedule_id)}
                          onClick={() => void stateAction(row)}
                        >
                          {row.schedule.is_enabled ? "Pause" : "Resume"}
                        </Button>
                        {sharedClaim && (
                          <p role="status">
                            {sharedClaim.request.label} is awaiting
                            reconciliation. Open its original review.
                          </p>
                        )}
                        <Button
                          variant="secondary"
                          aria-describedby={description}
                          disabled={
                            !!blocked ||
                            (!!uncertain && !ownUnknown) ||
                            !!sharedClaim
                          }
                          onClick={(event) => {
                            const remembered = intents
                              .get(api)
                              ?.get(`${actor}:${row.schedule_id}`);
                            if (
                              remembered?.operationId &&
                              entries.get(remembered.operationId)
                                ?.observation === "settled"
                            )
                              intents
                                .get(api)
                                ?.delete(`${actor}:${row.schedule_id}`);
                            void tasks.openTask({
                              id: `run-${row.schedule_id}`,
                              title: `Run routine #${row.schedule_id}`,
                              trigger: event.currentTarget,
                              fallback: heading.current,
                              content: (
                                <RunTask
                                  row={row}
                                  actor={actor}
                                  onIntentChange={() =>
                                    intentsChanged((value) => value + 1)
                                  }
                                />
                              ),
                            });
                          }}
                        >
                          {ownUnknown ? "Check run request" : "Run now"}
                        </Button>
                        <Button
                          variant="secondary"
                          aria-describedby={description}
                          aria-expanded={openHistory.has(row.schedule_id)}
                          aria-controls={`routine-${row.schedule_id}-history`}
                          onClick={() => void history(row)}
                        >
                          History
                        </Button>
                        <Button
                          variant="danger"
                          aria-describedby={description}
                          disabled={!!blocked || !!uncertain}
                          busy={pending.has(row.schedule_id)}
                          onClick={() => void remove(row)}
                        >
                          Delete routine
                        </Button>
                      </div>
                      {current?.operation && (
                        <OperationView operation={current.operation} />
                      )}
                      <div
                        id={`routine-${row.schedule_id}-history`}
                        hidden={!openHistory.has(row.schedule_id)}
                      >
                        {histories[row.schedule_id] === undefined ? (
                          <p role="status">Loading recent history…</p>
                        ) : histories[row.schedule_id] === null ? (
                          <p>
                            History unavailable. Close and reload routines to
                            try again.
                          </p>
                        ) : histories[row.schedule_id]?.length === 0 ? (
                          <p>No executions yet.</p>
                        ) : (
                          histories[row.schedule_id]?.map((run) => {
                            const detail = safeAvailableDetail(
                                run.result_detail,
                                run.id,
                              ),
                              origin =
                                "triggered_by" in run ? run.triggered_by : null;
                            return (
                              <section
                                className="sh-routine-history-row"
                                key={run.id}
                              >
                                <h3>
                                  Run #{run.id} · {run.status}
                                </h3>
                                <p>
                                  {originLabel(
                                    origin === "manual" ||
                                      origin === "scheduler"
                                      ? origin
                                      : null,
                                  )}
                                </p>
                                {detail ? (
                                  <ExecutionDetails detail={detail} />
                                ) : (
                                  <p>
                                    Step details unavailable. No verified result
                                    is inferred.
                                  </p>
                                )}
                              </section>
                            );
                          })
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            </>
          )
        )}
      </Workspace>
    </div>
  );
}
