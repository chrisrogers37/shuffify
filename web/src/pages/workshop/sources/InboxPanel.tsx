import { useEffect } from "react";
import { Link } from "react-router";
import { Artwork } from "../../../components/Artwork";
import { Button, Field, Input, Toggle } from "../../../components/ui/controls";
import { useConfirm } from "../../../components/tasks/ConfirmDialog";
import { useTasks } from "../../../components/tasks/TaskPanel";
import type { WorkshopToolContext } from "../workspace";
import { SourceSetupPanel } from "./SourceSetupPanel";
import {
  SourceActor,
  ActionReceipt,
  Continuations,
  SourceFeedback,
  readMessage,
  useRowReturn,
  useSources,
} from "./hooks";
import { useOpenRun } from "./RunReview";
export function InboxPanel(context: WorkshopToolContext) {
  const { controller: c } = context,
    { t, state } = useSources(c),
    tasks = useTasks(),
    confirm = useConfirm(),
    rowReturn = useRowReturn(),
    run = useOpenRun(context),
    pending = t.visiblePending(),
    link = state.status.value?.raid_link,
    canDraft = c.permission("draft");
  useEffect(() => {
    if (t.getSnapshot().pending.phase === "idle") void t.loadPending();
    if (t.getSnapshot().status.phase === "idle") void t.loadStatus();
  }, [t]);
  const setup = (trigger: HTMLElement) =>
    tasks.openTask(
      {
        id: `${c.actor}:${c.playlistId}:source-setup`,
        title: "Source setup",
        content: (
          <SourceActor controller={c}>
            <SourceSetupPanel {...context} />
          </SourceActor>
        ),
        trigger,
        fallback: context.fallback,
        scrollKey: `${c.actor}:${c.playlistId}:source-setup`,
      },
      "push",
    );
  const dismissAll = async () => {
    const ids = t.visiblePending().map((x) => x.id);
    if (!ids.length) return;
    if (
      await confirm.confirm({
        title: "Dismiss all pending tracks?",
        message: `Dismiss the ${ids.length} currently shown pending tracks. Staged tracks stay staged. Confirmed inbox dismissal remains even if collection cleanup fails.`,
        confirmLabel: `Dismiss ${ids.length} pending tracks`,
        danger: true,
      })
    ) {
      void t.dismissMany(ids);
      rowReturn.fallback.current?.focus();
    }
  };
  return (
    <div className="sh-sources-stack">
      <SourceFeedback controller={c} />
      <section className="sh-sources-stack" aria-labelledby="pending-heading">
        <h3 id="pending-heading" ref={rowReturn.fallback} tabIndex={-1}>
          Pending tracks ({pending.length})
        </h3>
        <p>
          Add to draft is local until Review &amp; save. Dismiss now confirms
          inbox removal first, then attempts cleanup of its captured collection.
        </p>
        <Button
          variant="secondary"
          busy={state.pending.phase === "loading"}
          onClick={() => {
            void t.loadPending();
          }}
        >
          Refresh pending tracks
        </Button>
        {readMessage(
          state.pending,
          () => {
            void t.loadPending();
          },
          "pending tracks",
        )}
        {!pending.length && state.pending.phase === "ready" && (
          <p>
            No pending tracks. Collect for review checks your watched sources.
          </p>
        )}
        <ul className="sh-sources-list">
          {pending.map((track) => (
            <li
              className="sh-sources-track"
              key={track.id}
              data-pending-id={track.id}
            >
              <Artwork
                name={track.track_name}
                src={track.track_image_url}
                spotifyUrl={`https://open.spotify.com/track/${track.track_uri.split(":").pop()}`}
              />
              <div>
                <h4>{track.track_name}</h4>
                <p>{track.track_artists ?? "Artist unavailable"}</p>
                <p className="sh-sources-muted">
                  From {track.source_name ?? "Unknown source"} · Pending #
                  {track.id}
                </p>
                <div className="sh-sources-actions">
                  <Button
                    variant="secondary"
                    aria-disabled={
                      !canDraft.allowed || state.status.phase !== "ready"
                    }
                    onClick={(e) => {
                      const back = rowReturn.after(
                        e.currentTarget,
                        "data-pending-id",
                        "button",
                      );
                      if (t.stagePending([track.id])) back();
                    }}
                  >
                    Add to draft
                  </Button>
                  <Button
                    variant="secondary"
                    aria-disabled={
                      !c.permission("collect").allowed ||
                      state.status.phase !== "ready"
                    }
                    onClick={(e) => {
                      const back = rowReturn.after(
                        e.currentTarget,
                        "data-pending-id",
                        "button",
                      );
                      void t.dismiss([track.id]).then(back);
                    }}
                  >
                    Dismiss now
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
        {pending.length > 0 && (
          <div className="sh-sources-actions">
            <Button
              aria-disabled={
                !canDraft.allowed || state.status.phase !== "ready"
              }
              onClick={() => {
                if (t.stagePending(pending.map((x) => x.id)))
                  rowReturn.fallback.current?.focus();
              }}
            >
              Add all to draft
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                void dismissAll();
              }}
            >
              Dismiss all now
            </Button>
          </div>
        )}
        {!canDraft.allowed && <p>{canDraft.reason}</p>}
        {state.stagedPending.length > 0 && (
          <p>
            {state.stagedPending.length} pending IDs staged for the next
            reviewed save.
          </p>
        )}
      </section>
      <section className="sh-sources-stack" aria-labelledby="collect-heading">
        <h3 id="collect-heading">Collect from sources</h3>
        <p>
          Each action reviews its exact settings and resources before it runs.
        </p>
        <Button
          aria-disabled={
            !state.status.value?.source_count ||
            !c.permission("collect").allowed
          }
          onClick={(e) => {
            void run("raid", e.currentTarget);
          }}
        >
          Collect for review
        </Button>
        {link && (
          <>
            <Button
              variant="secondary"
              onClick={(e) => {
                void run("drip", e.currentTarget);
              }}
            >
              Add collected tracks now
            </Button>
            <Button
              variant="secondary"
              aria-disabled={!state.status.value?.source_count}
              onClick={(e) => {
                void run("raid_and_drip", e.currentTarget);
              }}
            >
              Collect &amp; add now
            </Button>
          </>
        )}
        {readMessage(
          state.status,
          () => {
            void t.loadStatus();
          },
          "source status",
        )}
        <Button
          variant="secondary"
          onClick={(e) => {
            void setup(e.currentTarget);
          }}
        >
          Source setup
        </Button>
        <Link
          to={`/playlist/${c.playlistId}/setup?return_target=playlist`}
          className="sh-button-secondary"
        >
          Playlist setup
        </Link>
      </section>
      {link && (
        <section
          className="sh-sources-stack"
          aria-labelledby="automatic-heading"
        >
          <h3 id="automatic-heading">Automatic adding</h3>
          <p>
            These settings save separately. A manual one-shot can run while
            automatic adding is paused.
          </p>
          <Toggle
            label="Add collected tracks automatically"
            checked={link.drip_enabled}
            onChange={() => {
              void t.toggleDrip();
            }}
          />
          <Field
            id="source-drip-count"
            label="Tracks per run"
            help="A whole number from 1 to 50. Your unsaved value survives refresh."
          >
            {(props) => (
              <Input
                {...props}
                type="number"
                min="1"
                max="50"
                value={state.dripInput}
                onChange={(e) => t.setDripInput(e.currentTarget.value)}
              />
            )}
          </Field>
          {state.dripInput !== String(link.drip_count) && (
            <Button
              busy={state.busy}
              onClick={() => {
                void t.saveCount().then((ok) => {
                  if (ok) document.getElementById("source-drip-count")?.focus();
                });
              }}
            >
              Save count now
            </Button>
          )}
        </section>
      )}
      <Continuations controller={c} />
      {state.actions
        .filter((a) => !state.retained.some((x) => x.task.id === a.id))
        .map((a) => (
          <ActionReceipt key={a.id} controller={c} action={a} />
        ))}
    </div>
  );
}
