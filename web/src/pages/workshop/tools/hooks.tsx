import { Link } from "react-router";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useAnnouncer } from "../../../a11y/Announcer";
import { useWorkshop } from "../workspace";
import { getToolsController } from "./controller";
import type { WorkshopController } from "../core/controller";
export function useTools(core: WorkshopController) {
  const t = getToolsController(core);
  const state = useSyncExternalStore(t.subscribe, t.getSnapshot, t.getSnapshot);
  const workshop = useWorkshop(core);
  return { t, state, workshop };
}
export function useRetry(until: number) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (until <= Date.now()) return;
    const timer = setTimeout(
      () => setNow(Date.now()),
      Math.min(1000, until - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [until, now]);
  return Math.max(0, Math.ceil((until - Math.max(now, Date.now())) / 1000));
}
let announcementSequence = 0;
export function ToolFeedback({
  value,
  id,
}: {
  value: {
    error: string;
    message: string;
    retryUntil: number;
    operationId?: number | null;
  };
  id: string;
}) {
  const announce = useAnnouncer(),
    last = useRef("");
  const wait = useRetry(value.retryUntil);
  useEffect(() => {
    const text = value.error || value.message;
    if (text && text !== last.current)
      announce(
        text,
        `${id}-${++announcementSequence}`,
        value.error ? "assertive" : "polite",
      );
    last.current = text;
  }, [value.error, value.message, announce, id]);
  return (
    <>
      {value.error && <p className="sh-tools-error">{value.error}</p>}
      {value.message && <p className="sh-tools-notice">{value.message}</p>}
      {value.operationId && (
        <p className="sh-tools-notice">
          Operation #{value.operationId} retains this resource.{" "}
          <Link
            to={`/activity#run-${value.operationId}`}
            className="sh-tools-operation-link"
          >
            Inspect this operation in Activity
          </Link>
        </p>
      )}
      {wait > 0 && (
        <p className="sh-tools-muted">
          Wait {wait} {wait === 1 ? "second" : "seconds"} before trying this
          request again.
        </p>
      )}
    </>
  );
}
