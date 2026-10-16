import { useClock } from "../library/hooks";
import { useEffect, useState } from "react";
import { Button, Field, Input } from "../../components/ui/controls";
import { useConfirm } from "../../components/tasks/ConfirmDialog";
import { useWorkshop } from "./workspace";
import { ActorTask } from "./ReviewPanel";
import type { WorkshopController } from "./core/controller";
export function NamedDrafts({
  controller: c,
}: {
  controller: WorkshopController;
}) {
  const s = useWorkshop(c),
    now = useClock(s.retryUntil),
    [retained] = useState(() =>
      c.retainTool("named-drafts", () => ({ name: "" })),
    ),
    [name, setName] = useState(retained.name),
    { confirm } = useConfirm();
  useEffect(() => {
    void c.loadDrafts();
  }, [c]);
  return (
    <ActorTask actor={c.actor}>
      <div className="sh-workshop-panel-stack">
        <p>
          Keep a named copy of this draft order. These actions do not change
          Spotify.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void c.saveDraft(name);
          }}
        >
          <Field
            id="workshop-draft-name"
            label="Draft name"
            error={s.draftsError}
          >
            {(props) => (
              <Input
                {...props}
                maxLength={255}
                value={name}
                onChange={(e) => {
                  retained.name = e.currentTarget.value;
                  setName(retained.name);
                }}
              />
            )}
          </Field>
          <Button
            type="submit"
            busy={s.draftsBusy}
            disabled={
              now < s.retryUntil ||
              s.draftsUnknown ||
              !c.permission("history").allowed
            }
          >
            Save named draft
          </Button>
        </form>
        {s.draftsUnknown && (
          <p role="status">
            A local save or deletion is unconfirmed. Refresh the list to
            reconcile it before sending another change.
          </p>
        )}
        <Button
          variant="secondary"
          busy={s.draftsBusy}
          disabled={now < s.retryUntil}
          onClick={() => void c.loadDrafts()}
        >
          Refresh saved drafts
        </Button>
        {s.draftsLoaded && !s.drafts.length && <p>No named drafts yet.</p>}
        <ul className="sh-workshop-drafts">
          {s.drafts.map((d) => (
            <li key={d.id}>
              <h3>{d.session_name}</h3>
              <p>
                {d.track_count} tracks ·{" "}
                {d.updated_at ?? d.created_at ?? "Date unavailable"}
              </p>
              <div className="sh-workshop-action-buttons">
                <Button
                  variant="secondary"
                  disabled={
                    now < s.retryUntil ||
                    s.draftsBusy ||
                    !c.permission("draft").allowed
                  }
                  onClick={async () => {
                    if (
                      c.hasDraft() &&
                      !(await confirm({
                        title: "Load saved draft?",
                        message:
                          "Replace the local draft and return uncommitted source staging. The confirmed Spotify baseline stays unchanged.",
                        confirmLabel: "Load saved draft",
                        danger: true,
                      }))
                    )
                      return;
                    void c.loadNamedDraft(d.id, true);
                  }}
                >
                  Load {d.session_name}
                </Button>
                <Button
                  variant="secondary"
                  disabled={
                    now < s.retryUntil ||
                    s.draftsBusy ||
                    s.draftsUnknown ||
                    !c.permission("history").allowed
                  }
                  onClick={async () => {
                    if (
                      await confirm({
                        title: "Update saved draft?",
                        message: `Replace “${d.session_name}” with the complete current local order, including repeated tracks.`,
                        confirmLabel: "Update draft",
                      })
                    )
                      void c.saveDraft(d.session_name, d.id);
                  }}
                >
                  Update {d.session_name}
                </Button>
                <Button
                  variant="danger"
                  disabled={
                    now < s.retryUntil ||
                    s.draftsBusy ||
                    s.draftsUnknown ||
                    !c.permission("history").allowed
                  }
                  onClick={async () => {
                    if (
                      await confirm({
                        title: "Delete saved draft?",
                        message: `Delete “${d.session_name}”. Spotify and the current local draft will not change.`,
                        confirmLabel: "Delete draft",
                        danger: true,
                      })
                    )
                      void c.deleteDraft(d.id);
                  }}
                >
                  Delete {d.session_name}
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </ActorTask>
  );
}
