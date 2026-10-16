import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { OperationRestoreResource } from "../../api/contracts";
import { useSession } from "../../auth/SessionProvider";
import { useAnnouncer } from "../../a11y/Announcer";
import { useConfirm } from "../tasks/ConfirmDialog";
import { Button, Toggle } from "../ui/controls";
import { State } from "../ui/States";
import type { RestorationController } from "../../operations/restoration/controller";
import { useRestoration } from "../../operations/restoration/hooks";
import { useClock } from "../../pages/library/hooks";
import "./restoration.css";

const count = (value: unknown) =>
  Number.isSafeInteger(value) && Number(value) >= 0
    ? String(value)
    : "Unconfirmed";
const text = (value: unknown) =>
  value === true
    ? "Public"
    : value === false
      ? "Private"
      : value === null
        ? "Unavailable"
        : String(value);
function Resource({ resource }: { resource: OperationRestoreResource }) {
  return (
    <article className="sh-restoration-resource">
      <h4>{resource.name || resource.playlist_id}</h4>
      <p>
        {resource.role === "guard"
          ? "Checked only · no changes will be made here."
          : "Reviewed changes will be restored here."}
      </p>
      {resource.scope === "tracks" && (
        <>
          <dl className="sh-restoration-counts">
            <div>
              <dt>Current tracks</dt>
              <dd>{count(resource.current_count)}</dd>
            </div>
            <div>
              <dt>Desired tracks</dt>
              <dd>{count(resource.desired_count)}</dd>
            </div>
          </dl>
          <div className="sh-restoration-comparison">
            <section>
              <h5>Current ordered preview</h5>
              <ol aria-label="Current ordered preview">
                {resource.current_preview.map((uri, index) => (
                  <li key={index}>{uri}</li>
                ))}
              </ol>
              {resource.current_count === 0 && <p>Empty playlist.</p>}
            </section>
            <section>
              <h5>Desired ordered preview</h5>
              <ol aria-label="Desired ordered preview">
                {resource.desired_preview.map((uri, index) => (
                  <li key={index}>{uri}</li>
                ))}
              </ol>
              {resource.desired_count === 0 && <p>Empty playlist.</p>}
            </section>
          </div>
          <p className="sh-caption">
            Up to 20 ordered track references are shown. Repeated references
            represent separate occurrences.
          </p>
        </>
      )}
      {Object.keys(resource.desired_metadata).length > 0 && (
        <dl className="sh-restoration-metadata">
          {Object.entries(resource.desired_metadata).map(([field, value]) => (
            <div key={field}>
              <dt>
                {field === "public"
                  ? "Visibility"
                  : field === "name"
                    ? "Name"
                    : "Description"}
              </dt>
              <dd>
                Current:{" "}
                {text(
                  resource.current_metadata[
                    field as keyof typeof resource.current_metadata
                  ],
                )}
              </dd>
              <dd>Desired: {text(value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {resource.metadata_truncated && (
        <p>
          Long metadata is shortened in this preview. The complete reviewed
          values are retained.
        </p>
      )}
    </article>
  );
}

export function RestorationPanel({
  controller: c,
}: {
  controller: RestorationController;
}) {
  const state = useRestoration(c),
    session = useSession(),
    announce = useAnnouncer(),
    { confirm } = useConfirm();
  const [acknowledged, setAcknowledged] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null),
    submit = useRef<HTMLButtonElement>(null),
    transition = useRef(false);
  const now = useClock(state.retryUntil),
    wait = Math.max(0, Math.ceil((state.retryUntil - now) / 1000));
  const signed =
    session.status === "ready" &&
    session.session.signed_in &&
    session.session.user.id === c.actor;
  useEffect(() => setAcknowledged(false), [state.context]);
  useEffect(() => {
    if (!signed) return;
    const message = state.error || state.message;
    if (message)
      announce(
        message,
        `restore-${c.actor}-${c.parentId}-${state.phase}-${message}`,
        state.error ? "assertive" : "polite",
      );
  }, [signed, state.error, state.message, state.phase, c, announce]);
  useLayoutEffect(() => {
    if (!transition.current || state.phase === "submitting") return;
    if (
      document.activeElement === submit.current ||
      document.activeElement === document.body
    )
      heading.current?.focus();
    transition.current = false;
  }, [state.phase]);
  if (!signed)
    return (
      <State kind="error" title="Confirm the original account">
        The retained restoration is hidden until its original account is
        confirmed.
      </State>
    );
  const child = state.child,
    ready = state.phase === "ready";
  return (
    <div className="sh-restoration-stack">
      <h3 ref={heading} tabIndex={-1}>
        {ready
          ? "Review restoration"
          : state.phase === "resolved"
            ? "Restoration confirmed"
            : "Restoration request"}
      </h3>
      <p>
        Original operation #{c.parentId}. Previously accepted steps and checked
        resources stay protected.
      </p>
      {state.phase === "loading" && (
        <State
          kind="loading"
          title="Reading the owned operation and current playlists…"
        />
      )}
      {state.message && <p role="status">{state.message}</p>}
      {state.error && (
        <p className="sh-restoration-error" role="alert">
          {state.error}
        </p>
      )}
      {wait > 0 && (
        <p>
          Wait {wait} {wait === 1 ? "second" : "seconds"} before checking or
          retrying.
        </p>
      )}
      {state.context && (
        <section
          className="sh-restoration-stack"
          aria-label="Reviewed restoration comparison"
        >
          <p>
            Spotify is checked again before any change. This comparison remains
            the one you reviewed for this request.
          </p>
          {state.context.resources.map((resource) => (
            <Resource key={resource.playlist_id} resource={resource} />
          ))}
        </section>
      )}
      {ready && (
        <>
          <Toggle
            label="I have reviewed every playlist in this restoration."
            checked={acknowledged}
            onChange={setAcknowledged}
          />
          <Button
            ref={submit}
            aria-disabled={!acknowledged || wait > 0}
            onClick={() => {
              transition.current = true;
              void c.confirm();
            }}
          >
            Restore reviewed changes
          </Button>
        </>
      )}
      {state.phase === "submitting" && (
        <p>
          Submitting the retained review. Closing this panel does not cancel it.
        </p>
      )}
      {state.phase === "unknown" && (
        <>
          <p>
            This request may already be accepted. Its exact reviewed comparison
            and request key are retained.
          </p>
          <Button
            variant="secondary"
            aria-disabled={wait > 0}
            onClick={async () => {
              if (
                await confirm({
                  title: "Retry this exact restoration request?",
                  message:
                    "The earlier request may already be accepted. Use the same reviewed comparison and request key to check or reuse that operation.",
                  confirmLabel: "Retry exact request",
                })
              )
                void c.retryExact();
            }}
          >
            Retry this exact request
          </Button>
        </>
      )}
      {child && (
        <section
          className="sh-restoration-stack"
          aria-label="Accepted restoration result"
        >
          <h4>Restoration operation #{child.id}</h4>
          <p>{child.state.replaceAll("_", " ")}</p>
          <dl className="sh-restoration-counts">
            <div>
              <dt>Resources restored</dt>
              <dd>{count(child.result?.restored_resource_count)}</dd>
            </div>
            <div>
              <dt>Track positions changed</dt>
              <dd>{count(child.result?.changed_position_count)}</dd>
            </div>
          </dl>
          {state.phase !== "resolved" && (
            <Button
              variant="secondary"
              aria-disabled={wait > 0}
              onClick={() => c.observeChild()}
            >
              Check restoration status
            </Button>
          )}
        </section>
      )}
      {state.relatedChildId && state.relatedChildId !== child?.id && (
        <p>
          Related restoration operation #{state.relatedChildId} needs review.
          This request remains unconfirmed.
        </p>
      )}
      {c.canReview() && !ready && (
        <Button
          variant="secondary"
          aria-disabled={wait > 0}
          onClick={() => void c.loadReview()}
        >
          Load a fresh review
        </Button>
      )}
      {state.attempts.length > 1 && (
        <section aria-label="Retained restoration attempts">
          <h4>Earlier requests</h4>
          <ul>
            {state.attempts.slice(0, -1).map((attempt, index) => (
              <li key={attempt.key}>
                {attempt.operationId
                  ? `Operation #${attempt.operationId} · ${attempt.operation?.state.replaceAll("_", " ") ?? "Status unavailable"}`
                  : `Request ${index + 1} · admission unconfirmed`}
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="sh-caption">
        You can close this panel. The original request, accepted operation IDs
        and unresolved resource claims remain in this document.
      </p>
    </div>
  );
}
