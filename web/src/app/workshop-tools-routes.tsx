import { AuthGuard } from "../auth/SessionProvider";
import { AppShell } from "../shell/Shell";
import { WorkshopHub } from "../pages/workshop/WorkshopPage";
import { ToolsWorkshopEntry } from "../pages/workshop/tools";
export function workshopToolsRoutes() {
  return [
    {
      path: "/workshop",
      element: (
        <AppShell>
          <AuthGuard>
            <WorkshopHub />
          </AuthGuard>
        </AppShell>
      ),
    },
    {
      path: "/workshop/:playlistId",
      element: (
        <AppShell>
          <AuthGuard>
            <ToolsWorkshopEntry />
          </AuthGuard>
        </AppShell>
      ),
    },
  ];
}
