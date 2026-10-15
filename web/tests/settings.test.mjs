import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createApiClient } from "../src/api/client.ts";
import { ProtocolError, RateLimitError } from "../src/api/errors.ts";
import {
  draftFromSettings,
  validateDraft,
  updateFromDraft,
  isDirty,
  settingsProblem,
  loadSettings,
  saveSettings,
} from "../src/pages/settings/model.ts";
const context = JSON.parse(
  await readFile(
    new URL("../fixtures/valid/settings-custom-params.json", import.meta.url),
    "utf8",
  ),
);
const response = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
test("Settings draft preserves types and sends only editable fields, leaving rich stored parameters and unavailable preferences untouched", () => {
  const draft = draftFromSettings(context.settings);
  assert.deepEqual(validateDraft(draft), {});
  assert.equal(isDirty(draft, context.settings), false);
  assert.deepEqual(
    updateFromDraft({
      ...draft,
      default_algorithm: "",
      auto_snapshot_enabled: false,
      dashboard_show_recent_activity: false,
      max_snapshots_per_playlist: "50",
    }),
    {
      default_algorithm: null,
      auto_snapshot_enabled: false,
      dashboard_show_recent_activity: false,
      max_snapshots_per_playlist: 50,
    },
  );
  assert.deepEqual(context.settings.default_algorithm_params.custom_mode, {
    enabled: true,
    weights: [1, null, 2.5],
  });
});
test("Settings validation rejects missing, fractional and out of range limits without coercion", () => {
  const draft = draftFromSettings(context.settings);
  for (const invalid of ["", "0", "51", "1.5", "NaN", "2e1"]) {
    assert.equal(
      validateDraft({ ...draft, max_snapshots_per_playlist: invalid })
        .max_snapshots_per_playlist,
      "Choose a whole number from 1 to 50.",
    );
    assert.throws(
      () => updateFromDraft({ ...draft, max_snapshots_per_playlist: invalid }),
      TypeError,
    );
  }
  for (const valid of ["1", "50"])
    assert.deepEqual(
      validateDraft({ ...draft, max_snapshots_per_playlist: valid }),
      {},
    );
});
test("Settings GET and POST consume the same complete context and save is ordinary HTTP200 with CSRF", async () => {
  const calls = [];
  const api = createApiClient({
    getCsrfToken: () => "settings-csrf",
    transport: async (input, init) => {
      calls.push({ input, init });
      return response(context);
    },
  });
  assert.deepEqual(await loadSettings(api), context);
  assert.deepEqual(
    await saveSettings(api, draftFromSettings(context.settings)),
    context,
  );
  assert.equal(calls[1].init.method, "POST");
  assert.equal(calls[1].init.headers.get("X-CSRFToken"), "settings-csrf");
  assert.equal(calls[1].init.credentials, "same-origin");
  const data = JSON.parse(calls[1].init.body);
  assert.equal(typeof data.max_snapshots_per_playlist, "number");
  assert.equal(typeof data.auto_snapshot_enabled, "boolean");
  assert.equal("default_algorithm_params" in data, false);
  assert.equal("theme" in data, false);
});
test("Settings rejects a malformed completed context and unexpected queued acceptance without polling", async () => {
  const queued = JSON.parse(
    await readFile(
      new URL("../fixtures/valid/operation-queued.json", import.meta.url),
      "utf8",
    ),
  );
  for (const value of [
    {
      ...context,
      settings: { ...context.settings, max_snapshots_per_playlist: "10" },
    },
    { ...context, algorithm_options: [{ value: 1, label: "Bad" }] },
    { success: true },
  ]) {
    await assert.rejects(
      loadSettings(createApiClient({ transport: async () => response(value) })),
      ProtocolError,
    );
  }
  let calls = 0;
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async () => {
      calls++;
      return response({ ...queued, operation_id: queued.operation.id }, 202);
    },
  });
  await assert.rejects(
    saveSettings(api, draftFromSettings(context.settings)),
    ProtocolError,
  );
  assert.equal(calls, 1);
});
test("Settings rate limit preserves full retry duration and never automatically replays POST", async () => {
  let calls = 0;
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async () => {
      calls++;
      return response(
        {
          success: false,
          error_code: "rate_limited",
          message: "Please wait.",
          category: "error",
          retry_after: 90,
        },
        429,
        { "Retry-After": "120" },
      );
    },
  });
  try {
    await saveSettings(api, draftFromSettings(context.settings));
    assert.fail("Expected rate limit");
  } catch (error) {
    assert.ok(error instanceof RateLimitError);
    assert.equal(settingsProblem(error).retryAfterMs, 120000);
  }
  assert.equal(calls, 1);
});
test("Settings interrupted save is unconfirmed, never reported as queued or saved", async () => {
  const api = createApiClient({
    getCsrfToken: () => "csrf",
    transport: async () => {
      throw new TypeError("offline");
    },
  });
  try {
    await saveSettings(api, draftFromSettings(context.settings));
    assert.fail("Expected error");
  } catch (error) {
    const problem = settingsProblem(error);
    assert.match(problem.message, /Could not confirm your settings were saved/);
    assert.doesNotMatch(problem.message, /operation|queued|accepted/);
  }
});

test("Settings fixtures expose all registry algorithm choices and deterministic save/error/reconnect cases", async () => {
  const { createSettingsFixtures } =
    await import("../src/fixtures/settings.ts");
  const header = { "X-CSRFToken": "fixture-csrf-token" };
  const session = JSON.parse(
    await readFile(
      new URL("../fixtures/valid/session-signed-in.json", import.meta.url),
      "utf8",
    ),
  );
  header["X-CSRFToken"] = session.csrf_token;
  const happy = createSettingsFixtures("happy");
  const loaded = await (await happy("/api/settings", "GET")).json();
  assert.equal(loaded.algorithm_options.length, 8);
  assert.deepEqual(
    loaded.algorithm_options.slice(1).map((option) => option.value),
    loaded.algorithms.map((algorithm) => algorithm.class_name),
  );
  assert.equal(
    (
      await happy("/api/settings", "POST", {
        headers: header,
        body: JSON.stringify({
          max_snapshots_per_playlist: 30,
          auto_snapshot_enabled: false,
        }),
      })
    ).status,
    200,
  );
  const saved = await (await happy("/api/settings", "GET")).json();
  assert.equal(saved.settings.max_snapshots_per_playlist, 30);
  assert.equal(saved.settings.auto_snapshot_enabled, false);
  assert.deepEqual(saved.settings.default_algorithm_params.custom_mode, {
    enabled: true,
    weights: [1, null, 2.5],
  });
  for (const [scenario, status] of [
    ["settings-field-error", 400],
    ["settings-auth-loss", 401],
    ["settings-rate-limit", 429],
    ["settings-failure", 500],
    ["settings-conflict", 409],
  ]) {
    const fixture = createSettingsFixtures(scenario);
    assert.equal(
      (await fixture("/api/settings", "POST", { headers: header, body: "{}" }))
        .status,
      status,
    );
    if (scenario === "settings-auth-loss") {
      assert.equal(
        (await (await fixture("/api/session", "GET")).json()).signed_in,
        false,
      );
      assert.equal(
        (await (await fixture("/api/session", "GET")).json()).signed_in,
        true,
      );
    }
    assert.equal(
      (await fixture("/api/settings", "POST", { headers: header, body: "{}" }))
        .status,
      200,
    );
  }
  assert.equal(await happy("/api/unknown", "GET"), null);
});

test("Settings fixture initial context satisfies the same generated schema as backend payloads", async () => {
  const { default: Ajv } = await import("ajv");
  const schema = JSON.parse(
    await readFile(
      new URL("../schemas/settings-context.schema.json", import.meta.url),
      "utf8",
    ),
  );
  const value = JSON.parse(
    await readFile(
      new URL("../fixtures/settings/initial.json", import.meta.url),
      "utf8",
    ),
  );
  const validate = new Ajv({
    allErrors: true,
    strict: true,
    coerceTypes: false,
    useDefaults: false,
  }).compile(schema);
  assert.equal(validate(value), true, JSON.stringify(validate.errors));
});
