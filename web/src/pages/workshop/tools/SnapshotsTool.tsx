import { useEffect, useRef, useState } from "react";
import { Button, Field } from "../../../components/ui/controls";
import { useConfirm } from "../../../components/tasks/ConfirmDialog";
import type { WorkshopToolContext } from "../workspace";
import { ToolFeedback, useRetry, useTools } from "./hooks";
import {
  groupSnapshots,
  relativeTime,
  snapshotDate,
  snapshotType,
  trackDifference,
} from "./model";
export function SnapshotsTool(context: WorkshopToolContext) {
  const { controller: c } = context,
    { t, state, workshop } = useTools(c),
    s = state.snapshots,
    wait = useRetry(s.retryUntil),
    confirm = useConfirm(),
    heading = useRef<HTMLHeadingElement>(null),
    take = useRef<HTMLButtonElement>(null),
    description = useRef<HTMLTextAreaElement>(null),
    [preparing, setPreparing] = useState<number | null>(null),
    [reviewError, setReviewError] = useState("");
  useEffect(() => {
    if (!s.loaded && !s.busy && !s.error) void t.loadSnapshots();
  }, [s.loaded, s.busy, s.error, t]);
  useEffect(() => {
    if (s.form) description.current?.focus();
  }, [s.form]);
  const returnToSection = () =>
    requestAnimationFrame(() => {
      (take.current ?? heading.current)?.focus();
    });
  const cancel = () => {
    t.showSnapshotForm(false);
    returnToSection();
  };
  const create = async () => {
    await t.createSnapshot();
    if (!t.getSnapshot().snapshots.form) returnToSection();
  };
  const remove = async (id: number) => {
    if (!c.permission("snapshot-delete", String(id)).allowed) return;
    t.confirmingSnapshot(id);
    const accepted = await confirm.confirm({
      title: "Delete this snapshot?",
      message: `Delete snapshot #${id} from Shuffify. Spotify does not change.`,
      confirmLabel: "Delete snapshot",
      danger: true,
    });
    if (accepted) {
      const deleted = await t.deleteSnapshot(id);
      if (deleted) requestAnimationFrame(() => heading.current?.focus());
    }
    t.confirmingSnapshot(null);
  };
  const restore = async (id: number) => {
    if (preparing !== null || !c.permission("snapshot").allowed) return;
    setReviewError("");
    if (!(await context.resolveDraft("restore this snapshot"))) return;
    setPreparing(id);
    try {
      const review = await c.prepareSnapshotRestore(id);
      context.openReview(review);
    } catch (error) {
      setReviewError(
        error instanceof Error
          ? error.message
          : "The restore could not be reviewed.",
      );
    } finally {
      setPreparing(null);
    }
  };
  const groups = groupSnapshots(s.items),
    createPermission = c.permission("snapshot-create");
  return (
    <div className="sh-tools-stack">
      <h3 id="tools-snapshots-title" ref={heading} tabIndex={-1}>
        Snapshot timeline
      </h3>
      <p>
        Snapshots preserve an exact track order in Shuffify. Viewing, taking and
        deleting a snapshot leave Spotify unchanged. Restore reviews Spotify’s
        current state before queuing a change.
      </p>
      <div className="sh-tools-actions">
        <Button
          variant="secondary"
          ref={take}
          aria-describedby={
            !createPermission.allowed
              ? "tools-snapshot-create-rights"
              : undefined
          }
          aria-disabled={
            !createPermission.allowed || s.busy || s.form || wait > 0
          }
          onClick={() => t.showSnapshotForm(true)}
        >
          Take snapshot
        </Button>
        <Button
          variant="secondary"
          busy={s.busy}
          aria-disabled={wait > 0}
          onClick={() => {
            void t.loadSnapshots();
          }}
        >
          Refresh snapshots
        </Button>
      </div>
      {!createPermission.allowed && (
        <p id="tools-snapshot-create-rights" className="sh-tools-muted">
          {createPermission.reason}
        </p>
      )}
      {s.form && (
        <form
          className="sh-tools-stack sh-tools-card"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <Field
            id="tools-snapshot-description"
            label="Snapshot description"
            help="Optional, up to 500 characters. An empty description becomes Manual snapshot."
          >
            {(props) => (
              <textarea
                {...props}
                ref={description}
                className="sh-input"
                maxLength={500}
                rows={3}
                value={s.description}
                onChange={(e) =>
                  t.setSnapshotDescription(e.currentTarget.value)
                }
              />
            )}
          </Field>
          <div className="sh-tools-actions">
            <Button
              type="submit"
              busy={s.busy}
              aria-disabled={
                !createPermission.allowed || s.createUnknown || wait > 0
              }
            >
              {c.hasDraft() ? "Save draft snapshot" : "Save snapshot"}
            </Button>
            <Button variant="secondary" aria-disabled={s.busy} onClick={cancel}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      <ToolFeedback value={s} id="tools-snapshots" />
      {reviewError && (
        <ToolFeedback
          value={{ error: reviewError, message: "", retryUntil: 0 }}
          id="snapshot-review-error"
        />
      )}
      {s.createUnknown && (
        <Button
          variant="secondary"
          aria-disabled={!createPermission.allowed || s.busy}
          onClick={async () => {
            if (
              await confirm.confirm({
                title: "Create another snapshot?",
                message:
                  "The earlier save may have completed. This deliberately creates another snapshot when you submit the form again.",
                confirmLabel: "Allow another snapshot",
              })
            )
              t.allowAnotherSnapshot();
          }}
        >
          Create another snapshot
        </Button>
      )}
      {!s.loaded && s.busy && <p>Loading snapshots…</p>}
      {s.loaded && !s.items.length && (
        <p>No snapshots yet. Take one to preserve this draft’s exact order.</p>
      )}
      {groups.map((group) => (
        <section
          key={group.label}
          className="sh-tools-stack"
          aria-label={`${group.label} snapshots`}
        >
          <h3>{group.label}</h3>
          <ol className="sh-tools-list">
            {group.snapshots.map((snapshot) => {
              const id = snapshot.id,
                detail = s.details[id],
                date = snapshotDate(snapshot.created_at),
                restorePermission = c.permission("snapshot"),
                deletePermission = c.permission("snapshot-delete", String(id));
              return (
                <li
                  className="sh-tools-card sh-tools-stack"
                  data-snapshot-id={id}
                  key={id}
                >
                  <div className="sh-tools-snapshot-head">
                    <span className="sh-tools-badge">
                      {snapshotType(snapshot.snapshot_type)}
                    </span>
                    <time
                      dateTime={date?.toISOString()}
                      title={date?.toLocaleString() ?? "Date unavailable"}
                    >
                      {relativeTime(snapshot.created_at)}
                    </time>
                  </div>
                  <h4>
                    {snapshot.trigger_description ?? snapshot.playlist_name}
                  </h4>
                  <p>
                    {snapshot.track_count}{" "}
                    {snapshot.track_count === 1 ? "track" : "tracks"} ·{" "}
                    {trackDifference(
                      snapshot.track_count,
                      workshop.working.length,
                    )}
                  </p>
                  <p className="sh-tools-muted">
                    Snapshot #{id}
                    {date ? ` · ${date.toLocaleString()}` : ""}
                  </p>
                  <div className="sh-tools-actions">
                    <Button
                      variant="secondary"
                      aria-expanded={detail?.open ?? false}
                      aria-controls={`tools-snapshot-${id}-tracks`}
                      busy={detail?.busy ?? false}
                      onClick={() => {
                        if (detail?.open) t.closeSnapshot(id);
                        else void t.viewSnapshot(id);
                      }}
                    >
                      {detail?.open
                        ? "Hide tracks"
                        : detail?.error
                          ? "Retry viewing tracks"
                          : "View tracks"}
                    </Button>
                    <Button
                      variant="secondary"
                      busy={preparing === id}
                      aria-disabled={
                        !restorePermission.allowed || preparing !== null
                      }
                      aria-describedby={
                        !restorePermission.allowed
                          ? `tools-snapshot-${id}-restore-rights`
                          : undefined
                      }
                      onClick={() => {
                        void restore(id);
                      }}
                    >
                      Restore now
                    </Button>
                    <Button
                      variant="danger"
                      aria-disabled={
                        !deletePermission.allowed ||
                        s.busy ||
                        s.deleteUnknown === id ||
                        wait > 0
                      }
                      aria-describedby={
                        !deletePermission.allowed
                          ? `tools-snapshot-${id}-delete-rights`
                          : undefined
                      }
                      onClick={() => {
                        void remove(id);
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                  {!restorePermission.allowed && (
                    <p
                      id={`tools-snapshot-${id}-restore-rights`}
                      className="sh-tools-muted"
                    >
                      {restorePermission.reason}
                    </p>
                  )}
                  {!deletePermission.allowed && (
                    <p
                      id={`tools-snapshot-${id}-delete-rights`}
                      className="sh-tools-muted"
                    >
                      {deletePermission.reason}
                    </p>
                  )}
                  <div
                    id={`tools-snapshot-${id}-tracks`}
                    hidden={!detail?.open}
                  >
                    {detail?.busy && <p>Loading snapshot tracks…</p>}
                    {detail?.error && (
                      <ToolFeedback
                        value={{
                          error: detail.error,
                          message: "",
                          retryUntil: 0,
                        }}
                        id={`snapshot-view-${id}`}
                      />
                    )}
                    {detail?.value &&
                      (detail.value.track_uris.length ? (
                        <ol className="sh-tools-snapshot-tracks">
                          {detail.value.track_uris.map((uri, index) => {
                            const track = t.knownTrack(uri);
                            return (
                              <li key={`${uri}-${index}`}>
                                <span>{track?.name ?? uri}</span>
                                {track && (
                                  <small>
                                    {track.artists.filter(Boolean).join(", ") ||
                                      "Artist unavailable"}
                                  </small>
                                )}
                              </li>
                            );
                          })}
                        </ol>
                      ) : (
                        <p>
                          This snapshot is empty. Restoring it removes every
                          track from the Spotify playlist.
                        </p>
                      ))}
                  </div>
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </div>
  );
}
