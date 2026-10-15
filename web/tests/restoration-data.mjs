import { readFileSync } from "node:fs";
import { createApiClient } from "../src/api/client.ts";
import { createSessionStore } from "../src/auth/session-store.ts";
import { createOperationStore } from "../src/operations/store.ts";
import { createAdmissionRegistry } from "../src/operations/admissions.ts";

export const review = () =>
  JSON.parse(
    readFileSync(
      new URL(
        "../fixtures/valid/operation-restore-context.json",
        import.meta.url,
      ),
    ),
  ).context;
export const parent = () => ({
  id: 7,
  kind: "shuffle",
  state: "needs_attention",
  targets: ["target"],
  started_at: null,
  completed_at: null,
  progress: {},
  result: { success: false },
  recovery: { available: true, action: "restore", reason: "external_edit" },
});
export const child = (id = 8, state = "succeeded") => ({
  ...parent(),
  id,
  kind: "operation_restore",
  state,
  result:
    state === "succeeded"
      ? {
          success: true,
          message: "Verified restored.",
          parent_operation_id: 7,
          parent_resolution: "complete",
        }
      : {
          success: false,
          message: "Review required.",
          parent_operation_id: 7,
          parent_resolution: "not_started",
        },
  recovery: { available: false, action: null, reason: null },
});
export const json = (body, status = 200, headers = {}) =>
  Response.json(body, { status, headers });
export const error = (code, status, extra = {}) =>
  json(
    {
      success: false,
      category: "error",
      error_code: code,
      message: "Review the retained request.",
      retryable: false,
      ...extra,
    },
    status,
  );
export const flush = async () => {
  for (let index = 0; index < 8; index++)
    await new Promise((resolve) => setImmediate(resolve));
};
export async function setup(behavior = () => null, options = {}) {
  const calls = [];
  let actor = "owner",
    resolved = false,
    key = 0;
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async (path, init) => {
      const call = {
        path,
        method: init.method,
        key: new Headers(init.headers).get("Idempotency-Key"),
        body: init.body ? JSON.parse(init.body) : null,
      };
      calls.push(call);
      const special = await behavior(call, {
        resolve: () => {
          resolved = true;
        },
      });
      if (special) return special;
      if (path === "/api/operations/7/restore-context")
        return json({
          success: true,
          category: "success",
          message: "Review loaded.",
          context: review(),
        });
      if (path === "/api/operations/7")
        return json({
          success: true,
          operation: {
            ...parent(),
            recovery: resolved
              ? {
                  available: false,
                  action: null,
                  reason: "operation_already_restored",
                  resolved_operation_id: 8,
                }
              : parent().recovery,
          },
        });
      if (path === "/api/operations/7/restore")
        return json(
          {
            success: true,
            category: "info",
            message: "Accepted.",
            operation_id: 8,
            operation: { ...child(), state: "queued", result: null },
          },
          202,
        );
      if (path === "/api/operations/8") {
        resolved = true;
        return json({ success: true, operation: child() });
      }
      if (path === "/api/shuffle/target")
        return json(
          {
            success: true,
            category: "info",
            message: "Accepted.",
            operation_id: 7,
            operation: parent(),
          },
          202,
        );
      throw Error("Unexpected request " + path);
    },
  });
  const signed = JSON.parse(
    readFileSync(
      new URL("../fixtures/valid/session-signed-in.json", import.meta.url),
    ),
  );
  const session = createSessionStore(async () => ({
    ...signed,
    user: { ...signed.user, id: actor },
  }));
  await session.load();
  const operations = createOperationStore(api, {
    intervalMs: 0,
    maxReadRetries: 0,
    wait: async () => {},
  });
  const admissions = createAdmissionRegistry(api, "owner", operations);
  const { createRestorationController } = await import(
    "../src/operations/restoration/controller.ts"
  );
  const controller = createRestorationController(api, session, "owner", 7, {
    operations,
    admissions,
    key: () => `restore-${++key}`,
    ...options,
  });
  return {
    controller,
    api,
    session,
    operations,
    admissions,
    calls,
    setActor: (value) => {
      actor = value;
    },
  };
}
export async function retainParent(case_) {
  await case_.admissions.submit(
    {
      key: "parent-key",
      feature: "library",
      label: "Original shuffle",
      resources: [{ resource: "playlist:target", access: "write" }],
      path: "/api/shuffle/target",
      body: { encoding: "json", value: {} },
      expected: { kind: "shuffle", target: "target" },
    },
    "owner",
  );
}
