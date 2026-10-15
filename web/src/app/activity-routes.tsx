import { AuthGuard } from "../auth/SessionProvider";
import { AppShell } from "../shell/Shell";
import { Workspace } from "../components/tasks/TaskPanel";
import { ActivityPage } from "../pages/activity/ActivityPage";
export function activityRoutes() {
  return [
    {
      path: "/activity",
      element: (
        <AppShell>
          <AuthGuard>
            <Workspace>
              <ActivityPage />
            </Workspace>
          </AuthGuard>
        </AppShell>
      ),
    },
  ];
}
