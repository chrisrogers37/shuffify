import { AuthGuard } from "../auth/SessionProvider";
import { AppShell } from "../shell/Shell";
import { WorkshopEntry, WorkshopHub } from "../pages/workshop/WorkshopPage";
export function workshopRoutes() {
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
            <WorkshopEntry />
          </AuthGuard>
        </AppShell>
      ),
    },
  ];
}
