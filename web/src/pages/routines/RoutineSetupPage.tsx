import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import type { JobType, RoutineRow } from "../../api/contracts";
import { useServices, useSession } from "../../auth/SessionProvider";
import { useAnnouncer } from "../../a11y/Announcer";
import { ROUTINE_CONTINUATION_KEY as CONTINUATION_KEY } from "../../auth/browser-storage";
import {
  Button,
  Field,
  Input,
  Select,
  Toggle,
} from "../../components/ui/controls";
import { State } from "../../components/ui/States";
import { useNavigationBlocker } from "../../navigation/NavigationGuard";
import { unconfirmedMutation } from "./model";
import { createRoutineClient } from "./client";
import {
  activationBody,
  draftFromRow,
  labels,
  operationLabel,
  makeContinuation,
  parseNavigation,
  playlistPath,
  readContinuation,
} from "./model";
import type {
  ActivationReply,
  GuidedContext,
  ReviewRequest,
  RoutineDraft,
  RoutineReview,
} from "./model";
import "./routines.css";
export { CONTINUATION_KEY };
const defaultDraft: RoutineDraft = {
  job_type: "shuffle",
  algorithm_name: "BasicShuffle",
  algorithm_params: {},
  timing: { kind: "interval", interval: "daily", timezone: "UTC" },
};
export function RoutineSetupPage() {
  const { playlistId = "" } = useParams(),
    location = useLocation(),
    navigate = useNavigate(),
    { api } = useServices(),
    session = useSession(),
    announce = useAnnouncer(),
    client = useMemo(() => createRoutineClient(api), [api]);
  const actor =
    session.status === "ready" && session.session.signed_in
      ? session.session.user.id
      : "";
  const loadedActor = useRef(actor);
  const navigation = useMemo(() => {
    try {
      return parseNavigation(location.search);
    } catch {
      return null;
    }
  }, [location.search]);
  const [context, setContext] = useState<GuidedContext | null>(null),
    [draft, setDraft] = useState<RoutineDraft>(structuredClone(defaultDraft)),
    [selected, setSelected] = useState<number | null>(
      navigation?.scheduleId ?? null,
    ),
    [revision, setRevision] = useState<string | null>(null),
    [createAnother, setCreateAnother] = useState(false),
    [timingDirty, setTimingDirty] = useState(false),
    [dirty, setDirty] = useState(false),
    [review, setReview] = useState<RoutineReview | null>(null),
    [reviewRequest, setReviewRequest] = useState<ReviewRequest | null>(null),
    [accepted, setAccepted] = useState<ActivationReply | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [unknown, setUnknown] = useState(false),
    [stale, setStale] = useState(false),
    [handoff, setHandoff] = useState("");
  const request = useRef<ReturnType<typeof activationBody> | null>(null),
    inflight = useRef(false),
    read = useRef<Promise<GuidedContext> | null>(null),
    heading = useRef<HTMLHeadingElement>(null),
    requestKey = useRef(crypto.randomUUID());
  useNavigationBlocker(
    `routine-${playlistId}`,
    dirty,
    "Your unsaved routine changes will be lost.",
  );
  const query = navigation
    ? `?origin=${navigation.origin}${navigation.scheduleId ? `&schedule_id=${navigation.scheduleId}` : ""}`
    : "";
  const choose = (row: RoutineRow) => {
    setSelected(row.schedule_id);
    setRevision(row.schedule_revision);
    setDraft(draftFromRow(row));
    setCreateAnother(false);
    setTimingDirty(false);
    setReview(null);
    setStale(false);
    setDirty(false);
    request.current = null;
    requestKey.current = crypto.randomUUID();
  };
  useEffect(() => {
    if (!navigation || !playlistId) return;
    let active = true;
    read.current ??= client.guided(playlistId, query);
    void read.current.then(
      (value) => {
        if (!active) return;
        setContext(value);
        let continuation = null;
        try {
          continuation = readContinuation(
            sessionStorage.getItem(CONTINUATION_KEY),
            actor,
            playlistId,
          );
        } catch {
          /* Storage is optional. */
        }
        const wanted = continuation?.scheduleId ?? navigation.scheduleId,
          row = wanted
            ? value.routines.find((item) => item.schedule_id === wanted)
            : undefined;
        if (wanted && !row) {
          setError("This exact routine is unavailable.");
          return;
        }
        if (continuation) {
          setSelected(continuation.scheduleId);
          setRevision(continuation.revision);
          setDraft(continuation.draft);
          setDirty(true);
          setStale(!!row && row.schedule_revision !== continuation.revision);
          setCreateAnother(continuation.scheduleId === null);
          setError(
            "Playlist settings may have changed. Review the routine again before saving.",
          );
          try {
            sessionStorage.removeItem(CONTINUATION_KEY);
          } catch {
            /* Optional storage. */
          }
        } else if (row) choose(row);
      },
      (problem) => {
        if (active) setError(problem.message);
      },
    );
    return () => {
      active = false;
    };
  }, [client, playlistId, query, actor, navigation]);
  useEffect(() => {
    document.title = selected
      ? `Edit routine #${selected} · Shuffify`
      : "Create routine · Shuffify";
    heading.current?.focus();
  }, [selected]);
  useEffect(() => {
    if (!handoff) return;
    void navigate(handoff);
  }, [handoff, navigate]);
  function edit(next: Partial<RoutineDraft>) {
    setDraft((old) => ({ ...old, ...next }));
    setDirty(true);
    setReview(null);
    setReviewRequest(null);
    request.current = null;
    requestKey.current = crypto.randomUUID();
    setError("");
  }
  function parameter(name: string, value: string | number) {
    edit({ algorithm_params: { ...draft.algorithm_params, [name]: value } });
  }
  async function doReview() {
    if (inflight.current || !context || stale || unknown) return;
    inflight.current = true;
    setBusy(true);
    setError("");
    const definition: Record<string, unknown> = {
      job_type: draft.job_type,
      algorithm_name: ["shuffle", "raid_and_shuffle"].includes(draft.job_type)
        ? draft.algorithm_name
        : null,
      algorithm_params: structuredClone(draft.algorithm_params),
    };
    if (!selected || timingDirty)
      definition.timing = Object.fromEntries(
        Object.entries(draft.timing).filter(([key]) =>
          [
            "kind",
            "timezone",
            ...(draft.timing.kind === "interval"
              ? ["interval"]
              : draft.timing.kind === "advanced"
                ? ["expression"]
                : draft.timing.kind === "weekly"
                  ? ["time", "weekday"]
                  : ["time"]),
          ].includes(key),
        ),
      );
    const body: ReviewRequest = {
      request_key: requestKey.current,
      definition,
      create_another: createAnother,
      ...(selected
        ? { schedule_id: selected, expected_revision: revision }
        : {}),
    };
    try {
      const answer = await client.review(playlistId, body);
      const fresh = await client.guided(playlistId, query);
      setContext(fresh);
      if (answer.dependency_revision !== fresh.dependency_revision)
        throw Error(
          "Playlist settings changed after review. Check connections and review again.",
        );
      if (
        selected &&
        fresh.routines.find((row) => row.schedule_id === selected)
          ?.schedule_revision !== revision
      ) {
        setStale(true);
        throw Error(
          "This exact routine changed. Use its current definition before reviewing.",
        );
      }
      setReview(answer);
      setReviewRequest(structuredClone(body));
      request.current = activationBody(body, answer);
      announce("Routine reviewed. Nothing is saved yet.", crypto.randomUUID());
    } catch (problem) {
      setError(
        problem instanceof Error
          ? problem.message
          : "Review could not be completed.",
      );
      setReview(null);
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }
  async function activate() {
    if (
      inflight.current ||
      !request.current ||
      session.status !== "ready" ||
      !session.session.signed_in ||
      session.session.user.id !== loadedActor.current
    )
      return;
    inflight.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await client.activate(playlistId, request.current);
      setAccepted(result);
      setDirty(false);
      setUnknown(false);
      announce(`Routine #${result.schedule_id} saved.`, crypto.randomUUID());
    } catch (problem) {
      const unresolved = unconfirmedMutation(problem);
      setUnknown(unresolved);
      if (!unresolved) {
        setReview(null);
        setReviewRequest(null);
        request.current = null;
        requestKey.current = crypto.randomUUID();
      }
      setError(
        problem instanceof Error
          ? problem.message
          : "Activation could not be confirmed.",
      );
    } finally {
      inflight.current = false;
      setBusy(false);
    }
  }
  function setup() {
    if (!navigation) return;
    try {
      sessionStorage.setItem(
        CONTINUATION_KEY,
        JSON.stringify(
          makeContinuation(
            actor,
            playlistId,
            selected,
            revision,
            navigation.origin,
            draft,
          ),
        ),
      );
    } catch {
      setError(
        "This browser cannot retain the routine draft. Save a copy of your choices before leaving.",
      );
      return;
    }
    setDirty(false);
    setHandoff(`${playlistPath(playlistId)}/setup?return_target=routine`);
  }
  useEffect(() => {
    if (error) announce(error, crypto.randomUUID());
  }, [error, announce]);
  if (actor !== loadedActor.current)
    return (
      <State kind="error" title="Your account changed">
        <a href={location.pathname + location.search}>
          Reload this routine for your current account
        </a>
      </State>
    );
  if (!navigation)
    return (
      <div className="sh-container sh-routine-task">
        <h1 className="sh-heading">This routine link is unavailable</h1>
        <Link to="/routines">Back to routines</Link>
      </div>
    );
  const candidates =
      context?.routines.filter(
        (row) => row.definition.job_type === draft.job_type,
      ) ?? [],
    algorithm = context?.algorithms.find(
      (value) => value.class_name === draft.algorithm_name,
    ),
    capability = context?.capabilities[draft.job_type],
    canReview =
      !!context &&
      !busy &&
      !unknown &&
      !stale &&
      capability?.available !== false;
  return (
    <div className="sh-container sh-routine-task">
      <header className="sh-routine-task-header">
        <Link
          className="sh-button-secondary"
          to={
            navigation.origin === "routines"
              ? "/routines"
              : `/workshop/${encodeURIComponent(playlistId)}`
          }
        >
          Back to {navigation.origin === "routines" ? "routines" : "playlist"}
        </Link>
        <p className="sh-eyebrow">A little structure. A different listen.</p>
        <h1 className="sh-heading" tabIndex={-1} ref={heading}>
          {selected ? `Edit routine #${selected}` : "Create a routine"}
        </h1>
        <p>{context?.target.name ?? "Loading playlist…"}</p>
      </header>
      {error && (
        <State
          kind="error"
          title={unknown ? "Activation is not confirmed" : "Check this routine"}
        >
          {error}
        </State>
      )}
      {accepted ? (
        <section className="sh-routine-step sh-surface">
          <h2>Routine #{accepted.schedule_id} saved</h2>
          <p>{accepted.state}</p>
          <p>
            {accepted.state === "Deleted"
              ? "This accepted routine was subsequently deleted. It will not be recreated."
              : accepted.registration?.status === "registered"
                ? "Scheduling is registered."
                : "The definition is saved. Check its scheduling status in Routines."}
          </p>
          <Link
            className="sh-button"
            to={`/routines#routine-${accepted.schedule_id}`}
          >
            View exact routine
          </Link>
        </section>
      ) : (
        context && (
          <div className="sh-routine-layout">
            <div className="sh-routine-main">
              <fieldset
                disabled={busy || unknown}
                className="sh-routine-step sh-surface"
              >
                <legend className="sh-routine-title">
                  Choose what happens
                </legend>
                <Field id="routine-operation" label="Operation">
                  {(props) => (
                    <Select
                      {...props}
                      value={draft.job_type}
                      onChange={(event) =>
                        edit({
                          job_type: event.currentTarget.value as JobType,
                          algorithm_name: [
                            "shuffle",
                            "raid_and_shuffle",
                          ].includes(event.currentTarget.value)
                            ? "BasicShuffle"
                            : null,
                          algorithm_params:
                            event.currentTarget.value === "rotate"
                              ? {
                                  rotation_mode: "swap",
                                  target_size: 50,
                                  rotation_count: 5,
                                  protect_count: 0,
                                }
                              : {},
                        })
                      }
                    >
                      {Object.entries(labels).map(([value, label]) => (
                        <option value={value} key={value}>
                          {label}
                        </option>
                      ))}
                    </Select>
                  )}
                </Field>
                {!selected && candidates.length > 0 && (
                  <section className="sh-routine-group">
                    <h2>Choose an exact routine or create another</h2>
                    {candidates.map((row) => (
                      <Button
                        key={row.schedule_id}
                        variant="secondary"
                        onClick={() => choose(row)}
                      >
                        Edit routine #{row.schedule_id} · {row.timing.text}
                      </Button>
                    ))}
                    <Button
                      variant="secondary"
                      aria-pressed={createAnother}
                      onClick={() => {
                        setCreateAnother(true);
                        setDirty(true);
                        setReview(null);
                      }}
                    >
                      Create another routine
                    </Button>
                  </section>
                )}
                <section className="sh-routine-group">
                  <h2>Connections</h2>
                  <p>
                    {context.prerequisites.sources.length} saved source
                    {context.prerequisites.sources.length === 1 ? "" : "s"}
                  </p>
                  <p>
                    Collection:{" "}
                    {context.prerequisites.collection?.name ?? "Not linked"}
                  </p>
                  <p>
                    Archive:{" "}
                    {context.prerequisites.archive?.name ?? "Not linked"}
                  </p>
                  <p className="sh-caption">
                    These playlist settings are shared by every routine. Saving
                    setup does not activate this draft.
                  </p>
                  {capability?.available === false && (
                    <p className="sh-routine-warning">{capability.reason}</p>
                  )}
                  <Button variant="secondary" onClick={setup}>
                    Open playlist setup
                  </Button>
                </section>
                {["shuffle", "raid_and_shuffle"].includes(draft.job_type) && (
                  <section className="sh-routine-group">
                    <h2>Shuffle options</h2>
                    <Field id="routine-algorithm" label="Shuffle algorithm">
                      {(props) => (
                        <Select
                          {...props}
                          value={draft.algorithm_name ?? ""}
                          onChange={(event) =>
                            edit({
                              algorithm_name: event.currentTarget.value,
                              algorithm_params: {},
                            })
                          }
                        >
                          {!algorithm && (
                            <option value={draft.algorithm_name ?? ""}>
                              {draft.algorithm_name ?? "Choose an algorithm"}{" "}
                              (unavailable)
                            </option>
                          )}
                          {context.algorithms.map((value) => (
                            <option
                              key={value.class_name}
                              value={value.class_name}
                            >
                              {value.name}
                            </option>
                          ))}
                        </Select>
                      )}
                    </Field>
                    <p>{algorithm?.description}</p>
                    {algorithm &&
                      Object.entries(algorithm.parameters).map(
                        ([name, parameter]) => (
                          <Field
                            key={name}
                            id={`routine-param-${name}`}
                            label={name.replaceAll("_", " ")}
                            help={parameter.description}
                          >
                            {(props) =>
                              parameter.type === "string" ? (
                                <Select
                                  {...props}
                                  value={String(
                                    draft.algorithm_params[name] ??
                                      parameter.default,
                                  )}
                                  onChange={(event) =>
                                    parameterValue(
                                      name,
                                      event.currentTarget.value,
                                    )
                                  }
                                >
                                  {parameter.options.map((value) => (
                                    <option value={value} key={value}>
                                      {value}
                                    </option>
                                  ))}
                                </Select>
                              ) : (
                                <Input
                                  {...props}
                                  type="number"
                                  step={
                                    parameter.type === "integer" ? 1 : "any"
                                  }
                                  min={parameter.min}
                                  max={parameter.max}
                                  value={String(
                                    draft.algorithm_params[name] ??
                                      parameter.default,
                                  )}
                                  onChange={(event) =>
                                    parameterValue(
                                      name,
                                      event.currentTarget.value === ""
                                        ? ""
                                        : Number(event.currentTarget.value),
                                    )
                                  }
                                />
                              )
                            }
                          </Field>
                        ),
                      )}
                  </section>
                )}
                {draft.job_type === "rotate" && (
                  <section className="sh-routine-group">
                    <h2>Rotation options</h2>
                    {[
                      ["target_size", "Playlist size cap", 50],
                      ["rotation_count", "Tracks to rotate", 5],
                      ["protect_count", "Protected first tracks", 0],
                    ].map(([name, label, fallback]) => (
                      <Field
                        key={name}
                        id={`routine-${name}`}
                        label={String(label)}
                      >
                        {(props) => (
                          <Input
                            {...props}
                            type="number"
                            min={name === "protect_count" ? 0 : 1}
                            value={String(
                              draft.algorithm_params[String(name)] ?? fallback,
                            )}
                            onChange={(event) =>
                              parameter(
                                String(name),
                                event.currentTarget.value === ""
                                  ? ""
                                  : Number(event.currentTarget.value),
                              )
                            }
                          />
                        )}
                      </Field>
                    ))}
                  </section>
                )}
                {["drip", "raid_and_drip"].includes(draft.job_type) && (
                  <section className="sh-routine-group">
                    <h2>Adding collected tracks</h2>
                    <p>
                      Saved runs respect the collection’s shared adding setting.
                      This does not force it on.
                    </p>
                    <Field
                      id="routine-drip-count"
                      label="Tracks per run (optional override)"
                      help="Leave empty to use the shared collection count."
                    >
                      {(props) => (
                        <Input
                          {...props}
                          type="number"
                          min={1}
                          max={50}
                          value={String(
                            draft.algorithm_params.drip_count ?? "",
                          )}
                          onChange={(event) => {
                            const next = { ...draft.algorithm_params };
                            if (event.currentTarget.value === "")
                              delete next.drip_count;
                            else
                              next.drip_count = Number(
                                event.currentTarget.value,
                              );
                            edit({ algorithm_params: next });
                          }}
                        />
                      )}
                    </Field>
                  </section>
                )}
                <section className="sh-routine-group">
                  <h2>Timing</h2>
                  {selected && !timingDirty && (
                    <p>
                      The saved timing is preserved until you change it:{" "}
                      {
                        context.routines.find(
                          (row) => row.schedule_id === selected,
                        )?.timing.text
                      }
                    </p>
                  )}
                  <Field id="routine-timing-kind" label="Timing type">
                    {(props) => (
                      <Select
                        {...props}
                        value={String(draft.timing.kind)}
                        onChange={(event) => {
                          setTimingDirty(true);
                          edit({
                            timing: {
                              kind: event.currentTarget.value,
                              interval: "daily",
                              time: "09:00",
                              weekday: "mon",
                              expression: "0 9 * * *",
                              timezone: "UTC",
                            },
                          });
                        }}
                      >
                        {["interval", "daily", "weekly", "advanced"].map(
                          (value) => (
                            <option key={value} value={value}>
                              {value === "advanced" ? "Advanced cron" : value}
                            </option>
                          ),
                        )}
                      </Select>
                    )}
                  </Field>
                  {draft.timing.kind === "interval" ? (
                    <Field id="routine-interval" label="Interval">
                      {(props) => (
                        <Select
                          {...props}
                          value={String(draft.timing.interval ?? "daily")}
                          onChange={(event) =>
                            timing("interval", event.currentTarget.value)
                          }
                        >
                          {[
                            ["every_6h", "Every 6 hours"],
                            ["every_12h", "Every 12 hours"],
                            ["daily", "Every day"],
                            ["every_3d", "Every 3 days"],
                            ["weekly", "Every week"],
                          ].map(([value, label]) => (
                            <option value={value} key={value}>
                              {label}
                            </option>
                          ))}
                        </Select>
                      )}
                    </Field>
                  ) : draft.timing.kind === "advanced" ? (
                    <Field id="routine-cron" label="Five-field cron expression">
                      {(props) => (
                        <Input
                          {...props}
                          value={String(draft.timing.expression ?? "")}
                          onChange={(event) =>
                            timing("expression", event.currentTarget.value)
                          }
                        />
                      )}
                    </Field>
                  ) : (
                    <>
                      <Field id="routine-clock" label="Local time">
                        {(props) => (
                          <Input
                            {...props}
                            type="time"
                            value={String(draft.timing.time ?? "09:00")}
                            onChange={(event) =>
                              timing("time", event.currentTarget.value)
                            }
                          />
                        )}
                      </Field>
                      {draft.timing.kind === "weekly" && (
                        <Field id="routine-weekday" label="Weekday">
                          {(props) => (
                            <Select
                              {...props}
                              value={String(draft.timing.weekday ?? "mon")}
                              onChange={(event) =>
                                timing("weekday", event.currentTarget.value)
                              }
                            >
                              {[
                                "mon",
                                "tue",
                                "wed",
                                "thu",
                                "fri",
                                "sat",
                                "sun",
                              ].map((value) => (
                                <option key={value}>{value}</option>
                              ))}
                            </Select>
                          )}
                        </Field>
                      )}
                    </>
                  )}
                  <Field
                    id="routine-timezone"
                    label="IANA timezone"
                    help="Review validates the named timezone on the server."
                  >
                    {(props) => (
                      <Input
                        {...props}
                        value={String(draft.timing.timezone ?? "")}
                        onChange={(event) =>
                          timing("timezone", event.currentTarget.value)
                        }
                      />
                    )}
                  </Field>
                </section>
              </fieldset>
              {stale && (
                <Button
                  variant="secondary"
                  onClick={() => {
                    const row = context.routines.find(
                      (value) => value.schedule_id === selected,
                    );
                    if (row) choose(row);
                  }}
                >
                  Use current routine definition
                </Button>
              )}
              <div className="sh-routine-actions">
                <Button
                  busy={busy}
                  disabled={!canReview}
                  onClick={() => void doReview()}
                >
                  Review routine
                </Button>
                {unknown && (
                  <Button
                    variant="secondary"
                    busy={busy}
                    onClick={() => void activate()}
                  >
                    Check activation
                  </Button>
                )}
              </div>
              {review && reviewRequest && (
                <section
                  className="sh-routine-step sh-surface"
                  aria-labelledby="routine-review-heading"
                >
                  <h2 id="routine-review-heading">Review and activate</h2>
                  <p>
                    {operationLabel(review.definition.job_type)} ·{" "}
                    {review.definition.target_playlist_name}
                  </p>
                  <p>
                    {review.definition.schedule_type === "interval"
                      ? review.definition.schedule_value
                      : review.definition.schedule_value}{" "}
                    · {review.definition.timezone ?? "Scheduler timezone"}
                  </p>
                  <p>
                    {selected
                      ? `Update exact routine #${selected}.`
                      : "Create a new enabled routine."}{" "}
                    Only the server-reviewed definition and signed intent will
                    be saved.
                  </p>
                  {review.warnings.map((value) => (
                    <p key={value}>{value}</p>
                  ))}
                  <Button
                    busy={busy}
                    disabled={unknown}
                    onClick={() => void activate()}
                  >
                    {selected ? "Save routine" : "Activate routine"}
                  </Button>
                </section>
              )}
            </div>
            <aside className="sh-routine-summary sh-surface">
              <h2 className="sh-routine-title">Your routine</h2>
              <dl>
                <dt>Playlist</dt>
                <dd>{context.target.name}</dd>
                <dt>Operation</dt>
                <dd>{operationLabel(draft.job_type)}</dd>
                <dt>Identity</dt>
                <dd>{selected ? `Routine #${selected}` : "New routine"}</dd>
              </dl>
              <p>
                Nothing is saved until you activate. Playlist setup changes are
                saved separately.
              </p>
            </aside>
          </div>
        )
      )}
    </div>
  );
  function parameterValue(name: string, value: string | number) {
    parameter(name, value);
  }
  function timing(name: string, value: string) {
    setTimingDirty(true);
    edit({ timing: { ...draft.timing, [name]: value } });
  }
}
