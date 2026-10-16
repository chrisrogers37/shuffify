import { useEffect, useRef, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import { useSession } from "../../../auth/SessionProvider";
import { useConfirm } from "../../../components/tasks/ConfirmDialog";
import { Link } from "react-router";
import { Button } from "../../../components/ui/controls";
import { useRetry } from "../tools/hooks";
import { useAnnouncer } from "../../../a11y/Announcer";
import type { WorkshopController } from "../core/controller";
import { useWorkshop } from "../workspace";
import { getSourcesController } from "./controller";
import type { QueuedAction } from "./types";
import { OperationRestoration } from "../../../components/results/OperationRestoration";
import { OperationView } from "../../routines/OperationView";
import { record, playlistId } from "./model";
export function SourceActor({
  controller: c,
  children,
}: {
  controller: WorkshopController;
  children: ReactNode;
}) {
  const session = useSession();
  return session.status === "ready" &&
    session.session.signed_in &&
    session.session.user.id === c.actor ? (
    <>{children}</>
  ) : (
    <p>
      Confirm the original signed-in account before viewing or changing this
      retained source task.
    </p>
  );
}
export function useSources(c: WorkshopController) {
  const t = getSourcesController(c),
    state = useSyncExternalStore(t.subscribe, t.getSnapshot, t.getSnapshot),
    workshop = useWorkshop(c);
  return { t, state, workshop };
}
let announcementSequence = 0;
export function SourceAnnouncements({
  controller: c,
}: {
  controller: WorkshopController;
}) {
  const { state } = useSources(c),
    announce = useAnnouncer(),
    last = useRef("");
  const error =
    state.error ||
    [
      state.pair,
      state.status,
      state.pending,
      state.archive,
      state.owned,
      state.routines,
      ...Object.values(state.histories),
    ].find((x) => x.phase === "error")?.error ||
    state.actions.at(-1)?.error ||
    "";
  const message = error || state.message;
  useEffect(() => {
    if (message && message !== last.current)
      announce(
        message,
        `source-${c.actor}-${c.playlistId}-${++announcementSequence}`,
        error ? "assertive" : "polite",
      );
    last.current = message;
  }, [message, error, announce, c]);
  return null;
}
export function SourceFeedback({
  controller: c,
}: {
  controller: WorkshopController;
}) {
  const { t, state } = useSources(c),
    wait = useRetry(state.retryUntil);
  return (
    <>
      {state.error && <p className="sh-sources-error">{state.error}</p>}
      {state.message && <p>{state.message}</p>}
      {wait > 0 && (
        <p>
          Wait {wait} {wait === 1 ? "second" : "seconds"} before another
          request.
        </p>
      )}
      {state.configurationUnknown && (
        <Button
          variant="secondary"
          busy={state.busy}
          aria-disabled={wait > 0}
          onClick={() => {
            void t.refreshConfiguration();
          }}
        >
          Read current settings
        </Button>
      )}
    </>
  );
}
export function readMessage(
  value: { phase: string; error: string; retryUntil: number },
  retry: () => void,
  label: string,
) {
  return <ReadFeedback value={value} retry={retry} label={label} />;
}
function ReadFeedback({
  value,
  retry,
  label,
}: {
  value: { phase: string; error: string; retryUntil: number };
  retry: () => void;
  label: string;
}) {
  const wait = useRetry(value.retryUntil);
  return (
    <>
      {value.phase === "loading" && <p role="status">Loading {label}…</p>}
      {value.error && (
        <>
          <p className="sh-sources-error">{value.error}</p>
          <Button variant="secondary" aria-disabled={wait > 0} onClick={retry}>
            Retry {label}
          </Button>
        </>
      )}
    </>
  );
}
export function ActionReceipt({
  controller: c,
  action,
}: {
  controller: WorkshopController;
  action: QueuedAction;
}) {
  const { t } = useSources(c),
    retryWait = useRetry(action.retryUntil),
    confirm = useConfirm(),
    ack = t.creationAcknowledgement(action.id);
  return (
    <section className="sh-sources-receipt" aria-label={action.label}>
      <h4>{action.label}</h4>
      <p>{action.phase.replaceAll("_", " ")}</p>
      {action.error && <p className="sh-sources-error">{action.error}</p>}
      {action.phase === "unknown" && (
        <>
          <p>
            The original request may already be accepted. Keep its identity;
            this check reuses its exact key and body.
          </p>
          <Button
            variant="secondary"
            aria-disabled={!c.permission("pause").allowed || retryWait > 0}
            onClick={() => {
              void t.submit(action.id, true);
            }}
          >
            Check the same request
          </Button>
        </>
      )}
      {action.blockingOperationId && (
        <p>
          Another operation #{action.blockingOperationId} blocked this action.
          It is not this action’s receipt.{" "}
          <Link to={`/activity#run-${action.blockingOperationId}`}>
            Inspect blocking operation
          </Link>
        </p>
      )}
      {action.operationId && (
        <>
          <Link to={`/activity#run-${action.operationId}`}>
            Open operation #{action.operationId} in Activity
          </Link>
          <Button
            variant="secondary"
            aria-disabled={!c.permission("pause").allowed}
            onClick={() => t.retryStatus(action.operationId!)}
          >
            Check operation #{action.operationId}
          </Button>
        </>
      )}
      {action.operation && <OperationView operation={action.operation} />}
      <ResultFacts facts={action.result} />
      {action.operation && (
        <OperationRestoration
          operation={action.operation}
          requestKey={action.key}
          actor={c.actor}
        />
      )}
      {t.creationAcknowledged(action.id) && (
        <>
          <p>
            Orphan record #{action.operationId} retained. No playlist was
            adopted.
          </p>
          <Button
            variant="secondary"
            onClick={() => {
              void t.createAsset(
                action.kind === "pair_create" ? "archive" : "collection",
                true,
              );
            }}
          >
            Create another {action.kind === "pair_create" ? "archive" : "raid"}{" "}
            playlist
          </Button>
        </>
      )}
      {ack && (
        <>
          <p>
            The original accepted attempt #{ack.operationId} ended without a
            known creation identity or target association. Its orphan record
            will remain.
          </p>
          <Button
            variant="secondary"
            onClick={async () => {
              if (
                await confirm.confirm({
                  title: "Acknowledge unidentified creation?",
                  message: `Operation #${ack.operationId} may have created an unlinked Spotify playlist. This does not prove absence or delete it. Keep that orphan record and allow a new explicit creation or exact owned selection?`,
                  confirmLabel: "Keep orphan record and continue",
                })
              )
                t.acknowledgeCreation(action.id, ack);
            }}
          >
            Acknowledge unidentified creation
          </Button>
        </>
      )}
    </section>
  );
}
export function ResultFacts({
  facts,
}: {
  facts: Record<string, unknown> | null;
}) {
  if (!facts) return null;
  const labels: Record<string, string> = {
    database_status: "Inbox / database",
    source_status: "Source playlist",
    association_status: "Playlist association",
    outcome: "Creation outcome",
    privacy_confirmed: "Privacy confirmed",
    creation_unknown: "Creation unknown",
    dismissed_count: "Pending tracks dismissed",
    source_removed_count: "Source occurrences removed",
    promoted_count: "Pending tracks promoted",
    restored_count: "Tracks restored",
    archived_count: "Tracks archived",
    affected_count: "Physical occurrences affected",
    error_code: "Result code",
  };
  const known =
    record(facts.playlist) && playlistId(facts.playlist.id)
      ? facts.playlist
      : null;
  return (
    <>
      {known && (
        <p>
          Known playlist:{" "}
          <a
            href={`https://open.spotify.com/playlist/${String(known.id)}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            {typeof known.name === "string" ? known.name : "Created playlist"} ·{" "}
            {String(known.id)}
          </a>
          . Its association and privacy still follow the authored result below.
        </p>
      )}
      <dl className="sh-sources-facts">
        {Object.entries(labels)
          .filter(([k]) => k in facts)
          .map(([k, label]) => (
            <div key={k}>
              <dt>{label}</dt>
              <dd>{facts[k] === null ? "Unknown" : String(facts[k])}</dd>
            </div>
          ))}
        {Array.isArray(facts.dismissed_ids) && (
          <div>
            <dt>Confirmed pending IDs</dt>
            <dd>{facts.dismissed_ids.join(", ") || "None"}</dd>
          </div>
        )}
      </dl>
    </>
  );
}
export function Continuations({
  controller: c,
}: {
  controller: WorkshopController;
}) {
  const { t, state } = useSources(c);
  if (!state.retained.length) return null;
  return (
    <section
      className="sh-sources-stack"
      aria-labelledby="saved-source-heading"
    >
      <h3 id="saved-source-heading">Saved source work</h3>
      <p>
        The target save remains recorded. These steps affect only their captured
        source and inbox, never undoing that target save.
      </p>
      {state.retained.map((item) => (
        <article key={item.task.id} className="sh-sources-card">
          <h4>{item.task.label}</h4>
          <p>
            {item.task.source?.name ?? "Review inbox"} ·{" "}
            {item.task.source?.id ?? "No linked collection"}
          </p>
          <p>
            {item.task.selectedCount} selected · {item.phase}
          </p>
          <Link to={`/activity#run-${item.targetOperationId}`}>
            Target save #{item.targetOperationId}
          </Link>
          {item.result && (
            <>
              <p>{item.result.message}</p>
              <ResultFacts facts={item.result.facts} />
            </>
          )}
          {item.phase === "ready" && (
            <Button
              onClick={() => {
                void t.runTask(item.task.id);
              }}
            >
              Run reviewed cleanup
            </Button>
          )}
          {t.canReviewRetry(item.task.id) && (
            <Button
              variant="secondary"
              busy={state.busy}
              onClick={() => {
                void t.reviewRetry(item.task.id);
              }}
            >
              Review failed cleanup again
            </Button>
          )}
          {!["ready", "complete"].includes(item.phase) && (
            <Button
              variant="secondary"
              onClick={() => {
                void t.inspect(item.task.id);
              }}
            >
              Inspect captured source
            </Button>
          )}
          {item.inspection && (
            <section aria-label="Separate source inspection">
              <h5>Current inspection</h5>
              <p>{item.inspection.message}</p>
              <p>
                This observation does not replace the saved operation receipt or
                prove previous attribution.
              </p>
              <ResultFacts facts={item.inspection.facts} />
            </section>
          )}
          {state.actions
            .filter((x) => x.id === item.task.id)
            .map((action) => (
              <ActionReceipt key={action.id} controller={c} action={action} />
            ))}
        </article>
      ))}
    </section>
  );
}
export function useRowReturn() {
  const fallback = useRef<HTMLHeadingElement>(null);
  return {
    fallback,
    after(trigger: HTMLElement, attribute: string, selector: string) {
      const row = trigger.closest(`[${attribute}]`),
        siblings = row?.parentElement
          ? Array.from(row.parentElement.children)
          : [],
        index = row ? siblings.indexOf(row) : -1,
        next = siblings[index + 1] ?? siblings[index - 1],
        id = next?.getAttribute(attribute);
      return () =>
        requestAnimationFrame(() => {
          const target = id
            ? Array.from(document.querySelectorAll(`[${attribute}]`))
                .find((x) => x.getAttribute(attribute) === id)
                ?.querySelector<HTMLElement>(selector)
            : null;
          (target ?? fallback.current)?.focus();
        });
    },
  };
}
export function useInitial(load: () => void, active: boolean) {
  useEffect(() => {
    if (active) load();
  }, [active, load]);
}
