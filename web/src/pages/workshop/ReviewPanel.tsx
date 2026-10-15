import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { useSession } from "../../auth/SessionProvider";
import { Button } from "../../components/ui/controls";
import { State } from "../../components/ui/States";
import { useConfirm } from "../../components/tasks/ConfirmDialog";
import { useWorkshop } from "./workspace";
import type { WorkshopController } from "./core/controller";
import type { Occurrence } from "./core/types";
import { sameOrder, uris } from "./core/occurrences";
import { useClock } from "../library/hooks";
export function ActorTask({
  actor,
  children,
}: {
  actor: string;
  children: ReactNode;
}) {
  const s = useSession();
  return s.status === "ready" &&
    s.session.signed_in &&
    s.session.user.id === actor ? (
    <>{children}</>
  ) : (
    <State kind="error" title="Confirm the original account">
      The retained task is hidden until its original signed-in account is
      confirmed.
    </State>
  );
}
export function OrderView({
  title,
  rows,
}: {
  title: string;
  rows: readonly Occurrence[];
}) {
  return (
    <section className="sh-workshop-order">
      <h3>
        {title} · {rows.length}
      </h3>
      {rows.length ? (
        <ol>
          {rows.map((r, i) => (
            <li key={r.key}>
              <span>
                {i + 1}. {r.track.name}
              </span>
              <small>
                {r.track.artists.filter(Boolean).join(", ") ||
                  "Artist unavailable"}
                {r.track.added_at ? ` · Added ${r.track.added_at}` : ""}
              </small>
            </li>
          ))}
        </ol>
      ) : (
        <p>No tracks.</p>
      )}
    </section>
  );
}
export function ReviewPanel({
  controller: c,
}: {
  controller: WorkshopController;
}) {
  const s = useWorkshop(c),
    r = s.review,
    receipt = s.receipt,
    heading = useRef<HTMLHeadingElement>(null),
    run = useRef<HTMLButtonElement>(null),
    submitted = useRef(false),
    { confirm } = useConfirm(),
    [error, setError] = useState("");
  const now = useClock(Math.max(s.retryUntil, receipt?.retryUntil ?? 0));
  const relevant =
    receipt && (!r || receipt.review.id === r.id) ? receipt : null;
  useEffect(() => {
    if (
      submitted.current &&
      relevant?.status !== "submitting" &&
      (document.activeElement === run.current ||
        document.activeElement === document.body)
    ) {
      heading.current?.focus({ preventScroll: true });
      submitted.current = false;
    }
  }, [relevant?.status]);
  const canRun =
    !!r &&
    c.reviewIsCurrent() &&
    c.permission(r.kind === "privacy" ? "metadata" : "save").allowed &&
    !relevant;
  async function submit() {
    if (!r) return;
    submitted.current = true;
    setError("");
    try {
      await c.submitReviewed(r.id);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "This request could not be submitted.",
      );
    }
  }
  const current = s.observation;
  return (
    <ActorTask actor={c.actor}>
      <div className="sh-workshop-panel-stack">
        <h3 ref={heading} tabIndex={-1}>
          {relevant
            ? "Save result"
            : r?.kind === "privacy"
              ? "Review visibility"
              : r?.kind === "snapshot_restore"
                ? "Review snapshot restore"
                : "Reviewed order"}
        </h3>
        {r && !relevant && (
          <>
            <p>
              {r.kind === "commit"
                ? `Save the reviewed local draft to ${r.target.name}.`
                : r.kind === "snapshot_restore"
                  ? `Restore snapshot ${r.snapshotId} to ${r.target.name}. This changes Spotify now.`
                  : `Make ${r.target.name} ${r.public ? "public" : "private"}. This changes playlist metadata only.`}
            </p>
            {r.kind !== "privacy" && (
              <dl className="sh-workshop-summary">
                <div>
                  <dt>Added occurrences</dt>
                  <dd>{r.summary.added_count}</dd>
                </div>
                <div>
                  <dt>Removed occurrences</dt>
                  <dd>{r.summary.removed_count}</dd>
                </div>
                <div>
                  <dt>Shared order changes</dt>
                  <dd>{r.summary.order_changed ? "Yes" : "No"}</dd>
                </div>
              </dl>
            )}
            {r.kind !== "privacy" && (
              <div className="sh-workshop-compare">
                <OrderView
                  title={
                    r.kind === "snapshot_restore"
                      ? "Current at review"
                      : "Confirmed baseline"
                  }
                  rows={
                    r.kind === "snapshot_restore"
                      ? r.context.current.track_uris.map((uri, i) => ({
                          key: `review-current-${i}`,
                          uri,
                          track: c
                            .getSnapshot()
                            .working.find((row) => row.uri === uri)?.track ?? {
                            id: uri.split(":")[2]!,
                            uri,
                            name: "Saved track",
                            artists: [],
                            artist_urls: [],
                            duration_ms: null,
                            is_local: false,
                            added_at: null,
                            album_name: null,
                            album_image_url: null,
                            track_url: null,
                          },
                        }))
                      : r.baseline
                  }
                />
                <OrderView title="Reviewed desired order" rows={r.desired} />
              </div>
            )}
            {!c.reviewIsCurrent() && (
              <p role="status">
                The draft or configuration changed. This review cannot be sent.
                Close it and review again.
              </p>
            )}
            <Button ref={run} onClick={() => void submit()} disabled={!canRun}>
              {r.kind === "privacy"
                ? "Change visibility"
                : r.kind === "snapshot_restore"
                  ? "Restore snapshot now"
                  : "Save to Spotify"}
            </Button>
            <p className="sh-caption">
              The request uses this frozen order and original request key.
              Opening this review has changed nothing.
            </p>
          </>
        )}
        {relevant && (
          <section aria-label="Retained operation">
            <p role="status">{relevant.message}</p>
            <dl>
              <div>
                <dt>Request status</dt>
                <dd>{relevant.status}</dd>
              </div>
              {relevant.operationId && (
                <div>
                  <dt>Operation</dt>
                  <dd>
                    {relevant.operationId} ·{" "}
                    {relevant.operation?.state.replaceAll("_", " ") ??
                      "Status unavailable"}
                  </dd>
                </div>
              )}
              {relevant.result && (
                <>
                  <div>
                    <dt>Target</dt>
                    <dd>
                      {relevant.result.target_status.replaceAll("_", " ")}
                    </dd>
                  </div>
                  <div>
                    <dt>Confirmed batches</dt>
                    <dd>
                      {relevant.result.confirmed_batches} /{" "}
                      {relevant.result.total_batches}
                    </dd>
                  </div>
                </>
              )}
            </dl>
            {relevant.review.kind !== "privacy" && (
              <details className="sh-workshop-details">
                <summary>Reviewed request orders</summary>
                <OrderView
                  title="Reviewed baseline"
                  rows={relevant.review.baseline}
                />
                <OrderView
                  title="Reviewed desired order"
                  rows={relevant.review.desired}
                />
              </details>
            )}
            {relevant.status === "unknown" && (
              <>
                <p>
                  No new target save is safe until this receipt is reconciled.
                  Retrying this exact request checks or reuses its accepted
                  operation; it never selects a fresh action.
                </p>
                <Button
                  variant="secondary"
                  disabled={
                    now < relevant.retryUntil || !c.permission("pause").allowed
                  }
                  onClick={async () => {
                    if (
                      await confirm({
                        title: "Retry this exact request?",
                        message:
                          "Send the identical frozen request with its original key to check or reuse its accepted operation. No new shuffle or draft is selected.",
                        confirmLabel: "Retry exact request",
                      })
                    )
                      void c.retryExactRequest();
                  }}
                >
                  Retry this exact request
                </Button>
              </>
            )}
            {relevant.status === "refused" && (
              <Button
                variant="secondary"
                disabled={now < s.retryUntil}
                onClick={() =>
                  void c.prepareReview().catch((e) => setError(e.message))
                }
              >
                Review current draft again
              </Button>
            )}
            {relevant.operationId && (
              <Button variant="secondary" onClick={() => c.observeReceipt()}>
                Refresh operation status
              </Button>
            )}
            {relevant.operation && (
              <>
                <details className="sh-workshop-details">
                  <summary>Operation result details</summary>
                  <pre>
                    {JSON.stringify(relevant.operation.result, null, 2)}
                  </pre>
                </details>
                {relevant.operation.recovery.available && (
                  <p>
                    Recovery is available. Keep this operation ID and its
                    resource details for the recovery workflow.
                  </p>
                )}
              </>
            )}
          </section>
        )}
        {error && <p role="alert">{error}</p>}
        <section aria-labelledby="workshop-current-heading">
          <h3 id="workshop-current-heading">Compare with current Spotify</h3>
          <p>
            Inspection is read-only. A matching order does not prove that an
            unknown operation settled or that history was confirmed.
          </p>
          <Button
            variant="secondary"
            busy={s.checking}
            disabled={now < s.retryUntil}
            onClick={() => void c.checkCurrent()}
          >
            Check current Spotify
          </Button>
          {current && (
            <>
              <p>
                Loaded {current.loaded_at}.{" "}
                {sameOrder(current.track_uris, uris(s.working))
                  ? "Current order matches this draft."
                  : "Current order differs from this draft."}
              </p>
              <div className="sh-workshop-compare">
                <OrderView title="Local draft" rows={s.working} />
                <OrderView
                  title="Current Spotify"
                  rows={current.tracks.map((track, i) => ({
                    key: `current-${i}`,
                    uri: track.uri,
                    track,
                  }))}
                />
              </div>
              <Button
                variant="secondary"
                disabled={!c.permission("history").allowed}
                onClick={async () => {
                  if (
                    await confirm({
                      title: "Use current Spotify as baseline?",
                      message:
                        "Replace this local draft and return uncommitted source staging. This does not write Spotify or reset history. Saved or uncertain operation results stay retained.",
                      confirmLabel: "Use current order",
                      danger: true,
                    })
                  )
                    c.adoptCurrent(current.id);
                }}
              >
                Use current Spotify as baseline
              </Button>
            </>
          )}
          {!current && !s.checking && s.notice && (
            <p role="status">{s.notice}</p>
          )}
        </section>
      </div>
    </ActorTask>
  );
}
