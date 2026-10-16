import { test } from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
import { createSessionStore } from "../src/auth/session-store.ts";
import { createWorkshopController } from "../src/pages/workshop/core/controller.ts";
import { createToolsController } from "../src/pages/workshop/tools/controller.ts";
import { context, signed, json } from "./workshop-data.mjs";
test("independent bootstrap holds defer lock installation without fabricating a user refusal notice", async () => {
  const api = createApiClient({
      transport: async (path) =>
        json(
          path.endsWith("/locks") ? { success: true, locks: [] } : context(),
        ),
    }),
    session = createSessionStore(async () => signed("alice"));
  await session.load();
  const c = createWorkshopController(api, session, "alice", "target");
  const release = c.claim({
    owner: "pair-bootstrap",
    resource: "tracks:target",
    access: "write",
    reason: "Confirm archive settings first.",
  });
  const t = createToolsController(c);
  await t.initialize();
  assert.equal(t.getSnapshot().locks.phase, "installing");
  assert.equal(c.permission("draft").allowed, false);
  assert.equal(c.getSnapshot().notice, "");
  release();
  assert.equal(t.getSnapshot().locks.phase, "ready");
  assert.equal(c.permission("draft").allowed, true);
  assert.equal(c.getSnapshot().notice, "");
  const releaseUser = c.claim({
    owner: "other-user-action",
    resource: "tracks:target",
    access: "write",
    reason: "An actual target action remains.",
  });
  assert.equal(c.setLocks({}), false);
  assert.equal(c.getSnapshot().notice, "An actual target action remains.");
  releaseUser();
});
