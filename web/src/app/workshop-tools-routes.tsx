import { AuthGuard } from "../auth/SessionProvider";
import { AppShell } from "../shell/Shell";
import { WorkshopHub } from "../pages/workshop/WorkshopPage";
import { SourcesWorkshopEntry } from "../pages/workshop/sources";
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
            <SourcesWorkshopEntry />
          </AuthGuard>
        </AppShell>
      ),
    },
  ];
}
