# Durable operation execution

`job_executions` stores both execution history and durable commands. Rows with
`state IS NULL` are history and are never claimed. Commands reserve their sorted
target set while queued, running, or compensating. Admission checks the request
key before target conflicts: an identical request returns its accepted identity;
reusing a key for a different command is a conflict.

All interactive queue routes share a fixed window of **10 new submissions per
minute per signed-in Spotify user**. A Workshop save can submit a target
commit followed by several source cleanup tasks. Ten leaves room for that
sequence and a second edit within the minute. Admission first resolves the
idempotency key and checks the five-outstanding-operation cap. It then charges
the Redis-backed budget before capturing the command, so a new attempt that
fails capture or finds a busy target still counts.

An exhausted window returns HTTP 429 with `rate_limited` and a `Retry-After`
in seconds when Redis supplies the reset time. At five outstanding operations
the response is `outstanding_operation_limit` and the user can retry when an
operation finishes. Exact replays return the original operation, and reusing
a key for different intent returns HTTP 409. Scheduled fires, worker retries,
status polls and inspect-only finalizer requests spend no interactive budget.

The worker drains interactive operations and scheduled fires through the same
untargeted claim. Each ready row waits from its stored `started_at` or its
owner's most recent untargeted claim, whichever is later. The longest wait
wins. Ties go to the oldest `started_at`, then the lowest operation ID.
Admission sets `started_at`. Claiming a queued row resets it to the claim
time. Deferral and expired-lease recovery preserve that stored time. A deferred
row becomes eligible at its retry deadline.

When Alice queues five operations and Bob queues one, with all six ready before
the first claim, Alice gets the first turn and Bob gets the second. Alice's
remaining operations keep their order. The lease transaction records
`last_claimed_at` on the selected row. Workers share this history across
restarts and can look it up by owner and claim time. Existing rows begin with
no recorded turn, and ownerless commands share one turn group.
A targeted claim checks only the requested row and leaves turn history alone.
Concurrent PostgreSQL claimers rank the committed turns visible to them and
skip locked operations, so simultaneous grants can go to the same owner.

The repository owns short database transactions. PostgreSQL row locks with
`SKIP LOCKED` coordinate claims, and each claim receives a fresh random lease
owner token. Checkpoints, renewals, and completion require that unexpired token.
Dedicated session advisory locks cover every target across business commits.
Lease expiry permits recovery but does not permit writes while another runner
still holds a target lock. Background commands also run one at a time per
user. The refresh token needs no lock of theirs: every refresh, the web's
included, locks the users row in a short transaction of its own
(`CredentialRefresher`), so a web refresh never waits for a run.

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
