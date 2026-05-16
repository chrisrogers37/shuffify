-- wooklyn_loss_timeline.sql
--
-- Forensic timeline for any playlist matched by name. Joins
--   schedules
--   × job_executions
--   × playlist_snapshots
--   × activity_log
-- to reconstruct what each rotation/raid/shuffle job claimed vs. what
-- the next snapshot observed, surfacing silent track losses.
--
-- Read-only. Enforces SET default_transaction_read_only and a
-- statement_timeout so accidental runs cannot impact production.
--
-- Parameters (set via psql -v):
--   playlist_name_pattern : ILIKE pattern, e.g. '%WOOKLYN%' (REQUIRED)
--   days_back             : history window in days, default 60
--
-- Usage:
--   psql "$DATABASE_URL" \
--     -v ON_ERROR_STOP=1 \
--     -v playlist_name_pattern="'%WOOKLYN%'" \
--     -v days_back=60 \
--     -f scripts/forensics/wooklyn_loss_timeline.sql
--
-- The matched-playlist resolution UNIONs schedule names and
-- snapshot names so playlists renamed mid-history still surface.
--
-- See scripts/forensics/README.md for output interpretation.

\set ON_ERROR_STOP on
SET default_transaction_read_only = on;
SET statement_timeout = '60s';

-- Default days_back when not provided.
\if :{?days_back}
\else
    \set days_back 60
\endif

-- 1) Matched playlists (name match across schedules + snapshots).
\echo '== 1. Matched playlists =='
WITH matched_from_schedules AS (
    SELECT DISTINCT
        user_id,
        target_playlist_id      AS playlist_id,
        target_playlist_name    AS observed_name
    FROM schedules
    WHERE target_playlist_name ILIKE :playlist_name_pattern
),
matched_from_snapshots AS (
    SELECT DISTINCT
        user_id,
        playlist_id,
        playlist_name           AS observed_name
    FROM playlist_snapshots
    WHERE playlist_name ILIKE :playlist_name_pattern
)
SELECT user_id, playlist_id, observed_name, 'schedule' AS source
FROM matched_from_schedules
UNION
SELECT user_id, playlist_id, observed_name, 'snapshot' AS source
FROM matched_from_snapshots
ORDER BY playlist_id, source;

-- 2) Schedules targeting any matched playlist.
\echo
\echo '== 2. Schedules =='
WITH matched_playlists AS (
    SELECT user_id, target_playlist_id AS playlist_id
      FROM schedules
     WHERE target_playlist_name ILIKE :playlist_name_pattern
    UNION
    SELECT user_id, playlist_id
      FROM playlist_snapshots
     WHERE playlist_name ILIKE :playlist_name_pattern
)
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
JOIN matched_playlists mp
  ON mp.user_id = s.user_id
 AND mp.playlist_id = s.target_playlist_id
ORDER BY s.last_run_at DESC NULLS LAST;

-- 3) Job execution history within the window.
\echo
\echo '== 3. Job executions (within window) =='
WITH matched_schedules AS (
    SELECT s.id
    FROM schedules s
    WHERE EXISTS (
        SELECT 1 FROM schedules s2
        WHERE s2.id = s.id
          AND s2.target_playlist_name ILIKE :playlist_name_pattern
    )
    OR EXISTS (
        SELECT 1 FROM playlist_snapshots ps
        WHERE ps.user_id = s.user_id
          AND ps.playlist_id = s.target_playlist_id
          AND ps.playlist_name ILIKE :playlist_name_pattern
    )
)
SELECT
    je.id              AS execution_id,
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
WHERE je.started_at >= NOW() - (INTERVAL '1 day' * :days_back)
ORDER BY je.started_at DESC;

-- 4) Snapshot timeline within the window.
\echo
\echo '== 4. Snapshots (within window) =='
WITH matched_playlists AS (
    SELECT DISTINCT user_id, target_playlist_id AS playlist_id
      FROM schedules
     WHERE target_playlist_name ILIKE :playlist_name_pattern
    UNION
    SELECT DISTINCT user_id, playlist_id
      FROM playlist_snapshots
     WHERE playlist_name ILIKE :playlist_name_pattern
)
SELECT
    ps.id              AS snapshot_id,
    ps.user_id,
    ps.playlist_id,
    ps.playlist_name,
    ps.snapshot_type,
    LEFT(ps.trigger_description, 100) AS trigger_preview,
    ps.track_count,
    ps.created_at
FROM playlist_snapshots ps
JOIN matched_playlists mp
  ON mp.user_id = ps.user_id
 AND mp.playlist_id = ps.playlist_id
WHERE ps.created_at >= NOW() - (INTERVAL '1 day' * :days_back)
ORDER BY ps.created_at DESC;

-- 5) Activity log entries within the window.
\echo
\echo '== 5. Activity log (within window) =='
WITH matched_playlists AS (
    SELECT DISTINCT user_id, target_playlist_id AS playlist_id
      FROM schedules
     WHERE target_playlist_name ILIKE :playlist_name_pattern
    UNION
    SELECT DISTINCT user_id, playlist_id
      FROM playlist_snapshots
     WHERE playlist_name ILIKE :playlist_name_pattern
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
WHERE al.created_at >= NOW() - (INTERVAL '1 day' * :days_back)
ORDER BY al.created_at DESC;

-- 6) Per-execution pre/post snapshot pairing — the silent-loss detector.
--    For each execution, find the snapshot taken just before it
--    (pre_count) and the next snapshot taken after it (next_count).
--    drift_pre_to_next < 0 indicates tracks lost across the run.
\echo
\echo '== 6. Loss reconstruction (drift < 0 = silent loss) =='
WITH matched_playlists AS (
    SELECT
        s.id                     AS schedule_id,
        s.user_id,
        s.target_playlist_id     AS playlist_id,
        s.target_playlist_name
    FROM schedules s
    WHERE s.target_playlist_name ILIKE :playlist_name_pattern
       OR EXISTS (
           SELECT 1 FROM playlist_snapshots ps
           WHERE ps.user_id = s.user_id
             AND ps.playlist_id = s.target_playlist_id
             AND ps.playlist_name ILIKE :playlist_name_pattern
       )
)
SELECT
    je.started_at,
    mp.target_playlist_name,
    je.id                              AS execution_id,
    je.status,
    je.tracks_added,
    je.tracks_total                    AS reported_total,
    (
        SELECT ps.track_count
          FROM playlist_snapshots ps
         WHERE ps.user_id = mp.user_id
           AND ps.playlist_id = mp.playlist_id
           AND ps.created_at <= je.started_at
         ORDER BY ps.created_at DESC
         LIMIT 1
    )                                  AS pre_count,
    (
        SELECT ps.track_count
          FROM playlist_snapshots ps
         WHERE ps.user_id = mp.user_id
           AND ps.playlist_id = mp.playlist_id
           AND ps.created_at >= je.completed_at
         ORDER BY ps.created_at ASC
         LIMIT 1
    )                                  AS next_count,
    (
        SELECT ps.track_count
          FROM playlist_snapshots ps
         WHERE ps.user_id = mp.user_id
           AND ps.playlist_id = mp.playlist_id
           AND ps.created_at >= je.completed_at
         ORDER BY ps.created_at ASC
         LIMIT 1
    ) -
    (
        SELECT ps.track_count
          FROM playlist_snapshots ps
         WHERE ps.user_id = mp.user_id
           AND ps.playlist_id = mp.playlist_id
           AND ps.created_at <= je.started_at
         ORDER BY ps.created_at DESC
         LIMIT 1
    )                                  AS drift_pre_to_next,
    (
        SELECT ps.id
          FROM playlist_snapshots ps
         WHERE ps.user_id = mp.user_id
           AND ps.playlist_id = mp.playlist_id
           AND ps.created_at <= je.started_at
         ORDER BY ps.created_at DESC
         LIMIT 1
    )                                  AS pre_snapshot_id
FROM job_executions je
JOIN matched_playlists mp ON mp.schedule_id = je.schedule_id
WHERE je.started_at >= NOW() - (INTERVAL '1 day' * :days_back)
ORDER BY je.started_at DESC;

-- 7) Restore quick-card — the most recent snapshot per matched
--    playlist, with the flask-shell command to re-apply it.
\echo
\echo '== 7. Restore quick-card =='
WITH matched_playlists AS (
    SELECT DISTINCT
        user_id,
        target_playlist_id AS playlist_id,
        target_playlist_name AS playlist_name
    FROM schedules
    WHERE target_playlist_name ILIKE :playlist_name_pattern
    UNION
    SELECT DISTINCT user_id, playlist_id, playlist_name
    FROM playlist_snapshots
    WHERE playlist_name ILIKE :playlist_name_pattern
),
latest AS (
    SELECT DISTINCT ON (ps.playlist_id)
        mp.playlist_name        AS target_playlist_name,
        ps.id                   AS snapshot_id,
        ps.user_id,
        ps.playlist_id,
        ps.snapshot_type,
        ps.track_count,
        ps.created_at
    FROM playlist_snapshots ps
    JOIN matched_playlists mp
      ON mp.user_id = ps.user_id
     AND mp.playlist_id = ps.playlist_id
    ORDER BY ps.playlist_id, ps.created_at DESC
)
SELECT
    target_playlist_name,
    snapshot_id,
    user_id,
    playlist_id,
    snapshot_type,
    track_count,
    created_at,
    'PlaylistSnapshotService.restore_to_playlist('
        || snapshot_id::text
        || ', user_id='
        || user_id::text
        || ', api=<spotify api>)'
                                AS flask_shell_command_hint
FROM latest
ORDER BY target_playlist_name;
