import type { ReactNode } from "react";
import { Announcer } from "../a11y/Announcer";
import { ConfirmProvider } from "../components/tasks/ConfirmDialog";
import { TaskProvider } from "../components/tasks/TaskPanel";
import { NavigationGuard } from "../navigation/NavigationGuard";
import { SessionProvider } from "../auth/SessionProvider";
import type { Services } from "../auth/SessionProvider";
export function Providers({
  services,
  children,
}: {
  services: Services;
  children: ReactNode;
}) {
  return (
    <Announcer>
      <SessionProvider services={services}>
        <ConfirmProvider>
          <NavigationGuard>
            <TaskProvider>{children}</TaskProvider>
          </NavigationGuard>
        </ConfirmProvider>
      </SessionProvider>
    </Announcer>
  );
}
