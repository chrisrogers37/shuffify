import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createApiClient } from "../src/api/client.ts";
import { createSessionStore } from "../src/auth/session-store.ts";
import { createOperationStore } from "../src/operations/store.ts";
import { getAdmissionRegistry } from "../src/operations/admissions.ts";
import { createLibraryController } from "../src/pages/library/controller.ts";
import { createLibraryFixtures } from "../src/fixtures/library.ts";
import { createRestorationController } from "../src/operations/restoration/controller.ts";
import { flush, review, parent, child, json } from "./restoration-data.mjs";

for (const blocking of [false, true]) {
  test(`a confirmed restoration enables a new Library review while preserving ${blocking ? "blocking" : "original"} facts`, async () => {
    const signed = JSON.parse(
      readFileSync(
        new URL("../fixtures/valid/session-signed-in.json", import.meta.url),
      ),
    );
    const actor = signed.user.id,
      fixture = createLibraryFixtures(
        blocking ? "library-busy" : "library-recovery",
      );
    let parentId = blocking ? 99 : null,
      restored = false;
    const calls = [];
    const original = () => ({
      ...parent(),
      id: parentId,
      targets: ["alice-mix"],
      result: { success: false, operation: { aggregate_status: "unknown" } },
    });
    const api = createApiClient({
      getCsrfToken: () => signed.csrf_token,
      transport: async (path, init) => {
        calls.push({ path, method: init.method });
        if (path === `/api/operations/${parentId}/restore-context`) {
          const value = review();
          value.operation_id = parentId;
          value.resources[0].playlist_id = "alice-mix";
          value.reviewed.reviewed_resources[0].playlist_id = "alice-mix";
          return json({ success: true, context: value });
        }
        if (path === `/api/operations/${parentId}/restore`)
          return json(
            {
              success: true,
              operation_id: 900,
              operation: {
                ...child(900),
                state: "queued",
                result: null,
                targets: ["alice-mix"],
              },
            },
            202,
          );
        if (path === "/api/operations/900") {
          restored = true;
          return json({
            success: true,
            operation: {
              ...child(900),
              targets: ["alice-mix"],
              result: {
                success: true,
                parent_operation_id: parentId,
                parent_resolution: "complete",
              },
            },
          });
        }
        if (path === `/api/operations/${parentId}` && parentId !== null)
          return json({
            success: true,
            operation: {
              ...original(),
              recovery: restored
                ? {
                    available: false,
                    action: null,
                    reason: "operation_already_restored",
                    resolved_operation_id: 900,
                  }
                : original().recovery,
            },
          });
        const response = await fixture(path, init.method, init);
        if (!response) throw Error("Unexpected request " + path);
        if (response.status === 202) {
          const data = await response.clone().json();
          parentId = data.operation_id;
        }
        return response;
      },
    });
    const session = createSessionStore(async () => signed);
    await session.load();
    const operations = createOperationStore(api, {
      intervalMs: 0,
      wait: async () => {},
      maxReadRetries: 0,
    });
    const admissions = getAdmissionRegistry(api, actor, operations);
    const library = createLibraryController(api, session, actor, {
      operations,
      key: () => "library-original",
    });
    await library.load();
    const id = await library.begin("alice-mix", "shuffle");
    await flush();
    await library.dispatch(id);
    await flush();
    assert.equal(library.blocked("alice-mix"), true);
    const old = structuredClone(
      operations.getSnapshot().get(parentId).operation,
    );
    const restoration = createRestorationController(
      api,
      session,
      actor,
      parentId,
      { operations, admissions, key: () => "reviewed-restoration" },
    );
    await restoration.loadReview();
    await restoration.confirm();
    await flush();
    assert.equal(restoration.getSnapshot().phase, "resolved");
    assert.equal(library.blocked("alice-mix"), false);
    assert.deepEqual(operations.getSnapshot().get(parentId).operation, old);
    const receipt = library.getSnapshot().receipts.get(id);
    assert.equal(receipt.operationId, blocking ? null : parentId);
    if (blocking) assert.equal(receipt.busyId, parentId);
    const next = await library.begin("alice-mix", "shuffle");
    await flush();
    assert.equal(library.getSnapshot().receipts.get(next).status, "ready");
    assert.equal(
      calls.filter(
        (call) => call.path === `/api/operations/${parentId}/restore`,
      ).length,
      1,
    );
  });
}
