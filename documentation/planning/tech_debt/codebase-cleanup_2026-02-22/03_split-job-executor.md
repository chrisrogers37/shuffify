# Phase 03: Split Job Executor Service

**Status:** 🔧 IN PROGRESS
**Started:** 2026-02-23

## Header

| Field | Value |
|-------|-------|
| **PR Title** | Refactor: Split monolithic job executor into executors package |
| **Risk Level** | Medium |
| **Effort** | Medium (~3 hours) |
| **Files Created** | 5 |
| **Files Modified** | 6 |
| **Files Deleted** | 1 |

### Files Created
1. `shuffify/services/executors/__init__.py`
2. `shuffify/services/executors/base_executor.py`
3. `shuffify/services/executors/raid_executor.py`
4. `shuffify/services/executors/shuffle_executor.py`
5. `shuffify/services/executors/rotate_executor.py`

### Files Modified
1. `shuffify/services/__init__.py` (update import path)
2. `shuffify/scheduler.py` (update import path)
3. `shuffify/routes/schedules.py` (update import path)
4. `shuffify/services/raid_sync_service.py` (update 2 lazy imports)
5. `tests/services/test_job_executor_service.py` (update import path)
6. `tests/services/test_job_executor_rotate.py` (update import path)

### Files Deleted
1. `shuffify/services/job_executor_service.py` (replaced by `executors/` package)

---

## Context

`shuffify/services/job_executor_service.py` is a 969-line monolithic file containing all scheduled job execution logic: raid, shuffle, rotation (3 modes), token management, lifecycle recording, and snapshot handling. This makes it the largest service file in the codebase and violates the project's separation of concerns principle.

The file contains a single class (`JobExecutorService`) with 15 static methods spanning 4 distinct responsibility domains:
- **Lifecycle** (execute, execute_now, record success/failure, create execution record)
- **Raid** (execute raid, auto-snapshot before raid, fetch raid sources)
- **Shuffle** (execute shuffle with inline auto-snapshot)
- **Rotation** (execute rotate, validate config, auto-snapshot, archive/refresh/swap modes)

Splitting into a focused `executors/` package improves readability, makes each operation independently testable, and creates a clear extension point for future job types.

---

## Dependencies

- **Phase 01 (safe_commit extraction)**: Must be completed first. The new executor modules will use `safe_commit()` from `shuffify.services.base` instead of bare `db.session.commit()` calls. Phase 01 ensures `safe_commit()` is already consistently used across the service layer.
- **Blocks Phase 06**: Phase 06 (if it modifies executor behavior) depends on the new file structure being in place.

---

## Detailed Implementation Plan

### Public API Contract (MUST NOT CHANGE)

The only two public entry points are:

1. `JobExecutorService.execute(schedule_id: int) -> None` -- called by `shuffify/scheduler.py:312`
2. `JobExecutorService.execute_now(schedule_id: int, user_id: int) -> dict` -- called by `shuffify/routes/schedules.py:341`

Both callers import from `shuffify.services.job_executor_service`. After this phase, they will import from `shuffify.services.executors` (which re-exports identically). The `routes/schedules.py` import also needs updating.

### Method-to-Module Mapping

| Original Location (line) | Method | Target Module |
|--------------------------|--------|---------------|
| 37-40 | `JobExecutionError` | `base_executor.py` |
| 43-44 | `class JobExecutorService` | `base_executor.py` |
| 46-98 | `execute()` | `base_executor.py` |
| 100-112 | `_create_execution_record()` | `base_executor.py` |
| 114-179 | `_record_success()` | `base_executor.py` |
| 181-215 | `_record_failure()` | `base_executor.py` |
| 217-259 | `execute_now()` | `base_executor.py` |
| 261-335 | `_get_spotify_api()` | `base_executor.py` |
| 337-370 | `_execute_job_type()` | `base_executor.py` |
| 524-535 | `_batch_add_tracks()` | `base_executor.py` |
| 372-444 | `_execute_raid()` | `raid_executor.py` |
| 446-487 | `_auto_snapshot_before_raid()` | `raid_executor.py` |
| 489-522 | `_fetch_raid_sources()` | `raid_executor.py` |
| 537-648 | `_execute_shuffle()` | `shuffle_executor.py` |
| 556-592 | *(inline auto-snapshot)* | `shuffle_executor.py` as `_auto_snapshot_before_shuffle()` |
| 650-740 | `_execute_rotate()` | `rotate_executor.py` |
| 742-791 | `_validate_rotation_config()` | `rotate_executor.py` |
| 793-833 | `_auto_snapshot_before_rotate()` | `rotate_executor.py` |
| 835-860 | `_rotate_archive()` | `rotate_executor.py` |
| 862-911 | `_rotate_refresh()` | `rotate_executor.py` |
| 913-969 | `_rotate_swap()` | `rotate_executor.py` |

### Step 1: Create `shuffify/services/executors/__init__.py`

This file provides backward-compatible re-exports so that `from shuffify.services.job_executor_service import JobExecutorService` can be replaced with `from shuffify.services.executors import JobExecutorService` and the parent `__init__.py` only needs to change the import source.

```python
"""
Job executor package.

Splits the monolithic job_executor_service into focused modules:
- base_executor: Lifecycle, token management, dispatch, shared utilities
- raid_executor: Raid-specific operations
- shuffle_executor: Shuffle-specific operations
- rotate_executor: Rotation modes and pairing logic

Public API (backward-compatible):
    from shuffify.services.executors import (
        JobExecutorService,
        JobExecutionError,
    )
"""

from shuffify.services.executors.base_executor import (
    JobExecutorService,
    JobExecutionError,
)

__all__ = [
    "JobExecutorService",
    "JobExecutionError",
]
```

### Step 2: Create `shuffify/services/executors/base_executor.py`

This module contains the `JobExecutorService` class with lifecycle methods, token management, dispatch, and shared utilities. The dispatch method (`_execute_job_type`) imports the operation-specific methods from sibling modules.

```python
"""
Base job executor: lifecycle, token management, dispatch, and shared utilities.

Contains the JobExecutorService class which is the single public entry
point for all job execution. Operation-specific logic is delegated to
sibling modules (raid_executor, shuffle_executor, rotate_executor).
"""

import logging
from datetime import datetime, timezone
from typing import List

from shuffify.models.db import db, Schedule, JobExecution, User
from shuffify.services.base import safe_commit
from shuffify.services.token_service import (
    TokenService,
    TokenEncryptionError,
)
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import SpotifyTokenError
from shuffify.enums import JobType, ActivityType

logger = logging.getLogger(__name__)


class JobExecutionError(Exception):
    """Raised when a scheduled job fails to execute."""

    pass


class JobExecutorService:
    """Service that executes scheduled playlist operations."""

    @staticmethod
    def execute(schedule_id: int) -> None:
        """
        Execute a scheduled job.

        This is the main entry point called by the scheduler.
        It handles all error scenarios and records the execution.
        """
        execution = None
        schedule = None

        try:
            schedule = db.session.get(Schedule, schedule_id)
            if not schedule:
                logger.error(
                    f"Schedule {schedule_id} not found, "
                    f"skipping"
                )
                return

            if not schedule.is_enabled:
                logger.info(
                    f"Schedule {schedule_id} is disabled, "
                    f"skipping"
                )
                return

            execution = (
                JobExecutorService._create_execution_record(
                    schedule_id
                )
            )

            user = db.session.get(User, schedule.user_id)
            if not user:
                raise JobExecutionError(
                    f"User {schedule.user_id} not found"
                )

            api = JobExecutorService._get_spotify_api(user)

            result = JobExecutorService._execute_job_type(
                schedule, api
            )

            JobExecutorService._record_success(
                execution, schedule, result
            )

        except Exception as e:
            JobExecutorService._record_failure(
                execution, schedule, e, schedule_id
            )

    @staticmethod
    def _create_execution_record(
        schedule_id: int,
    ) -> JobExecution:
        """Create a running execution record in the database."""
        execution = JobExecution(
            schedule_id=schedule_id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.session.add(execution)
        safe_commit(
            f"create execution record for schedule {schedule_id}",
            JobExecutionError,
        )
        return execution

    @staticmethod
    def _record_success(
        execution: JobExecution,
        schedule: Schedule,
        result: dict,
    ) -> None:
        """Record a successful job execution."""
        execution.status = "success"
        execution.completed_at = datetime.now(timezone.utc)
        execution.tracks_added = result.get(
            "tracks_added", 0
        )
        execution.tracks_total = result.get(
            "tracks_total", 0
        )

        schedule.last_run_at = datetime.now(timezone.utc)
        schedule.last_status = "success"
        schedule.last_error = None

        db.session.commit()

        # Log activity (non-blocking)
        try:
            from shuffify.services.activity_log_service import (  # noqa: E501
                ActivityLogService,
            )

            ActivityLogService.log(
                user_id=schedule.user_id,
                activity_type=(
                    ActivityType.SCHEDULE_RUN
                ),
                description=(
                    f"Scheduled "
                    f"{schedule.job_type} on "
                    f"'{schedule.target_playlist_name}'"
                    f" completed"
                ),
                playlist_id=(
                    schedule.target_playlist_id
                ),
                playlist_name=(
                    schedule.target_playlist_name
                ),
                metadata={
                    "schedule_id": schedule.id,
                    "job_type": schedule.job_type,
                    "tracks_added": result.get(
                        "tracks_added", 0
                    ),
                    "tracks_total": result.get(
                        "tracks_total", 0
                    ),
                    "triggered_by": "scheduler",
                },
            )
        except Exception:
            pass

        logger.info(
            f"Schedule {schedule.id} executed "
            f"successfully: "
            f"added={result.get('tracks_added', 0)}, "
            f"total={result.get('tracks_total', 0)}"
        )

    @staticmethod
    def _record_failure(
        execution,
        schedule,
        error: Exception,
        schedule_id: int,
    ) -> None:
        """Record a failed job execution."""
        logger.error(
            f"Schedule {schedule_id} execution "
            f"failed: {error}",
            exc_info=True,
        )
        try:
            if execution:
                execution.status = "failed"
                execution.completed_at = datetime.now(
                    timezone.utc
                )
                execution.error_message = str(error)[:1000]

            if schedule:
                schedule.last_run_at = datetime.now(
                    timezone.utc
                )
                schedule.last_status = "failed"
                schedule.last_error = str(error)[:1000]

            db.session.commit()
        except Exception as db_err:
            logger.error(
                f"Failed to record execution failure: "
                f"{db_err}"
            )
            db.session.rollback()

    @staticmethod
    def execute_now(
        schedule_id: int, user_id: int
    ) -> dict:
        """
        Manually trigger a schedule execution (from the UI).

        Raises:
            JobExecutionError: If execution fails.
        """
        from shuffify.services.scheduler_service import (
            SchedulerService,
            ScheduleNotFoundError,
        )

        try:
            schedule = SchedulerService.get_schedule(
                schedule_id, user_id
            )
        except ScheduleNotFoundError:
            raise JobExecutionError(
                f"Schedule {schedule_id} not found"
            )

        # Execute synchronously
        JobExecutorService.execute(schedule_id)

        # Reload to get updated status
        db.session.refresh(schedule)

        if schedule.last_status == "failed":
            raise JobExecutionError(
                f"Execution failed: {schedule.last_error}"
            )

        return {
            "status": schedule.last_status,
            "last_run_at": (
                schedule.last_run_at.isoformat()
                if schedule.last_run_at
                else None
            ),
        }

    @staticmethod
    def _get_spotify_api(user: User) -> SpotifyAPI:
        """
        Create a SpotifyAPI client using the user's stored
        refresh token.

        Raises:
            JobExecutionError: If token decryption or refresh
                fails.
        """
        if not user.encrypted_refresh_token:
            raise JobExecutionError(
                f"User {user.spotify_id} has no stored "
                f"refresh token. User must log in to enable "
                f"scheduled operations."
            )

        try:
            refresh_token = TokenService.decrypt_token(
                user.encrypted_refresh_token
            )
        except TokenEncryptionError as e:
            raise JobExecutionError(
                f"Failed to decrypt refresh token for user "
                f"{user.spotify_id}: {e}"
            )

        try:
            from flask import current_app

            credentials = SpotifyCredentials.from_flask_config(
                current_app.config
            )
            auth_manager = SpotifyAuthManager(credentials)

            token_info = TokenInfo(
                access_token="expired_placeholder",
                token_type="Bearer",
                expires_at=0,
                refresh_token=refresh_token,
            )

            api = SpotifyAPI(
                token_info,
                auth_manager,
                auto_refresh=True,
            )

            # Update stored refresh token if it was rotated
            new_token = api.token_info
            if (
                new_token.refresh_token
                and new_token.refresh_token != refresh_token
            ):
                user.encrypted_refresh_token = (
                    TokenService.encrypt_token(
                        new_token.refresh_token
                    )
                )
                db.session.commit()
                logger.info(
                    f"Updated rotated refresh token for "
                    f"user {user.spotify_id}"
                )

            return api

        except SpotifyTokenError as e:
            raise JobExecutionError(
                f"Failed to refresh Spotify token for user "
                f"{user.spotify_id}: {e}"
            )

    @staticmethod
    def _execute_job_type(
        schedule: Schedule, api: SpotifyAPI
    ) -> dict:
        """Execute the appropriate operation based on job type."""
        from shuffify.services.executors.raid_executor import (
            execute_raid,
        )
        from shuffify.services.executors.shuffle_executor import (
            execute_shuffle,
        )
        from shuffify.services.executors.rotate_executor import (
            execute_rotate,
        )

        if schedule.job_type == JobType.RAID:
            return execute_raid(schedule, api)
        elif schedule.job_type == JobType.SHUFFLE:
            return execute_shuffle(schedule, api)
        elif schedule.job_type == JobType.RAID_AND_SHUFFLE:
            result = execute_raid(schedule, api)
            shuffle_result = execute_shuffle(schedule, api)
            result["tracks_total"] = shuffle_result[
                "tracks_total"
            ]
            return result
        elif schedule.job_type == JobType.ROTATE:
            return execute_rotate(schedule, api)
        else:
            raise JobExecutionError(
                f"Unknown job type: {schedule.job_type}"
            )

    @staticmethod
    def _batch_add_tracks(
        api: SpotifyAPI,
        playlist_id: str,
        uris: List[str],
        batch_size: int = 100,
    ) -> None:
        """Add tracks to a playlist in batches."""
        for i in range(0, len(uris), batch_size):
            batch = uris[i: i + batch_size]
            api._ensure_valid_token()
            api._sp.playlist_add_items(playlist_id, batch)
```

**Key changes from original:**
1. `_create_execution_record()` now uses `safe_commit()` instead of bare `db.session.commit()` (Phase 01 dependency).
2. `_execute_job_type()` imports from sibling executor modules instead of calling `self._execute_*` methods.
3. `_record_success()` and `_record_failure()` retain their original commit patterns -- `_record_success` uses bare commit (it's in a hot path where the job already succeeded), and `_record_failure` has its own try/except with rollback (must never raise). These are deliberate -- do NOT convert them to `safe_commit()`.
4. Token rotation commit at line 323 remains bare -- this is a non-critical update that should not block the job. Do NOT convert to `safe_commit()`.

### Step 3: Create `shuffify/services/executors/raid_executor.py`

```python
"""
Raid executor: pull new tracks from source playlists into target.
"""

import logging
from typing import List

from shuffify.models.db import Schedule
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.enums import SnapshotType
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)

logger = logging.getLogger(__name__)


def execute_raid(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """
    Pull new tracks from source playlists into the target.
    """
    # Import here to avoid circular dependency
    from shuffify.services.executors.base_executor import (
        JobExecutorService,
        JobExecutionError,
    )

    target_id = schedule.target_playlist_id
    source_ids = schedule.source_playlist_ids or []

    if not source_ids:
        logger.info(
            f"Schedule {schedule.id}: no source playlists "
            f"configured, skipping raid"
        )
        target_tracks = api.get_playlist_tracks(target_id)
        return {
            "tracks_added": 0,
            "tracks_total": len(target_tracks),
        }

    try:
        target_tracks = api.get_playlist_tracks(target_id)
        target_uris = {
            t.get("uri")
            for t in target_tracks
            if t.get("uri")
        }

        _auto_snapshot_before_raid(
            schedule, target_tracks
        )

        new_uris = _fetch_raid_sources(
            api, source_ids, target_uris
        )

        if not new_uris:
            logger.info(
                f"Schedule {schedule.id}: "
                f"no new tracks to add"
            )
            return {
                "tracks_added": 0,
                "tracks_total": len(target_tracks),
            }

        JobExecutorService._batch_add_tracks(
            api, target_id, new_uris
        )

        total = len(target_tracks) + len(new_uris)
        logger.info(
            f"Schedule {schedule.id}: added "
            f"{len(new_uris)} tracks to "
            f"{schedule.target_playlist_name} "
            f"(total: {total})"
        )

        return {
            "tracks_added": len(new_uris),
            "tracks_total": total,
        }

    except SpotifyNotFoundError:
        raise JobExecutionError(
            f"Target playlist {target_id} not found. "
            f"It may have been deleted."
        )
    except SpotifyAPIError as e:
        raise JobExecutionError(
            f"Spotify API error during raid: {e}"
        )


def _auto_snapshot_before_raid(
    schedule: Schedule,
    target_tracks: list,
) -> None:
    """Create an auto-snapshot before a scheduled raid
    if enabled."""
    try:
        pre_raid_uris = [
            t.get("uri")
            for t in target_tracks
            if t.get("uri")
        ]
        if (
            pre_raid_uris
            and PlaylistSnapshotService
            .is_auto_snapshot_enabled(
                schedule.user_id
            )
        ):
            PlaylistSnapshotService.create_snapshot(
                user_id=schedule.user_id,
                playlist_id=(
                    schedule.target_playlist_id
                ),
                playlist_name=(
                    schedule.target_playlist_name
                    or schedule.target_playlist_id
                ),
                track_uris=pre_raid_uris,
                snapshot_type=(
                    SnapshotType.AUTO_PRE_RAID
                ),
                trigger_description=(
                    "Before scheduled raid"
                ),
            )
    except Exception as snap_err:
        logger.warning(
            "Auto-snapshot before scheduled "
            f"raid failed: {snap_err}"
        )


def _fetch_raid_sources(
    api: SpotifyAPI,
    source_ids: list,
    target_uris: set,
) -> List[str]:
    """
    Fetch new tracks from source playlists not already
    in target.

    Returns:
        List of new track URIs (deduplicated).
    """
    new_uris: List[str] = []
    for source_id in source_ids:
        try:
            source_tracks = api.get_playlist_tracks(
                source_id
            )
            for track in source_tracks:
                uri = track.get("uri")
                if (
                    uri
                    and uri not in target_uris
                    and uri not in new_uris
                ):
                    new_uris.append(uri)
        except SpotifyNotFoundError:
            logger.warning(
                f"Source playlist {source_id} "
                f"not found, skipping"
            )
            continue
    return new_uris
```

**Key design decisions:**
- Functions are module-level (not class methods) since they are operation-specific.
- `execute_raid()` imports `JobExecutorService` and `JobExecutionError` from `base_executor` inside the function body to avoid circular imports. The base dispatches to raid, and raid needs `_batch_add_tracks` and `JobExecutionError` from base.
- `_auto_snapshot_before_raid` and `_fetch_raid_sources` are private module-level functions (prefixed with `_`).

### Step 4: Create `shuffify/services/executors/shuffle_executor.py`

The key change here is extracting the inline auto-snapshot code (original lines 556-592) into `_auto_snapshot_before_shuffle()`.

```python
"""
Shuffle executor: run shuffle algorithms on target playlists.
"""

import logging

from shuffify.models.db import Schedule
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.enums import SnapshotType
from shuffify.shuffle_algorithms.registry import ShuffleRegistry
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)

logger = logging.getLogger(__name__)


def execute_shuffle(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """Run a shuffle algorithm on the target playlist."""
    from shuffify.services.executors.base_executor import (
        JobExecutionError,
    )

    target_id = schedule.target_playlist_id
    algorithm_name = schedule.algorithm_name

    if not algorithm_name:
        raise JobExecutionError(
            f"Schedule {schedule.id}: "
            f"no algorithm configured for shuffle"
        )

    try:
        raw_tracks = api.get_playlist_tracks(target_id)
        if not raw_tracks:
            return {"tracks_added": 0, "tracks_total": 0}

        _auto_snapshot_before_shuffle(
            schedule, raw_tracks, algorithm_name
        )

        tracks = []
        for t in raw_tracks:
            if t.get("uri"):
                tracks.append(
                    {
                        "id": t.get("id", ""),
                        "name": t.get("name", ""),
                        "uri": t["uri"],
                        "artists": [
                            a.get("name", "")
                            for a in t.get("artists", [])
                        ],
                        "album": t.get("album", {}),
                    }
                )

        if not tracks:
            return {"tracks_added": 0, "tracks_total": 0}

        algorithm_class = ShuffleRegistry.get_algorithm(
            algorithm_name
        )
        algorithm = algorithm_class()
        params = schedule.algorithm_params or {}
        shuffled_uris = algorithm.shuffle(
            tracks, **params
        )

        api.update_playlist_tracks(
            target_id, shuffled_uris
        )

        logger.info(
            f"Schedule {schedule.id}: shuffled "
            f"{schedule.target_playlist_name} "
            f"with {algorithm_name}"
        )

        return {
            "tracks_added": 0,
            "tracks_total": len(shuffled_uris),
        }

    except SpotifyNotFoundError:
        raise JobExecutionError(
            f"Target playlist {target_id} not found"
        )
    except ValueError as e:
        raise JobExecutionError(
            f"Invalid algorithm '{algorithm_name}': {e}"
        )
    except SpotifyAPIError as e:
        raise JobExecutionError(
            f"Spotify API error during shuffle: {e}"
        )


def _auto_snapshot_before_shuffle(
    schedule: Schedule,
    raw_tracks: list,
    algorithm_name: str,
) -> None:
    """Create an auto-snapshot before a scheduled shuffle
    if enabled."""
    try:
        pre_shuffle_uris = [
            t["uri"]
            for t in raw_tracks
            if t.get("uri")
        ]
        if (
            pre_shuffle_uris
            and PlaylistSnapshotService
            .is_auto_snapshot_enabled(
                schedule.user_id
            )
        ):
            PlaylistSnapshotService.create_snapshot(
                user_id=schedule.user_id,
                playlist_id=schedule.target_playlist_id,
                playlist_name=(
                    schedule.target_playlist_name
                    or schedule.target_playlist_id
                ),
                track_uris=pre_shuffle_uris,
                snapshot_type=(
                    SnapshotType
                    .SCHEDULED_PRE_EXECUTION
                ),
                trigger_description=(
                    "Before scheduled "
                    f"{algorithm_name}"
                ),
            )
    except Exception as snap_err:
        logger.warning(
            "Auto-snapshot before scheduled "
            f"shuffle failed: {snap_err}"
        )
```

**Key change:** The inline auto-snapshot block (original lines 556-592) is now a standalone `_auto_snapshot_before_shuffle()` function, consistent with the existing `_auto_snapshot_before_raid()` and `_auto_snapshot_before_rotate()` patterns.

### Step 5: Create `shuffify/services/executors/rotate_executor.py`

```python
"""
Rotate executor: rotation modes and pairing logic for
production/archive playlist management.
"""

import logging

from shuffify.models.db import Schedule
from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyNotFoundError,
)
from shuffify.enums import SnapshotType, RotationMode
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)

logger = logging.getLogger(__name__)


def execute_rotate(
    schedule: Schedule, api: SpotifyAPI
) -> dict:
    """
    Rotate tracks between production and archive
    playlists.
    """
    from shuffify.services.executors.base_executor import (
        JobExecutorService,
        JobExecutionError,
    )

    target_id = schedule.target_playlist_id
    rotation_mode, rotation_count, pair = (
        _validate_rotation_config(schedule)
    )
    archive_id = pair.archive_playlist_id

    try:
        prod_tracks = api.get_playlist_tracks(
            target_id
        )
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

        _auto_snapshot_before_rotate(
            schedule, prod_uris, rotation_mode
        )

        actual_count = min(
            rotation_count, len(prod_uris)
        )
        if actual_count == 0:
            return {
                "tracks_added": 0,
                "tracks_total": len(prod_uris),
            }

        oldest_uris = prod_uris[:actual_count]

        if rotation_mode == RotationMode.ARCHIVE_OLDEST:
            return _rotate_archive(
                api, schedule, target_id,
                archive_id, oldest_uris,
                prod_uris, actual_count,
            )
        elif rotation_mode == RotationMode.REFRESH:
            return _rotate_refresh(
                api, schedule, target_id,
                archive_id, prod_uris,
                actual_count,
            )
        elif rotation_mode == RotationMode.SWAP:
            return _rotate_swap(
                api, schedule, target_id,
                archive_id, oldest_uris,
                prod_uris, actual_count,
            )
        else:
            raise JobExecutionError(
                "Unknown rotation mode: "
                "{}".format(rotation_mode)
            )

    except JobExecutionError:
        raise
    except SpotifyNotFoundError:
        raise JobExecutionError(
            "Playlist not found during rotation. "
            "Target: {}, Archive: {}".format(
                target_id, archive_id
            )
        )
    except SpotifyAPIError as e:
        raise JobExecutionError(
            "Spotify API error during "
            "rotation: {}".format(e)
        )


def _validate_rotation_config(
    schedule: Schedule,
) -> tuple:
    """
    Extract and validate rotation parameters from
    schedule.

    Returns:
        Tuple of (rotation_mode, rotation_count, pair).

    Raises:
        JobExecutionError: If mode is invalid or no
            pair found.
    """
    from shuffify.services.executors.base_executor import (
        JobExecutionError,
    )
    from shuffify.services.playlist_pair_service import (
        PlaylistPairService,
    )

    params = schedule.algorithm_params or {}
    rotation_mode = params.get(
        "rotation_mode", RotationMode.ARCHIVE_OLDEST
    )
    rotation_count = max(
        1, int(params.get("rotation_count", 5))
    )

    valid_modes = set(RotationMode)
    if rotation_mode not in valid_modes:
        raise JobExecutionError(
            "Invalid rotation_mode: "
            "{}".format(rotation_mode)
        )

    pair = PlaylistPairService.get_pair_for_playlist(
        user_id=schedule.user_id,
        production_playlist_id=(
            schedule.target_playlist_id
        ),
    )
    if not pair:
        raise JobExecutionError(
            "No archive pair found for playlist "
            "{}. Create a pair in the workshop "
            "first.".format(
                schedule.target_playlist_id
            )
        )

    return rotation_mode, rotation_count, pair


def _auto_snapshot_before_rotate(
    schedule: Schedule,
    prod_uris: list,
    rotation_mode: str,
) -> None:
    """Create an auto-snapshot before rotation if
    enabled."""
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
                playlist_id=(
                    schedule.target_playlist_id
                ),
                playlist_name=(
                    schedule.target_playlist_name
                    or schedule.target_playlist_id
                ),
                track_uris=prod_uris,
                snapshot_type=(
                    SnapshotType.AUTO_PRE_ROTATE
                ),
                trigger_description=(
                    "Before scheduled "
                    "{} rotation".format(
                        rotation_mode
                    )
                ),
            )
    except Exception as snap_err:
        logger.warning(
            "Auto-snapshot before rotation "
            "failed: %s", snap_err
        )


def _rotate_archive(
    api, schedule, target_id, archive_id,
    oldest_uris, prod_uris, actual_count,
):
    """Archive oldest tracks from production."""
    from shuffify.services.executors.base_executor import (
        JobExecutorService,
    )

    JobExecutorService._batch_add_tracks(
        api, archive_id, oldest_uris
    )
    api.playlist_remove_items(
        target_id, oldest_uris
    )

    logger.info(
        "Schedule %s: archived %d oldest tracks "
        "from '%s'",
        schedule.id, actual_count,
        schedule.target_playlist_name,
    )

    return {
        "tracks_added": 0,
        "tracks_total": (
            len(prod_uris) - actual_count
        ),
    }


def _rotate_refresh(
    api, schedule, target_id, archive_id,
    prod_uris, actual_count,
):
    """Replace oldest production tracks with newest
    archive tracks."""
    from shuffify.services.executors.base_executor import (
        JobExecutorService,
    )

    archive_tracks = api.get_playlist_tracks(
        archive_id
    )
    archive_uris = [
        t["uri"]
        for t in archive_tracks
        if t.get("uri")
    ]

    prod_set = set(prod_uris)
    available = [
        u for u in archive_uris
        if u not in prod_set
    ]
    refresh_uris = available[-actual_count:]
    remove_count = min(
        actual_count, len(refresh_uris)
    )
    to_remove = prod_uris[:remove_count]

    if refresh_uris:
        api.playlist_remove_items(
            target_id, to_remove
        )
        JobExecutorService._batch_add_tracks(
            api, target_id, refresh_uris
        )

    new_total = (
        len(prod_uris) - remove_count
        + len(refresh_uris)
    )

    logger.info(
        "Schedule %s: refreshed %d tracks in '%s'",
        schedule.id, len(refresh_uris),
        schedule.target_playlist_name,
    )

    return {
        "tracks_added": len(refresh_uris),
        "tracks_total": new_total,
    }


def _rotate_swap(
    api, schedule, target_id, archive_id,
    oldest_uris, prod_uris, actual_count,
):
    """Exchange tracks between production and
    archive."""
    from shuffify.services.executors.base_executor import (
        JobExecutorService,
    )

    archive_tracks = api.get_playlist_tracks(
        archive_id
    )
    archive_uris = [
        t["uri"]
        for t in archive_tracks
        if t.get("uri")
    ]

    prod_set = set(prod_uris)
    available = [
        u for u in archive_uris
        if u not in prod_set
    ]
    swap_in_uris = available[-actual_count:]
    swap_out_uris = oldest_uris[
        :len(swap_in_uris)
    ]

    if swap_in_uris and swap_out_uris:
        JobExecutorService._batch_add_tracks(
            api, archive_id, swap_out_uris
        )
        api.playlist_remove_items(
            target_id, swap_out_uris
        )

        JobExecutorService._batch_add_tracks(
            api, target_id, swap_in_uris
        )
        api.playlist_remove_items(
            archive_id, swap_in_uris
        )

    swapped = min(
        len(swap_in_uris),
        len(swap_out_uris),
    )

    logger.info(
        "Schedule %s: swapped %d tracks between "
        "'%s' and archive",
        schedule.id, swapped,
        schedule.target_playlist_name,
    )

    return {
        "tracks_added": swapped,
        "tracks_total": len(prod_uris),
    }
```

### Step 6: Update `shuffify/services/__init__.py`

Change the import source for `JobExecutorService` and `JobExecutionError`.

**Before** (lines 97-100):
```python
# Job Executor Service
from shuffify.services.job_executor_service import (
    JobExecutorService,
    JobExecutionError,
)
```

**After**:
```python
# Job Executor Service
from shuffify.services.executors import (
    JobExecutorService,
    JobExecutionError,
)
```

No other changes to this file. The `__all__` list remains identical.

### Step 7: Update `shuffify/scheduler.py`

Change the lazy import inside `_execute_scheduled_job()`.

**Before** (lines 308-309):
```python
            from shuffify.services.job_executor_service import (
                JobExecutorService,
            )
```

**After**:
```python
            from shuffify.services.executors import (
                JobExecutorService,
            )
```

This is the only change in `scheduler.py`. The function signature, behavior, and all other code remain identical.

### Step 8: Update `shuffify/routes/schedules.py`

Change the import at the top of the file.

**Before** (lines 42-44):
```python
from shuffify.services.job_executor_service import (
    JobExecutorService,
)
```

**After**:
```python
from shuffify.services.executors import (
    JobExecutorService,
)
```

This is the only change in this file.

### Step 9: Delete `shuffify/services/job_executor_service.py`

After verifying all tests pass with the new import paths, delete the original monolithic file. Use `git rm`:

```bash
git rm shuffify/services/job_executor_service.py
```

**IMPORTANT:** Do NOT delete this file until Steps 1-8 are complete and all tests pass. If any test fails, the original file serves as the reference for debugging.

---

## Test Plan

### Existing Tests (49 total -- import path changes only)

Both test files need their import statements updated. No test logic changes.

#### `tests/services/test_job_executor_service.py` (236 lines, ~12 tests)

**Before** (lines 10-13):
```python
from shuffify.services.job_executor_service import (
    JobExecutorService,
    JobExecutionError,
)
```

**After**:
```python
from shuffify.services.executors import (
    JobExecutorService,
    JobExecutionError,
)
```

Additionally, all `@patch` decorators that reference `shuffify.services.job_executor_service` must be updated to reference `shuffify.services.executors.base_executor`. There are 4 patch targets in this file:

1. **Line 186-188** (and similar):
```python
# Before
@patch(
    "shuffify.services.job_executor_service"
    ".TokenService"
)
# After
@patch(
    "shuffify.services.executors.base_executor"
    ".TokenService"
)
```

2. **Line 189-191**:
```python
# Before
@patch(
    "shuffify.services.job_executor_service"
    ".SpotifyAPI"
)
# After
@patch(
    "shuffify.services.executors.base_executor"
    ".SpotifyAPI"
)
```

3. **Line 192-194**:
```python
# Before
@patch(
    "shuffify.services.job_executor_service"
    ".SpotifyAuthManager"
)
# After
@patch(
    "shuffify.services.executors.base_executor"
    ".SpotifyAuthManager"
)
```

4. **Line 195-197**:
```python
# Before
@patch(
    "shuffify.services.job_executor_service"
    ".SpotifyCredentials"
)
# After
@patch(
    "shuffify.services.executors.base_executor"
    ".SpotifyCredentials"
)
```

#### `tests/services/test_job_executor_rotate.py` (693 lines, ~37 tests)

**Before** (lines 11-14):
```python
from shuffify.services.job_executor_service import (
    JobExecutorService,
    JobExecutionError,
)
```

**After**:
```python
from shuffify.services.executors import (
    JobExecutorService,
    JobExecutionError,
)
```

Additionally, ALL `@patch` decorators referencing `shuffify.services.job_executor_service.PlaylistSnapshotService` must be updated. These appear at the following lines and all follow the same pattern:

```python
# Before (appears at lines 81-83, 118-120, 149-151, 175-177, 205-207,
#          231-233, 258-260, 299-301, 333-335, 366-368, 410-412,
#          444-446, 480-482, 544-546, 576-578, 605-607, 665-668)
@patch(
    "shuffify.services.job_executor_service"
    ".PlaylistSnapshotService"
)
# After
@patch(
    "shuffify.services.executors.rotate_executor"
    ".PlaylistSnapshotService"
)
```

**CRITICAL:** The patch target for `PlaylistSnapshotService` depends on which executor module imports it. In the rotation tests, it must patch the rotate executor's import. For any shuffle tests that test auto-snapshot, it would need to patch the shuffle executor's import.

For the rotation tests, `PlaylistSnapshotService` is imported in `rotate_executor.py`, so the patch target becomes `shuffify.services.executors.rotate_executor.PlaylistSnapshotService`.

The `PlaylistPairService` patches reference `shuffify.services.playlist_pair_service.PlaylistPairService.get_pair_for_playlist` -- these do NOT change because `_validate_rotation_config` imports `PlaylistPairService` lazily from `shuffify.services.playlist_pair_service`, not from the executor module.

### Verification Commands

```bash
# Run all tests (must see 49 passing in the two executor test files)
pytest tests/services/test_job_executor_service.py tests/services/test_job_executor_rotate.py -v

# Run full test suite to confirm no regressions
pytest tests/ -v

# Lint check
flake8 shuffify/services/executors/
flake8 shuffify/scheduler.py
flake8 shuffify/routes/schedules.py
flake8 shuffify/services/__init__.py
```

---

## Documentation Updates

### `CLAUDE.md`

Update the "Key Files to Know" table. Replace the `job_executor_service.py` entry:

**Before:**
```
| `shuffify/services/` | 17 service modules (auth, playlist, shuffle, state, token, scheduler, job_executor, user, workshop_session, upstream_source, activity_log, dashboard, login_history, playlist_snapshot, user_settings, playlist_pair, raid_sync) |
```

**After:**
```
| `shuffify/services/` | 17 service modules (auth, playlist, shuffle, state, token, scheduler, user, workshop_session, upstream_source, activity_log, dashboard, login_history, playlist_snapshot, user_settings, playlist_pair, raid_sync) + executors package |
```

Also add a new row to the key files table:

```
| `shuffify/services/executors/` | Job executor package (base_executor, raid_executor, shuffle_executor, rotate_executor) |
```

### `CHANGELOG.md`

Add under `## [Unreleased]`:

```markdown
### Changed
- **Job Executor Service** - Split monolithic 969-line `job_executor_service.py` into focused `executors/` package
  - `base_executor.py`: Lifecycle, token management, dispatch, shared utilities
  - `raid_executor.py`: Raid-specific operations
  - `shuffle_executor.py`: Shuffle-specific operations with extracted auto-snapshot
  - `rotate_executor.py`: Rotation modes and pairing logic
  - Public API unchanged: `JobExecutorService.execute()` and `JobExecutorService.execute_now()`
```

---

## Stress Testing & Edge Cases

### Circular Import Prevention

The executor modules have a circular dependency pattern: `base_executor` dispatches to `raid_executor`, `shuffle_executor`, and `rotate_executor`, while those modules need `JobExecutionError` and `_batch_add_tracks` from `base_executor`. This is handled by:

1. `base_executor._execute_job_type()` uses **lazy imports** (inside the function body) for the three executor modules.
2. Each executor module uses **lazy imports** for `JobExecutorService` and `JobExecutionError` inside function bodies where needed.

**DO NOT** move these imports to the top of the file -- it will cause `ImportError` due to circular dependencies.

### Backward Compatibility

- `from shuffify.services.job_executor_service import JobExecutorService` will **break** after the file is deleted. All import paths must be updated before deletion.
- `from shuffify.services.executors import JobExecutorService` works via the `__init__.py` re-export.
- `from shuffify.services import JobExecutorService` continues to work (updated in `services/__init__.py`).

### Module-Level vs Class-Level Functions

The executor functions (`execute_raid`, `execute_shuffle`, `execute_rotate`) are **module-level functions**, not `JobExecutorService` static methods. This is intentional -- they are implementation details dispatched by the base class. However, `_batch_add_tracks` remains on `JobExecutorService` because it is a shared utility used by multiple executors and needs a single reference point.

---

## Verification Checklist

- [ ] All 5 new files created in `shuffify/services/executors/`
- [ ] `shuffify/services/__init__.py` updated to import from `executors`
- [ ] `shuffify/scheduler.py` updated to import from `executors`
- [ ] `shuffify/routes/schedules.py` updated to import from `executors`
- [ ] `tests/services/test_job_executor_service.py` import and patch paths updated
- [ ] `tests/services/test_job_executor_rotate.py` import and patch paths updated
- [ ] `shuffify/services/job_executor_service.py` deleted via `git rm`
- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/services/test_job_executor_service.py -v` -- all tests pass
- [ ] `pytest tests/services/test_job_executor_rotate.py -v` -- all tests pass
- [ ] `pytest tests/ -v` -- full suite passes (no regressions)
- [ ] CHANGELOG.md updated
- [ ] CLAUDE.md key files table updated

---

## What NOT To Do

1. **DO NOT move `_batch_add_tracks` to a separate utility module.** It stays on `JobExecutorService` in `base_executor.py`. Moving it would break the existing test that patches `JobExecutorService._batch_add_tracks`.

2. **DO NOT convert `_record_success()` or `_record_failure()` to use `safe_commit()`.** `_record_failure` has its own try/except with rollback that must never raise (it would mask the original error). `_record_success` is a hot path where failure is unexpected and the existing pattern is fine.

3. **DO NOT convert the token rotation commit (in `_get_spotify_api`) to use `safe_commit()`.** This is a non-critical update that should not block job execution.

4. **DO NOT put imports of sibling executor modules at the top of any executor file.** This will cause circular `ImportError`. Always use lazy imports inside function bodies.

5. **DO NOT change the `PlaylistPairService` patch paths in rotation tests.** The `PlaylistPairService` is imported lazily from `shuffify.services.playlist_pair_service` inside `_validate_rotation_config()`, so the patch target remains `shuffify.services.playlist_pair_service.PlaylistPairService.get_pair_for_playlist`.

6. **DO NOT rename the executor functions to match the old static method names.** The old names were `_execute_raid`, `_execute_shuffle`, `_execute_rotate` (with underscore prefix indicating private). The new module-level functions are `execute_raid`, `execute_shuffle`, `execute_rotate` (no underscore -- they are the public API of their respective modules). The dispatch in `_execute_job_type` calls these new names.

7. **DO NOT create a backward-compatibility shim at the old path `shuffify/services/job_executor_service.py`.** The only two external callers are explicitly updated in this phase. A shim would add dead code.

8. **DO NOT add new tests in this phase.** The goal is a pure structural refactor. All 49 existing tests must pass with only import path changes. If any test needs logic changes, something went wrong in the split.
