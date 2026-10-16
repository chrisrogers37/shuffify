# Durable direct operations

Shuffle, Undo, visibility changes, snapshot restoration and Workshop target
commits are admitted by the API and executed by `python -m shuffify.worker`.
Their HTTP handlers validate the session, database, CSRF token and local input
without constructing a Spotify client. Read endpoints can contact Spotify.

## Admission and status

A valid write returns HTTP `202`, `operation_id`, a sanitized `operation`, and
`Location: /api/operations/<id>`. Acceptance means the command is saved; it does
not mean Spotify has changed. The owning browser polls that location for the
terminal result. Status requests require the session and database, and do not
authenticate with Spotify. Another user receives `404`.

Clients send `Idempotency-Key` with each distinct submitted intent. Keys contain
1–128 characters. A matching retry returns the original operation before
reading current history or a selected snapshot again. Changing that intent
with the same key returns `409 request_key_conflict`. An active operation
reserves all its targets, so another command for a reserved target returns
`409 playlist_busy` with its operation ID. Commands without a key still reserve
their targets but cannot identify a retry after completion.

The command records the validated input, selected snapshot contents and relevant
browser history binding. Snapshot retries therefore preserve the accepted order
even if the snapshot is subsequently deleted. Commands contain neither the full
Flask session nor credentials. Public response schemas are generated from
`web/src/api/contracts.ts`; command schemas remain private worker input.

## Execution and recovery

The worker owns a renewable lease and sorted playlist locks, plus a credential
lock for the user. Randomized algorithm choices and original observations are
checkpointed before any mutation. Each HTTP batch has a durable intent and
acknowledgment. Command steps retain their plan ranges, results and working
history so recovery does not select another shuffle or treat a completed write
as its original baseline.

Visibility plans authorize and reconcile metadata without reading playlist
items. Even a visibility no-op has a frozen metadata observation and fresh
verification on recovery. Unsupported playlist items therefore do not prevent
a metadata-only change.

Confirmed prefixes can continue from the journal. A dispatched request observed
at its baseline is ambiguous without a settlement guarantee and requires review.
An external edit produces `needs_attention`; the worker does not overwrite it.
Failures that require restoration persist their abort direction before separate
journaled compensation starts. Legacy compensation loops cannot run under the
durable observer. Rate-limit rejections persist their retry deadline, release
the lease, and resume the same operation when due. Other ready targets continue
while one operation is deferred.

The worker drains at startup and after PostgreSQL notifications. A dedicated
`LISTEN` connection also wakes the worker after registration or reconnect, which
covers commits whose notification was missed. Known lease and retry deadlines
arrange their own wake; there is no idle database polling loop. SIGTERM stops
new claims and allows the active command to settle.

The worker's public `GET /health` reports whether its serving loop is running and
accepting work. It does not require scheduler leadership: a replacement can
become healthy while the incumbent drains. Authenticated `/internal/health`
and registration reads report the actual scheduler leader and registration
state independently. Only the holder of the scheduler advisory lock installs
and fires schedules; a healthy standby cannot claim that those schedules are
registered locally. A stopped or draining worker returns 503.

Before another prepared track batch, an uncached metadata read compares both
its snapshot and frozen playlist metadata. A mismatch requires a full read;
initial, recovered and final reconciliation still read complete contents.
Unacknowledged dispatched writes cannot use this shortcut. Transient read
failures and exhausted budgets persist a retry deadline and retain the current
forward or compensation direction. No provider request runs before that deadline.
Compensation receives an independent bounded budget instead of inheriting a
spent forward budget.

Recovered protection requires a verified nonempty current boundary. A fresh
complete reconciliation also retains its own proof before checkpointing it,
so a database publication failure cannot reverse freshly verified effects.
An empty next boundary cannot inherit protection from an accepted predecessor.

A saved terminal candidate never replays forward work. Lost access, missing
playlists or unsupported items settle it as `needs_attention`, invalidating
current-state claims and releasing its active reservations. Three unclassified
verification failures also require attention. Lease loss and explicit deferral
remain worker controls and do not consume that failure allowance.

The scheduler recovers the most recent eligible due instant within its one-hour
grace window after restart or leadership transfer. Durable scheduled-fire
identity prevents duplicate admission. Definition and enabled-state changes
stamp a nullable `Schedule.definition_changed_at` in the same transaction;
execution bookkeeping leaves it unchanged. Existing rows without that stamp
use their last update conservatively. Interval eligibility starts one interval
after creation, retaining the original cadence phase. Pause and Resume cannot
backfill a slot that predates the current eligible definition.

## History and local effects

Undo history is isolated by user and an opaque server-generated browser scope.
The latest confirmed operation receipt is authoritative; old Flask-session
history is imported only when no receipt exists. Clear receipts are tombstones.
A library loads its requested histories in one scoped query that projects only
receipts, rather than repeatedly fetching complete command journals.

History becomes visible only when its operation has a matching confirmed
terminal state. Polling never mirrors it into the browser session. Undo freezes
the selected revision and refuses a newer revision before dispatch. A rollback
retains the original history. Failed history confirmation preserves verified
Spotify facts and produces `needs_attention` without compensating a verified
save solely because history failed.

Snapshot creation and retention, activity logging, and track-lock reconciliation
use short fenced local transactions. Each effect and its receipt commit
together. Optional bookkeeping failures have durable receipts and do not repeat
on recovery. No database transaction spans Spotify I/O. Schedule deletion
detaches durable operations instead of deleting their history receipts, and
legacy stale-execution cleanup excludes durable operations.

## Verification

Tests exercise the real Spotify API batching and HTTP observer against local
providers, including lost responses, crash boundaries, duplicate occurrences,
partial batches, durable deferral, history publication and bookkeeping receipts.
Admission and polling tests forbid Spotify authentication and transport calls.
PostgreSQL tests cover keyed admission races, claim fencing, lock ownership and
notification reconnects. The isolated live harness runs the API and idle worker
as separate processes and checks the provider's actual playlist contents.
