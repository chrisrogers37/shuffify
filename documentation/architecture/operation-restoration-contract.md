# Reviewed operation restoration

This backend contract supports a separate, explicitly reviewed operation. It does not rerun the original action. The React frontend's restoration controls use it.

## Read the review

`GET /api/operations/{parent_id}/restore-context` requires the signed-in owner of the original operation. Ownership is checked locally before Spotify authentication or reads. Foreign and missing operations return the same 404 response.

Only an owned `needs_attention` operation with confirmed, known writable effects is eligible. An `operation_restore` child cannot become another restoration parent; both review and admission return `nested_restoration_unavailable` locally. Unknown dispatched original or compensation requests block review. Created playlists cannot be recreated, adopted, deleted, or restored through this action. A current unaccepted step may be restored; previously accepted children and read-only dependencies are guards, never write destinations.

A successful response has the usual success envelope and a `context` containing:

- `kind: "operation_restore"`, `operation_id`, and `available: true`;
- `reviewed`, with `version: 1`, the server's scope fingerprint, and one current-resource digest for every reserved resource, ordered by playlist ID;
- `resources`, with playlist ID/name, `role` (`restore` or `guard`), observation `scope` (`tracks` or `metadata`), `write_fields`, current/desired counts, ordered previews, and current/desired values of only the written metadata fields.

Track previews contain at most 20 URIs and preserve duplicate occurrences. Metadata strings contain at most 1,024 characters; `metadata_truncated` identifies truncation. Names contain at most 255 characters. The digest always binds the complete values, never the preview. Track digests also bind the fresh snapshot. Metadata-only observations do not require representable track items or bind unrelated track snapshots. At most 100 total resources are permitted; a larger scope returns `restore_scope_too_large` before any provider read and remains forbidden at admission and worker recapture.

The review exposes no journal, plans, baselines, compensation document, command payload, credentials, or lease tokens. Contract types are in `web/src/api/contracts.ts`; generated schemas and shared fixtures are in `web/schemas` and `web/fixtures`.

## Confirm one immutable request

`POST /api/operations/{parent_id}/restore` accepts only:

```json
{"reviewed":{"version":1,"reviewed_scope":"<64 lowercase hex characters>","reviewed_resources":[{"playlist_id":"<id>","digest":"<64 lowercase hex characters>"}]}}
```

The client should retain a stable `Idempotency-Key` with this exact request body until the admission outcome is known. A successful admission returns 202, an owned `operation_id`/operation DTO, and a `Location` header for status polling. Admission performs local database reads only. Desired orders and metadata are derived from the owned original journal; client-selected desired contents are rejected.

Exact-key replay returns the same child before volatile capture, even after parent resolution or an unresolved child. Reusing a key with different review contents returns `request_key_conflict`. An unknown browser response must not cause automatic POST replay with a new key. Poll only the accepted ID; a 409's blocking ID does not authorize inferring or rerunning another action.

A parent row lock remains held through the enqueue transaction. Only one unresolved restoration child may exist for an owned parent. `restoration_attempt_unresolved` includes its owned `operation_id`. A new review is allowed after a conclusively finished failed refusal with no dispatched or attempted original or compensation intent and no accepted boundary. A terminal state alone is insufficient. An attempted, rolled-back, partial, or unknown child remains blocked; this package has no grandchild reconciliation workflow.

## Worker and publication

The worker reserves every parent target. It revalidates owned parent scope, authorizes all resources, and compares all review digests before the first write. A changed dependency refuses the entire restoration before dispatch. It replaces only known current-step track effects, preserving exact order and duplicates, and changes only metadata fields actually written by the original current step. Unrelated current metadata is preserved. Earlier accepted and read-only resources are freshly guarded before writing and during recovery.

One composite boundary covers all restoration writes. Its complete fresh proof precedes history preparation, required lock reconciliation, optional Activity, and parent resolution. The same fenced local transaction publishes the accepted child receipt, working history, and `parent.journal.runtime.restoration.operation_id`. The original parent's state, result, plans, and history are retained. The original recovery projection becomes unavailable with reason `operation_already_restored` and `resolved_operation_id`.

A lost atomic-acceptance reply uses the proven receipt. Local history/lock/receipt failures after verified provider effects return attention without provider compensation and without claiming parent resolution. Provider failure before acceptance compensates only this restoration child, retaining the original parent's state. Deferred requests keep the same child and honor Retry-After. No create/delete operation is issued.

## Results and refusals

An accepted child exposes `result.parent_operation_id`, `parent_resolution` (`complete`, `unknown`, `not_started`), `restored_resource_count`, `changed_position_count`, bounded field names and authorized resource facts. `changed_position_count` counts differing positions in the reviewed order versus desired order, including additions/removals. A verified no-op has zero counts. A confirmed child rollback has zero net restoration counts and reports its reviewed starting resource facts. A fresh unresolved conflict invalidates current contents and counts; a local publication-only failure may retain already verified provider facts.

Common authored refusal codes include `operation_not_restorable`, `write_completion_unknown`, `created_playlist_requires_review`, `restore_scope_too_large`, `restoration_attempt_unresolved`, `operation_already_restored`, `restore_scope_changed`, `restore_review_changed`, and `invalid_restore_review`. Read failures use `authentication_required`, `restore_access_changed`, `unsupported_playlist_items`, `restore_read_changed`, or `restore_read_failed`. A provider 429 returns `rate_limited` with the original positive integer `Retry-After`/`retry_after` when supplied. Messages never echo private provider or database errors.

Existing operation recovery data remains bounded. `available: true, action: "restore"` means local eligibility to load this review, not permission to bypass it. Unresolved child projections include `restoration_operation_id`; confirmed resolution projections include `resolved_operation_id`. Clients must use these authored fields instead of inspecting private payloads or guessing action types from blocking IDs.
