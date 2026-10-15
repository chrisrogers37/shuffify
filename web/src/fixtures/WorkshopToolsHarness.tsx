import { WorkshopEntry } from "../pages/workshop/WorkshopPage";
import { Button } from "../components/ui/controls";
import type { WorkshopTool } from "../pages/workshop/workspace";
const tools: readonly WorkshopTool[] = [
  {
    id: "algorithms",
    title: "Algorithm preview",
    render: () => <Button>Fixture preview action</Button>,
  },
  {
    id: "locks",
    title: "Track locks",
    render: () => <p>Fixture lock panel</p>,
  },
];
/** Fixture-only integration harness; no feature implementation or production route. */
export function WorkshopToolsHarness() {
  return <WorkshopEntry tools={tools} />;
}
