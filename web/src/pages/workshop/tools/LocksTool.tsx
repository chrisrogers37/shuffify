import { useRef } from "react";
import { Button } from "../../../components/ui/controls";
import type { WorkshopToolContext } from "../workspace";
import type { Occurrence } from "../core/types";
import { ToolFeedback, useRetry, useTools } from "./hooks";
import { lockAction, lockLabel } from "./model";
export function LockRowAction({
  context,
  occurrence,
}: {
  context: WorkshopToolContext;
  occurrence: Occurrence;
}) {
  const { t, state } = useTools(context.controller),
    s = state.locks,
    lock = s.items.find((l) => l.track_uri === occurrence.uri),
    p = context.controller.permission("locks"),
    wait = useRetry(s.retryUntil);
  return (
    <div className="sh-tools-row-lock">
      <p>
        {lock ? lockLabel(lock) : "Not held"}. A lock applies to every
        occurrence of this track.
      </p>
      <Button
        variant="secondary"
        busy={s.busy}
        aria-disabled={
          !p.allowed || s.phase !== "ready" || s.unknown || wait > 0
        }
        aria-describedby={
          !p.allowed ? `lock-rights-${occurrence.key}` : undefined
        }
        onClick={() => {
          void t.toggleLock(occurrence.key);
        }}
      >
        {lockAction(lock)}
      </Button>
      {!p.allowed && (
        <p id={`lock-rights-${occurrence.key}`} className="sh-tools-muted">
          {p.reason}
        </p>
      )}
      {(s.unknown || s.error) && (
        <ToolFeedback value={s} id={`row-lock-${occurrence.key}`} />
      )}
      {s.unknown && (
        <Button
          variant="secondary"
          onClick={() => {
            void t.loadLocks();
          }}
        >
          Refresh locks
        </Button>
      )}
    </div>
  );
}
export function LocksTool({ controller: c }: WorkshopToolContext) {
  const { t, state, workshop } = useTools(c),
    s = state.locks,
    p = c.permission("locks"),
    wait = useRetry(s.retryUntil),
    heading = useRef<HTMLHeadingElement>(null);
  const unlock = async () => {
    await t.unlockAll();
    if (!t.getSnapshot().locks.items.length) heading.current?.focus();
  };
  return (
    <div className="sh-tools-stack">
      <h3 ref={heading} tabIndex={-1}>
        Active track locks
      </h3>
      <p>
        Lock a track for 30 days, then permanently, then release it. Every
        occurrence of a held URI keeps its position during preview and row
        moves.
      </p>
      <p>
        {s.items.length} active {s.items.length === 1 ? "lock" : "locks"} ·{" "}
        {s.items.filter((l) => l.lock_tier === "super").length} permanent
      </p>
      <ToolFeedback value={s} id="tools-locks" />
      <div className="sh-tools-actions">
        <Button
          variant="secondary"
          busy={s.busy}
          aria-disabled={wait > 0 || s.phase === "installing"}
          onClick={() => {
            void t.loadLocks();
          }}
        >
          Refresh locks
        </Button>
        {s.items.length > 0 && (
          <Button
            variant="secondary"
            busy={s.busy}
            aria-disabled={!p.allowed || s.unknown || wait > 0}
            onClick={() => {
              void unlock();
            }}
          >
            Unlock all tracks now
          </Button>
        )}
      </div>
      {!p.allowed && <p className="sh-tools-muted">{p.reason}</p>}
      {s.phase === "loading" && <p>Loading active locks…</p>}
      {s.phase === "ready" && !s.items.length && (
        <p>No tracks are held. Use a track’s Actions to set its lock.</p>
      )}
      <ul className="sh-tools-list">
        {s.items.map((lock) => {
          const rows = workshop.working.filter((o) => o.uri === lock.track_uri),
            track = t.knownTrack(lock.track_uri);
          return (
            <li key={lock.id} className="sh-tools-card">
              <h4>{track?.name ?? lock.track_uri}</h4>
              <p>{lockLabel(lock)}</p>
              <p className="sh-tools-muted">
                {rows.length} matching{" "}
                {rows.length === 1 ? "occurrence" : "occurrences"} in this draft
                {rows.length
                  ? ` · positions ${workshop.working.flatMap((o, i) => (o.uri === lock.track_uri ? [i + 1] : [])).join(", ")}`
                  : ""}
              </p>
              {rows[0] && (
                <Button
                  variant="secondary"
                  busy={s.busy}
                  aria-disabled={!p.allowed || s.unknown || wait > 0}
                  onClick={() => {
                    void t.toggleLock(rows[0]!.key);
                  }}
                >
                  {lockAction(lock)}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
