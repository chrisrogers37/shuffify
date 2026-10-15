import { createBrowserRouter, Outlet } from "react-router";
import type { Services } from "../auth/SessionProvider";
import { PublicShell } from "../shell/Shell";
import { ErrorPage } from "../pages/errors/ErrorPage";
import { Providers } from "./Providers";
export function createAppRouter(services: Services) {
  return createBrowserRouter([
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
  ]);
}
