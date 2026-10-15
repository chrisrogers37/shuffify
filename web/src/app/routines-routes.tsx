import { AuthGuard } from "../auth/SessionProvider";
import { AppShell } from "../shell/Shell";
import { RoutinesPage } from "../pages/routines/RoutinesPage";
import { RoutineSetupPage } from "../pages/routines/RoutineSetupPage";
import { PlaylistSetupPage } from "../pages/routines/PlaylistSetupPage";
export function routinesRoutes() {
  return [
    {
      path: "/routines",
      element: (
        <AppShell>
          <AuthGuard>
            <RoutinesPage />
          </AuthGuard>
        </AppShell>
      ),
    },
    {
      path: "/playlist/:playlistId/routines/new",
      element: (
        <AppShell>
          <AuthGuard>
            <RoutineSetupPage />
          </AuthGuard>
        </AppShell>
      ),
    },
    {
      path: "/playlist/:playlistId/setup",
      element: (
        <AppShell>
          <AuthGuard>
            <PlaylistSetupPage />
          </AuthGuard>
        </AppShell>
      ),
    },
  ];
}
