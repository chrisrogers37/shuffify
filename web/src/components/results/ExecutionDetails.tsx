import { Link } from "react-router";
import type { AvailableExecutionDetail } from "../../api/contracts";
import {
  bookkeeping,
  bookkeepingStatus,
  countLines,
  factStatus,
  playlistHref,
  recordStatus,
  restoreStatus,
  roles,
  steps,
  stepStatus,
} from "../../pages/activity/model";
/** Bounded verified facts, shared by Activity and the later routine History port. */
export function ExecutionDetails({
  detail,
}: {
  detail: AvailableExecutionDetail;
}) {
  return (
    <div className="sh-result">
      <ol className="sh-result-steps">
        {detail.steps.map((step, index) => (
          <li className="sh-result-step" key={`${index}-${step.step}`}>
            <p className="sh-result-step-heading">
              {steps[step.step]} — {stepStatus[step.status]}
            </p>
            {countLines(step).length > 0 && (
              <ul className="sh-result-counts">
                {countLines(step).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
            {step.resources.length > 0 && (
              <ul className="sh-result-resources">
                {step.resources.map((fact, i) => (
                  <li key={i}>
                    {fact.phase === "archive_purge"
                      ? "Archive cleanup"
                      : fact.kind
                        ? roles[fact.kind]
                        : "Playlist resource"}
                    : {factStatus[fact.status]}
                  </li>
                ))}
              </ul>
            )}
            {step.bookkeeping.length > 0 && (
              <ul className="sh-result-bookkeeping">
                {step.bookkeeping.map((fact, i) => (
                  <li key={i}>
                    {bookkeeping[fact.kind]}:{" "}
                    {
                      (fact.kind === "pending_restore"
                        ? restoreStatus
                        : fact.kind === "execution_record"
                          ? recordStatus
                          : bookkeepingStatus)[fact.status]
                    }
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
      {detail.execution_record === "unknown" && (
        <p className="sh-result-record-warning">
          Run history could not be verified.
        </p>
      )}
      {detail.resource_ids && Object.keys(detail.resource_ids).length > 0 && (
        <nav
          className="sh-activity-resource-links"
          aria-label="Run playlist links"
        >
          {(Object.keys(roles) as (keyof typeof roles)[]).map((role) => {
            const href = playlistHref(detail.resource_ids?.[role] ?? null);
            return href ? (
              <Link key={role} to={href}>
                {roles[role]}
              </Link>
            ) : null;
          })}
        </nav>
      )}
    </div>
  );
}
