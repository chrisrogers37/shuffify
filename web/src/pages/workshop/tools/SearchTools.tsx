import { useId } from "react";
import { Artwork } from "../../../components/Artwork";
import { Button, Field, Input } from "../../../components/ui/controls";
import type { DeepReadonly } from "../core/types";
import type { WorkshopTrack } from "../../../api/contracts";
import type { WorkshopToolContext } from "../workspace";
import { duration } from "./model";
import { ToolFeedback, useRetry, useTools } from "./hooks";
function ResultTrack({
  track,
  added,
  allowed,
  reason,
  onAdd,
}: {
  track: DeepReadonly<WorkshopTrack>;
  added: boolean;
  allowed: boolean;
  reason: string;
  onAdd: () => void;
}) {
  const rightsId = useId();
  return (
    <li className="sh-tools-track">
      <Artwork
        src={track.album_image_url}
        name={track.name}
        spotifyUrl={`https://open.spotify.com/track/${encodeURIComponent(track.id)}`}
      />
      <div className="sh-tools-track-copy">
        <h4>{track.name}</h4>
        <p>
          {track.artists.filter(Boolean).join(", ") || "Artist unavailable"}
        </p>
        <p className="sh-tools-muted">
          {track.album_name ?? "Album unavailable"} ·{" "}
          {duration(track.duration_ms)}
        </p>
        <Button
          variant="secondary"
          aria-disabled={added || !allowed}
          aria-describedby={!allowed ? rightsId : undefined}
          onClick={onAdd}
        >
          {added ? "Already in draft" : "Add to draft"}
        </Button>
        {!allowed && (
          <p id={rightsId} className="sh-tools-muted">
            {reason}
          </p>
        )}
      </div>
    </li>
  );
}
export function CatalogTool(context: WorkshopToolContext) {
  const { controller: c } = context,
    { t, state, workshop } = useTools(c),
    s = state.catalog,
    p = c.permission("draft"),
    wait = useRetry(s.retryUntil);
  return (
    <div className="sh-tools-stack">
      <p>
        Search Spotify’s catalog and add missing tracks to this draft. Spotify
        playlists stay unchanged until you review and save.
      </p>
      <form
        className="sh-tools-stack"
        onSubmit={(e) => {
          e.preventDefault();
          void t.search();
        }}
      >
        <Field
          id="tools-catalog-query"
          label="Track, artist or album"
          help="Up to 200 characters."
        >
          {(props) => (
            <Input
              {...props}
              type="search"
              maxLength={200}
              value={s.query}
              onChange={(e) => t.setQuery(e.currentTarget.value)}
            />
          )}
        </Field>
        <Button
          type="submit"
          busy={s.busy}
          aria-disabled={!c.permission("snapshot-read").allowed || wait > 0}
        >
          Search tracks
        </Button>
      </form>
      <ToolFeedback value={s} id="tools-catalog" />
      {s.loadedQuery && (
        <p className="sh-tools-muted">Results for “{s.loadedQuery}”</p>
      )}
      <ul className="sh-tools-list">
        {s.tracks.map((track, index) => (
          <ResultTrack
            key={`${track.uri}-${index}`}
            track={track}
            added={workshop.working.some((o) => o.uri === track.uri)}
            allowed={p.allowed}
            reason={p.reason}
            onAdd={() => {
              t.addTrack(track.uri);
            }}
          />
        ))}
      </ul>
      {s.more && (
        <Button
          variant="secondary"
          busy={s.busy}
          aria-disabled={s.query.trim() !== s.loadedQuery || wait > 0}
          onClick={() => {
            void t.search(true);
          }}
        >
          Load more tracks
        </Button>
      )}
      <Button
        variant="secondary"
        onClick={(e) => context.openTool("external", e.currentTarget, "push")}
      >
        Import from a playlist
      </Button>
    </div>
  );
}
export function ExternalTool(context: WorkshopToolContext) {
  const { controller: c } = context,
    { t, state, workshop } = useTools(c),
    s = state.external,
    result = s.result,
    p = c.permission("draft"),
    wait = useRetry(s.retryUntil),
    recent = workshop.context?.recently_loaded.slice(0, 5) ?? [];
  return (
    <div className="sh-tools-stack">
      <p>
        Load a playlist by link, ID or name. Adding tracks copies only missing
        tracks into this draft and leaves the source playlist unchanged.
      </p>
      <form
        className="sh-tools-stack"
        onSubmit={(e) => {
          e.preventDefault();
          void t.loadExternal();
        }}
      >
        <Field id="tools-external-input" label="Playlist link, ID or name">
          {(props) => (
            <Input
              {...props}
              maxLength={200}
              value={s.input}
              onChange={(e) => t.setExternalInput(e.currentTarget.value)}
            />
          )}
        </Field>
        <Button
          type="submit"
          busy={s.busy}
          aria-disabled={!c.permission("snapshot-read").allowed || wait > 0}
        >
          Load playlist
        </Button>
      </form>
      <ToolFeedback value={s} id="tools-external" />
      {!!recent.length && (
        <section
          className="sh-tools-stack"
          aria-labelledby="tools-recent-title"
        >
          <h3 id="tools-recent-title">Recently loaded</h3>
          <ul className="sh-tools-list">
            {recent.map((r) => (
              <li key={r.id} className="sh-tools-card">
                <h4>{r.name}</h4>
                <p className="sh-tools-muted">
                  {r.track_count === null
                    ? "Count unavailable"
                    : `${r.track_count} tracks`}{" "}
                  · {r.owner_id}
                </p>
                <Button
                  variant="secondary"
                  busy={s.busy}
                  aria-disabled={wait > 0}
                  onClick={() => {
                    void t.loadExternal(r.id);
                  }}
                >
                  Load {r.name}
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {result?.mode === "search" && (
        <section className="sh-tools-stack">
          <h3>Choose a playlist</h3>
          {!result.playlists.length && (
            <p>No playlists found. Try a link or another name.</p>
          )}
          <ul className="sh-tools-list">
            {result.playlists.map((r) => (
              <li key={r.id} className="sh-tools-track" data-source-id={r.id}>
                <Artwork
                  src={r.image_url}
                  name={r.name}
                  spotifyUrl={`https://open.spotify.com/playlist/${encodeURIComponent(r.id)}`}
                />
                <div className="sh-tools-track-copy">
                  <h4>{r.name}</h4>
                  <p>{r.owner_display_name}</p>
                  <p>
                    {r.total_tracks === null
                      ? "Count unavailable"
                      : `${r.total_tracks} tracks`}
                  </p>
                  <Button
                    variant="secondary"
                    busy={s.busy}
                    aria-disabled={wait > 0}
                    onClick={() => {
                      void t.loadExternal(r.id);
                    }}
                  >
                    Load {r.name}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
      {result && result.mode !== "search" && (
        <section
          className="sh-tools-stack"
          data-loaded-source={result.playlist.id}
        >
          <h3>{result.playlist.name}</h3>
          <p>
            {result.playlist.track_count === null
              ? "Count unavailable"
              : `${result.playlist.track_count} tracks`}{" "}
            · {result.playlist.owner_id}
          </p>
          {result.playlist.description && <p>{result.playlist.description}</p>}
          {result.mode === "restricted" ? (
            <>
              <p>{result.message}</p>
              <Button
                variant="secondary"
                onClick={(e) => {
                  t.setQuery(result.suggested_search);
                  context.openTool("catalog", e.currentTarget, "push");
                }}
              >
                Search the catalog instead
              </Button>
            </>
          ) : (
            <>
              <Button
                aria-disabled={
                  !p.allowed ||
                  result.tracks.every((track) =>
                    workshop.working.some((o) => o.uri === track.uri),
                  )
                }
                onClick={() => {
                  t.appendExternal();
                }}
              >
                Add missing tracks to draft
              </Button>
              {!p.allowed && <p>{p.reason}</p>}
              <ul className="sh-tools-list">
                {result.tracks.map((track, index) => (
                  <ResultTrack
                    key={`${track.uri}-${index}`}
                    track={track}
                    added={workshop.working.some((o) => o.uri === track.uri)}
                    allowed={p.allowed}
                    reason={p.reason}
                    onAdd={() => {
                      t.addTrack(track.uri, "external");
                    }}
                  />
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </div>
  );
}
