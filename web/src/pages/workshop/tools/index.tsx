import { useEffect, useRef } from "react";
import type {ReactNode} from 'react';
import { useParams } from "react-router";
import { useServices, useSession } from "../../../auth/SessionProvider";
import { WorkshopPage } from "../WorkshopPage";
import { getWorkshopController } from "../core/controller";
import type { WorkshopController } from "../core/controller";
import type { WorkshopTool } from "../workspace";
import { AlgorithmTool } from "./AlgorithmTool";
import { LocksTool, LockRowAction } from "./LocksTool";
import { CatalogTool, ExternalTool } from "./SearchTools";
import { SnapshotsTool } from "./SnapshotsTool";
import { DetailsTool } from "./DetailsTool";
import { getToolsController } from "./controller";
import { useTools, ToolFeedback } from "./hooks";
import { Button } from "../../../components/ui/controls";
import "./tools.css";
export const workshopTools: readonly WorkshopTool[] = [
  {
    id: "algorithms",
    title: "Shuffle options",
    render: (context) => <AlgorithmTool {...context} />,
  },
  {
    id: "locks",
    title: "Track locks",
    render: (context) => <LocksTool {...context} />,
    rowActions: (context, occurrence) => (
      <LockRowAction context={context} occurrence={occurrence} />
    ),
  },
  {
    id: "catalog",
    title: "Add tracks",
    render: (context) => <CatalogTool {...context} />,
  },
  {
    id: "external",
    title: "Import playlist",
    render: (context) => <ExternalTool {...context} />,
  },
  {
    id: "snapshots",
    title: "Snapshots",
    render: (context) => <SnapshotsTool {...context} />,
  },
  {
    id: "details",
    title: "Playlist details",
    render: (context) => <DetailsTool {...context} />,
  },
];
function LockBootstrapNotice({
  controller,
}: {
  controller: WorkshopController;
}) {
  const { t, state } = useTools(controller);
  return state.locks.phase === "error" &&
    controller.permission("snapshot-read").allowed ? (
    <div className="sh-container sh-tools-bootstrap">
      <ToolFeedback value={state.locks} id="locks-bootstrap" />
      <Button
        variant="secondary"
        onClick={() => {
          void t.loadLocks();
        }}
      >
        Retry active lock read
      </Button>
    </div>
  ) : null;
}
export function ToolsWorkshopPage({
  controller,
  tools = workshopTools,
  children,
}: {
  controller: WorkshopController;
  tools?: readonly WorkshopTool[];
  children?:ReactNode;
}) {
  useEffect(() => {
    void getToolsController(controller).initialize();
  }, [controller]);
  return (
    <>
      <LockBootstrapNotice controller={controller} />
      <WorkshopPage controller={controller} tools={tools}>{children}</WorkshopPage>
    </>
  );
}
export function ToolsWorkshopEntry() {
  const { playlistId } = useParams(),
    { api, session: store } = useServices(),
    session = useSession(),
    last = useRef<string | null>(null);
  if (session.status === "ready" && session.session.signed_in)
    last.current = session.session.user.id;
  return last.current && playlistId ? (
    <ToolsWorkshopPage
      key={`${last.current}:${playlistId}`}
      controller={getWorkshopController(api, store, last.current, playlistId)}
    />
  ) : null;
}
