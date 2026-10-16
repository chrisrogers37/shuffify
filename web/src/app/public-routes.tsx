import { PublicShell } from "../shell/Shell";
import { LandingPage } from "../pages/public/LandingPage";
import { LegalPage } from "../pages/public/LegalPage";
export function publicRoutes() {
  return [
    {
      path: "/",
      element: (
        <PublicShell>
          <LandingPage />
        </PublicShell>
      ),
    },
    {
      path: "/terms",
      element: (
        <PublicShell>
          <LegalPage document="terms" />
        </PublicShell>
      ),
    },
    {
      path: "/privacy",
      element: (
        <PublicShell>
          <LegalPage document="privacy" />
        </PublicShell>
      ),
    },
  ];
}
