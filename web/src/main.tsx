import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
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
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  );
} else {
  services = createServices();
  const { createAppRouter } = await import("./app/App");
  const router = createAppRouter(services);
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <RouterProvider router={router} />
    </StrictMode>,
  );
}
