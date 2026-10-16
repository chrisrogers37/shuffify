import { Link } from "react-router";
import { Button, Field, Select, Toggle } from "../../../components/ui/controls";
import { useConfirm } from "../../../components/tasks/ConfirmDialog";
import type { WorkshopToolContext } from "../workspace";
import {
  ActionReceipt,
  SourceFeedback,
  readMessage,
  useSources,
} from "./hooks";
export function ArchiveSetupPanel(context: WorkshopToolContext) {
  const { controller: c } = context,
    { t, state } = useSources(c),
    confirm = useConfirm(),
    pair = state.pair.value,
    old = state.archiveSource,
    changed =
      (state.stagedRestore.length > 0 || state.archiveQueue.length > 0) &&
      old?.id !== (pair?.archive_playlist_id ?? null);
  const unlink = async () => {
    if (!pair) return;
    if (
      await confirm.confirm({
        title: "Unlink the archive playlist?",
        message: `${pair.archive_playlist_name ?? pair.archive_playlist_id} and its tracks remain on Spotify. Removed tracks will stop going there, and Rotate needs an archive.`,
        confirmLabel: "Unlink archive",
        danger: true,
      })
    )
      await t.unlinkArchive();
  };
  const retarget = async () => {
    if (!pair) return;
    if (
      await confirm.confirm({
        title: "Review new archive",
        message: `Unsubmitted staging names ${old?.name} (${old?.id}). Use ${pair.archive_playlist_name ?? "Archive"} (${pair.archive_playlist_id}) for its next review? Saved and unknown source work keeps its original archive.`,
        confirmLabel: "Use reviewed archive",
      })
    )
      await t.retarget("archive", old?.id ?? null, pair.archive_playlist_id);
  };
  return (
    <div className="sh-sources-stack">
      <SourceFeedback controller={c} />
      <h3>Pairing and automatic archiving</h3>
      <p>
        An archive is another Spotify playlist for music set aside. Rotate
        routines are managed separately in Routines.
      </p>
      <Button
        variant="secondary"
        busy={state.pair.phase === "loading"}
        onClick={() => {
          void t.loadPair();
        }}
      >
        Refresh archive setup
      </Button>
      {readMessage(
        state.pair,
        () => {
          void t.loadPair();
        },
        "archive settings",
      )}
      {changed && (
        <section className="sh-sources-warning">
          <p>
            Staged work still names {old?.name} ({old?.id}); the current archive
            is {pair?.archive_playlist_name ?? "none"} (
            {pair?.archive_playlist_id ?? "no archive"}).
          </p>
          {pair && (
            <Button
              variant="secondary"
              onClick={() => {
                void retarget();
              }}
            >
              Review new archive
            </Button>
          )}
        </section>
      )}
      {pair ? (
        <>
          <p>
            {pair.archive_playlist_name ?? "Archive"} ·{" "}
            {pair.archive_playlist_id}
          </p>
          <Toggle
            label="Archive removed tracks automatically"
            checked={pair.auto_archive_on_remove}
            onChange={() => {
              void t.toggleArchive();
            }}
          />
          <p>
            This setting saves now and affects future manual removals. Existing
            queued occurrences stay staged.
          </p>
          <Button
            variant="danger"
            aria-disabled={
              state.stagedRestore.length > 0 ||
              state.archiveQueue.length > 0 ||
              state.busy
            }
            onClick={() => {
              void unlink();
            }}
          >
            Unlink archive playlist
          </Button>
        </>
      ) : (
        <>
          <p>No archive linked.</p>
          <Button
            onClick={() => {
              void t.createAsset("archive", true);
            }}
          >
            Create archive playlist now
          </Button>
          <Field id="source-archive-choice" label="Link one of your playlists">
            {(props) => (
              <Select
                {...props}
                value={state.selectedArchive}
                onFocus={() => {
                  if (state.owned.phase === "idle") void t.loadOwned();
                }}
                onChange={(e) => t.selectArchive(e.currentTarget.value)}
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
            aria-disabled={!state.selectedArchive}
            onClick={() => {
              void t.createAsset("archive", false, state.selectedArchive);
            }}
          >
            Link as archive now
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
      <p>
        Names are not creation proof. Choose an exact owned ID; an unconfirmed
        creation request keeps its original key and result.
      </p>
      <Button
        variant="secondary"
        onClick={(e) => context.openTool("routines", e.currentTarget, "push")}
      >
        View all routines for this playlist
      </Button>
      <Link
        to={`/playlist/${c.playlistId}/setup?return_target=playlist`}
        className="sh-button-secondary"
      >
        Playlist setup
      </Link>
      {state.actions
        .filter((x) => x.kind === "pair_create")
        .map((x) => (
          <ActionReceipt key={x.id} controller={c} action={x} />
        ))}
    </div>
  );
}
