import { useLayoutEffect, useRef, useSyncExternalStore } from "react";
import { Link } from "react-router";
import type { Operation } from "../../api/contracts";
import { useSession } from "../../auth/SessionProvider";
import { Button } from "../../components/ui/controls";
import { State } from "../../components/ui/States";
import { useTasks } from "../../components/tasks/TaskPanel";
import type { LibraryController } from "./controller";
import { resultFacts, record } from "./api";
import { OperationRestoration } from "../../components/results/OperationRestoration";
import { useLibrary, useClock } from "./hooks";
function Facts({ operation }: { operation: Operation }) {
  const facts = resultFacts(operation.result);
  return (
    <div className="sh-library-facts">
      <p>{facts.message}</p>
      {facts.resources.length > 0 && (
        <>
          <h3>Spotify resources</h3>
          <ul>
            {facts.resources.map((fact, index) => (
              <li key={index}>
                <strong>{fact.name}</strong>: {fact.status.replaceAll("_", " ")}
                {fact.count !== null
                  ? ` · ${fact.count} tracks affected`
                  : " · affected count unconfirmed"}
                {fact.verification
                  ? ` · ${fact.verification} verification`
                  : ""}
                {fact.error ? ` · ${fact.error.replaceAll("_", " ")}` : ""}
              </li>
            ))}
          </ul>
        </>
      )}
      {facts.bookkeeping.length > 0 && (
        <>
          <h3>App records</h3>
          <ul>
            {facts.bookkeeping.map((fact, index) => (
              <li key={index}>
                {fact.name}: {fact.status.replaceAll("_", " ")}
                {fact.error ? ` · ${fact.error.replaceAll("_", " ")}` : ""}
              </li>
            ))}
          </ul>
        </>
      )}
      {operation.recovery.reason && <p>{operation.recovery.reason}</p>}
      <details>
        <summary>Exact retained operation details</summary>
        <pre>{JSON.stringify(operation, null, 2)}</pre>
      </details>
    </div>
  );
}
export function ReviewPanel({
  controller: c,
  receiptId,
}: {
  controller: LibraryController;
  receiptId: number;
}) {
  const s = useLibrary(c),
    observed = useSyncExternalStore(
      c.operations.subscribe,
      c.operations.getSnapshot,
      c.operations.getSnapshot,
    ),
    r = s.receipts.get(receiptId),
    tasks = useTasks(),
    session = useSession(),
    now = useClock(Math.max(r?.retryUntil ?? 0, r?.inspectionRetryUntil ?? 0));
  const signed =
    session.status === "ready" &&
    session.session.signed_in &&
    session.session.user.id === c.actor;
  const run = useRef<HTMLButtonElement>(null),
    resultHeading = useRef<HTMLHeadingElement>(null),
    errorRegion = useRef<HTMLDivElement>(null),
    transitionFocus = useRef(false);
  useLayoutEffect(() => {
    if (!signed) {
      transitionFocus.current = false;
      return;
    }
    if (
      !transitionFocus.current ||
      !r ||
      ["loading", "sending"].includes(r.status)
    )
      return;
    if (
      document.activeElement === document.body ||
      document.activeElement === run.current
    ) {
      (r.status === "ready"
        ? run.current
        : r.status === "accepted"
          ? resultHeading.current
          : errorRegion.current
      )?.focus({ preventScroll: true });
    }
    transitionFocus.current = false;
  }, [signed, r?.status, r?.operationId]);
  if (!signed)
    return (
      <State kind="empty" title="Confirm the original session">
        <p>
          This retained task belongs to another or unconfirmed account. Its
          details remain private until the original account is confirmed. Close
          this panel to continue.
        </p>
      </State>
    );
  if (!r) return <p>This review is no longer available.</p>;
  const id = r.operationId ?? r.busyId,
    entry = id === null ? null : observed.get(id),
    op = entry?.operation,
    review = r.review,
    seconds = Math.max(0, Math.ceil((r.retryUntil - now) / 1000)),
    stale = c.stale(r);
  return (
    <div className="sh-library-task">
      <p className="sh-eyebrow">{r.name}</p>
      {r.status === "loading" ? (
        <State kind="loading" title="Reading the current playlist…" />
      ) : (
        <>
          {review && (
            <section aria-label="Fresh action review">
              <p>
                Review read at{" "}
                <time dateTime={review.current.loaded_at}>
                  {new Date(review.current.loaded_at).toLocaleTimeString()}
                </time>
                .
                {r.status === "ready"
                  ? " Nothing changes until you choose Run."
                  : ""}
              </p>
              <h3>{review.label}</h3>
              {review.kind === "shuffle" ? (
                <>
                  <p>
                    {review.current.track_count} current tracks ·{" "}
                    {review.action.algorithm.name}
                  </p>
                  <dl>
                    {Object.entries(review.action.params).map(([k, v]) => (
                      <div key={k}>
                        <dt>{k.replaceAll("_", " ")}</dt>
                        <dd>{v}</dd>
                      </div>
                    ))}
                  </dl>
                </>
              ) : review.kind === "privacy" ? (
                <p>
                  Change from {review.current.public ? "public" : "private"} to{" "}
                  <strong>{review.action.public ? "public" : "private"}</strong>
                  .
                </p>
              ) : (
                <>
                  <p>
                    Restore saved version {review.action.selected_index + 1}{" "}
                    from version {review.action.current_index + 1}.
                  </p>
                  <p>
                    {review.action.summary.added_count} added ·{" "}
                    {review.action.summary.removed_count} removed · order{" "}
                    {review.action.summary.order_changed
                      ? "changes"
                      : "unchanged"}
                  </p>
                </>
              )}
              <ul>
                {review.resources.map((resource, index) => (
                  <li key={index}>
                    {resource.resource_name}: Spotify{" "}
                    {resource.mutation_type.replaceAll("_", " ")} write
                    {resource.field ? ` (${resource.field})` : ""}
                  </li>
                ))}
              </ul>
              <p>
                {review.required_bookkeeping.length
                  ? "Saved playlist history must also be confirmed."
                  : "No saved playlist history change is required."}
              </p>
            </section>
          )}
          {stale && r.status === "ready" && (
            <p role="alert">
              Your shuffle options changed after this review. Close and review
              the new options before running.
            </p>
          )}
          {r.error && (
            <div role="alert" tabIndex={-1} ref={errorRegion}>
              <p>{r.error}</p>
              {Array.isArray(r.rejection?.field_errors) && (
                <ul>
                  {r.rejection.field_errors.map((field, index) =>
                    record(field) ? (
                      <li key={index}>
                        {typeof field.path === "string" ? field.path : "Option"}
                        :{" "}
                        {typeof field.message === "string"
                          ? field.message
                          : "Check this value."}
                      </li>
                    ) : null,
                  )}
                </ul>
              )}
              {seconds > 0 && (
                <p>Wait {seconds} seconds before trying again.</p>
              )}
            </div>
          )}
          {r.rejection && (
            <details>
              <summary>
                {r.status === "unknown"
                  ? "Exact response details"
                  : "Exact refusal details"}
              </summary>
              <pre>{JSON.stringify(r.rejection, null, 2)}</pre>
            </details>
          )}
          {(r.status === "ready" || r.status === "sending") && (
            <div className="sh-library-actions">
              <Button
                ref={run}
                busy={r.status === "sending"}
                aria-disabled={
                  stale || !signed || s.loading || c.blocked(r.playlistId)
                }
                onClick={() => {
                  transitionFocus.current = true;
                  void c.dispatch(r.id);
                }}
              >
                Run{" "}
                {r.intent.kind === "privacy"
                  ? "visibility change"
                  : r.intent.kind === "undo"
                    ? "restore"
                    : "shuffle"}
              </Button>
              {r.status === "ready" && (
                <Button
                  variant="secondary"
                  onClick={() => void tasks.closeTask()}
                >
                  Cancel
                </Button>
              )}
            </div>
          )}
          {r.status === "sending" && (
            <State kind="loading" title="Confirming queue admission…" />
          )}
          {r.status === "unknown" && (
            <>
              <p>
                Retry this exact request checks or reuses its operation using
                the original key. It never chooses a fresh shuffle. Do this only
                to reconcile this request.
              </p>
              <p className="sh-caption">
                Retained request key: <code>{r.key}</code>
              </p>
              <Button
                variant="secondary"
                aria-disabled={!signed || s.loading || seconds > 0}
                onClick={() => {
                  transitionFocus.current = true;
                  void c.dispatch(r.id, true);
                }}
              >
                Retry this exact request
              </Button>
              {!signed && (
                <p>
                  Confirm the same signed-in account before retrying. The
                  unknown details are retained.
                </p>
              )}
            </>
          )}
          {r.status === "refused" && id === null && (
            <Button
              variant="secondary"
              aria-disabled={seconds > 0 || !signed || c.blocked(r.playlistId)}
              onClick={() => {
                transitionFocus.current = true;
                void c.reviewAgain(r.id);
              }}
            >
              Review again
            </Button>
          )}
          {r.status === "cancelled" && (
            <p>This review was closed without dispatching a change.</p>
          )}
          {(r.inspections.length > 0 ||
            !!r.inspectionError ||
            r.status === "unknown" ||
            (id !== null &&
              (entry?.observation === "unavailable" ||
                (op &&
                  ["failed", "needs_attention", "rolled_back"].includes(
                    op.state,
                  ))))) && (
            <section aria-label="Current state inspection">
              <Button
                variant="secondary"
                busy={r.inspecting}
                aria-disabled={!signed || now < r.inspectionRetryUntil}
                onClick={() => void c.inspect(r.id)}
              >
                Inspect current state
              </Button>
              <p>
                This only reads the playlist. A match does not establish what
                caused it or confirm queue admission or saved history.
              </p>
              {r.inspectionError && <p role="alert">{r.inspectionError}</p>}
              {now < r.inspectionRetryUntil && (
                <p>
                  Wait {Math.ceil((r.inspectionRetryUntil - now) / 1000)}{" "}
                  seconds before inspecting again.
                </p>
              )}
              {r.inspections.length > 0 && (
                <ol>
                  {r.inspections.map((observation, index) => (
                    <li key={index}>
                      <p>
                        <strong>
                          Inspection result:{" "}
                          {observation.status.replaceAll("_", " ")}
                        </strong>{" "}
                        ·{" "}
                        <time dateTime={observation.checkedAt}>
                          {new Date(observation.checkedAt).toLocaleTimeString()}
                        </time>
                      </p>
                      <p>{observation.message}</p>
                      <details>
                        <summary>Exact inspection details {index + 1}</summary>
                        <pre>{JSON.stringify(observation.raw, null, 2)}</pre>
                      </details>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          )}
          {id !== null && (
            <section aria-label="Retained operation result">
              <h3 ref={resultHeading} tabIndex={-1}>
                Operation #{id}
              </h3>
              <p role="status">
                {entry?.observation === "unavailable"
                  ? "Status unavailable — the operation may still be running."
                  : op
                    ? op.state.replaceAll("_", " ")
                    : "Reading operation status…"}
              </p>
              {r.busyId !== null && (
                <p>
                  This playlist is busy. This is the existing operation, not a
                  new request.
                </p>
              )}
              {op?.progress.batches_done !== undefined && (
                <p>
                  {op.progress.batches_done} of{" "}
                  {op.progress.total_batches ?? "unknown"} batches{" "}
                  {op.progress.phase === "compensating"
                    ? "restored"
                    : "completed"}
                </p>
              )}
              {entry?.error && <p>{entry.error.message}</p>}
              {entry?.observation === "unavailable" && (
                <Button
                  variant="secondary"
                  aria-disabled={!signed}
                  onClick={() => {
                    c.operations.observe(id);
                    resultHeading.current?.focus({ preventScroll: true });
                  }}
                >
                  Retry status read
                </Button>
              )}
              {op?.result && <Facts operation={op} />}
              {op && r.operationId === op.id && (
                <OperationRestoration
                  operation={op}
                  requestKey={r.key}
                  actor={c.actor}
                />
              )}
              <Link
                className="sh-library-link"
                to={`/workshop/${encodeURIComponent(r.playlistId)}`}
              >
                Open {r.name} in Workshop
              </Link>
              <p>
                You can close this panel. Accepted work and its results remain
                available in this Library for this document.
              </p>
            </section>
          )}
        </>
      )}
    </div>
  );
}
