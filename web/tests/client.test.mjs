import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createApiClient } from "../src/api/client.ts";
import {
  ApiRequestError,
  ConflictError,
  MissingCsrfTokenError,
  NetworkError,
  ProtocolError,
  RateLimitError,
  retryDeadline,
  UnauthorizedError,
} from "../src/api/errors.ts";
import { pollOperation } from "../src/api/operations.ts";
import { createServices } from "../src/app/services.ts";

const signedIn = JSON.parse(
  await readFile(
    new URL("../fixtures/valid/session-signed-in.json", import.meta.url),
  ),
);
const signedOut = JSON.parse(
  await readFile(
    new URL("../fixtures/valid/session-signed-out.json", import.meta.url),
  ),
);
const queued = JSON.parse(
  await readFile(
    new URL("../fixtures/valid/operation-queued.json", import.meta.url),
  ),
).operation;
const operation = (state, id = queued.id) => ({ ...queued, id, state });
const response = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers });
const apiError = (code, extra = {}) => ({
  success: false,
  error_code: code,
  message: "Please try again.",
  category: "error",
  ...extra,
});
const accepted = (value = queued) => ({
  success: true,
  operation_id: value.id,
  operation: value,
});
const context = (value) => ({ success: true, operation: value });

function scriptedTransport(steps) {
  const requests = [];
  const transport = async (input, init) => {
    requests.push({ input, init });
    const next = steps.shift();
    assert.notEqual(next, undefined, `Unexpected request: ${input}`);
    return typeof next === "function" ? next(input, init) : next;
  };
  return { transport, requests };
}

test("session reads use same-origin credentials and validate signed-in/out data", async () => {
  const wire = scriptedTransport([response(signedIn), response(signedOut)]);
  const client = createApiClient({ transport: wire.transport });
  assert.deepEqual(await client.getSession(), signedIn);
  assert.deepEqual(await client.getSession(), signedOut);
  assert.equal(wire.requests[0].input, "/api/session");
  assert.equal(wire.requests[0].init.credentials, "same-origin");
  assert.equal(
    new Headers(wire.requests[0].init.headers).get("Accept"),
    "application/json",
  );
  assert.equal(
    new Headers(wire.requests[0].init.headers).has("Content-Type"),
    false,
  );
});

test("unsafe requests attach current CSRF and preserve caller payload and request key", async () => {
  const wire = scriptedTransport([response(accepted(), 202)]);
  const client = createApiClient({
    transport: wire.transport,
    getCsrfToken: () => "session-csrf",
  });
  const payload = {
    request_key: "frozen-intent-key",
    playlist_id: "alice-mix",
  };
  const result = await client.request("/api/shuffle", {
    method: "POST",
    json: payload,
  });
  assert.equal(result.status, "accepted");
  assert.deepEqual(result.operation, queued);
  assert.equal(wire.requests.length, 1);
  assert.equal(wire.requests[0].init.body, JSON.stringify(payload));
  assert.equal(
    new Headers(wire.requests[0].init.headers).get("X-CSRFToken"),
    "session-csrf",
  );
  assert.equal(
    new Headers(wire.requests[0].init.headers).get("Content-Type"),
    "application/json",
  );
});

test("missing CSRF rejects an unsafe request before transport runs", async () => {
  const wire = scriptedTransport([]);
  const client = createApiClient({ transport: wire.transport });
  await assert.rejects(
    client.request("/api/shuffle", { method: "POST", json: {} }),
    MissingCsrfTokenError,
  );
  assert.equal(wire.requests.length, 0);
});

test("credentialed requests reject cross-origin URLs and escaping paths", async () => {
  const wire = scriptedTransport([]);
  const client = createApiClient({ transport: wire.transport });
  for (const path of [
    "https://evil.invalid/api/x",
    "//evil.invalid/api/x",
    "/api/../logout",
    "/api/%2e%2e/logout",
    "/api/x#fragment",
    "/api\\evil",
    "/logout",
  ]) {
    await assert.rejects(client.request(path), TypeError, path);
  }
  assert.equal(wire.requests.length, 0);
});

test("completed envelope preserves snake_case response fields", async () => {
  const data = { success: true, some_field: "preserved" };
  const client = createApiClient({ transport: async () => response(data) });
  assert.deepEqual(await client.request("/api/context"), {
    status: "completed",
    data,
  });
});

test("malformed, empty, missing and inconsistent envelopes are protocol errors", async () => {
  const values = [
    new Response("", { status: 200 }),
    new Response("{broken", { status: 200 }),
    response({}),
    response({ success: false }, 200),
    response({ success: true }, 500),
    response(accepted(), 200),
    response({ success: true }, 202),
    response({ ...accepted(), operation_id: 999 }, 202),
    response(
      { ...accepted(), operation: { ...queued, state: "finished" } },
      202,
    ),
    response({ ...signedIn, csrf_token: "" }),
  ];
  for (const value of values) {
    const client = createApiClient({
      transport: async () => value,
      getCsrfToken: () => "csrf",
    });
    const invoke =
      value === values.at(-1)
        ? client.getSession()
        : client.request("/api/context");
    await assert.rejects(invoke, ProtocolError);
  }
});

test("401 notifies shared session owner without replaying unsafe request", async () => {
  const wire = scriptedTransport([response(apiError("auth_required"), 401)]);
  let unauthorized = 0;
  const client = createApiClient({
    transport: wire.transport,
    getCsrfToken: () => "csrf",
    onUnauthorized: () => {
      unauthorized += 1;
    },
  });
  await assert.rejects(
    client.request("/api/shuffle", {
      method: "POST",
      json: { request_key: "same-key" },
    }),
    UnauthorizedError,
  );
  assert.equal(unauthorized, 1);
  assert.equal(wire.requests.length, 1);
});

const signedInWith = (csrf_token) => response({ ...signedIn, csrf_token });
const sent = (wire) =>
  wire.requests.map(({ input, init }) => [
    init.method,
    input,
    new Headers(init.headers).get("X-CSRFToken"),
    init.body ?? null,
  ]);
// Each session state the store publishes: the token it holds, "signed-out", or its status.
const published = (services) => {
  const states = [];
  services.session.subscribe(() => {
    const state = services.session.getSnapshot();
    states.push(
      state.status !== "ready"
        ? state.status
        : state.session.signed_in
          ? state.session.csrf_token
          : "signed-out",
    );
  });
  return states;
};
const csrfRejected = {
  success: false,
  error_code: "session_verification_expired",
  message:
    "Your request could not be verified. Please refresh the page and try again.",
  category: "error",
};
const directRejected = (error_code, message) => ({
  status: "failed",
  message,
  error: message,
  operation: {
    kind: "shuffle",
    aggregate_status: "partial",
    resources: [],
    bookkeeping: [],
    error_code,
    retryable: false,
  },
  success: false,
  category: "error",
  error_code: "invalid_request",
});

for (const [name, path, rejection] of [
  ["a write", "/api/settings", csrfRejected],
  [
    "a direct action",
    "/api/shuffle/playlist-1",
    directRejected(
      "session_verification_expired",
      "Session verification failed. This attempt did not run. Reloading loses the recovery details kept on this page; earlier Spotify changes remain.",
    ),
  ],
]) {
  test(`a stale CSRF token on ${name} renews the session in place and resends the write once`, async () => {
    const wire = scriptedTransport([
      signedInWith("stale-csrf"),
      response(rejection, 400),
      signedInWith("fresh-csrf"),
      response({ success: true, saved: true }),
    ]);
    const services = createServices(wire.transport);
    await services.session.load();
    const states = published(services);
    const result = await services.api.request(path, {
      method: "POST",
      json: { request_key: "same-intent" },
    });
    assert.deepEqual(result, {
      status: "completed",
      data: { success: true, saved: true },
    });
    assert.deepEqual(sent(wire), [
      ["GET", "/api/session", null, null],
      ["POST", path, "stale-csrf", '{"request_key":"same-intent"}'],
      ["GET", "/api/session", null, null],
      ["POST", path, "fresh-csrf", '{"request_key":"same-intent"}'],
    ]);
    assert.deepEqual(states, ["fresh-csrf"]);
  });
}

test("two writes refused for a stale CSRF token at the same moment share one session read and are each resent once", async () => {
  const wire = scriptedTransport([
    signedInWith("stale-csrf"),
    response(csrfRejected, 400),
    response(csrfRejected, 400),
    signedInWith("fresh-csrf"),
    response({ success: true, saved: "first" }),
    response({ success: true, saved: "second" }),
  ]);
  const services = createServices(wire.transport);
  await services.session.load();
  const states = published(services);
  const results = await Promise.allSettled(
    ["first", "second"].map((request_key) =>
      services.api.request("/api/settings", {
        method: "POST",
        json: { request_key },
      }),
    ),
  );
  assert.deepEqual(sent(wire), [
    ["GET", "/api/session", null, null],
    ["POST", "/api/settings", "stale-csrf", '{"request_key":"first"}'],
    ["POST", "/api/settings", "stale-csrf", '{"request_key":"second"}'],
    ["GET", "/api/session", null, null],
    ["POST", "/api/settings", "fresh-csrf", '{"request_key":"first"}'],
    ["POST", "/api/settings", "fresh-csrf", '{"request_key":"second"}'],
  ]);
  assert.deepEqual(results, [
    {
      status: "fulfilled",
      value: { status: "completed", data: { success: true, saved: "first" } },
    },
    {
      status: "fulfilled",
      value: { status: "completed", data: { success: true, saved: "second" } },
    },
  ]);
  assert.deepEqual(states, ["fresh-csrf"]);
});

test("a resend rejected for its CSRF token again is reported and not sent a third time", async () => {
  const wire = scriptedTransport([
    signedInWith("stale-csrf"),
    response(csrfRejected, 400),
    signedInWith("fresh-csrf"),
    response(csrfRejected, 400),
  ]);
  const services = createServices(wire.transport);
  await services.session.load();
  await assert.rejects(
    services.api.request("/api/settings", { method: "POST", json: {} }),
    (error) => {
      assert.ok(error instanceof ApiRequestError);
      assert.equal(error.status, 400);
      assert.equal(error.error.error_code, "session_verification_expired");
      return true;
    },
  );
  assert.deepEqual(sent(wire), [
    ["GET", "/api/session", null, null],
    ["POST", "/api/settings", "stale-csrf", "{}"],
    ["GET", "/api/session", null, null],
    ["POST", "/api/settings", "fresh-csrf", "{}"],
  ]);
});

for (const [name, path, rejection] of [
  [
    "a refused write",
    "/api/settings",
    {
      success: false,
      error_code: "invalid_request",
      message: "Request body must be a JSON object.",
      category: "error",
    },
  ],
  [
    "a refused direct action",
    "/api/shuffle/playlist-1",
    directRejected(
      "invalid_request",
      "Check the shuffle method and its options.",
    ),
  ],
]) {
  test(`${name} is reported without a session read or a resend`, async () => {
    const wire = scriptedTransport([
      signedInWith("stale-csrf"),
      response(rejection, 400),
    ]);
    const services = createServices(wire.transport);
    await services.session.load();
    const states = published(services);
    await assert.rejects(
      services.api.request(path, { method: "POST", json: {} }),
      (error) => {
        assert.ok(error instanceof ApiRequestError);
        assert.equal(error.status, 400);
        assert.equal(error.error.error_code, "invalid_request");
        return true;
      },
    );
    assert.deepEqual(sent(wire), [
      ["GET", "/api/session", null, null],
      ["POST", path, "stale-csrf", "{}"],
    ]);
    assert.deepEqual(states, []);
  });
}

test("a CSRF rejection code on a status other than 400 is reported unchanged, without a session read or a resend", async () => {
  const wire = scriptedTransport([
    signedInWith("stale-csrf"),
    response(csrfRejected, 403),
  ]);
  const services = createServices(wire.transport);
  await services.session.load();
  const states = published(services);
  await assert.rejects(
    services.api.request("/api/settings", { method: "POST", json: {} }),
    (error) => {
      assert.ok(error instanceof ApiRequestError);
      assert.equal(error.status, 403);
      assert.deepEqual(error.error, csrfRejected);
      return true;
    },
  );
  assert.deepEqual(sent(wire), [
    ["GET", "/api/session", null, null],
    ["POST", "/api/settings", "stale-csrf", "{}"],
  ]);
  assert.deepEqual(states, []);
});

for (const [outcome, sessionRead, states] of [
  ["finds the session ended", response(signedOut), ["signed-out"]],
  [
    "fails",
    () => {
      throw new TypeError("connection lost");
    },
    [],
  ],
]) {
  test(`when the session read after a CSRF rejection ${outcome}, the rejection stands and nothing is resent`, async () => {
    const wire = scriptedTransport([
      signedInWith("stale-csrf"),
      response(csrfRejected, 400),
      sessionRead,
    ]);
    const services = createServices(wire.transport);
    await services.session.load();
    const seen = published(services);
    await assert.rejects(
      services.api.request("/api/settings", { method: "POST", json: {} }),
      (error) => {
        assert.ok(error instanceof ApiRequestError);
        assert.equal(error.status, 400);
        assert.equal(error.error.error_code, "session_verification_expired");
        return true;
      },
    );
    assert.deepEqual(sent(wire), [
      ["GET", "/api/session", null, null],
      ["POST", "/api/settings", "stale-csrf", "{}"],
      ["GET", "/api/session", null, null],
    ]);
    assert.deepEqual(seen, states);
  });
}

test("409 exposes the busy operation and never replays admission", async () => {
  const wire = scriptedTransport([
    response(apiError("operation_busy", { operation_id: 42 }), 409),
  ]);
  const client = createApiClient({
    transport: wire.transport,
    getCsrfToken: () => "csrf",
  });
  await assert.rejects(
    client.request("/api/shuffle", { method: "POST", json: {} }),
    (error) => {
      assert.ok(error instanceof ConflictError);
      assert.equal(error.error.operation_id, 42);
      assert.equal(error.status, 409);
      return true;
    },
  );
  assert.equal(wire.requests.length, 1);
});

test("429 preserves the full largest retry hint without replaying a write", async () => {
  const wire = scriptedTransport([
    response(apiError("rate_limited", { retry_after: 90 }), 429, {
      "Retry-After": "120",
    }),
  ]);
  const client = createApiClient({
    transport: wire.transport,
    getCsrfToken: () => "csrf",
  });
  await assert.rejects(
    client.request("/api/shuffle", { method: "POST", json: {} }),
    (error) => {
      assert.ok(error instanceof RateLimitError);
      assert.equal(error.retryAfterMs, 120_000);
      return true;
    },
  );
  assert.equal(wire.requests.length, 1);
});

test("a 429 with a null retry_after is a rate limit with its message and no hint", async () => {
  const message = "Spotify is busy. Nothing was changed; try again shortly.";
  const client = createApiClient({
    transport: async () =>
      response(
        apiError("rate_limited", {
          message,
          retryable: true,
          retry_after: null,
        }),
        429,
      ),
  });
  await assert.rejects(
    client.request("/api/playlist/p1/setup/context"),
    (error) => {
      assert.ok(error instanceof RateLimitError);
      assert.equal(error.message, message);
      assert.equal(error.retryAfterMs, null);
      return true;
    },
  );
});

for (const [name, error, deadline] of [
  [
    "a 429 waits its retry hint",
    new RateLimitError(apiError("rate_limited"), 30_000),
    1_030_000,
  ],
  [
    "a 429 without a retry hint waits one second",
    new RateLimitError(apiError("rate_limited")),
    1_001_000,
  ],
  [
    "a 503 waits its retry hint",
    new ApiRequestError(503, apiError("unavailable"), 30_000),
    1_030_000,
  ],
  [
    "a 503 without a retry hint does not wait",
    new ApiRequestError(503, apiError("unavailable")),
    null,
  ],
  [
    "a 400 with a retry hint does not wait",
    new ApiRequestError(400, apiError("invalid_request"), 30_000),
    null,
  ],
  [
    "a network failure does not wait",
    new NetworkError("POST", new TypeError("Failed to fetch")),
    null,
  ],
]) {
  test(`retryDeadline: ${name}`, () => {
    assert.equal(retryDeadline(error, 1_000_000), deadline);
  });
}

test("network failure marks unsafe admission unknown and never retries", async () => {
  const wire = scriptedTransport([
    () => {
      throw new TypeError("connection lost");
    },
  ]);
  const client = createApiClient({
    transport: wire.transport,
    getCsrfToken: () => "csrf",
  });
  await assert.rejects(
    client.request("/api/shuffle", {
      method: "POST",
      json: { request_key: "frozen-key" },
    }),
    (error) => {
      assert.ok(error instanceof NetworkError);
      assert.equal(error.admissionUnknown, true);
      return true;
    },
  );
  assert.equal(wire.requests.length, 1);
});

test("poll waits through running and compensating until the actual terminal result", async () => {
  const terminal = {
    ...operation("rolled_back"),
    result: { success: false, message: "Restored." },
  };
  const wire = scriptedTransport([
    response(context(operation("running"))),
    response(context(operation("compensating"))),
    response(context(terminal)),
  ]);
  const client = createApiClient({ transport: wire.transport });
  const waits = [];
  const updates = [];
  assert.deepEqual(
    await pollOperation(client, queued.id, {
      intervalMs: 7,
      wait: async (ms) => {
        waits.push(ms);
      },
      onUpdate: (value) => {
        updates.push(value.state);
      },
    }),
    terminal,
  );
  assert.deepEqual(waits, [7, 7]);
  assert.deepEqual(updates, ["running", "compensating", "rolled_back"]);
  assert.ok(
    wire.requests.every(
      (request) =>
        request.init.method === "GET" &&
        request.input === `/api/operations/${queued.id}`,
    ),
  );
});

test("poll honors complete 429 retry hint before the next GET", async () => {
  const wire = scriptedTransport([
    response(apiError("rate_limited", { retry_after: 90 }), 429),
    response(context(operation("succeeded"))),
  ]);
  const client = createApiClient({ transport: wire.transport });
  const waits = [];
  await pollOperation(client, queued.id, {
    intervalMs: 2,
    wait: async (ms) => {
      waits.push(ms);
    },
  });
  assert.deepEqual(waits, [90_000]);
});

test("simultaneous observers share one poll loop and one observer can abort alone", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const wire = scriptedTransport([
    async () => {
      await gate;
      return response(context(operation("running")));
    },
    response(context(operation("needs_attention"))),
  ]);
  const client = createApiClient({ transport: wire.transport });
  const abort = new AbortController();
  const first = pollOperation(client, queued.id, {
    signal: abort.signal,
    wait: async () => {},
  });
  const firstRejected = assert.rejects(first, { name: "AbortError" });
  const second = pollOperation(client, queued.id, { wait: async () => {} });
  abort.abort();
  release();
  await firstRejected;
  assert.equal((await second).state, "needs_attention");
  assert.equal(wire.requests.length, 2);
  assert.equal(wire.requests[0].init.signal.aborted, false);
});

test("aborting every observer stops only polling and passes abort to pending GET", async () => {
  let observedSignal;
  const wire = scriptedTransport([
    (_input, init) =>
      new Promise((_resolve, reject) => {
        observedSignal = init.signal;
        init.signal.addEventListener(
          "abort",
          () => {
            reject(init.signal.reason);
          },
          { once: true },
        );
      }),
  ]);
  const client = createApiClient({ transport: wire.transport });
  const abort = new AbortController();
  const result = pollOperation(client, queued.id, { signal: abort.signal });
  const rejected = assert.rejects(result, { name: "AbortError" });
  await Promise.resolve();
  abort.abort();
  await rejected;
  await Promise.resolve();
  assert.equal(observedSignal.aborted, true);
  assert.equal(wire.requests.length, 1);
  assert.equal(wire.requests[0].init.method, "GET");
});

test("operation reads reject an identity mismatch instead of observing another operation", async () => {
  const client = createApiClient({
    transport: async () => response(context(operation("succeeded", 99))),
  });
  await assert.rejects(pollOperation(client, queued.id), ProtocolError);
});

test("body disconnect preserves unknown write admission and read network classification", async () => {
  for (const method of ["GET", "POST"]) {
    const brokenBody = new ReadableStream({
      start(controller) {
        controller.error(new TypeError("connection interrupted"));
      },
    });
    const client = createApiClient({
      transport: async () => new Response(brokenBody),
      getCsrfToken: () => "csrf",
    });
    await assert.rejects(
      client.request("/api/context", { method }),
      (error) => {
        assert.ok(error instanceof NetworkError);
        assert.equal(error.admissionUnknown, method === "POST");
        return true;
      },
    );
  }
});

test("session rejects an unknown auth notice code instead of displaying untyped OAuth text", async () => {
  const client = createApiClient({
    transport: async () =>
      response({
        ...signedOut,
        notice: {
          code: "raw_provider_error",
          category: "error",
          message: "untyped",
        },
      }),
  });
  await assert.rejects(client.getSession(), ProtocolError);
});

test("poll exhausts bounded transient read retries and leaves admission untouched", async () => {
  const wire = scriptedTransport(
    Array.from({ length: 4 }, () => () => {
      throw new TypeError("offline");
    }),
  );
  const client = createApiClient({ transport: wire.transport });
  const waits = [];
  await assert.rejects(
    pollOperation(client, queued.id, {
      wait: async (ms) => {
        waits.push(ms);
      },
    }),
    NetworkError,
  );
  assert.deepEqual(waits, [1000, 2000, 4000]);
  assert.equal(wire.requests.length, 4);
  assert.ok(wire.requests.every((request) => request.init.method === "GET"));
});

test("effect replay rejoins the original GET before cancelling its previous observation", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const wire = scriptedTransport([
    async () => {
      await gate;
      return response(context(operation("succeeded")));
    },
  ]);
  const client = createApiClient({ transport: wire.transport });
  const abort = new AbortController();
  const first = pollOperation(client, queued.id, { signal: abort.signal });
  const firstRejected = assert.rejects(first, { name: "AbortError" });
  abort.abort();
  const second = pollOperation(client, queued.id);
  release();
  await firstRejected;
  assert.equal((await second).state, "succeeded");
  assert.equal(wire.requests.length, 1);
  assert.equal(wire.requests[0].init.signal.aborted, false);
});

test("aborting the last observer clears the real poll timer", async () => {
  let progress;
  const seen = new Promise((resolve) => {
    progress = resolve;
  });
  const wire = scriptedTransport([response(context(operation("running")))]);
  const client = createApiClient({ transport: wire.transport });
  const abort = new AbortController();
  const observing = pollOperation(client, queued.id, {
    signal: abort.signal,
    intervalMs: 60_000,
    onUpdate: progress,
  });
  const rejected = assert.rejects(observing, { name: "AbortError" });
  await seen;
  abort.abort();
  await rejected;
  assert.equal(wire.requests.length, 1);
});

test("HTTP-date Retry-After retains its actual future delay", async () => {
  const retryAt = Date.parse("Wed, 21 Oct 2099 07:28:00 GMT");
  const before = Date.now();
  const client = createApiClient({
    transport: async () =>
      response(apiError("rate_limited"), 429, {
        "Retry-After": new Date(retryAt).toUTCString(),
      }),
  });
  await assert.rejects(client.request("/api/context"), (error) => {
    assert.ok(error instanceof RateLimitError);
    assert.ok(error.retryAfterMs >= retryAt - Date.now());
    assert.ok(error.retryAfterMs <= retryAt - before);
    return true;
  });
});

test("HTTP 401 invalidates session even when its JSON envelope is broken", async () => {
  let unauthorized = 0;
  const client = createApiClient({
    transport: async () => new Response("{broken", { status: 401 }),
    onUnauthorized: () => {
      unauthorized += 1;
    },
  });
  await assert.rejects(client.request("/api/context"), ProtocolError);
  assert.equal(unauthorized, 1);
});
