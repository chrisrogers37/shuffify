import initial from "../../fixtures/activity/initial.json" with { type: "json" };
import signedIn from "../../fixtures/valid/session-signed-in.json" with { type: "json" };
import signedOut from "../../fixtures/valid/session-signed-out.json" with { type: "json" };
import { parseActivityContext } from "../pages/activity/model.ts";
const seed = parseActivityContext(initial);
export const activityScenarios = [
  "activity-empty",
  "activity-failure",
  "activity-rate-limit",
  "activity-auth-loss",
  "activity-large",
] as const;
const json = (body: unknown, status = 200, headers: HeadersInit = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
export function createActivityFixtures(scenario: string) {
  let calls = 0,
    signed = true;
  const context = structuredClone(seed);
  if (scenario === "activity-empty") {
    context.activities = [];
    context.executions = [];
    context.stats = {
      total_shuffles: 0,
      total_scheduled_runs: 0,
      total_snapshots: 0,
      active_schedule_count: 0,
    };
  }
  if (scenario === "activity-large") {
    context.stats.total_scheduled_runs = 24;
    context.activities = Array.from({ length: 100 }, (_, i) => ({
      ...structuredClone(seed.activities[i % seed.activities.length]!),
      id: i + 1,
    }));
    context.executions = Array.from({ length: 20 }, (_, i) => {
      const run = structuredClone(seed.executions[i % seed.executions.length]!);
      const id = i + 1;
      if (
        run.result_detail.detail_status === "available" &&
        run.result_detail.execution_id === run.id
      )
        run.result_detail.execution_id = id;
      return { ...run, id };
    });
  }
  return async (path: string, method: string): Promise<Response | null> => {
    if (
      path === "/api/session" &&
      method === "GET" &&
      scenario === "activity-auth-loss"
    )
      return json(signed ? signedIn : signedOut);
    if (path !== "/api/activity") return null;
    if (method !== "GET")
      return json(
        {
          success: false,
          error_code: "method_not_allowed",
          category: "error",
          message: "Activity is read-only.",
        },
        405,
      );
    calls++;
    if (
      calls === 1 &&
      [
        "activity-failure",
        "activity-rate-limit",
        "activity-auth-loss",
      ].includes(scenario)
    ) {
      const limited = scenario === "activity-rate-limit",
        unauthorized = scenario === "activity-auth-loss";
      if (unauthorized) signed = false;
      return json(
        {
          success: false,
          error_code: limited
            ? "rate_limited"
            : unauthorized
              ? "authentication_required"
              : "activity_unavailable",
          category: "error",
          message: limited
            ? "Wait before loading activity."
            : unauthorized
              ? "Your session ended."
              : "Activity is temporarily unavailable.",
          ...(limited ? { retry_after: 2 } : {}),
        },
        limited ? 429 : unauthorized ? 401 : 503,
        limited ? { "Retry-After": "1" } : {},
      );
    }
    return json(context);
  };
}
