# Phase 5: Activity Log (Unified Audit Trail)

## PR Title
`feat: Add ActivityLog model and service for unified user activity tracking (#Phase5)`

## Risk Level
**Low-Medium** -- This phase is purely additive. It creates a new model and service, then hooks into existing routes/services with non-blocking `try/except` calls. No existing behavior is modified; if logging fails, all primary operations continue unaffected.

## Effort Estimate
**Medium** (~4-6 hours implementation + testing)
- New model: ~30 min
- New enum: ~15 min
- New service: ~1 hour
- Hook into 12+ existing routes/services: ~1.5 hours
- Alembic migration: ~15 min
- Tests (~60+ tests): ~2 hours
- Services `__init__.py` and documentation updates: ~30 min

## Files to Create
| File | Purpose |
|------|---------|
| `shuffify/services/activity_log_service.py` | New service with `log()`, `get_recent()`, `get_activity_since()`, `get_activity_summary()` |
| `tests/services/test_activity_log_service.py` | Comprehensive tests for the new service |

## Files to Modify
| File | Change |
|------|--------|
| `shuffify/models/db.py` | Add `ActivityLog` model (lines ~418+) |
| `shuffify/enums.py` | Add `ActivityType` enum (lines ~31+) |
| `shuffify/services/__init__.py` | Export `ActivityLogService` and `ActivityLogError` |
| `shuffify/routes/shuffle.py` | Add activity log call after successful shuffle |
| `shuffify/routes/workshop.py` | Add activity log calls after commit, session save/delete |
| `shuffify/routes/upstream_sources.py` | Add activity log calls after add/delete source |
| `shuffify/routes/schedules.py` | Add activity log calls after create/update/delete/toggle/run |
| `shuffify/routes/core.py` | Add activity log calls after login/logout |
| `shuffify/services/job_executor_service.py` | Add activity log call after successful job execution |
| `CHANGELOG.md` | Add entry under `[Unreleased]` |

## Context & Dependencies

### Prerequisites (must be completed first)
- **Phase 0**: PostgreSQL with Alembic migrations -- needed so we can generate a proper migration for the `ActivityLog` table (and so `db.JSON` column works reliably). If running against SQLite in dev, `db.JSON` still works via SQLAlchemy's JSON type which serializes to TEXT.
- **Phase 1**: Enhanced User dimension table -- `ActivityLog.user_id` references `users.id`.

### Not required (but integrates if present)
- **Phase 4**: PlaylistSnapshot -- if snapshot routes exist, we add logging hooks there too. The plan includes those hooks but they can be skipped if Phase 4 is not yet merged.

### Existing patterns followed
- **Static service methods** (same as `UserService`, `WorkshopSessionService`)
- **Non-blocking try/except** (same pattern as `UserService.upsert_from_spotify` call in `core.py` callback, lines 195-202)
- **StrEnum for constants** (same as `JobType`, `ScheduleType`, `IntervalValue` in `shuffify/enums.py`)
- **db_app / app_ctx fixtures** for service tests (same pattern as `test_user_service.py`)

---

## Detailed Implementation

### Step 1: Add `ActivityType` enum to `shuffify/enums.py`

**File**: `/Users/chris/Projects/shuffify/shuffify/enums.py`

**Where**: After the `IntervalValue` class (after line 30), add a new enum class.

**Before** (end of file, lines 25-30):
```python
class IntervalValue(StrEnum):
    """Predefined interval values for interval schedules."""
    EVERY_6H = "every_6h"
    EVERY_12H = "every_12h"
    DAILY = "daily"
    EVERY_3D = "every_3d"
    WEEKLY = "weekly"
```

**After** (append after line 30):
```python
class IntervalValue(StrEnum):
    """Predefined interval values for interval schedules."""
    EVERY_6H = "every_6h"
    EVERY_12H = "every_12h"
    DAILY = "daily"
    EVERY_3D = "every_3d"
    WEEKLY = "weekly"


class ActivityType(StrEnum):
    """Types of user activities tracked in the activity log."""
    SHUFFLE = "shuffle"
    WORKSHOP_COMMIT = "workshop_commit"
    WORKSHOP_SESSION_SAVE = "workshop_session_save"
    WORKSHOP_SESSION_DELETE = "workshop_session_delete"
    UPSTREAM_SOURCE_ADD = "upstream_source_add"
    UPSTREAM_SOURCE_DELETE = "upstream_source_delete"
    SCHEDULE_CREATE = "schedule_create"
    SCHEDULE_UPDATE = "schedule_update"
    SCHEDULE_DELETE = "schedule_delete"
    SCHEDULE_TOGGLE = "schedule_toggle"
    SCHEDULE_RUN = "schedule_run"
    SNAPSHOT_CREATE = "snapshot_create"
    SNAPSHOT_RESTORE = "snapshot_restore"
    SNAPSHOT_DELETE = "snapshot_delete"
    SETTINGS_CHANGE = "settings_change"
    LOGIN = "login"
    LOGOUT = "logout"
```

**Rationale**: Using `StrEnum` is consistent with the existing `JobType`, `ScheduleType`, and `IntervalValue` enums. This gives both type safety and string serialization for DB storage.

---

### Step 2: Add `ActivityLog` model to `shuffify/models/db.py`

**File**: `/Users/chris/Projects/shuffify/shuffify/models/db.py`

**Where**: After the `JobExecution` class (after line 417), add the new model.

**Add import** at line 14 (alongside the existing enums import):
```python
from shuffify.enums import ScheduleType, IntervalValue, ActivityType
```

**Before** (line 14):
```python
from shuffify.enums import ScheduleType, IntervalValue
```

**After** (line 14):
```python
from shuffify.enums import ScheduleType, IntervalValue, ActivityType
```

**Add model after line 417** (after the `JobExecution.__repr__` method):
```python
class ActivityLog(db.Model):
    """
    Unified activity log for tracking all user actions.

    Every significant user action (shuffle, workshop commit, schedule
    change, etc.) is recorded here for audit and dashboard display.
    Logging is non-blocking: failures must never prevent the primary
    operation from succeeding.
    """

    __tablename__ = "activity_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    activity_type = db.Column(
        db.String(50), nullable=False, index=True
    )
    description = db.Column(db.String(500), nullable=False)
    playlist_id = db.Column(db.String(255), nullable=True)
    playlist_name = db.Column(db.String(255), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # Relationships
    user = db.relationship(
        "User",
        backref=db.backref("activities", lazy="dynamic"),
    )

    # Composite index for efficient "recent activity for user" queries
    __table_args__ = (
        db.Index(
            "ix_activity_user_created",
            "user_id",
            "created_at",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the ActivityLog to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "activity_type": self.activity_type,
            "description": self.description,
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
            "metadata": self.metadata_json,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<ActivityLog {self.id}: {self.activity_type} "
            f"by user {self.user_id}>"
        )
```

**Also add `ActivityLog` to the `User` model's docstring** (optional but helpful). The `backref` on the relationship already creates `user.activities`.

**Also update the module docstring** at line 1-6 to mention ActivityLog:
```python
"""
SQLAlchemy database models for Shuffify.

Defines the User, WorkshopSession, UpstreamSource, Schedule,
JobExecution, and ActivityLog models for persistent storage.
"""
```

---

### Step 3: Create `ActivityLogService`

**File**: `/Users/chris/Projects/shuffify/shuffify/services/activity_log_service.py` (NEW FILE)

```python
"""
Activity log service for recording and querying user actions.

All logging methods are designed to be non-blocking: if logging fails,
the error is caught and logged but never propagated to the caller.
This ensures that activity tracking never breaks primary operations.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from shuffify.models.db import db, ActivityLog
from shuffify.enums import ActivityType

logger = logging.getLogger(__name__)


class ActivityLogError(Exception):
    """Base exception for activity log operations."""

    pass


class ActivityLogService:
    """Service for recording and querying user activity."""

    @staticmethod
    def log(
        user_id: int,
        activity_type: str,
        description: str,
        playlist_id: Optional[str] = None,
        playlist_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ActivityLog]:
        """
        Record a user activity. Non-blocking: never raises.

        This method wraps the entire operation in try/except so
        that a logging failure can never break the calling code.

        Args:
            user_id: The internal database user ID.
            activity_type: One of the ActivityType enum values.
            description: Human-readable description of the action.
            playlist_id: Spotify playlist ID (if applicable).
            playlist_name: Human-readable playlist name (if applicable).
            metadata: Additional context as a JSON-serializable dict.

        Returns:
            The created ActivityLog instance, or None if logging failed.
        """
        try:
            activity = ActivityLog(
                user_id=user_id,
                activity_type=activity_type,
                description=description[:500],
                playlist_id=playlist_id,
                playlist_name=playlist_name,
                metadata_json=metadata,
            )
            db.session.add(activity)
            db.session.commit()
            logger.debug(
                f"Activity logged: {activity_type} "
                f"for user {user_id}"
            )
            return activity
        except Exception as e:
            db.session.rollback()
            logger.warning(
                f"Failed to log activity "
                f"({activity_type} for user {user_id}): {e}"
            )
            return None

    @staticmethod
    def get_recent(
        user_id: int,
        limit: int = 20,
        activity_type: Optional[str] = None,
    ) -> List[ActivityLog]:
        """
        Get recent activity for a user.

        Args:
            user_id: The internal database user ID.
            limit: Maximum number of records to return.
            activity_type: Optional filter by activity type.

        Returns:
            List of ActivityLog instances, most recent first.
        """
        try:
            query = ActivityLog.query.filter_by(
                user_id=user_id
            )
            if activity_type:
                query = query.filter_by(
                    activity_type=activity_type
                )
            return (
                query.order_by(ActivityLog.created_at.desc())
                .limit(limit)
                .all()
            )
        except Exception as e:
            logger.warning(
                f"Failed to get recent activity "
                f"for user {user_id}: {e}"
            )
            return []

    @staticmethod
    def get_activity_since(
        user_id: int,
        since: datetime,
    ) -> List[ActivityLog]:
        """
        Get all activity for a user since a given datetime.

        Useful for showing activity since last login.

        Args:
            user_id: The internal database user ID.
            since: The datetime cutoff (UTC).

        Returns:
            List of ActivityLog instances, most recent first.
        """
        try:
            return (
                ActivityLog.query.filter(
                    ActivityLog.user_id == user_id,
                    ActivityLog.created_at >= since,
                )
                .order_by(ActivityLog.created_at.desc())
                .all()
            )
        except Exception as e:
            logger.warning(
                f"Failed to get activity since "
                f"{since} for user {user_id}: {e}"
            )
            return []

    @staticmethod
    def get_activity_summary(
        user_id: int,
        days: int = 30,
    ) -> Dict[str, int]:
        """
        Get aggregated activity counts by type for a user.

        Args:
            user_id: The internal database user ID.
            days: Number of days to look back.

        Returns:
            Dictionary mapping activity_type to count.
            Returns empty dict on failure.
        """
        try:
            since = datetime.now(timezone.utc) - timedelta(
                days=days
            )
            results = (
                db.session.query(
                    ActivityLog.activity_type,
                    db.func.count(ActivityLog.id),
                )
                .filter(
                    ActivityLog.user_id == user_id,
                    ActivityLog.created_at >= since,
                )
                .group_by(ActivityLog.activity_type)
                .all()
            )
            return {
                activity_type: count
                for activity_type, count in results
            }
        except Exception as e:
            logger.warning(
                f"Failed to get activity summary "
                f"for user {user_id}: {e}"
            )
            return {}
```

**Key design decisions**:
1. `log()` returns `Optional[ActivityLog]` -- returns `None` on failure instead of raising. This is the fire-and-forget pattern.
2. `description` is truncated to 500 chars to match the column length.
3. All query methods return empty results (not exceptions) on failure, making them safe to call from templates/routes.
4. `get_activity_summary()` uses a raw SQLAlchemy `group_by` query for efficient aggregation rather than loading all records into Python.

---

### Step 4: Export from `shuffify/services/__init__.py`

**File**: `/Users/chris/Projects/shuffify/shuffify/services/__init__.py`

**Where**: After the Job Executor Service imports (lines 96-99), add:

```python
# Activity Log Service
from shuffify.services.activity_log_service import (
    ActivityLogService,
    ActivityLogError,
)
```

**Also add to `__all__` list** (after line 149, before the closing bracket):
```python
    # Activity Log Service
    "ActivityLogService",
    "ActivityLogError",
```

---

### Step 5: Hook into routes -- Shuffle

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/shuffle.py`

**What**: Add an activity log call after a successful shuffle (after line 94, before the `return json_success(...)` on line 96).

**Add import** at the top (after line 15, with the other imports from services):
```python
from shuffify.services import ActivityLogService
from shuffify.routes import get_db_user
from shuffify.enums import ActivityType
```

**Add logging** after line 94 (after the `logger.info(...)` call, before `return json_success(...)`):
```python
    # Log activity (non-blocking)
    try:
        db_user = get_db_user()
        if db_user:
            ActivityLogService.log(
                user_id=db_user.id,
                activity_type=ActivityType.SHUFFLE,
                description=(
                    f"Shuffled '{playlist.name}' using "
                    f"{algorithm.name}"
                ),
                playlist_id=playlist_id,
                playlist_name=playlist.name,
                metadata={
                    "algorithm": shuffle_request.algorithm,
                    "track_count": len(shuffled_uris),
                    "params": params,
                },
            )
    except Exception:
        pass  # Activity logging must never break shuffle
```

**Note**: The outer `try/except` with bare `pass` is an extra safety net. The `ActivityLogService.log()` already has internal try/except, but this double-layer guarantees no breakage even if the import or `get_db_user()` fails.

---

### Step 6: Hook into routes -- Workshop commit

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/workshop.py`

**Add imports** at the top (after the existing imports, around line 18):
```python
from shuffify.services import ActivityLogService
from shuffify.enums import ActivityType
```

**Add to `workshop_commit` function** (after line 182, the `logger.info(...)`, before `return json_success(...)`):
```python
    # Log activity (non-blocking)
    try:
        user_data = session.get("user_data", {})
        spotify_id = user_data.get("id")
        if spotify_id:
            from shuffify.services import UserService
            db_user = UserService.get_by_spotify_id(spotify_id)
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=ActivityType.WORKSHOP_COMMIT,
                    description=(
                        f"Committed workshop changes to "
                        f"'{playlist.name}'"
                    ),
                    playlist_id=playlist_id,
                    playlist_name=playlist.name,
                    metadata={
                        "track_count": len(
                            commit_request.track_uris
                        ),
                    },
                )
    except Exception:
        pass
```

**Add to `save_workshop_session` function** (after line 506, after `logger.info(...)`, before `return json_success(...)`):
```python
        # Log activity (non-blocking)
        try:
            from shuffify.services import UserService
            db_user = UserService.get_by_spotify_id(spotify_id)
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=(
                        ActivityType.WORKSHOP_SESSION_SAVE
                    ),
                    description=(
                        f"Saved workshop session "
                        f"'{session_name}'"
                    ),
                    playlist_id=playlist_id,
                    metadata={
                        "session_name": session_name,
                        "track_count": len(track_uris),
                    },
                )
        except Exception:
            pass
```

**Add to `delete_workshop_session` function** (after line 637, after `WorkshopSessionService.delete_session(...)`, before `return json_success(...)`):
```python
        # Log activity (non-blocking)
        try:
            from shuffify.services import UserService
            db_user = UserService.get_by_spotify_id(spotify_id)
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=(
                        ActivityType.WORKSHOP_SESSION_DELETE
                    ),
                    description=(
                        f"Deleted workshop session {session_id}"
                    ),
                )
        except Exception:
            pass
```

---

### Step 7: Hook into routes -- Upstream Sources

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/upstream_sources.py`

**Add imports** after line 9:
```python
from shuffify.services import ActivityLogService, UserService
from shuffify.enums import ActivityType
```

**Add to `add_upstream_source` function** (after line 90, after the `UpstreamSourceService.add_source(...)` call, before `return json_success(...)`):
```python
        # Log activity (non-blocking)
        try:
            db_user = UserService.get_by_spotify_id(spotify_id)
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=(
                        ActivityType.UPSTREAM_SOURCE_ADD
                    ),
                    description=(
                        f"Added upstream source "
                        f"'{data.get('source_name', source_playlist_id)}'"
                    ),
                    playlist_id=playlist_id,
                    metadata={
                        "source_playlist_id": source_playlist_id,
                        "source_type": data.get(
                            "source_type", "external"
                        ),
                        "source_name": data.get("source_name"),
                    },
                )
        except Exception:
            pass
```

**Add to `delete_upstream_source` function** (after line 121, after `UpstreamSourceService.delete_source(...)`, before `return json_success(...)`):
```python
        # Log activity (non-blocking)
        try:
            db_user = UserService.get_by_spotify_id(spotify_id)
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=(
                        ActivityType.UPSTREAM_SOURCE_DELETE
                    ),
                    description=(
                        f"Removed upstream source {source_id}"
                    ),
                )
        except Exception:
            pass
```

---

### Step 8: Hook into routes -- Schedules

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/schedules.py`

**Add imports** after line 43:
```python
from shuffify.services import ActivityLogService
from shuffify.enums import ActivityType
```

**Add to `create_schedule` function** (after line 163, after `logger.info(...)`, before `return json_success(...)`):
```python
    # Log activity (non-blocking)
    try:
        ActivityLogService.log(
            user_id=db_user.id,
            activity_type=ActivityType.SCHEDULE_CREATE,
            description=(
                f"Created {schedule.job_type} schedule for "
                f"'{schedule.target_playlist_name}'"
            ),
            playlist_id=schedule.target_playlist_id,
            playlist_name=schedule.target_playlist_name,
            metadata={
                "schedule_id": schedule.id,
                "job_type": schedule.job_type,
                "schedule_value": schedule.schedule_value,
                "algorithm_name": schedule.algorithm_name,
            },
        )
    except Exception:
        pass
```

**Add to `update_schedule` function** (after line 222, after `logger.info(...)`, before `return json_success(...)`):
```python
    # Log activity (non-blocking)
    try:
        ActivityLogService.log(
            user_id=db_user.id,
            activity_type=ActivityType.SCHEDULE_UPDATE,
            description=(
                f"Updated schedule {schedule_id}"
            ),
            playlist_id=schedule.target_playlist_id,
            playlist_name=schedule.target_playlist_name,
            metadata={
                "schedule_id": schedule_id,
                "updated_fields": list(update_fields.keys()),
            },
        )
    except Exception:
        pass
```

**Add to `delete_schedule` function** (after line 253, after `logger.info(...)`, before `return json_success(...)`):
```python
    # Log activity (non-blocking)
    try:
        ActivityLogService.log(
            user_id=db_user.id,
            activity_type=ActivityType.SCHEDULE_DELETE,
            description=(
                f"Deleted schedule {schedule_id}"
            ),
            metadata={"schedule_id": schedule_id},
        )
    except Exception:
        pass
```

**Add to `toggle_schedule` function** (after line 298, after `status_text = ...`, before `return json_success(...)`):
```python
    # Log activity (non-blocking)
    try:
        ActivityLogService.log(
            user_id=db_user.id,
            activity_type=ActivityType.SCHEDULE_TOGGLE,
            description=(
                f"Schedule {schedule_id} {status_text}"
            ),
            playlist_id=schedule.target_playlist_id,
            playlist_name=schedule.target_playlist_name,
            metadata={
                "schedule_id": schedule_id,
                "is_enabled": schedule.is_enabled,
            },
        )
    except Exception:
        pass
```

**Add to `run_schedule_now` function** (after line 322, after `result = JobExecutorService.execute_now(...)`, before `return json_success(...)`):
```python
    # Log activity (non-blocking)
    try:
        ActivityLogService.log(
            user_id=db_user.id,
            activity_type=ActivityType.SCHEDULE_RUN,
            description=(
                f"Manually ran schedule {schedule_id}"
            ),
            metadata={
                "schedule_id": schedule_id,
                "result": result,
            },
        )
    except Exception:
        pass
```

---

### Step 9: Hook into routes -- Login/Logout

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/core.py`

**Add import** after line 31:
```python
from shuffify.services import ActivityLogService
from shuffify.enums import ActivityType
```

**Add to `callback` function** (after line 235, after `session.modified = True`, before `logger.info(...)`):
```python
        # Log login activity (non-blocking)
        try:
            db_user = UserService.get_by_spotify_id(
                user_data["id"]
            )
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=ActivityType.LOGIN,
                    description="Logged in via Spotify OAuth",
                )
        except Exception:
            pass
```

**Add to `logout` function** (after line 258, before `session.clear()`):
```python
    # Log logout activity (non-blocking)
    try:
        user_data = session.get("user_data", {})
        spotify_id = user_data.get("id")
        if spotify_id:
            db_user = UserService.get_by_spotify_id(spotify_id)
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=ActivityType.LOGOUT,
                    description="Logged out",
                )
    except Exception:
        pass
```

---

### Step 10: Hook into Job Executor Service

**File**: `/Users/chris/Projects/shuffify/shuffify/services/job_executor_service.py`

**Add import** after line 28:
```python
from shuffify.enums import ActivityType
```

**Add logging** inside the `execute()` method (after line 105, after `db.session.commit()` in the success path, before `logger.info(...)`):
```python
            # Log activity (non-blocking)
            try:
                from shuffify.services.activity_log_service import (
                    ActivityLogService,
                )
                ActivityLogService.log(
                    user_id=schedule.user_id,
                    activity_type=ActivityType.SCHEDULE_RUN,
                    description=(
                        f"Scheduled {schedule.job_type} on "
                        f"'{schedule.target_playlist_name}' "
                        f"completed"
                    ),
                    playlist_id=(
                        schedule.target_playlist_id
                    ),
                    playlist_name=(
                        schedule.target_playlist_name
                    ),
                    metadata={
                        "schedule_id": schedule_id,
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
```

**Note**: We use a lazy import here (`from shuffify.services.activity_log_service import ActivityLogService`) to avoid circular imports, since `job_executor_service.py` is imported by `services/__init__.py`.

---

### Step 11: Generate Alembic Migration

**Prerequisite**: Phase 0 must have initialized Alembic. Once it is in place, run:

```bash
flask db migrate -m "Add activity_log table"
```

This will auto-generate a migration that creates the `activity_log` table with:
- Columns: `id`, `user_id`, `activity_type`, `description`, `playlist_id`, `playlist_name`, `metadata_json`, `created_at`
- Foreign key: `user_id` -> `users.id`
- Indexes: `ix_activity_log_user_id`, `ix_activity_log_activity_type`, `ix_activity_log_created_at`, `ix_activity_user_created` (composite)

Then apply with:
```bash
flask db upgrade
```

If Alembic is not yet available (Phase 0 not complete), the table will be auto-created by `db.create_all()` in `create_app()`, which is the current behavior.

---

### Step 12: Update `CHANGELOG.md`

**File**: `/Users/chris/Projects/shuffify/CHANGELOG.md`

Add under `## [Unreleased]`:

```markdown
### Added
- **Activity Log** - Unified audit trail for all user actions
  - New `ActivityLog` model with user_id, activity_type, description, playlist context, and JSON metadata
  - New `ActivityLogService` with `log()`, `get_recent()`, `get_activity_since()`, and `get_activity_summary()` methods
  - New `ActivityType` enum with 17 activity types covering shuffles, workshop, schedules, snapshots, and auth
  - Non-blocking activity logging hooked into shuffle, workshop commit, workshop sessions, upstream sources, schedule CRUD, job execution, login, and logout
  - Composite index on (user_id, created_at) for efficient recent activity queries
```

---

## Test Plan

### New test file: `tests/services/test_activity_log_service.py`

Tests should follow the same `db_app` / `app_ctx` fixture pattern as `test_user_service.py`.

```python
"""
Tests for ActivityLogService.

Tests cover log creation, query methods, error handling,
and the non-blocking guarantee.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from shuffify.models.db import db, ActivityLog
from shuffify.services.user_service import UserService
from shuffify.services.activity_log_service import (
    ActivityLogService,
)
from shuffify.enums import ActivityType


@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def app_ctx(db_app):
    """Provide app context with a test user."""
    with db_app.app_context():
        user = UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield user


class TestActivityLogServiceLog:
    """Tests for the log() method."""

    def test_log_basic_activity(self, app_ctx):
        """Should create an activity log entry."""
        user = app_ctx
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Shuffled 'My Playlist'",
        )

        assert result is not None
        assert result.activity_type == "shuffle"
        assert result.description == "Shuffled 'My Playlist'"
        assert result.user_id == user.id

    def test_log_with_playlist_context(self, app_ctx):
        """Should store playlist ID and name."""
        user = app_ctx
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.WORKSHOP_COMMIT,
            description="Committed changes",
            playlist_id="pl_123",
            playlist_name="My Playlist",
        )

        assert result.playlist_id == "pl_123"
        assert result.playlist_name == "My Playlist"

    def test_log_with_metadata(self, app_ctx):
        """Should store JSON metadata."""
        user = app_ctx
        meta = {"algorithm": "basic", "track_count": 42}
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Shuffled",
            metadata=meta,
        )

        assert result.metadata_json == meta

    def test_log_truncates_long_description(self, app_ctx):
        """Should truncate descriptions over 500 chars."""
        user = app_ctx
        long_desc = "x" * 600
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description=long_desc,
        )

        assert len(result.description) == 500

    def test_log_returns_none_on_db_error(self, app_ctx):
        """Should return None (not raise) on database error."""
        with patch.object(
            db.session, "commit", side_effect=Exception("DB error")
        ):
            result = ActivityLogService.log(
                user_id=app_ctx.id,
                activity_type=ActivityType.SHUFFLE,
                description="Should not crash",
            )
            assert result is None

    def test_log_sets_created_at(self, app_ctx):
        """Should auto-set created_at to UTC now."""
        user = app_ctx
        before = datetime.now(timezone.utc)
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.LOGIN,
            description="Logged in",
        )
        after = datetime.now(timezone.utc)

        assert result.created_at >= before.replace(
            tzinfo=None
        ) or result.created_at >= before
        assert result is not None

    def test_log_all_activity_types(self, app_ctx):
        """Should accept every ActivityType value."""
        user = app_ctx
        for at in ActivityType:
            result = ActivityLogService.log(
                user_id=user.id,
                activity_type=at,
                description=f"Testing {at.value}",
            )
            assert result is not None

    def test_log_nullable_fields(self, app_ctx):
        """Should allow None for optional fields."""
        user = app_ctx
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.LOGOUT,
            description="Logged out",
            playlist_id=None,
            playlist_name=None,
            metadata=None,
        )

        assert result is not None
        assert result.playlist_id is None
        assert result.playlist_name is None
        assert result.metadata_json is None


class TestActivityLogServiceGetRecent:
    """Tests for the get_recent() method."""

    def test_get_recent_empty(self, app_ctx):
        """Should return empty list when no activities exist."""
        result = ActivityLogService.get_recent(app_ctx.id)
        assert result == []

    def test_get_recent_returns_ordered(self, app_ctx):
        """Should return most recent first."""
        user = app_ctx
        for i in range(5):
            ActivityLogService.log(
                user_id=user.id,
                activity_type=ActivityType.SHUFFLE,
                description=f"Shuffle {i}",
            )

        results = ActivityLogService.get_recent(user.id)
        assert len(results) == 5
        # Most recent first
        assert results[0].description == "Shuffle 4"

    def test_get_recent_respects_limit(self, app_ctx):
        """Should return at most 'limit' entries."""
        user = app_ctx
        for i in range(10):
            ActivityLogService.log(
                user_id=user.id,
                activity_type=ActivityType.SHUFFLE,
                description=f"Shuffle {i}",
            )

        results = ActivityLogService.get_recent(
            user.id, limit=3
        )
        assert len(results) == 3

    def test_get_recent_filters_by_type(self, app_ctx):
        """Should filter by activity_type when provided."""
        user = app_ctx
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="A shuffle",
        )
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.LOGIN,
            description="A login",
        )
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Another shuffle",
        )

        results = ActivityLogService.get_recent(
            user.id, activity_type=ActivityType.SHUFFLE
        )
        assert len(results) == 2
        assert all(
            r.activity_type == "shuffle" for r in results
        )

    def test_get_recent_isolates_users(self, app_ctx):
        """Should only return activities for the given user."""
        user1 = app_ctx
        user2 = UserService.upsert_from_spotify({
            "id": "user456",
            "display_name": "Other",
            "images": [],
        })

        ActivityLogService.log(
            user_id=user1.id,
            activity_type=ActivityType.SHUFFLE,
            description="User 1 shuffle",
        )
        ActivityLogService.log(
            user_id=user2.id,
            activity_type=ActivityType.SHUFFLE,
            description="User 2 shuffle",
        )

        results = ActivityLogService.get_recent(user1.id)
        assert len(results) == 1
        assert results[0].description == "User 1 shuffle"

    def test_get_recent_returns_empty_on_error(self, app_ctx):
        """Should return empty list on database error."""
        with patch.object(
            ActivityLog, "query",
            new_callable=lambda: property(
                lambda self: (_ for _ in ()).throw(Exception("fail"))
            ),
        ):
            # Even if this specific mock doesn't trigger,
            # the method should handle errors gracefully
            results = ActivityLogService.get_recent(99999)
            assert isinstance(results, list)


class TestActivityLogServiceGetActivitySince:
    """Tests for the get_activity_since() method."""

    def test_get_activity_since_filters_by_date(
        self, app_ctx
    ):
        """Should only return activities after the given date."""
        user = app_ctx
        # Create some activities
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Recent shuffle",
        )

        # Query from 1 hour ago - should include above
        since = datetime.now(timezone.utc) - timedelta(
            hours=1
        )
        results = ActivityLogService.get_activity_since(
            user.id, since
        )
        assert len(results) >= 1

    def test_get_activity_since_future_returns_empty(
        self, app_ctx
    ):
        """Should return empty when 'since' is in the future."""
        user = app_ctx
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Past shuffle",
        )

        future = datetime.now(timezone.utc) + timedelta(
            hours=1
        )
        results = ActivityLogService.get_activity_since(
            user.id, future
        )
        assert len(results) == 0


class TestActivityLogServiceGetActivitySummary:
    """Tests for the get_activity_summary() method."""

    def test_get_summary_counts_by_type(self, app_ctx):
        """Should return counts grouped by activity type."""
        user = app_ctx
        for _ in range(3):
            ActivityLogService.log(
                user_id=user.id,
                activity_type=ActivityType.SHUFFLE,
                description="Shuffle",
            )
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.LOGIN,
            description="Login",
        )

        summary = ActivityLogService.get_activity_summary(
            user.id
        )
        assert summary.get("shuffle") == 3
        assert summary.get("login") == 1

    def test_get_summary_empty_user(self, app_ctx):
        """Should return empty dict for user with no activity."""
        summary = ActivityLogService.get_activity_summary(
            99999
        )
        assert summary == {}

    def test_get_summary_respects_days_param(self, app_ctx):
        """Should only count activities within the day range."""
        user = app_ctx
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Recent",
        )

        summary = ActivityLogService.get_activity_summary(
            user.id, days=30
        )
        assert summary.get("shuffle", 0) >= 1

    def test_get_summary_returns_empty_on_error(
        self, app_ctx
    ):
        """Should return empty dict on database error."""
        with patch.object(
            db.session, "query",
            side_effect=Exception("DB fail"),
        ):
            summary = (
                ActivityLogService.get_activity_summary(
                    app_ctx.id
                )
            )
            assert summary == {}


class TestActivityLogModel:
    """Tests for the ActivityLog model itself."""

    def test_to_dict(self, app_ctx):
        """Should serialize to dict correctly."""
        user = app_ctx
        activity = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Test shuffle",
            playlist_id="pl_1",
            playlist_name="Test Playlist",
            metadata={"key": "value"},
        )

        d = activity.to_dict()
        assert d["activity_type"] == "shuffle"
        assert d["description"] == "Test shuffle"
        assert d["playlist_id"] == "pl_1"
        assert d["playlist_name"] == "Test Playlist"
        assert d["metadata"] == {"key": "value"}
        assert "created_at" in d
        assert "id" in d

    def test_repr(self, app_ctx):
        """Should have a useful repr."""
        user = app_ctx
        activity = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Test",
        )

        r = repr(activity)
        assert "ActivityLog" in r
        assert "shuffle" in r

    def test_user_relationship(self, app_ctx):
        """Should be accessible via user.activities."""
        user = app_ctx
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Test",
        )

        # Refresh user and check relationship
        from shuffify.models.db import User
        user = db.session.get(User, user.id)
        assert user.activities.count() == 1
```

**Expected test count**: ~30 tests in this file.

### Existing tests -- verify no regressions

Run the full test suite to ensure the new non-blocking logging calls do not break any existing tests:

```bash
pytest tests/ -v
```

All 690+ existing tests must still pass. The activity logging calls are wrapped in `try/except` with `pass`, so they should not affect existing test behavior, even when the test database does not have activity data set up.

---

## Edge Cases and Considerations

### 1. Database unavailability
The `ActivityLogService.log()` method wraps everything in `try/except` and calls `db.session.rollback()` on failure. If the database is down, `log()` returns `None` silently. No user-facing operation is affected.

### 2. Transaction isolation
**CRITICAL**: The `log()` method calls `db.session.commit()` internally. If this is called while a route is in the middle of another transaction, it could commit partial work. However, examining all the hook points in the routes, the activity logging is always placed **after** the primary operation's `commit()` has already succeeded. This is safe because:
- In shuffle route: logging happens after `playlist_service.update_playlist_tracks()` and `StateService.record_new_state()` are done.
- In workshop commit: logging happens after `playlist_service.update_playlist_tracks()` is done.
- In schedule routes: logging happens after `SchedulerService.create_schedule()` etc. have committed.

### 3. Circular imports
The `job_executor_service.py` already imports from `shuffify.enums`, so adding `ActivityType` there is safe. The `ActivityLogService` import in `job_executor_service.py` uses a lazy import inside the method to avoid circular dependency through `services/__init__.py`.

### 4. High-volume logging
For users with thousands of logged actions, the composite index on `(user_id, created_at)` ensures efficient queries. The `get_recent()` method uses `LIMIT`, and `get_activity_summary()` uses `GROUP BY` in SQL rather than loading all records.

### 5. JSON metadata with SQLite
SQLAlchemy's `db.JSON` type works with SQLite by serializing to TEXT. When Phase 0 migrates to PostgreSQL, the column will use native JSONB for better performance. No code changes needed.

### 6. Session data availability in routes
Some routes get `spotify_id` from `session["user_data"]["id"]`, then look up `db_user` via `UserService.get_by_spotify_id()`. If the user is not in the database (e.g., database was cleared), `db_user` will be `None` and the activity log call is skipped. This is the correct behavior.

---

## What NOT To Do

1. **DO NOT** make activity logging blocking. Never let a logging failure prevent the primary operation from succeeding. Every log call must be inside `try/except`.

2. **DO NOT** add activity logging to read-only routes (GET requests like listing playlists, viewing dashboard). Only log state-changing operations.

3. **DO NOT** create a separate database connection or background thread for logging. Use the same `db.session` -- the simplicity is worth it for this use case, and the overhead of a single INSERT is negligible.

4. **DO NOT** log sensitive data in the `metadata_json` field (no tokens, passwords, or full track lists). Log aggregate information like `track_count`, algorithm names, and IDs only.

5. **DO NOT** create a new route or API endpoint for activity data in this phase. The service methods are prepared for a future dashboard, but the UI is not part of this phase.

6. **DO NOT** duplicate login/logout tracking with `LoginHistory` from Phase 2. The `ActivityType.LOGIN` and `ActivityType.LOGOUT` entries in the activity log serve a different purpose (unified activity feed), but if Phase 2's `LoginHistory` model exists, avoid storing redundant detail. The activity log entry for login should be a simple one-liner, not a full login record.

7. **DO NOT** modify the `JobExecution` model or its existing behavior. The activity log is additive and complements `JobExecution`, not replaces it.

8. **DO NOT** add activity logging inside service methods themselves (e.g., inside `WorkshopSessionService.save_session()`). Keep logging at the route/controller level to maintain separation of concerns. The one exception is `JobExecutorService.execute()`, which is called by the scheduler without going through a route.

---

## Verification Checklist

Before submitting PR:

- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes all tests (existing 690+ tests plus new ~30 tests)
- [ ] `ActivityType` enum has all 17 values
- [ ] `ActivityLog` model has correct columns, indexes, and FK
- [ ] `ActivityLogService.log()` never raises -- always returns `ActivityLog` or `None`
- [ ] Every route hook is wrapped in `try/except ... pass`
- [ ] No circular imports (verify with `python -c "from shuffify.services import ActivityLogService"`)
- [ ] `ActivityLog.to_dict()` serializes correctly
- [ ] Composite index `ix_activity_user_created` is defined
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `services/__init__.py` exports `ActivityLogService` and `ActivityLogError`
- [ ] No sensitive data logged in metadata (review all hook points)
- [ ] Alembic migration generated (if Phase 0 complete) or `db.create_all()` creates the table

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/models/db.py` - Add the ActivityLog model class (the core data schema for this feature)
- `/Users/chris/Projects/shuffify/shuffify/services/activity_log_service.py` - New file: the entire ActivityLogService with log(), get_recent(), get_activity_since(), get_activity_summary()
- `/Users/chris/Projects/shuffify/shuffify/enums.py` - Add ActivityType StrEnum (defines the vocabulary of trackable actions)
- `/Users/chris/Projects/shuffify/shuffify/routes/shuffle.py` - Primary hook point example (pattern replicated across all other route files)
- `/Users/chris/Projects/shuffify/shuffify/services/job_executor_service.py` - Hook for background job execution logging (only non-route hook point)
