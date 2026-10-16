import { useEffect } from "react";
import { Artwork } from "../../../components/Artwork";
import { Button } from "../../../components/ui/controls";
import { useTasks } from "../../../components/tasks/TaskPanel";
import type { WorkshopToolContext } from "../workspace";
import { ArchiveSetupPanel } from "./ArchiveSetupPanel";
import {
  SourceActor,
  Continuations,
  SourceFeedback,
  readMessage,
  useRowReturn,
  useSources,
} from "./hooks";
export function ArchivePanel(context: WorkshopToolContext) {
  const { controller: c } = context,
    { t, state } = useSources(c),
    tasks = useTasks(),
    rowReturn = useRowReturn(),
    tracks = t.visibleArchive(),
    pair = state.pair.value,
    draft = c.permission("draft");
  useEffect(() => {
    if (t.getSnapshot().pair.phase === "idle") void t.loadPair();
    if (t.getSnapshot().pair.value && t.getSnapshot().archive.phase === "idle")
      void t.loadArchive();
  }, [t, pair?.archive_playlist_id]);
  const setup = (trigger: HTMLElement) =>
    tasks.openTask(
      {
        id: `${c.actor}:${c.playlistId}:archive-setup`,
        title: "Archive setup",
        content: (
          <SourceActor controller={c}>
            <ArchiveSetupPanel {...context} />
          </SourceActor>
        ),
        trigger,
        fallback: context.fallback,
        scrollKey: `${c.actor}:${c.playlistId}:archive-setup`,
      },
      "push",
    );
  return (
    <div className="sh-sources-stack">
      <SourceFeedback controller={c} />
      <h3 ref={rowReturn.fallback} tabIndex={-1}>
        Your archive
      </h3>
      {readMessage(
        state.pair,
        () => {
          void t.loadPair();
        },
        "archive settings",
      )}
      {pair ? (
        <>
          <p>
            {pair.archive_playlist_name ?? "Archive"} ·{" "}
            {pair.archive_playlist_id}
          </p>
          <p>
            {pair.auto_archive_on_remove
              ? "Removed saved occurrences are staged for archiving when you save."
              : "Removed tracks are not archived automatically."}
          </p>
        </>
      ) : (
        state.pair.phase === "ready" && <p>No archive playlist is linked.</p>
      )}
      <Button
        variant="secondary"
        onClick={(e) => {
          void setup(e.currentTarget);
        }}
      >
        Archive setup
      </Button>
      {state.archiveQueue.length > 0 && (
        <p>
          {state.archiveQueue.length} removed occurrences queued for the next
          save.
        </p>
      )}
      {state.stagedRestore.length > 0 && (
        <p>{state.stagedRestore.length} archive rows staged for restoration.</p>
      )}
      {pair && (
        <section
          className="sh-sources-stack"
          aria-labelledby="archived-tracks-heading"
        >
          <h4 id="archived-tracks-heading">Archived tracks</h4>
          <p>
            Showing the most recently archived{" "}
            {Math.min(25, state.archive.value.tracks.length)} rows of{" "}
            {state.archive.phase === "ready"
              ? state.archive.value.total
              : "an unknown total"}
            . Add to draft is local; removal from the captured archive waits for
            a verified target save.
          </p>
          <Button
            variant="secondary"
            busy={state.archive.phase === "loading"}
            onClick={() => {
              void t.loadArchive();
            }}
          >
            Refresh archived tracks
          </Button>
          {readMessage(
            state.archive,
            () => {
              void t.loadArchive();
            },
            "archived tracks",
          )}
          {!tracks.length && state.archive.phase === "ready" && (
            <p>No archived tracks to show.</p>
          )}
          <ul className="sh-sources-list">
            {tracks.map((track, index) => (
              <li
                className="sh-sources-track"
                key={`${track.uri}-${index}`}
                data-archive-row={`${track.uri}-${index}`}
              >
                <Artwork
                  name={track.name}
                  src={track.album_image_url}
                  spotifyUrl={`https://open.spotify.com/track/${track.id}`}
                />
                <div>
                  <h4>{track.name}</h4>
                  <p>{track.artists.join(", ") || "Artist unavailable"}</p>
                  <p className="sh-sources-muted">
                    {track.album_name ?? "Album unavailable"}
                  </p>
                  <Button
                    variant="secondary"
                    aria-disabled={!draft.allowed}
                    onClick={(e) => {
                      const back = rowReturn.after(
                        e.currentTarget,
                        "data-archive-row",
                        "button",
                      );
                      if (t.stageArchive(false, track.uri)) back();
                    }}
                  >
                    Add to draft
                  </Button>
                </div>
              </li>
            ))}
          </ul>
          {tracks.length > 0 && (
            <Button
              aria-disabled={!draft.allowed}
              onClick={() => {
                if (t.stageArchive(true)) rowReturn.fallback.current?.focus();
              }}
            >
              Add all to draft
            </Button>
          )}
          {!draft.allowed && <p>{draft.reason}</p>}
        </section>
      )}
      <Continuations controller={c} />
    </div>
  );
}
