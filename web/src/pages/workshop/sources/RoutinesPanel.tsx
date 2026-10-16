import { useEffect, useState, useRef } from "react";
import { Link } from "react-router";
import { Button } from "../../../components/ui/controls";
import { useConfirm } from "../../../components/tasks/ConfirmDialog";
import { ExecutionDetails } from "../../../components/results/ExecutionDetails";
import type { JobExecution } from "../../../api/contracts";
import type { WorkshopToolContext } from "../workspace";
import { labels, safeAvailableDetail } from "../../routines/model";
import { SourceFeedback, readMessage, useRowReturn, useSources } from "./hooks";
import { useOpenRun } from "./RunReview";
function Execution({ execution }: { execution: JobExecution }) {
  const detail = safeAvailableDetail(execution.result_detail, execution.id);
  return (
    <section className="sh-sources-result">
      <p>
        Run #{execution.id} · {String(execution.status)} · Origin:{" "}
        {typeof (execution as unknown as Record<string, unknown>)
          .triggered_by === "string"
          ? String(
              (execution as unknown as Record<string, unknown>).triggered_by,
            )
          : "Unknown"}
      </p>
      {typeof execution.started_at === "string" && (
        <p>Started {new Date(execution.started_at).toLocaleString()}</p>
      )}
      {typeof execution.error_message === "string" && (
        <p>{execution.error_message}</p>
      )}
      <Link to={`/activity#run-${execution.id}`}>
        Full execution #{execution.id}
      </Link>
      {detail && <ExecutionDetails detail={detail} />}
    </section>
  );
}
export function RoutinesPanel(context: WorkshopToolContext) {
  const { controller: c } = context,
    { t, state } = useSources(c),
    confirm = useConfirm(),
    run = useOpenRun(context),
    [open, setOpen] = useState<Set<number>>(new Set()),
    rowReturn = useRowReturn(),
    pending = useRef(new Set<number>());
  useEffect(() => {
    if (t.getSnapshot().routines.phase === "idle") void t.loadRoutines();
  }, [t]);
  const change = async (id: number) => {
    if (pending.current.has(id)) return;
    pending.current.add(id);
    try {
      await t.routineState(id);
    } finally {
      pending.current.delete(id);
    }
  };
  return (
    <div className="sh-sources-stack">
      <SourceFeedback controller={c} />
      <h3 ref={rowReturn.fallback} tabIndex={-1}>
        Routines for this playlist
      </h3>
      <div className="sh-sources-actions">
        <Button
          variant="secondary"
          busy={state.routines.phase === "loading"}
          onClick={() => {
            void t.loadRoutines();
          }}
        >
          Refresh routines
        </Button>
        <Link
          className="sh-button"
          to={`/playlist/${c.playlistId}/routines/new?from=playlist`}
        >
          Create routine
        </Link>
      </div>
      {readMessage(
        state.routines,
        () => {
          void t.loadRoutines();
        },
        "routines",
      )}
      <p>{state.routines.value.length} exact routines</p>
      {state.routines.phase === "ready" && !state.routines.value.length && (
        <p>
          No routines for this playlist. Create a guided routine to collect,
          add, shuffle or rotate tracks.
        </p>
      )}
      {state.routines.value.map((row) => {
        const permission = c.permission("routine", String(row.schedule_id)),
          latest = state.latest[row.schedule_id],
          history = state.histories[row.schedule_id];
        return (
          <article
            className="sh-sources-card"
            data-routine-id={row.schedule_id}
            key={row.schedule_id}
          >
            <h4 id={`source-routine-${row.schedule_id}`}>
              {labels[row.schedule.job_type]} · Routine #{row.schedule_id}
            </h4>
            <p>
              {row.schedule.target_playlist_name ?? "Playlist"} ·{" "}
              {row.state_label}
            </p>
            <p>
              Planned: {row.timing.text} ·{" "}
              {row.timing.effective_timezone ?? "Timezone unavailable"}
            </p>
            {row.timing.legacy && (
              <p>Legacy timing uses the scheduler timezone.</p>
            )}
            {row.timing.explanation && <p>{row.timing.explanation}</p>}
            <p>
              Registered next run:{" "}
              {row.registration.status === "registered" &&
              row.registration.next_run_at
                ? new Date(row.registration.next_run_at).toLocaleString()
                : "Unavailable"}
            </p>
            {latest ? (
              <Execution execution={latest} />
            ) : (
              <p>Latest result unavailable or never run.</p>
            )}
            <div
              className="sh-sources-actions"
              aria-describedby={`source-routine-${row.schedule_id}`}
            >
              <Link
                className="sh-button-secondary"
                aria-disabled={!permission.allowed}
                onClick={(e) => {
                  if (!permission.allowed) e.preventDefault();
                }}
                to={`/playlist/${c.playlistId}/routines/new?schedule_id=${row.schedule_id}&from=playlist`}
              >
                Edit this routine
              </Link>
              <Button
                variant="secondary"
                aria-describedby={`source-routine-${row.schedule_id}`}
                onClick={() => {
                  void change(row.schedule_id);
                }}
              >
                {row.schedule.is_enabled ? "Pause" : "Resume"}
              </Button>
              <Button
                variant="secondary"
                aria-disabled={!permission.allowed}
                onClick={(e) => {
                  void run(
                    "schedule",
                    e.currentTarget,
                    row.schedule_id,
                    row.schedule.job_type,
                  );
                }}
              >
                Run now
              </Button>
              <Button
                variant="secondary"
                aria-expanded={open.has(row.schedule_id)}
                aria-controls={`source-history-${row.schedule_id}`}
                onClick={() => {
                  setOpen((old) => {
                    const next = new Set(old);
                    if (next.has(row.schedule_id)) next.delete(row.schedule_id);
                    else {
                      next.add(row.schedule_id);
                      if (!history || history.phase === "error")
                        void t.history(row.schedule_id);
                    }
                    return next;
                  });
                }}
              >
                History
              </Button>
              <Button
                variant="danger"
                aria-disabled={!permission.allowed || state.busy}
                onClick={async (e) => {
                  const back = rowReturn.after(
                    e.currentTarget,
                    "data-routine-id",
                    "a",
                  );
                  if (
                    await confirm.confirm({
                      title: "Delete this routine?",
                      message: `Delete routine #${row.schedule_id}. The Spotify playlist remains; durable operation receipts are retained.`,
                      confirmLabel: "Delete routine",
                      danger: true,
                    })
                  ) {
                    if (await t.deleteRoutine(row.schedule_id)) back();
                  }
                }}
              >
                Delete routine
              </Button>
            </div>
            {!permission.allowed && <p>{permission.reason}</p>}
            {open.has(row.schedule_id) && (
              <section
                id={`source-history-${row.schedule_id}`}
                aria-label={`History for routine ${row.schedule_id}`}
              >
                {history &&
                  readMessage(
                    history,
                    () => {
                      void t.history(row.schedule_id);
                    },
                    `history for routine ${row.schedule_id}`,
                  )}
                {history?.phase === "ready" && !history.value.length && (
                  <p>No recorded executions.</p>
                )}
                {history?.value.map((execution) => (
                  <Execution key={execution.id} execution={execution} />
                ))}
              </section>
            )}
          </article>
        );
      })}
      <Link className="sh-button-secondary" to="/routines">
        Manage all routines
      </Link>
    </div>
  );
}
