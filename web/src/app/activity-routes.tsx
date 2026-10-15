import { AuthGuard } from "../auth/SessionProvider";
import { AppShell } from "../shell/Shell";
import { ActivityPage } from "../pages/activity/ActivityPage";
export function activityRoutes() {
  return [
    {
      path: "/activity",
      element: (
        <AppShell>
          <AuthGuard>
            <ActivityPage />
          </AuthGuard>
        </AppShell>
      ),
    },
  ];
}
