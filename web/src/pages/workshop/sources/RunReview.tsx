import { Button } from "../../../components/ui/controls";
import type { WorkshopToolContext } from "../workspace";
import { useTasks } from "../../../components/tasks/TaskPanel";
import { record, records } from "./model";
import {
  SourceActor,
  ActionReceipt,
  SourceFeedback,
  useSources,
} from "./hooks";
export function RunReview({
  controller: c,
}: Pick<WorkshopToolContext, "controller">) {
  const { t, state, workshop } = useSources(c),
    r = state.runReview;
  if (!r) return <p>Open a fresh run review.</p>;
  const context = r.context,
    target = record(context.target) ? context.target : {},
    action = record(context.action) ? context.action : {},
    resources = Array.isArray(context.resources)
      ? records(context.resources)
      : [],
    steps = Array.isArray(context.steps) ? context.steps : [],
    receipt = r.actionId
      ? state.actions.find((x) => x.id === r.actionId)
      : null,
    stale =
      workshop.revision !== r.revision ||
      workshop.configRevision !== r.configRevision ||
      workshop.sourceRevision !== r.sourceRevision;
  return (
    <div className="sh-sources-stack">
      <h3>{r.label}</h3>
      <section aria-label="Reviewed target">
        <h4>Reviewed target</h4>
        <p>
          {typeof target.name === "string" && target.name.trim()
            ? target.name
            : "Name unavailable at review"}{" "}
          · {String(target.id ?? c.playlistId)}
        </p>
      </section>
      <SourceFeedback controller={c} />
      {!receipt && (
        <>
          <p>
            This fresh review changes nothing. Running uses the exact reviewed
            settings and resources.
          </p>
          <ol>
            {steps.map((step, i) => (
              <li key={i}>
                {typeof step === "string"
                  ? step.replaceAll("_", " ")
                  : record(step) && typeof step.label === "string"
                    ? step.label
                    : "Reviewed step"}
              </li>
            ))}
          </ol>
          <ul>
            {resources.map((resource, i) => (
              <li key={i}>
                {String(resource.resource_name ?? resource.role ?? "Resource")}{" "}
                · {String(resource.resource)} ·{" "}
                {resource.access === "write" ? "May change" : "Read only"}
              </li>
            ))}
          </ul>
          {Array.isArray(action.sources) && (
            <section>
              <h4>Sources</h4>
              <ul>
                {records(action.sources).map((source, i) => (
                  <li key={i}>{String(source.label ?? source.id)}</li>
                ))}
              </ul>
            </section>
          )}
          {typeof action.count === "number" && (
            <p>
              {action.count} tracks per manual add. A manual action does not
              enable automatic adding.
            </p>
          )}
          {record(action.algorithm) && (
            <p>
              Algorithm: {String(action.algorithm.name ?? action.algorithm.id)}
            </p>
          )}
          {record(action.rotation) && (
            <dl className="sh-sources-facts">
              {Object.entries(action.rotation).map(([name, value]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          )}
          {stale && (
            <p className="sh-sources-error">
              This review is stale. Return and review the current draft and
              settings.
            </p>
          )}
          <Button
            aria-disabled={stale || !c.permission("pause").allowed}
            onClick={() => {
              void t.runReviewed();
            }}
          >
            Run reviewed action
          </Button>
        </>
      )}
      {receipt && <ActionReceipt controller={c} action={receipt} />}
    </div>
  );
}
export function useOpenRun(context: WorkshopToolContext) {
  const tasks = useTasks(),
    { controller: c } = context,
    { t, state } = useSources(c);
  return async (
    kind: "raid" | "drip" | "raid_and_drip" | "schedule",
    trigger: HTMLElement,
    scheduleId?: number,
    jobType?: string,
  ) => {
    const writesTarget =
      kind === "schedule" ? jobType !== "raid" : kind !== "raid";
    if (writesTarget && !(await context.resolveDraft("run this action")))
      return;
    if (
      !writesTarget &&
      (state.stagedPending.length ||
        state.retained.some(
          (x) => x.task.kind === "raid_promotion" && x.phase !== "complete",
        ))
    ) {
      await context.openTool("sources", trigger, "push");
      return;
    }
    if (await t.prepareRun(kind, scheduleId))
      await tasks.openTask(
        {
          id: `workshop-run-${c.actor}-${c.playlistId}`,
          title: "Review live action",
          content: (
            <SourceActor controller={c}>
              <RunReview controller={c} />
            </SourceActor>
          ),
          trigger,
          fallback: context.fallback,
          scrollKey: `${c.actor}:${c.playlistId}:run`,
        },
        "push",
      );
  };
}
