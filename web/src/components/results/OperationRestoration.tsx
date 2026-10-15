import { useSyncExternalStore } from "react";
import type { Operation } from "../../api/contracts";
import { useServices, useSession } from "../../auth/SessionProvider";
import { Button } from "../ui/controls";
import { useTasks } from "../tasks/TaskPanel";
import { getAdmissionRegistry } from "../../operations/admissions";
import { getRestorationWorkspace } from "../../operations/restoration/controller";
import type { RestorationController } from "../../operations/restoration/controller";
import { RestorationPanel } from "./RestorationPanel";

function useOpenRestoration(actor: string) {
  const { api, session } = useServices(),
    tasks = useTasks();
  const workspace = getRestorationWorkspace(api, session, actor);
  const controllers = useSyncExternalStore(
    workspace.subscribe,
    workspace.getSnapshot,
    workspace.getSnapshot,
  );
  const open = (id: number, trigger: HTMLElement) => {
    const controller = workspace.get(id);
    void tasks.openTask(
      {
        id: `restoration-${actor}-${id}`,
        title: `Restore operation #${id}`,
        trigger,
        fallback: document.querySelector("#main-content h1"),
        scrollKey: `${actor}:restoration:${id}`,
        content: <RestorationPanel controller={controller} />,
      },
      "push",
    );
    if (controller.getSnapshot().phase === "idle") void controller.loadReview();
  };
  return { controllers, open };
}
interface OriginalProps {
  operation: Operation;
  requestKey: string;
  actor: string;
}
export function OperationRestoration(props: OriginalProps) {
  const session = useSession();
  return props.actor &&
    session.status === "ready" &&
    session.session.signed_in &&
    session.session.user.id === props.actor ? (
    <OriginalRestoration {...props} />
  ) : null;
}
function OriginalRestoration({ operation, requestKey, actor }: OriginalProps) {
  const { api } = useServices(),
    session = useSession(),
    { controllers, open } = useOpenRestoration(actor);
  const admissions = getAdmissionRegistry(api, actor);
  const claims = useSyncExternalStore(
    admissions.subscribe,
    admissions.getSnapshot,
    admissions.getSnapshot,
  );
  const original = claims.get(requestKey);
  const local = controllers.find(
    (controller) => controller.parentId === operation.id,
  );
  if (
    session.status !== "ready" ||
    !session.session.signed_in ||
    session.session.user.id !== actor ||
    original?.relation !== "original" ||
    original.operationId !== operation.id ||
    operation.kind === "operation_restore"
  )
    return null;
  if (
    !local &&
    !(
      operation.state === "needs_attention" &&
      operation.recovery.available &&
      operation.recovery.action === "restore"
    )
  )
    return null;
  return (
    <Button
      variant="secondary"
      onClick={(event) => open(operation.id, event.currentTarget)}
    >
      {local?.getSnapshot().attempts.length
        ? "View restoration request"
        : "Review restoration"}
    </Button>
  );
}
export function HistoricalOperationRestoration({
  operationId,
  actor,
}: {
  operationId: number;
  actor: string;
}) {
  const session = useSession();
  return actor &&
    Number.isSafeInteger(operationId) &&
    operationId > 0 &&
    session.status === "ready" &&
    session.session.signed_in &&
    session.session.user.id === actor ? (
    <HistoryEntry operationId={operationId} actor={actor} />
  ) : null;
}
function HistoryEntry({
  operationId,
  actor,
}: {
  operationId: number;
  actor: string;
}) {
  const { open } = useOpenRestoration(actor);
  return (
    <Button
      variant="secondary"
      onClick={(event) => open(operationId, event.currentTarget)}
    >
      View operation #{operationId}
    </Button>
  );
}
function Retained({ actor }: { actor: string }) {
  const { controllers, open } = useOpenRestoration(actor);
  const pending = controllers.filter((controller: RestorationController) => {
    const state = controller.getSnapshot();
    return state.attempts.length > 0 && state.phase !== "resolved";
  });
  if (!pending.length) return null;
  return (
    <section
      className="sh-restoration-retained"
      aria-label="Retained restoration requests"
    >
      <p>
        A restoration request still needs reconciliation. Its original
        comparison and operation IDs are retained.
      </p>
      {pending.map((controller) => (
        <Button
          key={controller.parentId}
          variant="secondary"
          onClick={(event) => open(controller.parentId, event.currentTarget)}
        >
          View restoration for operation #{controller.parentId}
        </Button>
      ))}
    </section>
  );
}
export function RetainedRestorationTasks() {
  const session = useSession();
  return session.status === "ready" && session.session.signed_in ? (
    <Retained key={session.session.user.id} actor={session.session.user.id} />
  ) : null;
}
