import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation } from "react-router";
import type { ActivityContext } from "../../api/contracts";
import { ApiRequestError, RateLimitError } from "../../api/errors";
import { useServices } from "../../auth/SessionProvider";
import { useAnnouncer } from "../../a11y/Announcer";
import { Button, Select } from "../../components/ui/controls";
import { State } from "../../components/ui/States";
import { ExecutionDetails } from "../../components/results/ExecutionDetails";
import {
  dateText,
  displayType,
  eventDescription,
  eventTypes,
  exactDetail,
  filterEvents,
  filterRuns,
  loadActivity,
  operations,
  originLabel,
  playlistHref,
  routineHref,
  runOperation,
  runTitle,
} from "./model";
const categories = [
  ["all", "All loaded runs"],
  ["complete", "Completed"],
  ["partial", "Partly completed"],
  ["unknown", "Needs review"],
  ["running", "In progress"],
  ["unavailable", "Step details unavailable"],
] as const;
export function ActivityPage() {
  const { api } = useServices(),
    announce = useAnnouncer(),
    identity = useId(),
    location = useLocation();
  const [context, setContext] = useState<ActivityContext | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [attempt, setAttempt] = useState(0),
    [operation, setOperation] = useState("all"),
    [category, setCategory] = useState("all"),
    [eventFilter, setEventFilter] = useState("all"),
    [expanded, setExpanded] = useState(new Set<number>()),
    [retryUntil, setRetryUntil] = useState(0),
    [now, setNow] = useState(Date.now);
  const read = useRef<{
      attempt: number;
      promise: Promise<ActivityContext>;
    } | null>(null),
    errorRef = useRef<HTMLDivElement>(null),
    sequence = useRef(0);
  const seconds = Math.max(0, Math.ceil((retryUntil - now) / 1000));
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    if (read.current?.attempt !== attempt)
      read.current = { attempt, promise: loadActivity(api) };
    void read.current.promise.then(
      (value) => {
        if (active) {
          setContext(value);
          setLoading(false);
        }
      },
      (problem) => {
        if (active) {
          setError(
            problem instanceof ApiRequestError
              ? problem.message
              : "The activity response could not be loaded. Please try again.",
          );
          if (problem instanceof RateLimitError)
            setRetryUntil(Date.now() + (problem.retryAfterMs ?? 1000));
          setLoading(false);
        }
      },
    );
    return () => {
      active = false;
    };
  }, [api, attempt]);
  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);
  useEffect(() => {
    setNow(Date.now());
    if (retryUntil <= Date.now()) return;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= retryUntil) clearInterval(timer);
    }, 250);
    return () => clearInterval(timer);
  }, [retryUntil]);
  useEffect(() => {
    const match = /^#run-([1-9]\d*)$/u.exec(location.hash),
      id = match ? Number(match[1]) : null;
    if (id === null || !context?.executions.some((run) => run.id === id))
      return;
    setOperation("all");
    setCategory("all");
    setExpanded((previous) => new Set([...previous, id]));
    const row = document.getElementById(`run-${id}`);
    row?.focus({ preventScroll: true });
    row?.scrollIntoView({ behavior: "instant", block: "start" });
  }, [location.hash, context]);
  const runs = context
      ? filterRuns(context.executions, operation, category)
      : [],
    events = context ? filterEvents(context.activities, eventFilter) : [],
    visibleRuns = new Set(runs.map((run) => run.id)),
    visibleEvents = new Set(events.map((event) => event.id));
  function changeRuns(
    nextOperation: string,
    nextCategory: string,
    control: HTMLSelectElement,
  ) {
    const shown = context
        ? filterRuns(context.executions, nextOperation, nextCategory)
        : [],
      active = document.activeElement?.closest<HTMLElement>(".sh-activity-run");
    if (active && !shown.some((run) => active.id === `run-${run.id}`))
      control.focus();
    setOperation(nextOperation);
    setCategory(nextCategory);
    announce(
      `${shown.length} loaded runs shown.`,
      `${identity}-filter-${++sequence.current}`,
    );
  }
  function changeEvents(value: string, control: HTMLSelectElement) {
    const shown = context ? filterEvents(context.activities, value) : [],
      active =
        document.activeElement?.closest<HTMLElement>(".sh-activity-event");
    if (active && !shown.some((event) => active.id === `event-${event.id}`))
      control.focus();
    setEventFilter(value);
    announce(
      `${shown.length} loaded events shown.`,
      `${identity}-filter-${++sequence.current}`,
    );
  }
  return (
    <div className="sh-activity-page">
      <header className="sh-container sh-activity-header">
        <h1 className="sh-heading" tabIndex={-1}>
          Activity
        </h1>
        <p className="sh-detail sh-text-muted">
          Recent routine runs and app events
        </p>
      </header>
      {loading ? (
        <div className="sh-container">
          <State kind="loading" title="Loading activity…" />
        </div>
      ) : error ? (
        <div className="sh-container" role="alert" tabIndex={-1} ref={errorRef}>
          <State
            kind="error"
            title="Activity could not be loaded"
            action={
              <Button
                aria-disabled={seconds > 0}
                onClick={() => {
                  if (seconds === 0) setAttempt((value) => value + 1);
                }}
              >
                Try again
              </Button>
            }
          >
            <p>{error}</p>
            {seconds > 0 && (
              <p aria-live="off">
                Try again in {seconds} {seconds === 1 ? "second" : "seconds"}.
              </p>
            )}
          </State>
        </div>
      ) : (
        context && (
          <>
            <div
              className="sh-container sh-activity-stats"
              role="group"
              aria-label="Activity totals"
            >
              {[
                [context.stats.total_shuffles, "Shuffles"],
                [context.stats.active_schedule_count, "Enabled routines"],
                [context.stats.total_scheduled_runs, "Routine runs"],
                [context.stats.total_snapshots, "Snapshots saved"],
              ].map(([value, label]) => (
                <div key={label} className="sh-activity-stat">
                  <p className="sh-activity-stat-value">{value}</p>
                  <p>{label}</p>
                </div>
              ))}
            </div>
            <section
              className="sh-container sh-activity-section"
              aria-labelledby="activity-runs-heading"
            >
              <div className="sh-activity-panel">
                <div className="sh-activity-panel-heading">
                  <div>
                    <h2 id="activity-runs-heading" className="sh-routine-title">
                      Recent routine runs
                    </h2>
                    <p className="sh-caption sh-text-muted">
                      Up to 20 recent routine runs loaded. Per-routine History
                      shows its latest 10.
                    </p>
                  </div>
                  <div className="sh-activity-filter">
                    <label htmlFor="activity-operation-filter">Operation</label>
                    <Select
                      id="activity-operation-filter"
                      value={operation}
                      onChange={(event) =>
                        changeRuns(
                          event.currentTarget.value,
                          category,
                          event.currentTarget,
                        )
                      }
                    >
                      <option value="all">All operations</option>
                      {Object.entries(operations).map(([value, label]) => (
                        <option value={value} key={value}>
                          {label}
                        </option>
                      ))}
                    </Select>
                    <label htmlFor="activity-run-filter">Show runs</label>
                    <Select
                      id="activity-run-filter"
                      value={category}
                      onChange={(event) =>
                        changeRuns(
                          operation,
                          event.currentTarget.value,
                          event.currentTarget,
                        )
                      }
                    >
                      {categories.map(([value, label]) => (
                        <option value={value} key={value}>
                          {label}
                        </option>
                      ))}
                    </Select>
                  </div>
                </div>
                <p id="activity-run-count" className="sh-caption sh-text-muted">
                  {runs.length} of {context.executions.length} loaded runs shown
                </p>
                {context.executions.length === 0 ? (
                  <p className="sh-activity-empty">No routine runs yet.</p>
                ) : (
                  <>
                    <p className="sh-activity-empty" hidden={runs.length !== 0}>
                      No loaded runs match this filter.
                    </p>
                    <div className="sh-activity-list">
                      {context.executions.map((run) => {
                        const detail = exactDetail(run),
                          op = runOperation(run),
                          href = routineHref(run.schedule_id),
                          name =
                            run.schedule_name ||
                            (run.schedule_id
                              ? `Routine #${run.schedule_id}`
                              : "Routine unavailable");
                        return (
                          <article
                            key={run.id}
                            id={`run-${run.id}`}
                            tabIndex={-1}
                            className="sh-activity-row sh-activity-run"
                            hidden={!visibleRuns.has(run.id)}
                          >
                            <div>
                              <div className="sh-activity-row-title">
                                <span className="sh-badge">
                                  {op === "unknown"
                                    ? "Routine"
                                    : operations[op]}
                                </span>
                                {href ? (
                                  <Link
                                    className="sh-activity-description sh-activity-link"
                                    to={href}
                                  >
                                    {name}
                                  </Link>
                                ) : (
                                  <span className="sh-activity-description">
                                    {name}
                                  </span>
                                )}
                              </div>
                              <p className="sh-activity-origin">
                                {originLabel(run.triggered_by)}
                              </p>
                              <Link
                                className="sh-activity-link sh-caption"
                                to={`#run-${run.id}`}
                                onClick={(event) => {
                                  if (
                                    event.button !== 0 ||
                                    event.metaKey ||
                                    event.ctrlKey ||
                                    event.altKey ||
                                    event.shiftKey
                                  )
                                    return;
                                  setExpanded(
                                    (previous) =>
                                      new Set([...previous, run.id]),
                                  );
                                  const row = document.getElementById(
                                    `run-${run.id}`,
                                  );
                                  row?.focus({ preventScroll: true });
                                  row?.scrollIntoView({
                                    behavior: "instant",
                                    block: "start",
                                  });
                                }}
                              >
                                Link to run #{run.id}
                              </Link>
                            </div>
                            <div className="sh-activity-result">
                              <p className="sh-result-heading">
                                {runTitle(run)}
                              </p>
                              <time
                                className="sh-result-time"
                                {...(run.started_at
                                  ? { dateTime: run.started_at }
                                  : {})}
                              >
                                {dateText(run.started_at)}
                              </time>
                              {detail ? (
                                <details
                                  open={expanded.has(run.id)}
                                  onToggle={(event) => {
                                    const open = event.currentTarget.open;
                                    setExpanded((previous) => {
                                      if (previous.has(run.id) === open)
                                        return previous;
                                      const next = new Set(previous);
                                      if (open) next.add(run.id);
                                      else next.delete(run.id);
                                      return next;
                                    });
                                  }}
                                >
                                  <summary>
                                    Run details{" "}
                                    <span className="sr-only">#{run.id}</span>
                                  </summary>
                                  <ExecutionDetails detail={detail} />
                                </details>
                              ) : (
                                <p className="sh-result-unavailable">
                                  Step details unavailable
                                </p>
                              )}
                            </div>
                          </article>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
            </section>
            <section
              className="sh-container sh-activity-section"
              aria-labelledby="activity-events-heading"
            >
              <div className="sh-activity-panel">
                <div className="sh-activity-panel-heading">
                  <div>
                    <h2
                      className="sh-routine-title"
                      id="activity-events-heading"
                    >
                      App activity
                    </h2>
                    <p className="sh-caption sh-text-muted">
                      Up to 100 recent events loaded, including manual actions.
                    </p>
                  </div>
                  <div className="sh-activity-filter">
                    <label htmlFor="activity-event-filter">Show events</label>
                    <Select
                      id="activity-event-filter"
                      value={eventFilter}
                      onChange={(event) =>
                        changeEvents(
                          event.currentTarget.value,
                          event.currentTarget,
                        )
                      }
                    >
                      <option value="all">All loaded events</option>
                      <option value="manual">Manual actions</option>
                      {eventTypes(context.activities).map((type) => (
                        <option key={type} value={`type:${type}`}>
                          {displayType(type)}
                        </option>
                      ))}
                    </Select>
                  </div>
                </div>
                <p
                  className="sh-caption sh-text-muted"
                  id="activity-event-count"
                >
                  {events.length} of {context.activities.length} loaded events
                  shown
                </p>
                {context.activities.length === 0 ? (
                  <p className="sh-activity-empty">No app activity yet.</p>
                ) : (
                  <>
                    <p
                      className="sh-activity-empty"
                      hidden={events.length !== 0}
                    >
                      No loaded events match this filter.
                    </p>
                    <div className="sh-activity-list">
                      {context.activities.map((event) => {
                        const href = playlistHref(event.playlist_id),
                          name =
                            event.playlist_id &&
                            event.playlist_id === event.playlist_name
                              ? "Playlist name unavailable"
                              : event.playlist_name;
                        return (
                          <article
                            key={event.id}
                            id={`event-${event.id}`}
                            className="sh-activity-row sh-activity-event"
                            hidden={!visibleEvents.has(event.id)}
                          >
                            <div className="sh-activity-event-body">
                              <p className="sh-activity-description">
                                {eventDescription(event)}
                              </p>
                              {name &&
                                (href ? (
                                  <Link
                                    className="sh-activity-link sh-caption"
                                    to={href}
                                  >
                                    {name}
                                  </Link>
                                ) : (
                                  <p className="sh-caption sh-text-muted">
                                    {name}
                                  </p>
                                ))}
                              <p
                                className={
                                  event.triggered_by === "manual"
                                    ? "sh-activity-manual"
                                    : "sh-activity-origin"
                                }
                              >
                                {originLabel(event.triggered_by)}
                              </p>
                            </div>
                            <time
                              className="sh-activity-time"
                              {...(event.created_at
                                ? { dateTime: event.created_at }
                                : {})}
                            >
                              {dateText(event.created_at, true)}
                            </time>
                          </article>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
            </section>
          </>
        )
      )}
    </div>
  );
}
