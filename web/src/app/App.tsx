import { createBrowserRouter, Outlet } from "react-router";
import type { Services } from "../auth/SessionProvider";
import { PublicShell } from "../shell/Shell";
import { ErrorPage } from "../pages/errors/ErrorPage";
import { Providers } from "./Providers";
import { publicRoutes } from "./public-routes";
import { settingsRoutes } from "./settings-routes";
import { activityRoutes } from "./activity-routes";
import { libraryRoutes } from "./library-routes";
export function createAppRoutes(services: Services) {
  return [
    {
      errorElement: (
        <PublicShell>
          <ErrorPage code={500} />
        </PublicShell>
      ),
      element: (
        <Providers services={services}>
          <Outlet />
        </Providers>
      ),
      children: [
        ...publicRoutes(),
        ...settingsRoutes(),
        ...activityRoutes(),
        ...libraryRoutes(),
        {
          path: "*",
          element: (
            <PublicShell>
              <ErrorPage />
            </PublicShell>
          ),
        },
      ],
    },
  ];
}
export function createAppRouter(services: Services) {
  return createBrowserRouter(createAppRoutes(services));
}
