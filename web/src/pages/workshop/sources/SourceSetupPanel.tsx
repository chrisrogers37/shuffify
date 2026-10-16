import { useEffect } from "react";
import { Link } from "react-router";
import { Button, Field, Input, Select } from "../../../components/ui/controls";
import { useConfirm } from "../../../components/tasks/ConfirmDialog";
import type { WorkshopToolContext } from "../workspace";
import type { UpstreamSource } from "./types";
import {
  ActionReceipt,
  SourceFeedback,
  readMessage,
  useSources,
  useRowReturn,
} from "./hooks";
export function SourceCount({
  source,
  onSave,
  value,
  onChange,
}: {
  source: UpstreamSource;
  onSave: (n: number) => void;
  value: string;
  onChange: (value: string) => void;
}) {
  const save = () => {
    const n = Number(value);
    if (Number.isInteger(n) && n >= 1 && n <= 100 && n !== source.raid_count)
      onSave(n);
    else if (!Number.isInteger(n) || n < 1 || n > 100)
      onChange(String(source.raid_count));
  };
  return (
    <Field
      id={`source-count-${source.id}`}
      label={`Tracks per collect for ${source.source_name ?? source.search_query ?? source.source_playlist_id ?? "source"} #${source.id}`}
      help="1–100; saves when you leave this field."
    >
      {(props) => (
        <Input
          {...props}
          type="number"
          min="1"
          max="100"
          value={value}
          onChange={(e) => onChange(e.currentTarget.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              e.currentTarget.blur();
            }
          }}
        />
      )}
    </Field>
  );
}
export function SourceSetupPanel(context: WorkshopToolContext) {
  const { controller: c } = context,
    { t, state } = useSources(c),
    confirm = useConfirm(),
    rowReturn = useRowReturn(),
    status = state.status.value,
    link = status?.raid_link,
    old = state.pendingSource,
    newId = link?.raid_playlist_id ?? null,
    changed = state.stagedPending.length > 0 && (old?.id ?? null) !== newId;
  useEffect(() => {
    if (t.getSnapshot().status.phase === "idle") void t.loadStatus();
  }, [t]);
  const unlink = async () => {
    if (!link) return;
    if (
      await confirm.confirm({
        title: "Unlink the raid playlist?",
        message: `${link.raid_playlist_name ?? link.raid_playlist_id} and its tracks remain on Spotify. Collected tracks stop going into it.`,
        confirmLabel: "Unlink raid playlist",
        danger: true,
      })
    ) {
      await t.unlinkCollection();
      rowReturn.fallback.current?.focus();
    }
  };
  const retarget = async () => {
    if (
      await confirm.confirm({
        title: "Review new collection",
        message: `Unsubmitted staged tracks currently name ${old?.name ?? "the inbox only"} (${old?.id ?? "no collection"}). Change their next review to ${link?.raid_playlist_name ?? "the inbox only"} (${newId ?? "no collection"})? Saved or unknown source work stays on its original source.`,
        confirmLabel: "Use reviewed collection",
      })
    )
      await t.retarget("collection", old?.id ?? null, newId);
  };
  return (
    <div className="sh-sources-stack">
      <SourceFeedback controller={c} />
      <h3 ref={rowReturn.fallback} tabIndex={-1}>
        Collections and watched sources
      </h3>
      <p>
        Watch source playlists for Collect. Shared source settings save
        separately from your draft and can affect multiple routines.
      </p>
      <Button
        variant="secondary"
        busy={state.status.phase === "loading"}
        onClick={() => {
          void t.loadStatus();
        }}
      >
        Refresh source setup
      </Button>
      {readMessage(
        state.status,
        () => {
          void t.loadStatus();
        },
        "source setup",
      )}
      {changed && (
        <section className="sh-sources-warning">
          <p>
            The collection changed. Staging still names{" "}
            {old?.name ?? "the inbox"} ({old?.id ?? "no collection"}); the
            current link is {link?.raid_playlist_name ?? "none"} (
            {newId ?? "no collection"}).
          </p>
          <Button
            variant="secondary"
            onClick={() => {
              void retarget();
            }}
          >
            Review new collection
          </Button>
        </section>
      )}
      <section className="sh-sources-stack">
        <h4>Raid playlist</h4>
        {link ? (
          <>
            <p>
              {link.raid_playlist_name ?? "Collection"} ·{" "}
              {link.raid_playlist_id}
            </p>
            <Button
              variant="danger"
              aria-disabled={state.stagedPending.length > 0 || state.busy}
              onClick={() => {
                void unlink();
              }}
            >
              Unlink raid playlist
            </Button>
          </>
        ) : (
          <>
            <p>
              No raid playlist is linked. You can still collect into the review
              inbox.
            </p>
            <Button
              onClick={() => {
                void t.createAsset("collection", true);
              }}
            >
              Create raid playlist now
            </Button>
            <Field
              id="source-collection-choice"
              label="Link one of your playlists"
            >
              {(props) => (
                <Select
                  {...props}
                  value={state.selectedCollection}
                  onFocus={() => {
                    if (state.owned.phase === "idle") void t.loadOwned();
                  }}
                  onChange={(e) => t.selectCollection(e.currentTarget.value)}
                >
                  <option value="">Choose exact playlist…</option>
                  {state.owned.value.map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.name} · {x.id}
                    </option>
                  ))}
                </Select>
              )}
            </Field>
            <Button
              variant="secondary"
              aria-disabled={!state.selectedCollection}
              onClick={() => {
                void t.createAsset(
                  "collection",
                  false,
                  state.selectedCollection,
                );
              }}
            >
              Link as raid playlist now
            </Button>
          </>
        )}
        <Button
          variant="secondary"
          busy={state.owned.phase === "loading"}
          onClick={() => {
            void t.loadOwned(true);
          }}
        >
          Refresh owned playlists
        </Button>
        {readMessage(
          state.owned,
          () => {
            void t.loadOwned(true);
          },
          "owned playlists",
        )}
        <p className="sh-sources-muted">
          Refreshing never adopts a matching name. Choose the exact playlist ID
          yourself.
        </p>
      </section>
      <form
        className="sh-sources-stack"
        onSubmit={(e) => {
          e.preventDefault();
          void t.addURL();
        }}
      >
        <h4>
          Watched sources ({status?.source_count ?? "unknown"} /{" "}
          {status?.max_sources ?? "unknown"})
        </h4>
        <Field
          id="source-url"
          label="Spotify playlist link, URI or ID"
          help="Watching saves the source now. Routine creation is a separate guided task."
        >
          {(props) => (
            <Input
              {...props}
              maxLength={1024}
              value={state.url}
              onChange={(e) => t.setURL(e.currentTarget.value)}
              required
            />
          )}
        </Field>
        <Button
          type="submit"
          busy={state.busy}
          aria-disabled={
            !state.url.trim() ||
            !status ||
            status.source_count >= status.max_sources
          }
        >
          Watch source now
        </Button>
      </form>
      <ul className="sh-sources-list">
        {status?.sources.map((source) => (
          <li
            className="sh-sources-card"
            key={source.id}
            data-source-id={source.id}
          >
            <h4>
              {source.source_name ??
                source.search_query ??
                source.source_playlist_id ??
                "Source"}
            </h4>
            <p>
              {source.source_type} · Source #{source.id}
            </p>
            {source.last_track_count !== null && (
              <p>{source.last_track_count} known tracks</p>
            )}
            {source.last_track_count === null && <p>Track count unavailable</p>}
            <SourceCount
              source={source}
              value={
                state.sourceCountInputs[source.id] ?? String(source.raid_count)
              }
              onChange={(value) => t.setSourceCountInput(source.id, value)}
              onSave={(n) => {
                void t.updateSourceCount(source.id, n);
              }}
            />
            <Button
              variant="danger"
              aria-disabled={state.busy}
              onClick={async (e) => {
                const back = rowReturn.after(
                  e.currentTarget,
                  "data-source-id",
                  "button",
                );
                if (
                  await confirm.confirm({
                    title: "Unwatch this source?",
                    message: `Unwatch ${source.source_name ?? source.search_query ?? source.source_playlist_id} (#${source.id}). Shared routines may change; existing inbox tracks remain.`,
                    confirmLabel: "Unwatch source",
                    danger: true,
                  })
                ) {
                  if (await t.unwatch(source.id)) back();
                }
              }}
            >
              Unwatch source
            </Button>
          </li>
        ))}
      </ul>
      <Button
        variant="secondary"
        onClick={(e) => context.openTool("routines", e.currentTarget, "push")}
      >
        View all routines for this playlist
      </Button>
      <Link
        className="sh-button-secondary"
        to={`/playlist/${c.playlistId}/setup?return_target=playlist`}
      >
        Playlist setup
      </Link>
      {state.actions
        .filter((a) => a.kind === "raid_link_create")
        .map((a) => (
          <ActionReceipt key={a.id} controller={c} action={a} />
        ))}
    </div>
  );
}
