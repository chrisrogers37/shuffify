import { useRef } from "react";
import { AuthGuard, useServices, useSession } from "../auth/SessionProvider";
import { AppShell } from "../shell/Shell";
import { Button } from "../components/ui/controls";
import { SettingsPage } from "../pages/settings/SettingsPage";
function SettingsRoute() {
  const snapshot = useSession(),
    { session } = useServices(),
    connected = useRef(false);
  if (snapshot.status === "ready" && snapshot.session.signed_in)
    connected.current = true;
  return (
    <AppShell>
      <AuthGuard>
        <SettingsPage />
      </AuthGuard>
      {connected.current &&
        snapshot.status === "ready" &&
        !snapshot.session.signed_in && (
          <div className="sh-container sh-settings-reconnect">
            <p>
              Your unsaved settings stay on this page while it remains open.
              After reconnecting, check your connection to continue.
            </p>
            <Button
              variant="secondary"
              onClick={() => {
                void session.refresh();
              }}
            >
              Check connection
            </Button>
          </div>
        )}
    </AppShell>
  );
}
export function settingsRoutes() {
  return [{ path: "/settings", element: <SettingsRoute /> }];
}
