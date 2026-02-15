# Phase 5: Scheduled Rotation Job Type -- Implementation Plan

## PR Title
`feat: Add scheduled rotation job type with archive/refresh/swap modes (#phase-5)`

## Risk Level: Medium
- New job type added to execution engine (affects existing `_execute_job_type` dispatch)
- New Spotify API method (`playlist_remove_items`)
- Cross-field schema validation for rotation-specific parameters
- Depends on Phase 3's `PlaylistPair` model and `PlaylistPairService`
- Modifies both the schedules page and workshop template

## Effort: ~6-8 hours

## Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `tests/services/test_job_executor_rotate.py` | Tests for `_execute_rotate()` and all three rotation modes |
| `tests/schemas/test_schedule_rotate_validation.py` | Tests for rotate-specific schema validation rules |

### Modified Files
| File | Change |
|------|--------|
| `shuffify/enums.py` | Add `ROTATE` to `JobType`, add `RotationMode` enum, add `AUTO_PRE_ROTATE` to `SnapshotType` |
| `shuffify/spotify/api.py` | Add `playlist_remove_items()` method |
| `shuffify/services/job_executor_service.py` | Add `_execute_rotate()`, update `_execute_job_type()` dispatch |
| `shuffify/schemas/schedule_requests.py` | Add rotate validation to `ScheduleCreateRequest` and `ScheduleUpdateRequest` |
| `shuffify/routes/schedules.py` | Add `GET /playlist/<id>/rotation-status` endpoint |
| `shuffify/templates/schedules.html` | Add "rotate" option to create-schedule modal, add rotation mode selector |
| `shuffify/templates/workshop.html` | Add rotation panel in Schedules sidebar tab (Phase 1 placeholder) |
| `CHANGELOG.md` | Add entry under `[Unreleased]` |

---

## Context

Shuffify already supports three job types for scheduled operations: `raid`, `shuffle`, and `raid_and_shuffle`. These cover pulling new tracks in and reordering, but they do not address the complementary problem: cycling stale tracks OUT of a playlist.

With Phase 3's Archive Playlist Pairing complete, users can pair a production playlist with an archive playlist. The missing piece is an automated job that periodically rotates tracks between them. Scheduled Rotation adds a fourth job type (`rotate`) with three distinct modes:

- **ARCHIVE_OLDEST** -- Move the N oldest tracks from the production playlist to the archive playlist. This keeps the production playlist fresh by cycling out tracks that have been in it the longest.
- **REFRESH** -- Replace the N oldest tracks in production with the N newest tracks from the archive. This creates a "breathing" playlist that continuously circulates tracks.
- **SWAP** -- Exchange N tracks between production and archive. The oldest N tracks in production go to archive, and the N most recently archived tracks come back to production. This is useful for playlists where users want periodic re-discovery of previously archived material.

All three modes use the `PlaylistPair` model from Phase 3 to know which archive playlist to target. Rotation parameters (`rotation_mode` and `rotation_count`) are stored in the existing `algorithm_params` JSON column on the `Schedule` model, requiring no database migration.

---

## Dependencies

- **Phase 1 (Sidebar Framework)**: The workshop Schedules tab placeholder is populated with a rotation panel.
- **Phase 3 (Archive Playlist Pairing)**: Provides `PlaylistPair` model, `PlaylistPairService`, and `playlist_pairs` table. The rotation job queries for the pair associated with the target playlist to find the archive playlist ID.
- **No database migration required**: Rotation parameters are stored in the `algorithm_params` JSON column already present on the `Schedule` model.

---

## Detailed Implementation

### Step 1: Enums (`shuffify/enums.py`)

**Add `ROTATE` to `JobType`:**

```python
class JobType(StrEnum):
    """Types of scheduled jobs."""
    RAID = "raid"
    SHUFFLE = "shuffle"
    RAID_AND_SHUFFLE = "raid_and_shuffle"
    ROTATE = "rotate"
```

**Add new `RotationMode` enum:**

```python
class RotationMode(StrEnum):
    """Modes for the scheduled rotation job type."""
    ARCHIVE_OLDEST = "archive_oldest"
    REFRESH = "refresh"
    SWAP = "swap"
```

**Add `AUTO_PRE_ROTATE` to `SnapshotType`:**

```python
class SnapshotType(StrEnum):
    """Types of playlist snapshots."""
    AUTO_PRE_SHUFFLE = "auto_pre_shuffle"
    AUTO_PRE_RAID = "auto_pre_raid"
    AUTO_PRE_COMMIT = "auto_pre_commit"
    AUTO_PRE_ROTATE = "auto_pre_rotate"
    MANUAL = "manual"
    SCHEDULED_PRE_EXECUTION = "scheduled_pre_execution"
```

### Step 2: Spotify API -- `playlist_remove_items()` (`shuffify/spotify/api.py`)

Add a new method to the `SpotifyAPI` class in the Playlist Operations section, after `update_playlist_tracks()`:

```python
@api_error_handler
def playlist_remove_items(
    self, playlist_id: str, track_uris: List[str]
) -> bool:
    """
    Remove specific tracks from a playlist.

    Args:
        playlist_id: The Spotify playlist ID.
        track_uris: List of track URIs to remove.

    Returns:
        True if removal succeeded.

    Raises:
        SpotifyNotFoundError: If playlist doesn't exist.
        SpotifyAPIError: If the removal fails.
    """
    self._ensure_valid_token()

    if not track_uris:
        return True

    # Spotify API accepts max 100 tracks per call
    for i in range(0, len(track_uris), self.BATCH_SIZE):
        batch = track_uris[i : i + self.BATCH_SIZE]
        items = [{"uri": uri} for uri in batch]
        self._sp.playlist_remove_all_occurrences_of_items(
            playlist_id, items
        )

    logger.info(
        f"Removed {len(track_uris)} tracks from "
        f"playlist {playlist_id}"
    )

    # Invalidate cache after modification
    if self._cache:
        self._cache.invalidate_playlist(playlist_id)
        if self._user_id:
            self._cache.invalidate_user_playlists(
                self._user_id
            )

    return True
```

Key design: Uses `playlist_remove_all_occurrences_of_items` from spotipy, which takes a list of `{"uri": ...}` dicts. Batched in groups of 100 consistent with `BATCH_SIZE`. Cache invalidated after modification.

### Step 3: Job Executor -- `_execute_rotate()` (`shuffify/services/job_executor_service.py`)

**Update imports:**

Add to existing imports at top of file:
```python
from shuffify.enums import JobType, SnapshotType, ActivityType, RotationMode
```

**Update `_execute_job_type()` dispatch:**

Add a new `elif` branch before the `else` in `_execute_job_type()`:

```python
elif schedule.job_type == JobType.ROTATE:
    return JobExecutorService._execute_rotate(
        schedule, api
    )
```

**Add `_execute_rotate()` static method:**

```python
@staticmethod
def _execute_rotate(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """
    Rotate tracks between production and archive playlists.

    Uses the PlaylistPair from Phase 3 to find the archive.
    Supports three modes:
    - archive_oldest: Move N oldest from production to archive
    - refresh: Replace N oldest in production with N newest from archive
    - swap: Exchange N tracks between production and archive
    """
    from shuffify.services.playlist_pair_service import (
        PlaylistPairService,
        PlaylistPairNotFoundError,
    )

    target_id = schedule.target_playlist_id
    params = schedule.algorithm_params or {}
    rotation_mode = params.get(
        "rotation_mode", RotationMode.ARCHIVE_OLDEST
    )
    rotation_count = max(1, int(params.get(
        "rotation_count", 5
    )))

    # Validate rotation mode
    valid_modes = set(RotationMode)
    if rotation_mode not in valid_modes:
        raise JobExecutionError(
            f"Invalid rotation_mode: {rotation_mode}"
        )

    # Look up the playlist pair
    try:
        pair = PlaylistPairService.get_pair_for_playlist(
            user_id=schedule.user_id,
            playlist_id=target_id,
        )
    except PlaylistPairNotFoundError:
        raise JobExecutionError(
            f"No archive pair found for playlist "
            f"{target_id}. Create a pair in the "
            f"workshop first."
        )

    archive_id = pair.archive_playlist_id

    try:
        # Fetch current production tracks
        prod_tracks = api.get_playlist_tracks(target_id)
        if not prod_tracks:
            return {
                "tracks_added": 0,
                "tracks_total": 0,
            }

        prod_uris = [
            t["uri"]
            for t in prod_tracks
            if t.get("uri")
        ]

        # --- Auto-snapshot before rotation ---
        try:
            if (
                prod_uris
                and PlaylistSnapshotService
                .is_auto_snapshot_enabled(
                    schedule.user_id
                )
            ):
                PlaylistSnapshotService.create_snapshot(
                    user_id=schedule.user_id,
                    playlist_id=target_id,
                    playlist_name=(
                        schedule.target_playlist_name
                        or target_id
                    ),
                    track_uris=prod_uris,
                    snapshot_type=(
                        SnapshotType.AUTO_PRE_ROTATE
                    ),
                    trigger_description=(
                        f"Before scheduled "
                        f"{rotation_mode} rotation"
                    ),
                )
        except Exception as snap_err:
            logger.warning(
                "Auto-snapshot before rotation "
                f"failed: {snap_err}"
            )
        # --- End auto-snapshot ---

        # Clamp rotation_count to available tracks
        actual_count = min(rotation_count, len(prod_uris))
        if actual_count == 0:
            return {
                "tracks_added": 0,
                "tracks_total": len(prod_uris),
            }

        # The "oldest" tracks are those at the front
        # of the playlist (index 0 = added earliest)
        oldest_uris = prod_uris[:actual_count]

        if rotation_mode == RotationMode.ARCHIVE_OLDEST:
            # Move oldest to archive, remove from production
            api._ensure_valid_token()
            # Add to archive in batches
            for i in range(
                0, len(oldest_uris), 100
            ):
                batch = oldest_uris[i:i + 100]
                api._sp.playlist_add_items(
                    archive_id, batch
                )
            # Remove from production
            api.playlist_remove_items(
                target_id, oldest_uris
            )

            logger.info(
                f"Schedule {schedule.id}: archived "
                f"{actual_count} oldest tracks from "
                f"'{schedule.target_playlist_name}'"
            )

            return {
                "tracks_added": 0,
                "tracks_total": (
                    len(prod_uris) - actual_count
                ),
            }

        elif rotation_mode == RotationMode.REFRESH:
            # Get archive tracks
            archive_tracks = api.get_playlist_tracks(
                archive_id
            )
            archive_uris = [
                t["uri"]
                for t in archive_tracks
                if t.get("uri")
            ]

            # Newest in archive = last N entries
            # (most recently added are at the end)
            available = [
                u for u in archive_uris
                if u not in set(prod_uris)
            ]
            refresh_uris = available[-actual_count:]
            # Clamp removal to how many replacements
            # we actually have
            remove_count = min(
                actual_count, len(refresh_uris)
            )
            to_remove = prod_uris[:remove_count]

            if refresh_uris:
                # Remove oldest from production
                api.playlist_remove_items(
                    target_id, to_remove
                )
                # Add fresh tracks to production
                api._ensure_valid_token()
                for i in range(
                    0, len(refresh_uris), 100
                ):
                    batch = refresh_uris[i:i + 100]
                    api._sp.playlist_add_items(
                        target_id, batch
                    )

            new_total = (
                len(prod_uris) - remove_count
                + len(refresh_uris)
            )

            logger.info(
                f"Schedule {schedule.id}: refreshed "
                f"{len(refresh_uris)} tracks in "
                f"'{schedule.target_playlist_name}'"
            )

            return {
                "tracks_added": len(refresh_uris),
                "tracks_total": new_total,
            }

        elif rotation_mode == RotationMode.SWAP:
            # Get archive tracks
            archive_tracks = api.get_playlist_tracks(
                archive_id
            )
            archive_uris = [
                t["uri"]
                for t in archive_tracks
                if t.get("uri")
            ]

            # Newest in archive (exclude those
            # already in production)
            available = [
                u for u in archive_uris
                if u not in set(prod_uris)
            ]
            swap_in_uris = available[-actual_count:]
            swap_out_uris = oldest_uris[
                :len(swap_in_uris)
            ]

            if swap_in_uris and swap_out_uris:
                # Move oldest from production to archive
                api._ensure_valid_token()
                for i in range(
                    0, len(swap_out_uris), 100
                ):
                    batch = swap_out_uris[i:i + 100]
                    api._sp.playlist_add_items(
                        archive_id, batch
                    )
                api.playlist_remove_items(
                    target_id, swap_out_uris
                )

                # Move newest from archive to production
                for i in range(
                    0, len(swap_in_uris), 100
                ):
                    batch = swap_in_uris[i:i + 100]
                    api._sp.playlist_add_items(
                        target_id, batch
                    )
                api.playlist_remove_items(
                    archive_id, swap_in_uris
                )

            swapped = min(
                len(swap_in_uris),
                len(swap_out_uris),
            )

            logger.info(
                f"Schedule {schedule.id}: swapped "
                f"{swapped} tracks between "
                f"'{schedule.target_playlist_name}' "
                f"and archive"
            )

            return {
                "tracks_added": swapped,
                "tracks_total": len(prod_uris),
            }

        else:
            raise JobExecutionError(
                f"Unknown rotation mode: "
                f"{rotation_mode}"
            )

    except SpotifyNotFoundError:
        raise JobExecutionError(
            f"Playlist not found during rotation. "
            f"Target: {target_id}, "
            f"Archive: {archive_id}"
        )
    except SpotifyAPIError as e:
        raise JobExecutionError(
            f"Spotify API error during rotation: {e}"
        )
```

### Step 4: Schema Validation (`shuffify/schemas/schedule_requests.py`)

**Update imports:**

```python
from shuffify.enums import (
    JobType, ScheduleType, IntervalValue, RotationMode,
)
```

**Add `VALID_ROTATION_MODES` constant:**

```python
VALID_ROTATION_MODES = set(RotationMode)
```

**Update `ScheduleCreateRequest.validate_job_requirements()` model validator:**

Add a new block after the existing shuffle and cron validations:

```python
if self.job_type == JobType.ROTATE:
    params = self.algorithm_params or {}
    rotation_mode = params.get("rotation_mode")
    if not rotation_mode:
        raise ValueError(
            "algorithm_params.rotation_mode required "
            "for job_type 'rotate'"
        )
    if rotation_mode not in VALID_ROTATION_MODES:
        raise ValueError(
            f"Invalid rotation_mode "
            f"'{rotation_mode}'. Must be one of: "
            f"{', '.join(sorted(VALID_ROTATION_MODES))}"
        )
    rotation_count = params.get("rotation_count")
    if rotation_count is not None:
        try:
            count = int(rotation_count)
            if count < 1:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError(
                "rotation_count must be a positive "
                "integer"
            )
```

Note: For `rotate`, `algorithm_name` is NOT required (rotation is not a shuffle algorithm). `source_playlist_ids` are NOT required (the archive is discovered via `PlaylistPair`). Only `algorithm_params` with `rotation_mode` is required, and `rotation_count` is optional (defaults to 5 in the executor).

### Step 5: Rotation Status Route (`shuffify/routes/schedules.py`)

Add a new route endpoint for the workshop sidebar to query rotation status for a given playlist:

```python
@main.route(
    "/playlist/<playlist_id>/rotation-status"
)
def rotation_status(playlist_id):
    """
    Get rotation status for a playlist.

    Returns the current pair info, active rotation
    schedule (if any), and last execution details.
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.",
            401,
        )

    from shuffify.services.playlist_pair_service import (
        PlaylistPairService,
        PlaylistPairNotFoundError,
    )

    # Check for pair
    pair_info = None
    try:
        pair = PlaylistPairService.get_pair_for_playlist(
            user_id=db_user.id,
            playlist_id=playlist_id,
        )
        pair_info = pair.to_dict()
    except PlaylistPairNotFoundError:
        pass

    # Find active rotate schedule for this playlist
    rotate_schedule = None
    schedules = (
        SchedulerService.get_user_schedules(db_user.id)
    )
    for s in schedules:
        if (
            s.target_playlist_id == playlist_id
            and s.job_type == JobType.ROTATE
        ):
            rotate_schedule = s.to_dict()
            break

    return jsonify({
        "success": True,
        "has_pair": pair_info is not None,
        "pair": pair_info,
        "has_rotation_schedule": (
            rotate_schedule is not None
        ),
        "rotation_schedule": rotate_schedule,
    })
```

**Add `JobType` import** at top of `shuffify/routes/schedules.py`:

```python
from shuffify.enums import ActivityType, JobType
```

### Step 6: Schedules Template Changes (`shuffify/templates/schedules.html`)

In the create-schedule modal, update the job type selector to include `rotate`:

1. **Add rotation option to job type dropdown**: Add `<option value="rotate">Rotate</option>` after the existing `raid_and_shuffle` option.

2. **Add rotation mode selector group** (hidden by default, shown when `job_type === 'rotate'`):

```html
<!-- Rotation Config (shown when job_type is 'rotate') -->
<div id="rotation-config" class="hidden space-y-3">
    <div>
        <label class="block text-white/70 text-sm font-semibold mb-1">
            Rotation Mode
        </label>
        <select id="rotation-mode"
                class="w-full rounded-lg bg-white/10 border border-white/20 text-white px-3 py-2">
            <option value="archive_oldest">Archive Oldest</option>
            <option value="refresh">Refresh from Archive</option>
            <option value="swap">Swap with Archive</option>
        </select>
        <p id="rotation-mode-desc" class="text-white/40 text-xs mt-1">
            Move the oldest tracks from your playlist to its archive.
        </p>
    </div>
    <div>
        <label class="block text-white/70 text-sm font-semibold mb-1">
            Tracks per Rotation
        </label>
        <input type="number" id="rotation-count"
               value="5" min="1" max="50"
               class="w-full rounded-lg bg-white/10 border border-white/20 text-white px-3 py-2">
        <p class="text-white/40 text-xs mt-1">
            How many tracks to rotate each time (1-50).
        </p>
    </div>
    <div class="rounded-lg bg-yellow-500/10 border border-yellow-500/30 p-3">
        <p class="text-yellow-200 text-xs">
            <strong>Requires Archive Pair:</strong> This playlist
            must be paired with an archive in the Workshop before
            rotation will work.
        </p>
    </div>
</div>
```

3. **Update JavaScript** in the create schedule modal:
- When job type changes to `rotate`: show `#rotation-config`, hide algorithm selector and source playlists selector.
- When job type changes away from `rotate`: hide `#rotation-config`.
- Update `createSchedule()` to include `rotation_mode` and `rotation_count` in `algorithm_params` when job type is `rotate`.
- Update rotation mode description text dynamically when the mode selector changes.
- In schedule card rendering, show rotation-specific badge (e.g., "Rotate: Archive Oldest x5").

### Step 7: Workshop Template Changes (`shuffify/templates/workshop.html`)

Replace the Schedules tab placeholder (Phase 1's "Coming in Phase 5" content) with a rotation panel. The panel shows:

1. **Rotation Status**: Whether the current playlist has an archive pair and an active rotation schedule.
2. **Quick Setup**: If no rotation schedule exists but a pair exists, show a "Set Up Rotation" button that opens the schedules page or creates inline.
3. **Active Rotation Summary**: If a rotation schedule exists, show mode, count, frequency, and last run time with status badge.
4. **Link to Schedules Page**: "Manage All Schedules" link to `/schedules`.

The panel fetches data from `GET /playlist/<id>/rotation-status` on tab activation (lazy load).

**JavaScript addition** (in new script block or appended to Phase 1's sidebar script):

```javascript
// Rotation panel state
const rotationState = {
    isLoaded: false,
    isLoading: false,
};

function onSchedulesTabActivated() {
    if (!rotationState.isLoaded && !rotationState.isLoading) {
        loadRotationStatus();
    }
}

async function loadRotationStatus() {
    rotationState.isLoading = true;
    const container = document.getElementById('rotation-panel-content');
    container.innerHTML = '<div class="text-center py-4"><div class="inline-block w-6 h-6 border-2 border-white/20 border-t-white/60 rounded-full animate-spin"></div></div>';

    try {
        const response = await fetch(
            '/playlist/' + workshopState.playlistId + '/rotation-status'
        );
        const data = await response.json();

        if (data.success) {
            renderRotationPanel(data);
            rotationState.isLoaded = true;
        } else {
            container.innerHTML = '<p class="text-red-400 text-sm">Failed to load rotation status.</p>';
        }
    } catch (err) {
        container.innerHTML = '<p class="text-red-400 text-sm">Network error loading rotation status.</p>';
    } finally {
        rotationState.isLoading = false;
    }
}

function renderRotationPanel(data) {
    // Renders pair status, active schedule info, and action links
    // into #rotation-panel-content
}
```

**Update Phase 1's `switchTab()` hook**: Add to the `switchTab` method in `workshopSidebar`:

```javascript
if (tabName === 'schedules' && typeof onSchedulesTabActivated === 'function') {
    onSchedulesTabActivated();
}
```

### Step 8: Update CHANGELOG.md

```markdown
### Added
- **Scheduled Rotation Job Type** - New `rotate` job type for automated track cycling between paired playlists
  - Three rotation modes: Archive Oldest, Refresh from Archive, and Swap
  - Configurable rotation count (tracks per execution, default 5)
  - Auto-snapshot before rotation with `AUTO_PRE_ROTATE` snapshot type
  - Rotation status API endpoint for workshop sidebar integration
  - Rotation mode selector in schedule creation modal
  - Workshop sidebar Schedules tab with rotation status panel
  - `playlist_remove_items()` added to Spotify API wrapper
```

---

## Test Plan

### Job Executor Rotation Tests (`tests/services/test_job_executor_rotate.py`) -- ~40 tests

**Test class: `TestExecuteRotateArchiveOldest`** (~12 tests)
- Moves N oldest tracks from production to archive
- Clamps rotation_count when playlist has fewer tracks
- Does nothing when production playlist is empty
- Handles large playlist (150+ tracks, verifies batching)
- Calls `playlist_add_items` on archive before `playlist_remove_items` on production
- Snapshot created before rotation when auto-snapshot enabled
- Snapshot skipped when auto-snapshot disabled
- Returns correct `tracks_added` and `tracks_total`
- Raises `JobExecutionError` when pair not found
- Raises `JobExecutionError` when target playlist not found (SpotifyNotFoundError)
- Raises `JobExecutionError` when archive playlist not found
- Raises `JobExecutionError` on Spotify API error during removal

**Test class: `TestExecuteRotateRefresh`** (~10 tests)
- Replaces N oldest production tracks with N newest archive tracks
- Skips tracks already in production (deduplication)
- Does nothing when archive is empty
- Handles partial refresh (archive has fewer unique tracks than requested)
- Correct `tracks_added` count reflects actual refreshed count
- Removes from production before adding from archive
- Returns correct total after refresh

**Test class: `TestExecuteRotateSwap`** (~10 tests)
- Exchanges N tracks between production and archive
- Deduplicates (archive tracks already in production skipped)
- Handles asymmetric swap (fewer available in archive than requested)
- Does nothing when archive has no unique tracks
- Verifies both playlists modified (add + remove on each)
- Correct `tracks_added` reflects actual swaps

**Test class: `TestExecuteRotateValidation`** (~8 tests)
- Invalid rotation_mode raises `JobExecutionError`
- Missing rotation_mode defaults to `archive_oldest`
- rotation_count defaults to 5 when not provided
- rotation_count of 0 is clamped to 1
- Negative rotation_count is clamped to 1
- Non-integer rotation_count is cast via `int()`
- `_execute_job_type()` dispatches to `_execute_rotate()` for `JobType.ROTATE`
- Unknown job type still raises `JobExecutionError`

### Schema Validation Tests (`tests/schemas/test_schedule_rotate_validation.py`) -- ~20 tests

**Test class: `TestRotateScheduleCreateValid`** (~8 tests)
- Valid rotate schedule with archive_oldest mode
- Valid rotate schedule with refresh mode
- Valid rotate schedule with swap mode
- rotation_count is optional (defaults handled by executor)
- algorithm_name is not required for rotate
- source_playlist_ids is not required for rotate
- rotation_count as string integer accepted
- Extra algorithm_params fields preserved

**Test class: `TestRotateScheduleCreateInvalid`** (~8 tests)
- Missing rotation_mode in algorithm_params raises ValidationError
- Empty algorithm_params raises ValidationError for rotate
- Invalid rotation_mode value raises ValidationError
- rotation_count of 0 raises ValidationError
- rotation_count of -1 raises ValidationError
- rotation_count as non-numeric string raises ValidationError
- Null algorithm_params raises ValidationError for rotate
- Job type "rotate" in VALID_JOB_TYPES set

**Test class: `TestRotateScheduleUpdate`** (~4 tests)
- Update to job_type "rotate" is valid
- Existing rotate schedule can be updated with new rotation_mode via algorithm_params
- "rotate" passes ScheduleUpdateRequest job_type validator
- Extra fields ignored per existing Config

### Existing Test Compatibility (~0 tests to modify)
- All existing tests pass unchanged because:
  - `VALID_JOB_TYPES` set updates automatically from the `JobType` enum
  - Existing `_execute_job_type()` dispatch has a clean `elif` chain
  - No existing model changes
  - No migration

**Total new tests: ~60**

---

## Stress Testing and Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Rotation count exceeds playlist size | Clamped to `len(prod_uris)` -- archives all tracks |
| Archive playlist deleted on Spotify externally | `SpotifyNotFoundError` caught, `JobExecutionError` raised with descriptive message |
| Archive pair deleted between schedule creation and execution | `PlaylistPairNotFoundError` caught, `JobExecutionError` with "Create a pair in the workshop first" |
| Empty production playlist | Returns `{tracks_added: 0, tracks_total: 0}` immediately |
| Empty archive during refresh/swap | No tracks available to bring in; production unchanged |
| Duplicate tracks between production and archive | Deduplication filters archive tracks already in production |
| 200+ tracks to rotate | Batched in groups of 100 for both add and remove API calls |
| rotation_count not provided in algorithm_params | Defaults to 5 in `_execute_rotate()` |
| Auto-snapshot fails | Warning logged, rotation continues (non-blocking) |
| User creates rotate schedule without pair | Schedule creates successfully; execution fails with clear error message and `failed` status |
| Race condition: pair deleted during rotation | Spotify API error caught at the specific operation that fails |
| Multiple rotate schedules for same playlist | Allowed (user manages this; schedules are independent) |

---

## Verification Checklist

- [ ] `flake8 shuffify/` passes
- [ ] `pytest tests/ -v` passes (all existing + ~60 new tests)
- [ ] `JobType.ROTATE` exists in `shuffify/enums.py`
- [ ] `RotationMode` enum has three members: `ARCHIVE_OLDEST`, `REFRESH`, `SWAP`
- [ ] `SnapshotType.AUTO_PRE_ROTATE` exists
- [ ] `SpotifyAPI.playlist_remove_items()` works with batching
- [ ] `_execute_job_type()` dispatches `ROTATE` to `_execute_rotate()`
- [ ] `_execute_rotate()` handles all three modes correctly
- [ ] `_execute_rotate()` creates auto-snapshot before rotation
- [ ] `_execute_rotate()` raises `JobExecutionError` when no pair exists
- [ ] Schema validates `rotation_mode` is required for rotate job type
- [ ] Schema validates `rotation_count` is positive integer when provided
- [ ] Schema does NOT require `algorithm_name` or `source_playlist_ids` for rotate
- [ ] Schedules page shows "Rotate" option in job type dropdown
- [ ] Rotation mode selector appears when "Rotate" is selected
- [ ] Workshop sidebar Schedules tab shows rotation status
- [ ] `rotation-status` endpoint returns pair and schedule info
- [ ] `CHANGELOG.md` updated
- [ ] No database migration needed (uses `algorithm_params` JSON)

---

## What NOT To Do

1. **Do NOT create a database migration.** Rotation parameters go in the existing `algorithm_params` JSON column on `Schedule`. No new tables, no new columns.
2. **Do NOT require `algorithm_name` for rotate schedules.** Rotation is not a shuffle algorithm. The mode and count are stored in `algorithm_params`, not `algorithm_name`.
3. **Do NOT require `source_playlist_ids` for rotate schedules.** The archive target is discovered via `PlaylistPairService.get_pair_for_playlist()`, not from source playlists.
4. **Do NOT modify `_execute_raid()` or `_execute_shuffle()`.** The rotate logic is entirely separate. Only `_execute_job_type()` gains a new `elif`.
5. **Do NOT import `PlaylistPairService` at module level in `job_executor_service.py`.** Use a deferred import inside `_execute_rotate()` to avoid circular imports (same pattern as `ActivityLogService`).
6. **Do NOT remove tracks before adding replacements.** In `refresh` and `swap` modes, adding the new tracks first is safer (prevents momentary empty playlist if the add call fails).
    - Exception: `archive_oldest` mode removes from production only after successfully adding to archive.
7. **Do NOT make rotation a blocking prerequisite for schedule creation.** Allow users to create rotate schedules even if no pair exists yet. The execution will fail with a clear error, and the user can create the pair later.
8. **Do NOT modify `SchedulerService.create_schedule()`.** The existing service method already accepts any `algorithm_params` dict. Validation happens in the schema layer.
9. **Do NOT use `playlist_replace_items()` for removal.** Use the new `playlist_remove_items()` which calls `playlist_remove_all_occurrences_of_items` to surgically remove specific tracks without affecting the rest of the playlist order.
10. **Do NOT forget to add the `onSchedulesTabActivated` hook** to Phase 1's `switchTab()` method. Without this hook, the rotation panel will never load.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/services/job_executor_service.py` - Core file: add `_execute_rotate()` and update dispatch
- `/Users/chris/Projects/shuffify/shuffify/enums.py` - Add ROTATE job type, RotationMode enum, AUTO_PRE_ROTATE snapshot type
- `/Users/chris/Projects/shuffify/shuffify/schemas/schedule_requests.py` - Add rotate-specific cross-field validation
- `/Users/chris/Projects/shuffify/shuffify/spotify/api.py` - Add `playlist_remove_items()` method
- `/Users/chris/Projects/shuffify/shuffify/templates/schedules.html` - Add rotate option to create-schedule modal UI