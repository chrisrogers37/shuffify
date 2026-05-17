# F2 — Auto-rollback from snapshot on verification failure

**Status:** COMPLETE — Started 2026-05-17
**Investigation:** [00_INVESTIGATION.md](00_INVESTIGATION.md)
**Targets:** RC6 (snapshots taken but unused), RC7 (audit-trail divergence)
**Depends on:** F1 (`PlaylistVerificationError` and `verify_playlist_state` exist)
**Risk:** Medium. Effort: Medium.

## Implementation note

The plan's section A (threading `_rollback_snapshots` through every executor
return dict) was skipped in favor of plan section D (post-hoc snapshot lookup)
as the **primary** path. The post-hoc query
`PlaylistSnapshot.query.filter(user_id == X, created_at >= execution.started_at)`
correctly captures every auto-snapshot taken during the job — including drip's
two snapshots (target + raid) and rotate's two snapshots (prod + archive) —
without per-executor changes. This is DRYer, automatically robust against
future executors that someone adds without reading this doc, and removes ~80
lines of threading code that would have lived in four executor files.

`JobExecution.error_metadata_json` column was NOT added — the existing
`error_message` field (1000 chars, str(PVE) summary) is enough for status
display, and the full structured diff lives on the `ActivityLog` row's
`metadata_json`. No migration required.

## Context

Pre-snapshots are already taken by `_auto_snapshot_before_{shuffle,rotate,raid,drip}`
in each executor — the user has full backups of every pre-state. But
`PlaylistSnapshotService.restore_snapshot` (`playlist_snapshot_service.py:149-178`)
only returns the saved URI list; no executor calls it. When F1 starts raising
`PlaylistVerificationError`, the playlist is left in whatever broken state Spotify
ended up in.

This fix wires the existing snapshots into a real rollback path: when F1 detects
a divergence, the pre-snapshot is re-applied via `api.update_playlist_tracks`,
`JobExecution.status` is set to `failed_rolled_back`, and the ActivityLog entry
captures `expected`, `actual`, `missing_uris`, and the snapshot ID used.

## Files touched

| File | Change |
|------|--------|
| `shuffify/services/executors/base_executor.py` | (a) Make `_auto_snapshot_before_*` return the created snapshot's id; (b) thread the snapshot id through to `JobExecutorService.execute`; (c) catch `PlaylistVerificationError` and run rollback; (d) record `failed_rolled_back` status |
| `shuffify/services/executors/rotate_executor.py` | `_auto_snapshot_before_rotate` returns `snapshot_id`; pass to a small context object the executor returns alongside the result |
| `shuffify/services/executors/shuffle_executor.py` | Same return-id contract |
| `shuffify/services/executors/drip_executor.py` | Same return-id contract; **two** snapshots are taken (target + raid). Both ids must be threaded |
| `shuffify/services/executors/raid_executor.py` | Same return-id contract |
| `shuffify/services/playlist_snapshot_service.py` | (Optional) add `restore_to_playlist(snapshot_id, user_id, api)` convenience that wraps `restore_snapshot` + `api.update_playlist_tracks` |
| `shuffify/models/db.py` | `JobExecution.status` is already a string column (`models/db.py:590-651`), so no migration. Add `JobExecution.error_metadata_json` if not already present (check column list) for structured diff payload — otherwise reuse `error_message` capped at 1000 chars |
| `shuffify/enums.py` | Add `JobExecutionStatus` constants (`SUCCESS`, `FAILED`, `FAILED_ROLLED_BACK`, `RUNNING`) — keep as plain strings for backwards compat with existing rows |
| `tests/services/executors/test_snapshot_rollback.py` | New test module |

## Approach

### A. Pass snapshot context out of executors

Today, executors return `{"tracks_added": N, "tracks_total": M}`. We expand
this to optionally include the snapshot ids taken during the run, then strip
that context back out before persisting it to `JobExecution`:

```python
# In every executor that auto-snapshots:
result = {
    "tracks_added": ...,
    "tracks_total": ...,
    "_rollback_snapshots": [
        {"playlist_id": target_id, "snapshot_id": tgt_snap_id},
        # drip also adds the raid snapshot
    ],
}
```

`_rollback_snapshots` is consumed by `JobExecutorService.execute` and **not**
persisted on `JobExecution` (the snapshot IDs are already discoverable from the
`playlist_snapshots` table by `(user_id, playlist_id, created_at)`).

### B. New `restore_to_playlist` convenience (recommended)

```python
# shuffify/services/playlist_snapshot_service.py

@staticmethod
def restore_to_playlist(
    snapshot_id: int,
    user_id: int,
    api,                # SpotifyAPI
) -> list[str]:
    """Restore a snapshot to its source playlist via Spotify API.

    Returns the URI list that was applied. Raises
    PlaylistSnapshotNotFoundError if the snapshot is not found or not owned.
    Raises whatever the Spotify API raises on write failure (caller decides
    how to react).
    """
    snapshot = PlaylistSnapshotService.get_snapshot(snapshot_id, user_id)
    uris = snapshot.track_uris or []
    api.update_playlist_tracks(snapshot.playlist_id, uris)
    logger.info(
        "Restored snapshot %s to playlist %s (%d tracks)",
        snapshot_id, snapshot.playlist_id, len(uris),
    )
    return uris
```

### C. Catch + rollback in `JobExecutorService.execute`

Modify `execute` (`base_executor.py:40-91`):

```python
try:
    ...
    result = JobExecutorService._execute_job_type(schedule, api)
    JobExecutorService._record_success(execution, schedule, result)

except PlaylistVerificationError as ve:
    JobExecutorService._record_rollback(
        execution, schedule, api, ve, schedule_id,
    )
except Exception as e:
    JobExecutorService._record_failure(
        execution, schedule, e, schedule_id,
    )
```

`_record_rollback` does:

1. For each `_rollback_snapshots` entry attached to the in-flight result OR
   discovered from the most recent snapshot for `(user_id, ve.playlist_id)`:
   call `PlaylistSnapshotService.restore_to_playlist(...)`.
2. Set `execution.status = "failed_rolled_back"`.
3. Build a structured diff payload:
   ```python
   payload = {
       "phase": ve.phase,
       "expected_count": len(ve.expected),
       "actual_count": len(ve.actual),
       "missing": ve.missing[:50],   # cap for storage
       "extra": ve.extra[:50],
       "missing_total": len(ve.missing),
       "extra_total": len(ve.extra),
       "restored_snapshot_id": <id used>,
       "restored_track_count": <len of restored>,
   }
   ```
4. Persist `payload` either on a new `JobExecution.error_metadata_json` column
   (preferred — see migration note) or, if we don't want a migration, JSON-dump
   into the existing `error_message` field truncated at 1000 chars.
5. Emit ActivityLog with `activity_type='SCHEDULE_RUN_ROLLED_BACK'` (new enum
   value in `enums.py:ActivityType`) carrying the same payload.
6. Log a high-severity message for Sentry (F5) capture.

If `_record_rollback` itself fails (e.g., Spotify down), it should fall back to
`_record_failure` and leave the playlist in its broken state — better than
silently doing nothing.

### D. Recovering snapshot context when executor didn't return it

If an old-pattern executor doesn't return `_rollback_snapshots` (e.g., a future
executor someone adds without reading this doc), `_record_rollback` should
look up the latest snapshot by `(user_id, ve.playlist_id, created_at)` from
`PlaylistSnapshot` and use that. This makes the rollback path robust against
forgetful contributors.

### E. New ActivityType

In `shuffify/enums.py:ActivityType`, add:

```python
SCHEDULE_RUN_ROLLED_BACK = "schedule_run_rolled_back"
```

The existing `SCHEDULE_RUN` continues to mean "ran and succeeded". This
separates the audit-trail signal cleanly.

## Tests (`tests/services/executors/test_snapshot_rollback.py`)

```python
def test_rollback_restores_pre_snapshot_uris()
def test_rollback_sets_status_failed_rolled_back()
def test_rollback_writes_structured_payload_to_activity_log()
def test_rollback_handles_missing_snapshot_id_falls_back_to_latest()
def test_rollback_drip_restores_both_target_and_raid()
def test_rollback_when_restore_fails_falls_back_to_plain_failure()
def test_no_rollback_on_non_verification_exceptions()
```

Each test mocks `SpotifyAPI` and `PlaylistSnapshotService` and asserts on the
DB state of `JobExecution` and `ActivityLog`.

## Verification

```bash
flake8 shuffify/
pytest tests/services/executors/test_snapshot_rollback.py -v
pytest tests/ -v
```

End-to-end on a preview deploy:

1. Create a snapshot-eligible playlist with 5 tracks. Set up a swap schedule.
2. Patch `SpotifyAPI.playlist_add_items` to skip the actual add (simulating
   silent Spotify dropout).
3. Run the schedule once. Expect:
   - `JobExecution.status == 'failed_rolled_back'`
   - Playlist has the original 5 tracks (restored)
   - ActivityLog entry has `phase='swap'`, `missing_total=...`
4. Confirm the auto-snapshot still exists (retention not breached) so the
   user can manually trigger another restore from the UI if needed.

## Rollout note

This fix is **only useful with F1**. Ship F1+F2 together in the same release.
If F2 is ever delayed behind F1, document clearly that rotations may
fail-loud-and-leave-broken-state during the gap.
