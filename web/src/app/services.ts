import { createApiClient } from "../api/client";
import type { Transport } from "../api/transport";
import { createSessionStore } from "../auth/session-store";
import type { Services } from "../auth/SessionProvider";
export function createServices(transport?: Transport): Services {
  let services: Services;
  const api = createApiClient({
    ...(transport ? { transport } : {}),
    getCsrfToken: () => {
      const state = services.session.getSnapshot();
      return state.status === "ready" && state.session.signed_in
        ? state.session.csrf_token
        : null;
    },
    onUnauthorized: () => {
      void services.session.refresh();
    },
  });
  services = { api, session: createSessionStore(() => api.getSession()) };
  return services;
}
