import { createSettingsFixtures, settingsScenarios } from "./settings.ts";
import signedIn from "../../fixtures/valid/session-signed-in.json";
import signedOut from "../../fixtures/valid/session-signed-out.json";
import signedOutNotice from "../../fixtures/valid/session-signed-out-notice.json";
import queued from "../../fixtures/valid/operation-queued.json";
import attention from "../../fixtures/valid/operation-needs-attention.json";
import library from "../../fixtures/valid/library-context.json";
import type { Transport } from "../api/transport";
const scenarios = [
  ...settingsScenarios,
  "happy",
  "signed-out",
  "signed-out-notice",
  "session-error",
  "empty",
  "error",
  "partial",
  "recovery",
  "rollback",
  "busy",
  "rate-limit",
] as const;
export type Scenario = (typeof scenarios)[number];
export function fixtureScenario(value: string | null): Scenario {
  if (value === null) return "happy";
  if (!(scenarios as readonly string[]).includes(value))
    throw Error("Unknown fixture scenario");
  return value as Scenario;
}
const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
const failure = (message: string, code = "fixture_error") => ({
  success: false,
  error_code: code,
  message,
  category: "error",
  retryable: true,
});
export function createFixtureTransport(scenario: Scenario): Transport {
  const settings = createSettingsFixtures(scenario);
  let sessionCalls = 0,
    polls = 0,
    accepted = false;
  return async (input, init) => {
    if (init?.signal?.aborted) throw init.signal.reason;
    const path = String(input),
      method = init?.method ?? "GET";
    const settingsResponse = await settings(path, method, init);
    if (settingsResponse) return settingsResponse;
    if (path === "/api/session" && method === "GET") {
      sessionCalls++;
      if (scenario === "session-error")
        return json(failure("Your session could not be loaded."), 503);
      return json(
        scenario === "signed-out"
          ? signedOut
          : scenario === "signed-out-notice"
            ? sessionCalls === 1
              ? signedOutNotice
              : signedOut
            : signedIn,
      );
    }
    if (path === "/api/library" && method === "GET")
      return json(
        scenario === "empty"
          ? {
              ...library,
              sections: { favorites: [], playlists: [], hidden: [] },
            }
          : library,
      );
    if (path === "/api/fixture/command" && method === "POST") {
      if (new Headers(init?.headers).get("X-CSRFToken") !== signedIn.csrf_token)
        return json(failure("Missing fixture CSRF.", "csrf_failed"), 400);
      if (scenario === "error")
        return json(failure("The fixture command was refused."), 500);
      if (scenario === "busy")
        return json(
          {
            ...failure(
              "This playlist has an operation in progress.",
              "playlist_busy",
            ),
            operation_id: 1,
          },
          409,
        );
      accepted = true;
      polls = 0;
      return json({ ...queued, operation_id: queued.operation.id }, 202);
    }
    if (path === "/api/operations/1" && method === "GET") {
      if (!accepted && scenario !== "busy")
        return json(failure("No fixture operation exists."), 404);
      const tick = polls++;
      if (scenario === "rate-limit" && tick === 0)
        return json(
          {
            ...failure("Wait before refreshing.", "rate_limited"),
            retry_after: 1,
          },
          429,
          { "Retry-After": "1" },
        );
      if (tick === 0) return json(queued);
      if (tick === 1)
        return json({
          success: true,
          operation: {
            ...queued.operation,
            state: scenario === "rollback" ? "compensating" : "running",
            started_at: "2026-01-01T12:00:00+00:00",
            progress: {
              batches_done: 1,
              total_batches: 2,
              phase: scenario === "rollback" ? "compensating" : "forward",
            },
          },
        });
      if (scenario === "recovery") return json(attention);
      const state =
        scenario === "partial"
          ? "failed"
          : scenario === "rollback"
            ? "rolled_back"
            : "succeeded";
      return json({
        success: true,
        operation: {
          ...queued.operation,
          state,
          started_at: "2026-01-01T12:00:00+00:00",
          completed_at: "2026-01-01T12:01:00+00:00",
          result: {
            success: state === "succeeded",
            message:
              state === "succeeded"
                ? "The fixture operation completed."
                : state === "rolled_back"
                  ? "Changes were verified as restored."
                  : "One step finished. Review the remaining changes.",
            ...(state === "succeeded"
              ? {}
              : {
                  error_code:
                    state === "rolled_back"
                      ? "operation_rolled_back"
                      : "partial_write",
                }),
          },
          progress: { batches_done: 2, total_batches: 2, phase: "forward" },
        },
      });
    }
    throw Error(`Unhandled fixture request: ${method} ${path}`);
  };
}
