import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router";
import { useServices, useSession } from "../../../auth/SessionProvider";
import { RetainedRestorationTasks } from "../../../components/results/OperationRestoration";
import { Button } from "../../../components/ui/controls";
import { useTasks } from "../../../components/tasks/TaskPanel";
import { useRetry } from "../tools/hooks";
import { ToolsWorkshopPage, workshopTools } from "../tools";
import { getWorkshopController } from "../core/controller";
import type { WorkshopController } from "../core/controller";
import type { WorkshopTool } from "../workspace";
import { useWorkshopWorkspace } from "../workspace";
import { getSourcesController } from "./controller";
import type { SourcesController } from "./controller";
import { InboxPanel } from "./InboxPanel";
import { ArchivePanel } from "./ArchivePanel";
import { RoutinesPanel } from "./RoutinesPanel";
import { SourceSetupPanel } from "./SourceSetupPanel";
import { ArchiveSetupPanel } from "./ArchiveSetupPanel";
import { useSources, SourceActor, SourceAnnouncements } from "./hooks";
import "./sources.css";
export const sourceTools: readonly WorkshopTool[] = [
  {
    id: "sources",
    title: "Inbox",
    render: (context) => <InboxPanel {...context} />,
  },
  {
    id: "archive",
    title: "Archive",
    render: (context) => <ArchivePanel {...context} />,
  },
  {
    id: "routines",
    title: "Routines",
    render: (context) => <RoutinesPanel {...context} />,
  },
];
export const allWorkshopTools: readonly WorkshopTool[] = [
  ...workshopTools,
  ...sourceTools,
];
function SourceBootstrap({
  controller: c,
}: {
  controller: WorkshopController;
}) {
  const { t, state } = useSources(c),
    wait = useRetry(state.retryUntil);
  return state.pair.phase === "error" ? (
    <div className="sh-container sh-sources-bootstrap">
      <p>{state.pair.error}</p>
      <Button
        variant="secondary"
        aria-disabled={wait > 0}
        onClick={() => {
          void t.loadPair();
        }}
      >
        Retry archive settings read
      </Button>
    </div>
  ) : null;
}
function SetupDeepLink({ kind }: { kind: string }) {
  const context = useWorkshopWorkspace(),
    c = context.controller,
    tasks = useTasks(),
    opened = useRef(false),
    { workshop } = useSources(c);
  useEffect(() => {
    if (
      opened.current ||
      workshop.phase !== "ready" ||
      !c.permission("pause").allowed
    )
      return;
    const frame = requestAnimationFrame(() => {
      opened.current = true;
      const trigger = document.querySelector<HTMLElement>(
          ".sh-workshop-navigation-tools button",
        ),
        fallback = document.querySelector<HTMLElement>("#main-content h1");
      void tasks.openTask(
        {
          id: `${c.actor}:${c.playlistId}:setup-link`,
          title: kind === "raids" ? "Source setup" : "Archive setup",
          content: (
            <SourceActor controller={c}>
              {kind === "raids" ? (
                <SourceSetupPanel {...context} />
              ) : (
                <ArchiveSetupPanel {...context} />
              )}
            </SourceActor>
          ),
          trigger,
          fallback,
          scrollKey: `${c.actor}:${c.playlistId}:setup-link`,
        },
        "push",
      );
    });
    return () => cancelAnimationFrame(frame);
  }, [context, c, kind, tasks, workshop.phase]);
  return null;
}
function CombinedPage({ controller: c }: { controller: WorkshopController }) {
  const [source, setSource] = useState<SourcesController | null>(null),
    [query] = useSearchParams();
  useEffect(() => {
    const value = getSourcesController(c);
    void value.initialize();
    setSource(value);
  }, [c]);
  const setup = query.get("setup");
  return source ? (
    <>
      <SourceAnnouncements controller={c} />
      <SourceBootstrap controller={c} />
      <ToolsWorkshopPage controller={c} tools={allWorkshopTools}>
        <RetainedRestorationTasks />
        {setup && ["raids", "archive", "rotation"].includes(setup) && (
          <SetupDeepLink kind={setup} />
        )}
      </ToolsWorkshopPage>
    </>
  ) : (
    <p className="sh-container" role="status">
      Loading source settings…
    </p>
  );
}
export function SourcesWorkshopEntry() {
  const { playlistId } = useParams(),
    { api, session: store } = useServices(),
    session = useSession(),
    last = useRef<string | null>(null);
  if (session.status === "ready" && session.session.signed_in)
    last.current = session.session.user.id;
  return last.current && playlistId ? (
    <CombinedPage
      key={`${last.current}:${playlistId}`}
      controller={getWorkshopController(api, store, last.current, playlistId)}
    />
  ) : null;
}
