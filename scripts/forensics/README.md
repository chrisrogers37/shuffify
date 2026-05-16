# Forensic scripts

Read-only SQL scripts for reconstructing silent state-loss timelines.

## `wooklyn_loss_timeline.sql`

Forensic reconstruction for any playlist matched by name. Joins
`schedules × job_executions × playlist_snapshots × activity_log` and emits
seven sections covering matched-playlist resolution, schedule config, run
history, snapshot timeline, activity log, per-run drift detection, and a
final restore quick-card.

Originally written to investigate silent track losses on the WOOKLYN
playlist (see
`documentation/planning/investigations/wooklyn-silent-loss_2026-05-16/`),
but the pattern parameter makes it reusable for any playlist by name.

### Safety

Every run starts with:

```sql
SET default_transaction_read_only = on;
SET statement_timeout = '60s';
```

The script makes no writes. It cannot be repurposed to mutate state without
removing those `SET` statements. The 60s timeout prevents accidental load
on the production database.

### Running it

```bash
# Default pattern + window
./scripts/forensics/run_wooklyn_timeline.sh

# Custom pattern
./scripts/forensics/run_wooklyn_timeline.sh '%my-playlist%'

# Custom pattern + 1-year window
./scripts/forensics/run_wooklyn_timeline.sh '%my-playlist%' 365
```

Or invoke `psql` directly:

```bash
psql "$DATABASE_URL" \
    -v ON_ERROR_STOP=1 \
    -v playlist_name_pattern="'%WOOKLYN%'" \
    -v days_back=60 \
    -f scripts/forensics/wooklyn_loss_timeline.sql
```

`DATABASE_URL` must be sourced from `.env` or the DigitalOcean app spec
before running. A read-only role is recommended even though the script
self-enforces read-only.

### Output sections

| # | Section | What it tells you |
|---|---------|-------------------|
| 1 | Matched playlists | Which `(user_id, playlist_id)` pairs the pattern resolved — useful when a playlist was renamed mid-history |
| 2 | Schedules | Every schedule targeting a matched playlist, with its current state |
| 3 | Job executions | Run history with executor-reported `tracks_added` and `tracks_total` |
| 4 | Snapshots | Snapshot timeline with stored `track_count` per row |
| 5 | Activity log | `SCHEDULE_RUN` and related entries, with `metadata_json` payloads |
| 6 | Loss reconstruction | The detector: `drift_pre_to_next < 0` means tracks were lost across that run |
| 7 | Restore quick-card | One row per playlist with the snapshot id ready to restore |

### Interpreting drift

Section 6's `drift_pre_to_next` column is the key signal:

- `0` or `NULL`: no drift detectable from snapshots (run was either correct, or no snapshot was taken on the next operation)
- `> 0`: playlist grew (raid/drip behaved as expected)
- `< 0`: playlist shrunk between snapshots — **silent track loss**

A run can claim `status='success'` and still show `drift < 0`. That is the
exact bug class this script was written to surface.

### Acting on results

If section 6 shows a string of negative drift rows: the silent-loss
hypothesis is confirmed at scale and the F1+F2 fixes
(`01_verify_wrapper.md`, `02_snapshot_rollback.md`) are urgent.

Use section 7's `snapshot_id` with the existing snapshot-restore UI or
manually via `flask shell`:

```python
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)
from shuffify.spotify.api import SpotifyAPI
# (instantiate api as in JobExecutorService._get_spotify_api)

uris = PlaylistSnapshotService.restore_snapshot(<snapshot_id>, <user_id>)
api.update_playlist_tracks("<playlist_id>", uris)
```

The `flask_shell_command_hint` column in section 7 emits a paste-ready
template per playlist.
