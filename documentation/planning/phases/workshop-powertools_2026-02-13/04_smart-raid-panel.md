# Phase 4: Smart Raid Panel -- Implementation Plan

## PR Title
`feat: Add smart raid panel with one-click source watching and schedule integration (#phase-4)`

## Risk Level: Low
- No new database models or migrations
- All backend functionality composes existing services (`UpstreamSourceService`, `SchedulerService`, `JobExecutorService`)
- New `RaidSyncService` is a thin orchestration layer over existing services
- New route module follows established patterns (`upstream_sources.py`, `snapshots.py`)
- Template changes are confined to the Raids tab content in the sidebar (Phase 1 placeholder replacement)
- No existing JavaScript functions are modified

## Effort: ~4-6 hours

## Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `shuffify/services/raid_sync_service.py` | Orchestration service: combines source management + schedule creation into unified operations |
| `shuffify/routes/raid_panel.py` | REST endpoints for the raid panel UI |
| `shuffify/schemas/raid_requests.py` | Pydantic validation schemas for raid panel requests |
| `tests/services/test_raid_sync_service.py` | Service layer tests |
| `tests/test_raid_panel_routes.py` | Route integration tests |
| `tests/schemas/test_raid_requests.py` | Schema validation tests |

### Modified Files
| File | Change |
|------|--------|
| `shuffify/services/__init__.py` | Export `RaidSyncService` and exceptions |
| `shuffify/schemas/__init__.py` | Export new raid schemas |
| `shuffify/routes/__init__.py` | Import `raid_panel` route module |
| `shuffify/services/upstream_source_service.py` | Add `count_sources_for_target()` static method |
| `shuffify/enums.py` | Add `RAID_WATCH_ADD`, `RAID_WATCH_REMOVE`, `RAID_SYNC_NOW` to `ActivityType` |
| `shuffify/templates/workshop.html` | Replace Raids tab placeholder with real UI, add raid panel JavaScript |
| `CHANGELOG.md` | Add entry under `[Unreleased]` |

---

## Context

The raid infrastructure already exists in Shuffify: `UpstreamSourceService` manages source-to-target playlist links, `SchedulerService` creates recurring jobs, and `JobExecutorService._execute_raid()` pulls new tracks from sources into a target. However, these components are disconnected -- setting up a simple "watch this playlist and auto-pull new tracks" requires navigating to the Schedules page, manually selecting sources, and configuring a schedule. This multi-step process is the number one UX friction point identified in the gap analysis.

The Smart Raid Panel solves this by providing a unified interface in the workshop sidebar's Raids tab. The key interaction is "Watch Playlist" -- a one-click operation that:
1. Registers the selected playlist as an upstream source (via `UpstreamSourceService`)
2. Creates or updates a raid schedule (via `SchedulerService`)
3. Optionally executes an immediate raid to pull in tracks now

The panel also displays all current sources for the workshop playlist, their status, and allows managing the raid schedule directly from the workshop.

---

## Dependencies

- **Phase 1 (Sidebar Tabs)**: The Raids tab UI lives in the `#sidebar-tab-raids` container and uses the `onRaidsTabActivated()` hook defined by Phase 1's `workshopSidebar.switchTab()`.
- **Existing services**: `UpstreamSourceService`, `SchedulerService`, `JobExecutorService` must be available and functional.
- **Database**: Requires `users`, `upstream_sources`, and `schedules` tables to exist (all created in earlier migrations).

---

## Detailed Implementation

### Step 1: New ActivityType Enums (`shuffify/enums.py`)

Add three new members to the `ActivityType` class:

```python
RAID_WATCH_ADD = "raid_watch_add"
RAID_WATCH_REMOVE = "raid_watch_remove"
RAID_SYNC_NOW = "raid_sync_now"
```

These distinguish raid panel actions from the lower-level `UPSTREAM_SOURCE_ADD`, `UPSTREAM_SOURCE_DELETE`, and `SCHEDULE_RUN` activity types. The raid panel logs these higher-level activity types because a single "Watch Playlist" action may trigger both a source add AND a schedule create, and the activity log should reflect the user-facing action, not the implementation details.

### Step 2: Add `count_sources_for_target()` to UpstreamSourceService (`shuffify/services/upstream_source_service.py`)

Add a new static method that returns the count of upstream sources for a given user and target playlist. This avoids fetching full ORM objects when the panel only needs to know "how many sources are configured?"

```python
@staticmethod
def count_sources_for_target(
    spotify_id: str, target_playlist_id: str
) -> int:
    """Count upstream sources for a target playlist."""
    user = User.query.filter_by(spotify_id=spotify_id).first()
    if not user:
        return 0
    return UpstreamSource.query.filter_by(
        user_id=user.id,
        target_playlist_id=target_playlist_id,
    ).count()
```

### Step 3: Pydantic Schemas (`shuffify/schemas/raid_requests.py`)

Three schemas:

**`WatchPlaylistRequest`**: Used for the "Watch Playlist" one-click operation.

Fields:
- `source_playlist_id: str` (required, `min_length=1`) -- the Spotify playlist ID to watch
- `source_playlist_name: Optional[str]` (`max_length=255`) -- display name for the source
- `source_url: Optional[str]` (`max_length=1024`) -- original URL if loaded from external
- `auto_schedule: bool` (default `True`) -- whether to auto-create/update a raid schedule
- `schedule_value: str` (default `"daily"`) -- interval for the auto-created schedule

Field validators:
- `validate_schedule_value`: if provided, must be a member of `IntervalValue` enum. Strip and lowercase input.
- `validate_source_playlist_name`: strip whitespace, convert empty string to None.

**`UnwatchPlaylistRequest`**: Used for removing a source from the watch list.

Fields:
- `source_id: int` (required, `gt=0`) -- the database ID of the `UpstreamSource` record

**`RaidNowRequest`**: Used for triggering an immediate raid from the panel.

Fields:
- `source_playlist_ids: Optional[List[str]]` (default `None`) -- if provided, raid only from these sources. If `None`, raid from all configured sources.

Field validators:
- `validate_source_ids`: if provided, list must not be empty (use `None` to mean "all sources"). Each ID must be a non-empty string.

The `Config` class on all schemas should set `extra = "ignore"` to silently drop unknown fields.

### Step 4: RaidSyncService (`shuffify/services/raid_sync_service.py`)

A stateless orchestration service that composes existing services. All methods are `@staticmethod`. Custom exceptions: `RaidSyncError`.

**Methods:**

**`watch_playlist(spotify_id, target_playlist_id, target_playlist_name, source_playlist_id, source_playlist_name, source_url, auto_schedule, schedule_value)`**

The core "one-click" operation. Steps:
1. Look up the `User` by `spotify_id`. Raise `RaidSyncError` if not found.
2. Call `UpstreamSourceService.add_source()` to register the source. This is idempotent (returns existing if duplicate).
3. If `auto_schedule` is True:
   a. Query `Schedule` for an existing raid schedule on this `target_playlist_id` for this user.
   b. If a schedule exists, update its `source_playlist_ids` to include `source_playlist_id` (if not already present) via `SchedulerService.update_schedule()`.
   c. If no schedule exists, create one via `SchedulerService.create_schedule()` with `job_type="raid"`, the given `schedule_value`, and `source_playlist_ids=[source_playlist_id]`.
   d. Register the schedule with APScheduler via `add_job_for_schedule()`.
4. Return a dict with `{"source": source.to_dict(), "schedule": schedule.to_dict() if schedule else None}`.

**`unwatch_playlist(spotify_id, source_id, target_playlist_id)`**

Steps:
1. Call `UpstreamSourceService.delete_source(source_id, spotify_id)` -- raises `UpstreamSourceNotFoundError` if not found.
2. Find the existing raid schedule for this target playlist.
3. If a schedule exists, remove `source_playlist_id` from its `source_playlist_ids`.
4. If the schedule's `source_playlist_ids` is now empty, delete the schedule and remove it from APScheduler.
5. Otherwise, update the schedule with the reduced list.
6. Return `True`.

**`get_raid_status(spotify_id, target_playlist_id)`**

Returns a summary dict for the Raids panel:
```python
{
    "sources": [source.to_dict() for source in sources],
    "schedule": schedule.to_dict() if schedule else None,
    "source_count": len(sources),
    "has_schedule": schedule is not None,
    "is_schedule_enabled": schedule.is_enabled if schedule else False,
    "last_run_at": schedule.last_run_at.isoformat() if schedule and schedule.last_run_at else None,
    "last_status": schedule.last_status if schedule else None,
}
```

**`raid_now(spotify_id, target_playlist_id, source_playlist_ids)`**

Triggers an immediate one-off raid without needing an existing schedule. Steps:
1. Look up user.
2. If `source_playlist_ids` is None, fetch all upstream sources for this target via `UpstreamSourceService.list_sources()` and use their IDs.
3. If no sources exist, raise `RaidSyncError("No sources configured")`.
4. Find or create a temporary schedule (or reuse an existing schedule) and call `JobExecutorService.execute_now()`.
5. Alternative simpler approach: call `JobExecutorService._execute_raid()` directly after obtaining a `SpotifyAPI` instance via `JobExecutorService._get_spotify_api(user)`. This avoids needing a schedule for ad-hoc raids. Create a minimal `Schedule`-like object or pass the needed parameters directly.

**Recommended simpler approach for `raid_now()`**: Rather than creating temporary schedules, directly construct the API client and execute the raid logic inline:
1. Get the `User` record and obtain `SpotifyAPI` via `JobExecutorService._get_spotify_api(user)`.
2. Get target tracks, get source tracks for each source_playlist_id, compute new URIs (deduplicated), add in batches of 100.
3. Return `{"tracks_added": N, "tracks_total": M}`.
4. This avoids polluting the `schedules` table with one-off entries.

**Helper: `_find_raid_schedule(user_id, target_playlist_id)`**

Queries `Schedule.query.filter_by(user_id=user_id, target_playlist_id=target_playlist_id, job_type=JobType.RAID).first()`. Also checks for `JobType.RAID_AND_SHUFFLE` schedules. Returns the first matching schedule or None.

### Step 5: Routes (`shuffify/routes/raid_panel.py`)

Five endpoints on the `main` Blueprint. All follow the established patterns from `upstream_sources.py` and `snapshots.py`: check auth via `require_auth()`, check DB via `is_db_available()`, get user data from session, use `json_error()`/`json_success()` helpers, log activities non-blockingly.

**`GET /playlist/<playlist_id>/raid-status`**
- Returns raid panel status (sources, schedule, last run).
- Calls `RaidSyncService.get_raid_status()`.
- Response: `{"success": true, "raid_status": {...}}`.

**`POST /playlist/<playlist_id>/raid-watch`**
- "Watch Playlist" one-click operation.
- Validates JSON body with `WatchPlaylistRequest`.
- Calls `RaidSyncService.watch_playlist()`.
- Logs `ActivityType.RAID_WATCH_ADD`.
- Response: `{"success": true, "message": "...", "source": {...}, "schedule": {...}}`.

**`POST /playlist/<playlist_id>/raid-unwatch`**
- "Unwatch" a source.
- Validates JSON body with `UnwatchPlaylistRequest`.
- Calls `RaidSyncService.unwatch_playlist()`.
- Logs `ActivityType.RAID_WATCH_REMOVE`.
- Response: `{"success": true, "message": "Source removed."}`.

**`POST /playlist/<playlist_id>/raid-now`**
- Trigger an immediate raid.
- Validates optional JSON body with `RaidNowRequest`.
- Calls `RaidSyncService.raid_now()`.
- Logs `ActivityType.RAID_SYNC_NOW`.
- Response: `{"success": true, "message": "...", "tracks_added": N, "tracks_total": M}`.

**`POST /playlist/<playlist_id>/raid-schedule-toggle`**
- Toggle the raid schedule on/off.
- Finds the raid schedule via `RaidSyncService._find_raid_schedule()`.
- Calls `SchedulerService.toggle_schedule()`.
- Logs `ActivityType.SCHEDULE_TOGGLE`.
- Response: `{"success": true, "schedule": {...}}`.

Error handling pattern (matching `upstream_sources.py`):
```python
try:
    result = RaidSyncService.watch_playlist(...)
    # log activity (non-blocking)
    try:
        ActivityLogService.log(...)
    except Exception:
        pass
    return json_success("Source watched.", ...)
except RaidSyncError as e:
    return json_error(str(e), 400)
except UpstreamSourceNotFoundError:
    return json_error("Source not found.", 404)
except ScheduleLimitError:
    return json_error("Schedule limit reached.", 400)
```

### Step 6: Register Routes and Exports

**`shuffify/routes/__init__.py`**: Add `raid_panel` to the imports at the bottom:
```python
from shuffify.routes import (  # noqa: E402, F401
    core,
    playlists,
    shuffle,
    workshop,
    upstream_sources,
    schedules,
    settings,
    snapshots,
    raid_panel,
)
```

**`shuffify/services/__init__.py`**: Add:
```python
# Raid Sync Service
from shuffify.services.raid_sync_service import (
    RaidSyncService,
    RaidSyncError,
)
```
And add `"RaidSyncService"` and `"RaidSyncError"` to the `__all__` list.

**`shuffify/schemas/__init__.py`**: Add:
```python
from .raid_requests import (
    WatchPlaylistRequest,
    UnwatchPlaylistRequest,
    RaidNowRequest,
)
```
And add these three to the `__all__` list.

### Step 7: Workshop Template Changes (`shuffify/templates/workshop.html`)

Replace the Phase 4 placeholder inside `#sidebar-tab-raids` (the `<div>` with the lightning bolt icon and "Coming in Phase 4" badge) with real Raids panel content.

**Raids Panel HTML Structure:**

```
#sidebar-tab-raids
  ├── h3 "Raids"
  ├── Quick Watch Section
  │   ├── Label "Watch a playlist"
  │   ├── Select dropdown (reuses workshop's playlist fetch)
  │   ├── "Watch" button
  │   └── Helper text
  ├── Watched Sources List
  │   ├── #raid-sources-list (container)
  │   │   ├── Source item (name, type badge, unwatch button)
  │   │   └── ...
  │   └── Empty state when no sources
  ├── Schedule Status
  │   ├── #raid-schedule-info
  │   │   ├── Schedule interval display
  │   │   ├── Last run time + status badge
  │   │   └── Toggle enabled/disabled switch
  │   └── "No schedule" state
  ├── Actions
  │   ├── "Raid Now" button (sync icon, triggers immediate raid)
  │   └── Status message area
  └── Loading state
```

**Raids Panel JavaScript:**

Add a new `<script>` block (or extend the sidebar script block from Phase 1) with the following functions in a `raidPanel` namespace object:

```javascript
const raidPanel = {
    loaded: false,
    isLoading: false,
    status: null,

    /** Called by workshopSidebar.switchTab('raids') via the hook */
    init() { ... },

    /** Fetch raid status from the server */
    loadStatus() { ... },

    /** Render the panel from the fetched status */
    render() { ... },

    /** Render the watched sources list */
    renderSources(sources) { ... },

    /** Render schedule info */
    renderSchedule(schedule) { ... },

    /** Watch a playlist (one-click) */
    watchPlaylist() { ... },

    /** Unwatch a source */
    unwatchSource(sourceId) { ... },

    /** Trigger immediate raid */
    raidNow() { ... },

    /** Toggle schedule enabled/disabled */
    toggleSchedule() { ... },
};

/** Hook called by workshopSidebar.switchTab('raids') */
function onRaidsTabActivated() {
    if (!raidPanel.loaded) {
        raidPanel.init();
    }
}
```

**Key UI behavior:**

- **Lazy loading**: The panel fetches data only when the Raids tab is first activated (via `onRaidsTabActivated()`). Subsequent tab switches reuse cached data. A "Refresh" link allows manual reload.
- **Watch playlist dropdown**: Reuses the same playlist data fetched by the source panel's `fetchUserPlaylists()`. If playlists are already loaded in `workshopState.playlistsLoaded`, the dropdown is populated immediately. Otherwise, it triggers a fetch.
- **Source list**: Each source shows its name, type badge ("own"/"external"), and an "Unwatch" button (trash icon).
- **Schedule section**: Shows the current interval (e.g., "Every day"), last run time (relative, e.g., "2 hours ago"), last status badge (green "success" / red "failed"), and a toggle switch for enable/disable.
- **Raid Now button**: Prominent action button. When clicked, shows a spinner, calls the `/raid-now` endpoint, and displays the result ("Added 5 new tracks").
- **No DB state**: If `is_db_available()` returns false on the server, the status endpoint returns 503 and the panel shows a "Database unavailable" message matching the pattern in the snapshot and archive panels.

### Step 8: Update CHANGELOG.md

Add under `## [Unreleased]` / `### Added`:

```markdown
- **Smart Raid Panel** - One-click playlist watching and raid management in the workshop sidebar
  - "Watch Playlist" one-click operation: registers source + auto-creates raid schedule
  - Source management: view, add, and remove watched playlists
  - Schedule control: toggle raid schedule on/off, view last run status
  - "Raid Now" button for immediate on-demand track sync
  - New `RaidSyncService` orchestration layer in `shuffify/services/raid_sync_service.py`
  - New `ActivityType` entries: `RAID_WATCH_ADD`, `RAID_WATCH_REMOVE`, `RAID_SYNC_NOW`
```

---

## Test Plan

### Service Tests (`tests/services/test_raid_sync_service.py`) -- ~25 tests

Tests use the same `db_app` and `app_ctx` fixture pattern as `test_upstream_source_service.py`: in-memory SQLite, test user created via `UserService.upsert_from_spotify()`.

**`TestWatchPlaylist` (~8 tests):**
- Watch a playlist creates source and schedule
- Watch a playlist with `auto_schedule=False` creates source but no schedule
- Watch a second source appends to existing schedule's `source_playlist_ids`
- Watch same source twice (idempotent -- returns existing source, does not duplicate in schedule)
- Watch with invalid user raises `RaidSyncError`
- Watch with custom `schedule_value` sets correct interval
- Watch respects schedule limit (5 max) -- raises `ScheduleLimitError` if exceeded on first schedule creation
- Returned dict contains expected keys (`source`, `schedule`)

**`TestUnwatchPlaylist` (~6 tests):**
- Unwatch removes source and removes ID from schedule
- Unwatch last source deletes the schedule entirely
- Unwatch non-existent source raises `UpstreamSourceNotFoundError`
- Unwatch wrong user's source raises `UpstreamSourceNotFoundError`
- Unwatch when no schedule exists still removes source successfully
- Unwatch updates schedule correctly (remaining sources still present)

**`TestGetRaidStatus` (~5 tests):**
- Status with sources and schedule returns full dict
- Status with no sources returns empty list and `has_schedule: false`
- Status with sources but no schedule returns sources and `has_schedule: false`
- Status for unknown user returns empty
- Status returns correct `last_run_at` and `last_status` from schedule

**`TestRaidNow` (~4 tests):**
- These tests mock the Spotify API calls since we cannot make real API calls in tests.
- Raid now with configured sources calls API and returns tracks_added count
- Raid now with no sources raises `RaidSyncError`
- Raid now with explicit `source_playlist_ids` uses only those sources
- Raid now with API error raises `RaidSyncError`

**`TestFindRaidSchedule` (~2 tests):**
- Returns raid schedule for user+target
- Returns None when no schedule exists

### Schema Tests (`tests/schemas/test_raid_requests.py`) -- ~15 tests

**`TestWatchPlaylistRequest` (~7 tests):**
- Valid minimal request (source_playlist_id only)
- Valid full request (all fields)
- Missing source_playlist_id raises ValidationError
- Empty source_playlist_id raises ValidationError
- Invalid schedule_value raises ValidationError
- Valid schedule_value from IntervalValue enum accepted
- Extra fields ignored (Config extra="ignore")

**`TestUnwatchPlaylistRequest` (~4 tests):**
- Valid request with source_id
- Missing source_id raises ValidationError
- source_id <= 0 raises ValidationError
- Non-integer source_id raises ValidationError

**`TestRaidNowRequest` (~4 tests):**
- Valid request with no source_playlist_ids (None = raid all)
- Valid request with explicit source_playlist_ids
- Empty list source_playlist_ids raises ValidationError
- source_playlist_ids with empty string elements raises ValidationError

### Route Tests (`tests/test_raid_panel_routes.py`) -- ~10 tests

Follow the pattern from `tests/test_snapshot_routes.py`: `db_app` fixture, `auth_client` fixture with session data, mock `require_auth`.

**Authentication tests (~3 tests):**
- GET `/playlist/p1/raid-status` unauthenticated returns 401
- POST `/playlist/p1/raid-watch` unauthenticated returns 401
- POST `/playlist/p1/raid-now` unauthenticated returns 401

**Validation tests (~3 tests):**
- POST `/playlist/p1/raid-watch` with invalid body returns 400
- POST `/playlist/p1/raid-unwatch` with missing source_id returns 400
- POST `/playlist/p1/raid-now` with empty source list returns 400

**DB unavailable tests (~2 tests):**
- GET `/playlist/p1/raid-status` when DB unavailable returns 503
- POST `/playlist/p1/raid-watch` when DB unavailable returns 503

**Success path tests (~2 tests):**
- GET `/playlist/p1/raid-status` returns expected structure (mock RaidSyncService)
- POST `/playlist/p1/raid-watch` returns source and schedule (mock RaidSyncService)

---

## Stress Testing and Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| User watches 10+ playlists as sources | All added to upstream_sources; single raid schedule with all IDs in `source_playlist_ids` JSON array |
| User unwatches all sources | Schedule is deleted; panel shows empty state |
| Source playlist deleted on Spotify | Raid execution logs warning for missing source, skips it, continues with others |
| User hits 5-schedule limit | `watch_playlist()` with `auto_schedule=True` raises `ScheduleLimitError` if no existing raid schedule exists for this target and user already has 5 schedules. Error shown in panel. |
| Raid Now on empty source list | Returns error: "No sources configured. Watch a playlist first." |
| Rapid "Raid Now" clicks | Button disabled during request (JS guard). Only one request at a time. |
| Source panel and Raids tab both show sources | They use different data sources: source panel uses client-side `workshopState.sourceTracks`; Raids tab fetches from DB via `/raid-status`. They may show different information (source panel = track-level; raids tab = playlist-level). This is intentional. |
| No DB available | All raid panel endpoints return 503. Panel shows "Database unavailable" message. Workshop still works normally for non-raid features. |
| Existing schedule is `raid_and_shuffle` type | `_find_raid_schedule()` matches both `raid` and `raid_and_shuffle` job types. Watch/unwatch correctly updates `source_playlist_ids` on either type. |
| Watch same playlist from different workshop pages | `UpstreamSourceService.add_source()` is idempotent for same (user, target, source) tuple. Schedule update also handles this gracefully. |
| Very long source playlist name (255+ chars) | Pydantic `max_length=255` on `source_playlist_name` truncates or rejects. Service layer also accepts None gracefully. |

---

## Verification Checklist

- [ ] `flake8 shuffify/` passes
- [ ] `pytest tests/ -v` passes
- [ ] All 5 raid panel endpoints require auth and check `is_db_available()`
- [ ] `WatchPlaylistRequest` validates schedule_value against `IntervalValue` enum
- [ ] `watch_playlist()` is idempotent for duplicate sources
- [ ] `unwatch_playlist()` deletes schedule when last source is removed
- [ ] `_find_raid_schedule()` matches both `raid` and `raid_and_shuffle` job types
- [ ] `raid_now()` works without an existing schedule
- [ ] Panel loads lazily on first Raids tab activation
- [ ] Panel shows "Database unavailable" when DB is down
- [ ] "Raid Now" button is disabled during execution
- [ ] Activity log entries for all three new activity types
- [ ] Schedule toggle updates APScheduler via `add_job_for_schedule()` / `remove_job_for_schedule()`
- [ ] CHANGELOG.md updated
- [ ] No new pip dependencies required

---

## What NOT To Do

1. **Do NOT create a new database model.** The raid panel composes existing `UpstreamSource` and `Schedule` models. No migration is needed.
2. **Do NOT create a new Blueprint.** Use the existing `main` Blueprint, consistent with all other route modules.
3. **Do NOT modify `JobExecutorService._execute_raid()`.** The existing raid execution logic is correct and sufficient. The `RaidSyncService` orchestrates around it.
4. **Do NOT duplicate Spotify API logic in `RaidSyncService`.** For `raid_now()`, reuse the API client creation pattern from `JobExecutorService._get_spotify_api()` and the batch-add pattern from `_execute_raid()`.
5. **Do NOT make the Raids tab fetch data on page load.** Use lazy loading via the `onRaidsTabActivated()` hook. The workshop page should not make extra API calls until the user actually opens the Raids tab.
6. **Do NOT modify Phase 1 sidebar JavaScript.** Phase 1 already calls `onRaidsTabActivated()` when the raids tab is selected. Phase 4 just needs to define that function.
7. **Do NOT store raid panel state in Flask session.** All raid configuration is in the database (upstream_sources, schedules). The panel fetches from the DB on each tab activation.
8. **Do NOT import `RaidSyncService` in `job_executor_service.py`.** The dependency flows one way: `RaidSyncService` calls `JobExecutorService`, not the reverse.
9. **Do NOT create schedules for each source individually.** One raid schedule per target playlist, with all source IDs in `source_playlist_ids`.
10. **Do NOT forget to handle `ScheduleLimitError`.** When a user already has 5 schedules and watches a playlist on a new target (requiring a new schedule), the error must be caught and shown in the UI.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/services/upstream_source_service.py` - Existing service to compose; needs one new method (`count_sources_for_target`)
- `/Users/chris/Projects/shuffify/shuffify/services/scheduler_service.py` - Schedule CRUD used by RaidSyncService for auto-schedule creation and updates
- `/Users/chris/Projects/shuffify/shuffify/routes/upstream_sources.py` - Pattern to follow for route structure, auth checks, DB checks, and error handling
- `/Users/chris/Projects/shuffify/shuffify/schemas/schedule_requests.py` - Pattern to follow for Pydantic validation with enum-based validators
- `/Users/chris/Projects/shuffify/shuffify/templates/workshop.html` - Template to modify (replace Raids tab placeholder with real UI at the `#sidebar-tab-raids` div, lines 209-219)