# Durable operation execution

`job_executions` stores both execution history and durable commands. Rows with
`state IS NULL` are history and are never claimed. Commands reserve their sorted
target set while queued, running, or compensating. Admission checks the request
key before target conflicts: an identical request returns its accepted identity;
reusing a key for a different command is a conflict.

The repository owns short database transactions. PostgreSQL row locks with
`SKIP LOCKED` coordinate claims, and each claim receives a fresh random lease
owner token. Checkpoints, renewals, and completion require that unexpired token.
Dedicated session advisory locks cover every target across business commits.
Lease expiry permits recovery but does not permit writes while another runner
still holds a target lock. Background commands also serialize each user's
credential use so rotated refresh tokens do not race between workers.

`OperationWorker` supplies a runtime to registered command handlers. The runtime
constructs Spotify access lazily, persists prepared JSON choices, and records
step outputs with their mutation-plan ranges. A resumed handler reuses those
choices and skips completed effects. Heartbeats use independent transactions.
SIGTERM prevents new claims and wakes an idle runner; an active command keeps
its locks and lease until it finishes. A process interruption leaves its running
or compensating row available for recovery. Known lease deadlines arrange a
wake without an idle database polling loop.

## Write-ahead journal

The Spotify mutation observer freezes a complete replacement, insertion,
removal, details change, or creation before dispatch. Track orders retain
duplicate occurrences. Replacement plans include the initial PUT, every
100-item append, and the empty-playlist PUT. Each actual HTTP attempt has a
persisted intent; confirmed batches retain Spotify's snapshot ID and progress.

Recovery reads every affected resource before resuming any write. Complete,
uncached, authorized reads compare ordered contents, metadata, and snapshots
with the recorded baseline and batch boundaries. A recognized boundary can
continue the frozen plan; an unrecognized change becomes `needs_attention`
without modifying the observed playlist. An ambiguous dispatch still at its
baseline requires evidence of settlement before it can be replayed.

Compensation has a separate frozen plan and cursor. Recovery of compensation
finishes that plan rather than restarting the original command. It also stops
if an external edit is observed. A dispatched creation whose response was lost
is never posted again: Spotify supplies no creation idempotency key, so an
unidentified playlist requires review. A returned creation identity is retained
and reused by a resumed command.

Spotify replacement does not provide a universal compare-and-swap operation.
Fresh reads detect observed external changes, but an independent editor can
still race the interval between a read and a write. Snapshot IDs do not make
Spotify and PostgreSQL one transaction.

## Public status and time budgets

`GET /api/operations/<id>` authorizes from the session and database without
constructing a Spotify client. It returns the owner's state, bounded batch
progress, stored service result, and recovery summary. Commands, journals,
journal baseline arrays, and lease tokens remain private. Stored service results
retain the public current-state fields needed by the recovery UI. The TypeScript
operation context generates its JSON schema alongside the page contracts.

API requests have one Spotify time budget covering authentication, paging, and
retries. Workers use a longer operation scope. A retry cannot sleep through its
remaining budget, and a rate-limit response retains its original `Retry-After`.
Journaled rate limits persist a retry deadline and release the claim until it
is due; recovery performs no Spotify reads or writes before that deadline.
The budget context is reset even when a view fails.
