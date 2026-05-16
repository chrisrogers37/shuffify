# F6 — Neon forensic SQL for WOOKLYN loss timeline

**Investigation:** [00_INVESTIGATION.md](00_INVESTIGATION.md)
**Status:** IN PROGRESS — Started 2026-05-16
**Targets:** "What has WOOKLYN actually lost, and which snapshot do I restore from?"
**Risk:** None — read-only SQL. Effort: Small.

## Implementation decisions (2026-05-16)

- **Broader playlist match.** Resolve `(user_id, playlist_id)` candidates via
  the union of: (a) `schedules` rows whose `target_playlist_name` matches the
  pattern, and (b) `playlist_snapshots` rows whose `playlist_name` matches.
  This catches playlists that were renamed mid-history — the snapshot table
  stores the name at-snapshot, so it preserves prior names.
- **Parameterized time window.** Default 60 days, override via
  `-v days_back=365`. Keeps the default query fast while supporting wider
  sweeps when needed.
- **Restore quick-card section.** A final section emits a one-row "this is the
  snapshot to restore from right now" answer with the exact `flask shell`
  snippet to apply it. Each numbered section is its own CTE so they can be
  run in isolation while iterating.

## Context

We have one direct piece of evidence — the `2026-05-13` DigitalOcean warning
about Schedule 11. Everything else is hypothesis. Before any code change ships,
we need to confirm the scope of damage in Neon:

- How many WOOKLYN rotations have run?
- For each run: did the executor claim success? What were `tracks_added` /
  `tracks_total`?
- How does the pre-snapshot track count compare to the post-run reported
  total?
- Which snapshot is the most-recent intact version of WOOKLYN, and what's its
  ID for restoration?

This fix is **purely operational** — a SQL script + a documented run procedure.
No application code changes.

## Files touched

| File | Change |
|------|--------|
| `scripts/forensics/wooklyn_loss_timeline.sql` | New file — the query (parameterized on playlist name) |
| `scripts/forensics/README.md` | New — run instructions, expected output, how to act on results |

Note: `scripts/` already exists in the repo (per the `gitStatus` at session
start). Place `forensics/` as a subdirectory there.

## Query

```sql
-- scripts/forensics/wooklyn_loss_timeline.sql
--
-- Read-only forensic timeline for any playlist matched by name.
-- Joins schedules × job_executions × playlist_snapshots × activity_log.
--
-- Parameter:
--   :playlist_name_pattern (e.g., '%WOOKLYN%')
--
-- Usage (psql, read-only, with statement_timeout):
--   psql "$DATABASE_URL" \
--     -v playlist_name_pattern="'%WOOKLYN%'" \
--     -f scripts/forensics/wooklyn_loss_timeline.sql

\set ON_ERROR_STOP on
SET default_transaction_read_only = on;
SET statement_timeout = '60s';

-- 1) Identify the playlist(s) and their schedules.
\echo '== Schedules targeting playlists matching pattern =='
SELECT
    s.id              AS schedule_id,
    s.user_id,
    s.job_type,
    s.target_playlist_id,
    s.target_playlist_name,
    s.algorithm_params,
    s.is_enabled,
    s.last_run_at,
    s.last_status,
    s.last_error
FROM schedules s
WHERE s.target_playlist_name ILIKE :playlist_name_pattern
ORDER BY s.last_run_at DESC NULLS LAST;

-- 2) Job execution history for those schedules (last 60 days).
\echo
\echo '== Recent job executions =='
WITH matched_schedules AS (
    SELECT id, user_id, target_playlist_id, target_playlist_name
    FROM schedules
    WHERE target_playlist_name ILIKE :playlist_name_pattern
)
SELECT
    je.id              AS execution_id,
    ms.target_playlist_name,
    je.schedule_id,
    je.status,
    je.started_at,
    je.completed_at,
    je.tracks_added,
    je.tracks_total,
    EXTRACT(EPOCH FROM (je.completed_at - je.started_at))::int
                        AS duration_seconds,
    LEFT(je.error_message, 200) AS error_message_preview
FROM job_executions je
JOIN matched_schedules ms ON ms.id = je.schedule_id
WHERE je.started_at >= NOW() - INTERVAL '60 days'
ORDER BY je.started_at DESC;

-- 3) Snapshot timeline with track counts (last 60 days).
\echo
\echo '== Snapshot timeline =='
WITH matched_playlists AS (
    SELECT DISTINCT user_id, target_playlist_id AS playlist_id
    FROM schedules
    WHERE target_playlist_name ILIKE :playlist_name_pattern
)
SELECT
    ps.id              AS snapshot_id,
    ps.user_id,
    ps.playlist_id,
    ps.playlist_name,
    ps.snapshot_type,
    ps.trigger_description,
    ps.track_count,
    ps.created_at
FROM playlist_snapshots ps
JOIN matched_playlists mp
  ON mp.user_id = ps.user_id
 AND mp.playlist_id = ps.playlist_id
WHERE ps.created_at >= NOW() - INTERVAL '60 days'
ORDER BY ps.created_at DESC;

-- 4) Activity log entries (last 60 days).
\echo
\echo '== Activity log =='
WITH matched_playlists AS (
    SELECT DISTINCT user_id, target_playlist_id AS playlist_id
    FROM schedules
    WHERE target_playlist_name ILIKE :playlist_name_pattern
)
SELECT
    al.id,
    al.activity_type,
    LEFT(al.description, 200) AS description_preview,
    al.metadata_json,
    al.created_at
FROM activity_log al
JOIN matched_playlists mp
  ON mp.user_id = al.user_id
 AND mp.playlist_id = al.playlist_id
WHERE al.created_at >= NOW() - INTERVAL '60 days'
ORDER BY al.created_at DESC;

-- 5) Loss reconstruction — pair each execution with the
--    snapshot taken closest before it (the "pre" snapshot) and
--    the next snapshot taken after it (the "post" if auto-snapshot
--    on the next rotation captured a hint).
\echo
\echo '== Pre/post snapshot pairing per execution =='
WITH matched_playlists AS (
    SELECT id              AS schedule_id,
           user_id,
           target_playlist_id AS playlist_id,
           target_playlist_name
    FROM schedules
    WHERE target_playlist_name ILIKE :playlist_name_pattern
),
exec_with_pre AS (
    SELECT
        je.id                                    AS execution_id,
        je.schedule_id,
        je.status,
        je.started_at,
        je.tracks_total                          AS reported_total,
        mp.user_id,
        mp.playlist_id,
        mp.target_playlist_name,
        (SELECT ps.id
           FROM playlist_snapshots ps
          WHERE ps.user_id = mp.user_id
            AND ps.playlist_id = mp.playlist_id
            AND ps.created_at <= je.started_at
          ORDER BY ps.created_at DESC
          LIMIT 1)                              AS pre_snapshot_id,
        (SELECT ps.track_count
           FROM playlist_snapshots ps
          WHERE ps.user_id = mp.user_id
            AND ps.playlist_id = mp.playlist_id
            AND ps.created_at <= je.started_at
          ORDER BY ps.created_at DESC
          LIMIT 1)                              AS pre_count,
        (SELECT ps.track_count
           FROM playlist_snapshots ps
          WHERE ps.user_id = mp.user_id
            AND ps.playlist_id = mp.playlist_id
            AND ps.created_at >= je.completed_at
          ORDER BY ps.created_at ASC
          LIMIT 1)                              AS next_snapshot_count
    FROM job_executions je
    JOIN matched_playlists mp ON mp.schedule_id = je.schedule_id
    WHERE je.started_at >= NOW() - INTERVAL '60 days'
)
SELECT
    started_at,
    target_playlist_name,
    execution_id,
    status,
    pre_count,
    reported_total,
    next_snapshot_count,
    (next_snapshot_count - pre_count) AS drift_pre_to_next,
    pre_snapshot_id                   AS restore_from_snapshot_id
FROM exec_with_pre
ORDER BY started_at DESC;

-- 6) Best snapshot to restore from now.
\echo
\echo '== Most recent intact snapshot per matched playlist =='
WITH matched_playlists AS (
    SELECT DISTINCT user_id,
                    target_playlist_id AS playlist_id,
                    target_playlist_name
    FROM schedules
    WHERE target_playlist_name ILIKE :playlist_name_pattern
)
SELECT DISTINCT ON (ps.playlist_id)
    mp.target_playlist_name,
    ps.id            AS snapshot_id,
    ps.snapshot_type,
    ps.track_count,
    ps.created_at
FROM playlist_snapshots ps
JOIN matched_playlists mp
  ON mp.user_id = ps.user_id
 AND mp.playlist_id = ps.playlist_id
ORDER BY ps.playlist_id, ps.created_at DESC;
```

## Companion script (optional but useful)

```bash
# scripts/forensics/run_wooklyn_timeline.sh
#!/usr/bin/env bash
set -euo pipefail

PATTERN="${1:-%WOOKLYN%}"

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL not set. Source from .env or DO app spec first." >&2
    exit 1
fi

psql "$DATABASE_URL" \
    -v ON_ERROR_STOP=1 \
    -v playlist_name_pattern="'${PATTERN}'" \
    -f "$(dirname "$0")/wooklyn_loss_timeline.sql"
```

Make executable, document in `scripts/forensics/README.md`. Pulling
`DATABASE_URL` from Neon's pooled connection string (read-only role
preferred) is the safest route.

## Expected output shape

For each section the script prints a header (`\echo`) followed by a table.
Reading order:

1. **Schedules** — confirm WOOKLYN's `schedule_id`. We expect Schedule 11
   (per the DO log).
2. **Job executions** — count of `success` vs `failed` runs in the last 60
   days, plus reported `tracks_total` per run.
3. **Snapshots** — count of `auto_pre_rotate` rows. Compare track counts
   across successive snapshots to spot losses.
4. **Activity log** — verify `SCHEDULE_RUN` entries align with executions.
5. **Pre/post pairing** — the key correlation: `pre_count` vs
   `next_snapshot_count`. Any negative `drift_pre_to_next` is a silent loss.
6. **Most recent intact snapshot** — `restore_from_snapshot_id` ready to feed
   into the UI's snapshot-restore flow or a direct API call.

## Acting on results

If section (5) shows a string of `drift_pre_to_next < 0` rows, the silent-loss
hypothesis is confirmed at scale and F1+F2 are urgent. If only one or two rows
show drift, the 5/13 incident is plausibly isolated — F1+F2 still ship to
prevent recurrence, but the rollout is less time-pressured.

If the user wants to restore WOOKLYN to its most-recent intact state right
now, use the `snapshot_id` from section (6) and the existing snapshot-restore
UI (or call `PlaylistSnapshotService.restore_snapshot` + `api.update_playlist_tracks`
manually via `flask shell`).

## Verification

Read-only sanity:

```bash
psql "$DATABASE_URL" \
    -v ON_ERROR_STOP=1 \
    -v playlist_name_pattern="'%nonexistent_playlist_name%'" \
    -f scripts/forensics/wooklyn_loss_timeline.sql
```

Expect zero rows in every section — confirms no side effects, query is safe
to run in production.

Then run with `%WOOKLYN%` and inspect.

## Rollout note

Ship before the code fixes — this query is the source of truth for **what
has already happened**, which informs urgency on F1/F2 rollout. It introduces
no code-path risk (read-only) so it can land as soon as it's reviewed.
