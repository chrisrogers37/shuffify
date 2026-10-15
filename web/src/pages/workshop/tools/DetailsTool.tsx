import { useRef, useState } from "react";
import { Button } from "../../../components/ui/controls";
import type { WorkshopToolContext } from "../workspace";
import { useTools, ToolFeedback, useRetry } from "./hooks";
export function DetailsTool(context: WorkshopToolContext) {
  const { controller: c } = context,
    { t, state, workshop } = useTools(c),
    playlist = workshop.context?.playlist,
    [reviewing, setReviewing] = useState(false),
    [error, setError] = useState(""),
    locksHeading = useRef<HTMLHeadingElement>(null),
    permission = c.permission("metadata"),
    wait = useRetry(state.locks.retryUntil);
  if (!playlist) return null;
  const review = async () => {
    if (reviewing || !c.permission("metadata").allowed) return;
    setReviewing(true);
    setError("");
    try {
      context.openReview(await c.prepareVisibility(!playlist.public));
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Visibility could not be reviewed.",
      );
    } finally {
      setReviewing(false);
    }
  };
  return (
    <div className="sh-tools-stack">
      <h3>{playlist.name}</h3>
      <p>{playlist.description || "No playlist description."}</p>
      <dl className="sh-tools-facts">
        <div>
          <dt>Owner</dt>
          <dd>{playlist.owner_id}</dd>
        </div>
        <div>
          <dt>Loaded from Spotify</dt>
          <dd>{workshop.baseline.length} tracks</dd>
        </div>
        <div>
          <dt>Current draft</dt>
          <dd>{workshop.working.length} tracks</dd>
        </div>
        <div>
          <dt>Visibility</dt>
          <dd>
            {playlist.public === true
              ? "Public"
              : playlist.public === false
                ? "Private"
                : "Unknown"}
          </dd>
        </div>
      </dl>
      <a
        className="sh-button-secondary"
        href={`https://open.spotify.com/playlist/${encodeURIComponent(playlist.id)}`}
        target="_blank"
        rel="noopener noreferrer"
      >
        Open playlist on Spotify
      </a>
      <p className="sh-tools-muted">
        Visibility is a separate reviewed Spotify change. Your local track draft
        remains available.
      </p>
      <Button
        busy={reviewing}
        aria-disabled={!permission.allowed}
        aria-describedby={
          !permission.allowed ? "tools-visibility-rights" : undefined
        }
        onClick={() => {
          void review();
        }}
      >
        Review making playlist {playlist.public ? "private" : "public"}
      </Button>
      {!permission.allowed && (
        <p id="tools-visibility-rights" className="sh-tools-muted">
          {permission.reason}
        </p>
      )}
      {error && (
        <ToolFeedback
          value={{ error, message: "", retryUntil: 0 }}
          id="visibility-review-error"
        />
      )}
      <h3 tabIndex={-1} ref={locksHeading}>
        Track locks
      </h3>
      <p>
        {state.locks.items.length} active ·{" "}
        {state.locks.items.filter((l) => l.lock_tier === "super").length}{" "}
        permanent
      </p>
      <ToolFeedback value={state.locks} id="details-locks" />
      <div className="sh-tools-actions">
        <Button
          variant="secondary"
          onClick={(e) => context.openTool("locks", e.currentTarget, "push")}
        >
          Manage track locks
        </Button>
        {state.locks.items.length > 0 && (
          <Button
            variant="secondary"
            busy={state.locks.busy}
            aria-disabled={
              !c.permission("locks").allowed || state.locks.unknown || wait > 0
            }
            onClick={async () => {
              await t.unlockAll();
              if (!t.getSnapshot().locks.items.length)
                locksHeading.current?.focus();
            }}
          >
            Unlock all tracks now
          </Button>
        )}
      </div>
    </div>
  );
}
