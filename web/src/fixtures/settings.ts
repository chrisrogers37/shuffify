import initial from "../../fixtures/settings/initial.json" with { type: "json" };
import signedIn from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import signedOut from "../../fixtures/valid/session-signed-out.json" with { type: "json" };
import type {
  ApiError,
  SettingsContext,
  SettingsUpdateRequest,
} from "../api/contracts";
export const settingsScenarios = [
  "settings-field-error",
  "settings-auth-loss",
  "settings-rate-limit",
  "settings-failure",
  "settings-conflict",
  "settings-load-error",
  "settings-network-error",
] as const;
export type SettingsScenario = (typeof settingsScenarios)[number];
const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
const failure = (message: string, extra: Partial<ApiError> = {}) => ({
  success: false,
  error_code: "settings_error",
  category: "error",
  message,
  ...extra,
});
/** Fixture-only deterministic endpoint adapter. Unknown requests are delegated, never fetched. */
export function createSettingsFixtures(scenario: string) {
  let value = structuredClone(initial) as SettingsContext,
    posts = 0,
    gets = 0,
    signed = true,
    expiredRead = false;
  return async (
    path: string,
    method: string,
    init?: RequestInit,
  ): Promise<Response | null> => {
    if (
      path === "/api/session" &&
      method === "GET" &&
      scenario === "settings-auth-loss"
    ) {
      if (!signed) {
        if (expiredRead) signed = true;
        else expiredRead = true;
      }
      return json(signed ? signedIn : signedOut);
    }
    if (path !== "/api/settings") return null;
    if (method === "GET") {
      gets++;
      if (scenario === "settings-load-error" && gets === 1)
        return json(failure("Settings are temporarily unavailable."), 503);
      return json(value);
    }
    if (method !== "POST") return json(failure("Method not allowed."), 405);
    if (new Headers(init?.headers).get("X-CSRFToken") !== signedIn.csrf_token)
      return json(
        failure("Missing fixture CSRF.", { error_code: "csrf_failed" }),
        400,
      );
    posts++;
    if (posts === 1) {
      if (scenario === "settings-field-error")
        return json(
          failure("Check your settings.", {
            field_errors: [
              {
                path: "max_snapshots_per_playlist",
                message: "Choose at most 20 snapshots.",
              },
            ],
          }),
          400,
        );
      if (scenario === "settings-auth-loss") {
        signed = false;
        return json(
          failure("Your session ended.", {
            error_code: "authentication_required",
          }),
          401,
        );
      }
      if (scenario === "settings-rate-limit")
        return json(
          failure("Wait before saving again.", { retry_after: 2 }),
          429,
          { "Retry-After": "1" },
        );
      if (scenario === "settings-failure")
        return json(failure("Settings could not be saved."), 500);
      if (scenario === "settings-conflict")
        return json(
          failure("Your preferences changed. Review them before saving again."),
          409,
        );
      if (scenario === "settings-network-error")
        throw new TypeError("Fixture connection interrupted.");
    }
    const update = JSON.parse(String(init?.body)) as SettingsUpdateRequest;
    value = {
      ...value,
      message: "Settings saved successfully.",
      settings: { ...value.settings, ...update },
    };
    return json(value);
  };
}
