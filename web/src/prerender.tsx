import { renderToString } from "react-dom/server";
import {
  createStaticHandler,
  createStaticRouter,
  StaticRouterProvider,
} from "react-router";
import { createAppRoutes } from "./app/App";
import { createServices } from "./app/services";
import { publicPages } from "./pages/public/meta";
export { publicPages };
export async function renderPublic(path: string) {
  if (!Object.hasOwn(publicPages, path))
    throw Error(`Unknown public prerender route: ${path}`);
  const routes = createAppRoutes(createServices()),
    handler = createStaticHandler(routes);
  const context = await handler.query(
    new Request(`https://shuffify.invalid${path}`),
  );
  if (context instanceof Response)
    throw Error(`Unexpected prerender response: ${context.status}`);
  const router = createStaticRouter(handler.dataRoutes, context);
  // No loaders or inline hydration payload: browser/server both begin with an unknown session.
  return renderToString(
    <StaticRouterProvider router={router} context={context} hydrate={false} />,
  );
}
