import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "../src/api/client.ts";
import { inspectCurrent } from "../src/pages/library/api.ts";

const uri = "spotify:track:" + "a".repeat(22);
const cases = [
  [
    "unknown track intent",
    { kind: "tracks", desired_track_uris: null },
    { kind: "tracks" },
  ],
  [
    "unknown privacy intent",
    { kind: "privacy", desired_public: null },
    { kind: "privacy" },
  ],
  [
    "known empty order",
    { kind: "tracks", desired_track_uris: [] },
    { kind: "tracks", desired_track_uris: [] },
  ],
  [
    "known duplicate order",
    { kind: "tracks", desired_track_uris: [uri, uri] },
    { kind: "tracks", desired_track_uris: [uri, uri] },
  ],
  [
    "known private intent",
    { kind: "privacy", desired_public: false },
    { kind: "privacy", desired_public: false },
  ],
  [
    "known public intent",
    { kind: "privacy", desired_public: true },
    { kind: "privacy", desired_public: true },
  ],
];
for (const [name, intent, expected] of cases) {
  test(`inspection wire body preserves ${name} without null fields or write admission`, async () => {
    const frozen = structuredClone(intent);
    const calls = [];
    const api = createApiClient({
      getCsrfToken: () => "inspection-csrf",
      transport: async (path, init) => {
        const body = JSON.parse(init.body);
        calls.push({ path, method: init.method, body });
        assert.equal(path, "/api/playlist/alice/inspect-live");
        assert.equal(init.method, "POST");
        assert.equal(init.headers.get("X-CSRFToken"), "inspection-csrf");
        assert.equal(init.headers.has("Idempotency-Key"), false);
        assert.deepEqual(body, expected);
        const field =
          intent.kind === "privacy" ? "desired_public" : "desired_track_uris";
        const known = Object.hasOwn(expected, field);
        return Response.json(
          {
            success: known,
            inspection_only: true,
            message: known
              ? "Current state matches."
              : "Retained intent is unavailable.",
            category: known ? "success" : "error",
            ...(known ? {} : { error_code: "upstream_error" }),
            ...(intent.kind === "privacy" && known
              ? { public: expected.desired_public }
              : {}),
            operation: {
              kind:
                intent.kind === "privacy" ? "privacy" : "track_inspection",
              aggregate_status: known ? "complete" : "unknown",
              error_code: known ? null : "intent_unavailable",
              resources: [
                {
                  resource: "alice",
                  status: known ? "not_required" : "unknown",
                  ...(intent.kind === "privacy"
                    ? {
                        current_metadata: {
                          public: expected.desired_public ?? false,
                        },
                      }
                    : {
                        current_track_uris:
                          expected.desired_track_uris ?? [],
                      }),
                },
              ],
            },
          },
          { status: known ? 200 : 502 },
        );
      },
    });
    const result = await inspectCurrent(api, "alice", intent);
    assert.equal(
      result.status,
      Object.keys(expected).length === 1 ? "unavailable" : "match",
    );
    assert.deepEqual(
      intent,
      frozen,
      "Nullable retained intent must remain unchanged",
    );
    assert.equal(
      calls.length,
      1,
      "Inspection dispatches once and never retries a write",
    );
  });
}
