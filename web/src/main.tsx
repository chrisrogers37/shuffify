import { StrictMode } from "react";
import { createRoot, hydrateRoot } from "react-dom/client";
import { RouterProvider } from "react-router/dom";
import "./design/index.css";
import { createServices } from "./app/services";
let services;
if (import.meta.env.VITE_FIXTURE_MODE === "true") {
  const { createFixtureTransport, fixtureScenario } =
    await import("./fixtures/transport");
  services = createServices(
    createFixtureTransport(
      fixtureScenario(new URLSearchParams(location.search).get("scenario")),
    ),
  );
  const { createFixtureRouter } = await import("./app/FixtureApp");
  const router = createFixtureRouter(services);
  mount(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  );
} else {
  services = createServices();
  const { createAppRouter } = await import("./app/App");
  const router = createAppRouter(services);
  mount(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  );
}

function mount(app: React.ReactNode) {
  const root = document.getElementById("root")!;
  if (
    root.hasChildNodes() &&
    root.dataset.prerenderedRoute ===
      (location.pathname.replace(/\/$/, "") || "/")
  )
    hydrateRoot(root, app);
  else createRoot(root).render(app);
}
