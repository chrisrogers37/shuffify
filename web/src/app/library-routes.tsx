import { AuthGuard } from "../auth/SessionProvider";
import { AppShell } from "../shell/Shell";
import { LibraryEntry } from "../pages/library/LibraryPage";
export function libraryRoutes() {
  return [
    {
      path: "/library",
      element: (
        <AppShell>
          <AuthGuard>
            <LibraryEntry />
          </AuthGuard>
        </AppShell>
      ),
    },
  ];
}
