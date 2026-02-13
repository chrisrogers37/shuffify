# Phase 4: Playlist Snapshots -- Persistent Pre-Mutation State Capture

## PR Title
`feat: Add PlaylistSnapshot model with auto/manual snapshot capture and restoration (#XX)`

## Risk Level
**Medium** -- Touches three existing mutation flows (shuffle, workshop commit, scheduled jobs) with auto-snapshot hooks. Each hook is a small insertion before the existing mutation call, and the feature degrades gracefully (snapshot failures do not block the original operation). However, the scheduled job executor runs outside a user session, which requires extra care.

## Effort
**Large** -- Approximately 2-3 days of focused implementation.
- New model + migration: ~2 hours
- New service: ~3 hours
- New snapshot routes (blueprint): ~3 hours
- Hook into 3 existing flows: ~2 hours
- Tests (~80-100 new tests): ~4-5 hours
- Pydantic schemas + docs: ~2 hours

## Files to Create or Modify

| Action | File | Purpose |
|--------|------|---------|
| CREATE | `shuffify/services/playlist_snapshot_service.py` | New service with CRUD + cleanup |
| CREATE | `shuffify/routes/snapshots.py` | New route module for snapshot management |
| CREATE | `tests/services/test_playlist_snapshot_service.py` | Service layer tests |
| CREATE | `tests/routes/test_snapshot_routes.py` | Route/endpoint tests |
| MODIFY | `shuffify/models/db.py` | Add `PlaylistSnapshot` model |
| MODIFY | `shuffify/services/__init__.py` | Export new service and exceptions |
| MODIFY | `shuffify/routes/__init__.py` | Import `snapshots` module |
| MODIFY | `shuffify/routes/shuffle.py` | Hook auto-snapshot before shuffle |
| MODIFY | `shuffify/routes/workshop.py` | Hook auto-snapshot before commit |
| MODIFY | `shuffify/services/job_executor_service.py` | Hook auto-snapshot before scheduled execution |
| MODIFY | `shuffify/enums.py` | Add `SnapshotType` enum |
| MODIFY | `shuffify/schemas/__init__.py` | Export new schema(s) |
| CREATE | `shuffify/schemas/snapshot_requests.py` | Pydantic schemas for snapshot endpoints |
| CREATE | `migrations/versions/xxxx_add_playlist_snapshots.py` | Alembic migration (generated) |

---

## Dependencies (from prior phases)

This phase depends on models introduced in Phases 0, 1, and 3:

1. **Phase 0**: PostgreSQL + Alembic migrations infrastructure must be operational. The `flask db migrate` and `flask db upgrade` commands must work. `Flask-Migrate` is already imported in `shuffify/__init__.py` (line 8).

2. **Phase 1**: Enhanced `User` model with the relationships used for FK lookups.

3. **Phase 3**: `UserSettings` model must exist with:
   - `auto_snapshot_enabled` (Boolean, default `True`)
   - `max_snapshots_per_playlist` (Integer, default `50`)

   The new `PlaylistSnapshotService` reads these settings to decide whether to auto-snapshot and how many to retain.

If Phase 3 is not yet merged, the auto-snapshot hooks can temporarily hard-code `auto_snapshot_enabled = True` and `max_snapshots_per_playlist = 50`, with a `# TODO: Read from UserSettings when Phase 3 lands` comment.

---

## Detailed Implementation

### Step 1: Add `SnapshotType` Enum

**File**: `/Users/chris/Projects/shuffify/shuffify/enums.py`

**After** the `IntervalValue` class (line 30), add:

```python
class SnapshotType(StrEnum):
    """Types of playlist snapshots."""
    AUTO_PRE_SHUFFLE = "auto_pre_shuffle"
    AUTO_PRE_RAID = "auto_pre_raid"
    AUTO_PRE_COMMIT = "auto_pre_commit"
    MANUAL = "manual"
    SCHEDULED_PRE_EXECUTION = "scheduled_pre_execution"
```

**Why an enum?** The existing codebase uses `StrEnum` for `JobType`, `ScheduleType`, and `IntervalValue` (lines 11-30 of `enums.py`). This keeps snapshot types consistent with that pattern and prevents typos in string comparisons.

---

### Step 2: Add `PlaylistSnapshot` Model

**File**: `/Users/chris/Projects/shuffify/shuffify/models/db.py`

**Location**: After the `JobExecution` class (after line 417), add the new model. Also add the import for the enum at the top.

**Add to imports** (line 14):
```python
from shuffify.enums import ScheduleType, IntervalValue, SnapshotType
```

**New model** (after `JobExecution`, approximately line 418):

```python
class PlaylistSnapshot(db.Model):
    """
    Point-in-time snapshot of a playlist's track ordering.

    Captured automatically before mutations (shuffle, raid, commit)
    or manually by the user. Enables restoring a playlist to any
    previous state, even across sessions.
    """

    __tablename__ = "playlist_snapshots"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    playlist_id = db.Column(db.String(255), nullable=False, index=True)
    playlist_name = db.Column(db.String(255), nullable=False)
    track_uris_json = db.Column(db.Text, nullable=False)
    track_count = db.Column(db.Integer, nullable=False, default=0)
    snapshot_type = db.Column(
        db.String(30),
        nullable=False,
        default=SnapshotType.MANUAL,
    )
    trigger_description = db.Column(db.String(500), nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = db.relationship(
        "User",
        backref=db.backref("playlist_snapshots", lazy="dynamic"),
    )

    # Composite index for efficient lookup: "all snapshots for this
    # user's playlist, ordered by recency"
    __table_args__ = (
        db.Index(
            "ix_snapshot_user_playlist_created",
            "user_id",
            "playlist_id",
            "created_at",
        ),
    )

    @property
    def track_uris(self) -> List[str]:
        """Deserialize the stored JSON into a list of URI strings."""
        if not self.track_uris_json:
            return []
        try:
            return json.loads(self.track_uris_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                f"Failed to decode track_uris_json for "
                f"PlaylistSnapshot {self.id}"
            )
            return []

    @track_uris.setter
    def track_uris(self, uris: List[str]) -> None:
        """Serialize a list of URI strings to JSON for storage."""
        self.track_uris_json = json.dumps(uris)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the PlaylistSnapshot to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "playlist_id": self.playlist_id,
            "playlist_name": self.playlist_name,
            "track_uris": self.track_uris,
            "track_count": self.track_count,
            "snapshot_type": self.snapshot_type,
            "trigger_description": self.trigger_description,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<PlaylistSnapshot {self.id}: {self.snapshot_type} "
            f"for playlist {self.playlist_id} "
            f"({self.track_count} tracks)>"
        )
```

**Also update the User model's docstring** (line 22-28) to mention the new relationship:
```python
"""
Spotify user record.

Created or updated on each OAuth login via the upsert pattern.
Links to all user-specific data (workshop sessions, upstream sources,
playlist snapshots).
"""
```

**Pattern followed**: The `track_uris` property/setter pattern matches exactly what `WorkshopSession` uses (lines 130-147 of `db.py`). The `to_dict()` and `__repr__` patterns match all existing models.

---

### Step 3: Generate Alembic Migration

After making the model change, generate the migration:

```bash
flask db migrate -m "Add playlist_snapshots table"
flask db upgrade
```

This will auto-generate a migration in `migrations/versions/` that creates the `playlist_snapshots` table with all columns and indices.

**Verify the generated migration includes:**
- `playlist_snapshots` table creation
- Columns: `id`, `user_id`, `playlist_id`, `playlist_name`, `track_uris_json`, `track_count`, `snapshot_type`, `trigger_description`, `created_at`
- Foreign key: `user_id` -> `users.id`
- Individual indices: `ix_playlist_snapshots_user_id`, `ix_playlist_snapshots_playlist_id`
- Composite index: `ix_snapshot_user_playlist_created`

---

### Step 4: Create `PlaylistSnapshotService`

**File**: `/Users/chris/Projects/shuffify/shuffify/services/playlist_snapshot_service.py`

This service follows the exact same CRUD pattern as `WorkshopSessionService` (in `/Users/chris/Projects/shuffify/shuffify/services/workshop_session_service.py`), with the addition of cleanup/retention logic.

```python
"""
Playlist snapshot service for capturing and restoring playlist states.

Handles CRUD operations for PlaylistSnapshot records, enabling users
to capture point-in-time snapshots of playlist track orderings and
restore them later.
"""

import logging
from typing import List, Optional

from shuffify.models.db import db, PlaylistSnapshot, User
from shuffify.enums import SnapshotType

logger = logging.getLogger(__name__)

# Default max snapshots if UserSettings is unavailable
DEFAULT_MAX_SNAPSHOTS_PER_PLAYLIST = 50


class PlaylistSnapshotError(Exception):
    """Base exception for playlist snapshot operations."""

    pass


class PlaylistSnapshotNotFoundError(PlaylistSnapshotError):
    """Raised when a snapshot cannot be found."""

    pass


class PlaylistSnapshotService:
    """Service for managing playlist snapshots."""

    @staticmethod
    def create_snapshot(
        user_id: int,
        playlist_id: str,
        playlist_name: str,
        track_uris: List[str],
        snapshot_type: str,
        trigger_description: Optional[str] = None,
    ) -> PlaylistSnapshot:
        """
        Create a new playlist snapshot.

        After creation, enforces the max_snapshots_per_playlist limit
        by deleting the oldest snapshots beyond the cap.

        Args:
            user_id: The internal database user ID.
            playlist_id: The Spotify playlist ID.
            playlist_name: Human-readable playlist name at time of snapshot.
            track_uris: Ordered list of track URIs.
            snapshot_type: One of the SnapshotType enum values.
            trigger_description: Optional description of what triggered
                the snapshot (e.g., "Before BasicShuffle").

        Returns:
            The created PlaylistSnapshot instance.

        Raises:
            PlaylistSnapshotError: If creation fails.
        """
        try:
            snapshot = PlaylistSnapshot(
                user_id=user_id,
                playlist_id=playlist_id,
                playlist_name=playlist_name,
                track_count=len(track_uris),
                snapshot_type=snapshot_type,
                trigger_description=trigger_description,
            )
            snapshot.track_uris = track_uris

            db.session.add(snapshot)
            db.session.commit()

            logger.info(
                f"Created {snapshot_type} snapshot for user "
                f"{user_id}, playlist {playlist_id} "
                f"({len(track_uris)} tracks)"
            )

            # Enforce retention limit
            max_snapshots = (
                PlaylistSnapshotService._get_max_snapshots(
                    user_id
                )
            )
            PlaylistSnapshotService.cleanup_old_snapshots(
                user_id, playlist_id, max_snapshots
            )

            return snapshot

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to create snapshot: {e}",
                exc_info=True,
            )
            raise PlaylistSnapshotError(
                f"Failed to create snapshot: {e}"
            )

    @staticmethod
    def get_snapshots(
        user_id: int,
        playlist_id: str,
        limit: int = 20,
    ) -> List[PlaylistSnapshot]:
        """
        Get snapshots for a playlist, newest first.

        Args:
            user_id: The internal database user ID.
            playlist_id: The Spotify playlist ID.
            limit: Maximum number of snapshots to return.

        Returns:
            List of PlaylistSnapshot instances, most recent first.
        """
        return (
            PlaylistSnapshot.query.filter_by(
                user_id=user_id, playlist_id=playlist_id
            )
            .order_by(PlaylistSnapshot.created_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_snapshot(
        snapshot_id: int, user_id: int
    ) -> PlaylistSnapshot:
        """
        Get a specific snapshot by ID with ownership check.

        Args:
            snapshot_id: The snapshot database ID.
            user_id: The internal database user ID (for ownership).

        Returns:
            PlaylistSnapshot instance.

        Raises:
            PlaylistSnapshotNotFoundError: If not found or not owned.
        """
        snapshot = db.session.get(PlaylistSnapshot, snapshot_id)
        if not snapshot or snapshot.user_id != user_id:
            raise PlaylistSnapshotNotFoundError(
                f"Snapshot {snapshot_id} not found"
            )
        return snapshot

    @staticmethod
    def restore_snapshot(
        snapshot_id: int, user_id: int
    ) -> List[str]:
        """
        Get the track URIs from a snapshot for restoration.

        The caller (route or service) is responsible for applying
        the URIs to the Spotify playlist via the Spotify API.

        Args:
            snapshot_id: The snapshot database ID.
            user_id: The internal database user ID (for ownership).

        Returns:
            List of track URIs in the snapshot's order.

        Raises:
            PlaylistSnapshotNotFoundError: If not found or not owned.
        """
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user_id
        )
        logger.info(
            f"Restoring snapshot {snapshot_id} for user "
            f"{user_id}, playlist {snapshot.playlist_id} "
            f"({snapshot.track_count} tracks)"
        )
        return snapshot.track_uris

    @staticmethod
    def delete_snapshot(
        snapshot_id: int, user_id: int
    ) -> bool:
        """
        Delete a snapshot with ownership check.

        Args:
            snapshot_id: The snapshot database ID.
            user_id: The internal database user ID (for ownership).

        Returns:
            True if deleted successfully.

        Raises:
            PlaylistSnapshotNotFoundError: If not found or not owned.
            PlaylistSnapshotError: If deletion fails.
        """
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user_id
        )

        try:
            db.session.delete(snapshot)
            db.session.commit()
            logger.info(
                f"Deleted snapshot {snapshot_id}"
            )
            return True

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to delete snapshot "
                f"{snapshot_id}: {e}",
                exc_info=True,
            )
            raise PlaylistSnapshotError(
                f"Failed to delete snapshot: {e}"
            )

    @staticmethod
    def cleanup_old_snapshots(
        user_id: int,
        playlist_id: str,
        max_count: int,
    ) -> int:
        """
        Enforce retention limit by deleting oldest snapshots.

        Keeps the most recent `max_count` snapshots and deletes
        any older ones.

        Args:
            user_id: The internal database user ID.
            playlist_id: The Spotify playlist ID.
            max_count: Maximum number of snapshots to retain.

        Returns:
            Number of snapshots deleted.
        """
        all_snapshots = (
            PlaylistSnapshot.query.filter_by(
                user_id=user_id, playlist_id=playlist_id
            )
            .order_by(PlaylistSnapshot.created_at.desc())
            .all()
        )

        if len(all_snapshots) <= max_count:
            return 0

        to_delete = all_snapshots[max_count:]
        deleted_count = 0

        try:
            for snapshot in to_delete:
                db.session.delete(snapshot)
                deleted_count += 1
            db.session.commit()

            logger.info(
                f"Cleaned up {deleted_count} old snapshots "
                f"for user {user_id}, playlist {playlist_id}"
            )
            return deleted_count

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to cleanup snapshots: {e}",
                exc_info=True,
            )
            return 0

    @staticmethod
    def _get_max_snapshots(user_id: int) -> int:
        """
        Get the max snapshots setting for a user.

        Reads from UserSettings if available (Phase 3),
        otherwise returns the default.

        Args:
            user_id: The internal database user ID.

        Returns:
            Maximum number of snapshots per playlist.
        """
        try:
            from shuffify.models.db import UserSettings

            settings = UserSettings.query.filter_by(
                user_id=user_id
            ).first()
            if settings and settings.max_snapshots_per_playlist:
                return settings.max_snapshots_per_playlist
        except (ImportError, Exception):
            pass

        return DEFAULT_MAX_SNAPSHOTS_PER_PLAYLIST

    @staticmethod
    def is_auto_snapshot_enabled(user_id: int) -> bool:
        """
        Check if auto-snapshot is enabled for a user.

        Reads from UserSettings if available (Phase 3),
        otherwise defaults to True.

        Args:
            user_id: The internal database user ID.

        Returns:
            True if auto-snapshots are enabled.
        """
        try:
            from shuffify.models.db import UserSettings

            settings = UserSettings.query.filter_by(
                user_id=user_id
            ).first()
            if settings is not None:
                return settings.auto_snapshot_enabled
        except (ImportError, Exception):
            pass

        return True
```

**Key design decisions**:

1. **`user_id` is the internal integer ID**, not the Spotify string ID. This matches the FK relationship and avoids an extra lookup per call. The calling code (routes) resolves the `user_id` once from `get_db_user()`.

2. **`_get_max_snapshots()` and `is_auto_snapshot_enabled()`** use a `try/except ImportError` pattern so they degrade gracefully if `UserSettings` doesn't exist yet (Phase 3 not merged). This keeps Phase 4 independently deployable.

3. **Cleanup runs after every `create_snapshot()`** to ensure the cap is always enforced. The cleanup is a simple query-and-delete, not a trigger or constraint.

4. **`restore_snapshot()` returns URIs only** -- it does not call the Spotify API. The route layer is responsible for applying the restoration, consistent with how `StateService.undo()` works (returns URIs, caller applies them).

---

### Step 5: Register Service in `__init__.py`

**File**: `/Users/chris/Projects/shuffify/shuffify/services/__init__.py`

**Add after the Job Executor Service block** (after line 99):

```python
# Playlist Snapshot Service
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
    PlaylistSnapshotError,
    PlaylistSnapshotNotFoundError,
)
```

**Add to `__all__`** list (after line 149):

```python
    # Playlist Snapshot Service
    "PlaylistSnapshotService",
    "PlaylistSnapshotError",
    "PlaylistSnapshotNotFoundError",
```

---

### Step 6: Create Pydantic Schemas for Snapshot Requests

**File**: `/Users/chris/Projects/shuffify/shuffify/schemas/snapshot_requests.py`

```python
"""
Pydantic schemas for playlist snapshot request validation.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class ManualSnapshotRequest(BaseModel):
    """Schema for creating a manual snapshot."""

    playlist_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable playlist name",
    )
    track_uris: List[str] = Field(
        ...,
        min_length=0,
        description="Ordered list of track URIs to snapshot",
    )
    trigger_description: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Optional description of why this snapshot was created",
    )

    @field_validator("track_uris")
    @classmethod
    def validate_track_uris(cls, v: List[str]) -> List[str]:
        """Ensure all URIs look like Spotify track URIs."""
        for uri in v:
            if not uri.startswith("spotify:track:"):
                raise ValueError(
                    f"Invalid track URI format: {uri}"
                )
        return v

    class Config:
        extra = "ignore"
```

**Update**: `/Users/chris/Projects/shuffify/shuffify/schemas/__init__.py`

Add to imports:

```python
from .snapshot_requests import (
    ManualSnapshotRequest,
)
```

Add to `__all__`:

```python
    # Snapshot schemas
    "ManualSnapshotRequest",
```

---

### Step 7: Create Snapshot Routes

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/snapshots.py`

```python
"""
Snapshot routes: list, create, view, restore, and delete playlist snapshots.
"""

import logging

from flask import session, request, jsonify
from pydantic import ValidationError

from shuffify.routes import (
    main,
    require_auth,
    json_error,
    json_success,
    get_db_user,
)
from shuffify.services import (
    PlaylistService,
    PlaylistSnapshotService,
    PlaylistSnapshotError,
    PlaylistSnapshotNotFoundError,
    StateService,
)
from shuffify import is_db_available
from shuffify.schemas import ManualSnapshotRequest
from shuffify.enums import SnapshotType

logger = logging.getLogger(__name__)


@main.route(
    "/playlist/<playlist_id>/snapshots", methods=["GET"]
)
def list_snapshots(playlist_id):
    """List all snapshots for a playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. "
            "Cannot load snapshots.",
            503,
        )

    user = get_db_user()
    if not user:
        return json_error(
            "User data not found in session.", 401
        )

    limit = request.args.get("limit", 20, type=int)
    limit = max(1, min(limit, 100))

    snapshots = PlaylistSnapshotService.get_snapshots(
        user.id, playlist_id, limit=limit
    )
    return jsonify({
        "success": True,
        "snapshots": [s.to_dict() for s in snapshots],
    })


@main.route(
    "/playlist/<playlist_id>/snapshots", methods=["POST"]
)
def create_manual_snapshot(playlist_id):
    """Create a manual snapshot of a playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable. "
            "Cannot create snapshot.",
            503,
        )

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    try:
        snap_request = ManualSnapshotRequest(**data)
    except ValidationError as e:
        return json_error(
            f"Invalid request: {e.error_count()} "
            f"validation error(s).",
            400,
        )

    user = get_db_user()
    if not user:
        return json_error(
            "User data not found in session.", 401
        )

    try:
        snapshot = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id=playlist_id,
            playlist_name=snap_request.playlist_name,
            track_uris=snap_request.track_uris,
            snapshot_type=SnapshotType.MANUAL,
            trigger_description=snap_request.trigger_description,
        )
        logger.info(
            f"User {user.spotify_id} created manual snapshot "
            f"for playlist {playlist_id}"
        )
        return json_success(
            "Snapshot created.",
            snapshot=snapshot.to_dict(),
        )
    except PlaylistSnapshotError as e:
        return json_error(str(e), 500)


@main.route(
    "/snapshots/<int:snapshot_id>", methods=["GET"]
)
def view_snapshot(snapshot_id):
    """View a snapshot's details."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable.", 503
        )

    user = get_db_user()
    if not user:
        return json_error(
            "User data not found in session.", 401
        )

    try:
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user.id
        )
        return jsonify({
            "success": True,
            "snapshot": snapshot.to_dict(),
        })
    except PlaylistSnapshotNotFoundError:
        return json_error("Snapshot not found.", 404)


@main.route(
    "/snapshots/<int:snapshot_id>/restore",
    methods=["POST"],
)
def restore_snapshot(snapshot_id):
    """Restore a playlist from a snapshot."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable.", 503
        )

    user = get_db_user()
    if not user:
        return json_error(
            "User data not found in session.", 401
        )

    try:
        # Get the snapshot's track URIs
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user.id
        )
        restore_uris = snapshot.track_uris
        playlist_id = snapshot.playlist_id

        if not restore_uris:
            return json_error(
                "Snapshot contains no tracks.", 400
            )

        # Auto-snapshot the CURRENT state before restoring
        # (so the user can undo the restore)
        if PlaylistSnapshotService.is_auto_snapshot_enabled(
            user.id
        ):
            try:
                playlist_service = PlaylistService(client)
                playlist = playlist_service.get_playlist(
                    playlist_id, include_features=False
                )
                current_uris = [
                    t["uri"] for t in playlist.tracks
                ]
                PlaylistSnapshotService.create_snapshot(
                    user_id=user.id,
                    playlist_id=playlist_id,
                    playlist_name=playlist.name,
                    track_uris=current_uris,
                    snapshot_type=SnapshotType.AUTO_PRE_COMMIT,
                    trigger_description=(
                        f"Before restoring snapshot {snapshot_id}"
                    ),
                )
            except Exception as e:
                logger.warning(
                    f"Failed to auto-snapshot before restore: {e}"
                )

        # Apply restoration to Spotify
        playlist_service = PlaylistService(client)
        playlist_service.update_playlist_tracks(
            playlist_id, restore_uris
        )

        # Update session state if it exists
        StateService.ensure_playlist_initialized(
            session, playlist_id, restore_uris
        )
        StateService.record_new_state(
            session, playlist_id, restore_uris
        )

        logger.info(
            f"Restored snapshot {snapshot_id} for playlist "
            f"{playlist_id}"
        )

        return json_success(
            f"Playlist restored from snapshot "
            f"({snapshot.track_count} tracks).",
            playlist_id=playlist_id,
            snapshot=snapshot.to_dict(),
        )

    except PlaylistSnapshotNotFoundError:
        return json_error("Snapshot not found.", 404)
    except Exception as e:
        logger.error(
            f"Failed to restore snapshot {snapshot_id}: {e}",
            exc_info=True,
        )
        return json_error(
            "Failed to restore snapshot. Please try again.",
            500,
        )


@main.route(
    "/snapshots/<int:snapshot_id>", methods=["DELETE"]
)
def delete_snapshot(snapshot_id):
    """Delete a snapshot."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error(
            "Database is unavailable.", 503
        )

    user = get_db_user()
    if not user:
        return json_error(
            "User data not found in session.", 401
        )

    try:
        PlaylistSnapshotService.delete_snapshot(
            snapshot_id, user.id
        )
        return json_success("Snapshot deleted.")
    except PlaylistSnapshotNotFoundError:
        return json_error("Snapshot not found.", 404)
    except PlaylistSnapshotError as e:
        return json_error(str(e), 500)
```

---

### Step 8: Register Snapshot Routes in Blueprint

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/__init__.py`

**At the bottom** (line 128), add `snapshots` to the import list:

```python
# BEFORE (line 121-128):
from shuffify.routes import (  # noqa: E402, F401
    core,
    playlists,
    shuffle,
    workshop,
    upstream_sources,
    schedules,
)

# AFTER:
from shuffify.routes import (  # noqa: E402, F401
    core,
    playlists,
    shuffle,
    workshop,
    upstream_sources,
    schedules,
    snapshots,
)
```

---

### Step 9: Hook Auto-Snapshot into Shuffle Route

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/shuffle.py`

**What changes**: Before the shuffle is applied to Spotify, capture a snapshot of the current track order.

**Current code** (lines 22-100 of `shuffle.py`, focusing on the `shuffle` function):

Add imports at the top (after line 10):

```python
from shuffify.services import (
    PlaylistService,
    ShuffleService,
    StateService,
    PlaylistUpdateError,
    PlaylistSnapshotService,
)
from shuffify.enums import SnapshotType
from shuffify import is_db_available
from shuffify.routes import get_db_user
```

**Insert auto-snapshot logic** after line 44 (`current_uris = [track["uri"] for track in playlist.tracks]`) and before line 46 (`StateService.ensure_playlist_initialized(...)`):

```python
    current_uris = [track["uri"] for track in playlist.tracks]

    # --- Auto-snapshot before shuffle ---
    if is_db_available():
        db_user = get_db_user()
        if db_user and PlaylistSnapshotService.is_auto_snapshot_enabled(
            db_user.id
        ):
            try:
                PlaylistSnapshotService.create_snapshot(
                    user_id=db_user.id,
                    playlist_id=playlist_id,
                    playlist_name=playlist.name,
                    track_uris=current_uris,
                    snapshot_type=SnapshotType.AUTO_PRE_SHUFFLE,
                    trigger_description=(
                        f"Before {shuffle_request.algorithm}"
                    ),
                )
            except Exception as e:
                logger.warning(
                    f"Auto-snapshot before shuffle failed: {e}"
                )
    # --- End auto-snapshot ---

    StateService.ensure_playlist_initialized(
        session, playlist_id, current_uris
    )
```

**Critical**: The auto-snapshot is wrapped in a `try/except` so that a snapshot failure NEVER blocks the shuffle operation. The `is_db_available()` check ensures we don't attempt database operations when the DB is down.

---

### Step 10: Hook Auto-Snapshot into Workshop Commit Route

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/workshop.py`

**Add imports** (add to existing import block around line 19-31):

```python
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    StateService,
    WorkshopSessionService,
    WorkshopSessionError,
    WorkshopSessionNotFoundError,
    WorkshopSessionLimitError,
    AuthenticationError,
    PlaylistError,
    PlaylistSnapshotService,
)
from shuffify.enums import SnapshotType
```

**Insert auto-snapshot** in the `workshop_commit` function. The current code at lines 156-162 fetches `current_uris` and initializes state. Insert the snapshot **after** `current_uris` is computed (line 158) and **before** the order-change check (line 164):

```python
    current_uris = [
        track["uri"] for track in playlist.tracks
    ]

    # --- Auto-snapshot before commit ---
    if is_db_available():
        db_user = get_db_user()
        if db_user and PlaylistSnapshotService.is_auto_snapshot_enabled(
            db_user.id
        ):
            try:
                PlaylistSnapshotService.create_snapshot(
                    user_id=db_user.id,
                    playlist_id=playlist_id,
                    playlist_name=playlist.name,
                    track_uris=current_uris,
                    snapshot_type=SnapshotType.AUTO_PRE_COMMIT,
                    trigger_description="Before workshop commit",
                )
            except Exception as e:
                logger.warning(
                    f"Auto-snapshot before commit failed: {e}"
                )
    # --- End auto-snapshot ---

    StateService.ensure_playlist_initialized(
        session, playlist_id, current_uris
    )
```

Note: The `get_db_user` helper is already available from the `shuffify.routes` package (defined in `__init__.py` line 102-113). The `is_db_available` is already imported at line 31.

---

### Step 11: Hook Auto-Snapshot into Job Executor Service

**File**: `/Users/chris/Projects/shuffify/shuffify/services/job_executor_service.py`

This is the most nuanced hook because the job executor runs in a background context (no Flask session), so it uses `user_id` from the `Schedule` model directly.

**Add imports** (after line 27):

```python
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
)
from shuffify.enums import SnapshotType
```

**Modify `_execute_shuffle`** (lines 384-457). Insert auto-snapshot **after** `raw_tracks` is fetched (line 399) and **before** the algorithm runs (line 422):

```python
    @staticmethod
    def _execute_shuffle(
        schedule: Schedule, api: SpotifyAPI
    ) -> dict:
        """Run a shuffle algorithm on the target playlist."""
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

            # --- Auto-snapshot before scheduled shuffle ---
            try:
                pre_shuffle_uris = [
                    t["uri"] for t in raw_tracks
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
                        playlist_id=target_id,
                        playlist_name=(
                            schedule.target_playlist_name
                            or target_id
                        ),
                        track_uris=pre_shuffle_uris,
                        snapshot_type=(
                            SnapshotType
                            .SCHEDULED_PRE_EXECUTION
                        ),
                        trigger_description=(
                            f"Before scheduled {algorithm_name}"
                        ),
                    )
            except Exception as snap_err:
                logger.warning(
                    f"Auto-snapshot before scheduled shuffle "
                    f"failed: {snap_err}"
                )
            # --- End auto-snapshot ---

            tracks = []
            for t in raw_tracks:
                # ... existing track mapping code ...
```

**Modify `_execute_raid`** (lines 294-382). Insert auto-snapshot **after** `target_tracks` is fetched and **before** new tracks are added:

```python
        try:
            target_tracks = api.get_playlist_tracks(target_id)
            target_uris = {
                t.get("uri")
                for t in target_tracks
                if t.get("uri")
            }

            # --- Auto-snapshot before scheduled raid ---
            try:
                pre_raid_uris = [
                    t.get("uri") for t in target_tracks
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
                        playlist_id=target_id,
                        playlist_name=(
                            schedule.target_playlist_name
                            or target_id
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
                    f"Auto-snapshot before scheduled raid "
                    f"failed: {snap_err}"
                )
            # --- End auto-snapshot ---

            new_uris: List[str] = []
            # ... existing raid logic ...
```

---

### Step 12: Write Tests

#### A. Service Tests

**File**: `/Users/chris/Projects/shuffify/tests/services/test_playlist_snapshot_service.py`

Follow the pattern from `/Users/chris/Projects/shuffify/tests/services/test_workshop_session_service.py`.

```python
"""
Tests for PlaylistSnapshotService.

Tests cover create, list, get, restore, delete, cleanup,
and auto-snapshot settings integration.
"""

import pytest

from shuffify.models.db import db
from shuffify.services.user_service import UserService
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
    PlaylistSnapshotError,
    PlaylistSnapshotNotFoundError,
    DEFAULT_MAX_SNAPSHOTS_PER_PLAYLIST,
)
from shuffify.enums import SnapshotType


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


class TestPlaylistSnapshotServiceCreate:
    """Tests for create_snapshot."""

    def test_create_snapshot(self, app_ctx):
        user = app_ctx
        uris = ["spotify:track:a", "spotify:track:b"]
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="playlist1",
            playlist_name="My Playlist",
            track_uris=uris,
            snapshot_type=SnapshotType.MANUAL,
        )

        assert snap.id is not None
        assert snap.track_uris == uris
        assert snap.track_count == 2
        assert snap.snapshot_type == SnapshotType.MANUAL
        assert snap.playlist_name == "My Playlist"

    def test_create_snapshot_auto_type(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Test",
            track_uris=["spotify:track:x"],
            snapshot_type=SnapshotType.AUTO_PRE_SHUFFLE,
            trigger_description="Before BasicShuffle",
        )
        assert snap.snapshot_type == SnapshotType.AUTO_PRE_SHUFFLE
        assert snap.trigger_description == "Before BasicShuffle"

    def test_create_snapshot_empty_uris(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user_id=user.id,
            playlist_id="p1",
            playlist_name="Empty",
            track_uris=[],
            snapshot_type=SnapshotType.MANUAL,
        )
        assert snap.track_count == 0
        assert snap.track_uris == []

    def test_create_snapshot_enforces_retention(self, app_ctx):
        """Should delete oldest snapshots beyond the max."""
        user = app_ctx
        # Create max+2 snapshots
        max_count = DEFAULT_MAX_SNAPSHOTS_PER_PLAYLIST
        for i in range(max_count + 2):
            PlaylistSnapshotService.create_snapshot(
                user_id=user.id,
                playlist_id="p1",
                playlist_name="Test",
                track_uris=[f"spotify:track:{i}"],
                snapshot_type=SnapshotType.MANUAL,
            )

        # Should have exactly max_count
        snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p1", limit=200
        )
        assert len(snaps) == max_count


class TestPlaylistSnapshotServiceGet:
    """Tests for get_snapshots and get_snapshot."""

    def test_get_snapshots_returns_newest_first(self, app_ctx):
        user = app_ctx
        PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "First", ["spotify:track:a"],
            SnapshotType.MANUAL,
        )
        PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "Second", ["spotify:track:b"],
            SnapshotType.MANUAL,
        )

        snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p1"
        )
        assert len(snaps) == 2
        assert snaps[0].playlist_name == "Second"

    def test_get_snapshots_filters_by_playlist(self, app_ctx):
        user = app_ctx
        PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "A", ["spotify:track:a"],
            SnapshotType.MANUAL,
        )
        PlaylistSnapshotService.create_snapshot(
            user.id, "p2", "B", ["spotify:track:b"],
            SnapshotType.MANUAL,
        )

        snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p1"
        )
        assert len(snaps) == 1

    def test_get_snapshots_respects_limit(self, app_ctx):
        user = app_ctx
        for i in range(5):
            PlaylistSnapshotService.create_snapshot(
                user.id, "p1", f"S{i}",
                [f"spotify:track:{i}"],
                SnapshotType.MANUAL,
            )

        snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p1", limit=3
        )
        assert len(snaps) == 3

    def test_get_snapshot_by_id(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "Test", ["spotify:track:a"],
            SnapshotType.MANUAL,
        )

        result = PlaylistSnapshotService.get_snapshot(
            snap.id, user.id
        )
        assert result.id == snap.id

    def test_get_snapshot_wrong_user_raises(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "Test", ["spotify:track:a"],
            SnapshotType.MANUAL,
        )

        other_user = UserService.upsert_from_spotify({
            "id": "other_user",
            "display_name": "Other",
            "images": [],
        })

        with pytest.raises(PlaylistSnapshotNotFoundError):
            PlaylistSnapshotService.get_snapshot(
                snap.id, other_user.id
            )

    def test_get_snapshot_nonexistent_raises(self, app_ctx):
        user = app_ctx
        with pytest.raises(PlaylistSnapshotNotFoundError):
            PlaylistSnapshotService.get_snapshot(
                99999, user.id
            )


class TestPlaylistSnapshotServiceRestore:
    """Tests for restore_snapshot."""

    def test_restore_returns_track_uris(self, app_ctx):
        user = app_ctx
        uris = ["spotify:track:a", "spotify:track:b", "spotify:track:c"]
        snap = PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "Test", uris,
            SnapshotType.MANUAL,
        )

        result = PlaylistSnapshotService.restore_snapshot(
            snap.id, user.id
        )
        assert result == uris

    def test_restore_wrong_user_raises(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "Test", ["spotify:track:a"],
            SnapshotType.MANUAL,
        )

        other_user = UserService.upsert_from_spotify({
            "id": "other",
            "display_name": "Other",
            "images": [],
        })

        with pytest.raises(PlaylistSnapshotNotFoundError):
            PlaylistSnapshotService.restore_snapshot(
                snap.id, other_user.id
            )


class TestPlaylistSnapshotServiceDelete:
    """Tests for delete_snapshot."""

    def test_delete_snapshot(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "Test", ["spotify:track:a"],
            SnapshotType.MANUAL,
        )
        snap_id = snap.id

        result = PlaylistSnapshotService.delete_snapshot(
            snap_id, user.id
        )
        assert result is True

        with pytest.raises(PlaylistSnapshotNotFoundError):
            PlaylistSnapshotService.get_snapshot(
                snap_id, user.id
            )

    def test_delete_wrong_user_raises(self, app_ctx):
        user = app_ctx
        snap = PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "Test", ["spotify:track:a"],
            SnapshotType.MANUAL,
        )

        other_user = UserService.upsert_from_spotify({
            "id": "other",
            "display_name": "Other",
            "images": [],
        })

        with pytest.raises(PlaylistSnapshotNotFoundError):
            PlaylistSnapshotService.delete_snapshot(
                snap.id, other_user.id
            )


class TestPlaylistSnapshotServiceCleanup:
    """Tests for cleanup_old_snapshots."""

    def test_cleanup_deletes_oldest(self, app_ctx):
        user = app_ctx
        for i in range(5):
            PlaylistSnapshotService.create_snapshot(
                user.id, "p1", f"S{i}",
                [f"spotify:track:{i}"],
                SnapshotType.MANUAL,
            )

        deleted = PlaylistSnapshotService.cleanup_old_snapshots(
            user.id, "p1", max_count=3
        )
        assert deleted == 2

        remaining = PlaylistSnapshotService.get_snapshots(
            user.id, "p1", limit=100
        )
        assert len(remaining) == 3

    def test_cleanup_noop_when_under_limit(self, app_ctx):
        user = app_ctx
        PlaylistSnapshotService.create_snapshot(
            user.id, "p1", "Test", ["spotify:track:a"],
            SnapshotType.MANUAL,
        )

        deleted = PlaylistSnapshotService.cleanup_old_snapshots(
            user.id, "p1", max_count=10
        )
        assert deleted == 0

    def test_cleanup_is_per_playlist(self, app_ctx):
        user = app_ctx
        for i in range(3):
            PlaylistSnapshotService.create_snapshot(
                user.id, "p1", f"S{i}",
                [f"spotify:track:{i}"],
                SnapshotType.MANUAL,
            )
            PlaylistSnapshotService.create_snapshot(
                user.id, "p2", f"S{i}",
                [f"spotify:track:{i}"],
                SnapshotType.MANUAL,
            )

        PlaylistSnapshotService.cleanup_old_snapshots(
            user.id, "p1", max_count=1
        )

        p1_snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p1", limit=100
        )
        p2_snaps = PlaylistSnapshotService.get_snapshots(
            user.id, "p2", limit=100
        )
        assert len(p1_snaps) == 1
        assert len(p2_snaps) == 3  # Untouched


class TestPlaylistSnapshotServiceAutoEnabled:
    """Tests for is_auto_snapshot_enabled."""

    def test_defaults_to_true(self, app_ctx):
        user = app_ctx
        assert PlaylistSnapshotService.is_auto_snapshot_enabled(
            user.id
        ) is True
```

#### B. Route Tests

**File**: `/Users/chris/Projects/shuffify/tests/routes/test_snapshot_routes.py`

Follow the pattern from the existing route tests. These will use the Flask test client with mocked services to test the HTTP layer.

```python
"""
Tests for snapshot routes.

Tests cover list, create, view, restore, and delete endpoints.
"""

import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db
from shuffify.services.user_service import UserService


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
    app.config["SCHEDULER_ENABLED"] = False

    with app.app_context():
        db.drop_all()
        db.create_all()
        UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def auth_client(db_app):
    """Authenticated test client with session user data."""
    import time

    with db_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "expires_at": time.time() + 3600,
                "refresh_token": "test_refresh",
            }
            sess["user_data"] = {
                "id": "user123",
                "display_name": "Test User",
            }
        yield client


class TestListSnapshots:
    """Tests for GET /playlist/<id>/snapshots."""

    @patch("shuffify.routes.snapshots.require_auth")
    def test_unauthenticated_returns_401(
        self, mock_auth, db_app
    ):
        mock_auth.return_value = None
        with db_app.test_client() as client:
            resp = client.get("/playlist/p1/snapshots")
            assert resp.status_code == 401

    # Additional route tests would follow this pattern...


class TestCreateManualSnapshot:
    """Tests for POST /playlist/<id>/snapshots."""

    # Tests for validation, success, error cases...
    pass


class TestRestoreSnapshot:
    """Tests for POST /snapshots/<id>/restore."""

    # Tests for restore flow including auto-snapshot before restore...
    pass


class TestDeleteSnapshot:
    """Tests for DELETE /snapshots/<id>."""

    # Tests for ownership check, success, 404 cases...
    pass
```

**Ensure `tests/routes/__init__.py` exists** (create if needed -- it may already exist or the `tests/routes/` directory may need to be created).

---

## Edge Cases and Considerations

### 1. Snapshot Failure Must Not Block Operations
Every auto-snapshot insertion is wrapped in `try/except`. If the database is down, if the snapshot creation fails for any reason, the original operation (shuffle, commit, raid) proceeds normally. This is the most critical design decision.

### 2. Large Playlists
A playlist with 10,000 tracks will produce a `track_uris_json` column of approximately 350KB (each URI is ~35 characters). With a default max of 50 snapshots, this is ~17MB per playlist per user in the worst case. This is acceptable for PostgreSQL (Phase 0). For future optimization, consider:
- Compressing the JSON with zlib before storage
- Storing only diffs between snapshots
- Adding a `size_bytes` column for monitoring

### 3. Race Condition in Scheduled Jobs
If two scheduled jobs execute simultaneously for the same playlist (edge case with overlapping schedules), both will snapshot and then mutate. This is acceptable -- both snapshots are valid pre-mutation states. The `created_at` timestamp ensures ordering.

### 4. Duplicate Snapshots
If a user triggers a shuffle and the playlist hasn't changed since the last snapshot, we still create a new snapshot. This is intentional -- the snapshot records "the state at this point in time before this specific operation" for audit purposes. The cleanup mechanism prevents unbounded growth.

### 5. Snapshot During Restore
The restore endpoint creates an auto-snapshot of the CURRENT state before applying the restoration. This prevents the user from losing their current arrangement when restoring to an old snapshot. This gives the user a full undo chain.

### 6. Job Executor Has No Session
The `_execute_shuffle` and `_execute_raid` methods in `JobExecutorService` run without a Flask session (background APScheduler context). The auto-snapshot hook uses `schedule.user_id` directly (an integer FK), so it doesn't need the session's `user_data`. This is already established by how the job executor accesses `schedule.user_id` on line 78.

### 7. Empty Playlist Snapshots
We allow creating snapshots of empty playlists (`track_uris=[]`). This is valid -- a user might want to record that a playlist was empty before a raid added tracks.

---

## Verification Checklist

- [ ] `PlaylistSnapshot` model exists in `db.py` with all specified columns
- [ ] Composite index `ix_snapshot_user_playlist_created` is present
- [ ] `SnapshotType` enum is in `enums.py`
- [ ] `PlaylistSnapshotService` has: `create_snapshot`, `get_snapshots`, `get_snapshot`, `restore_snapshot`, `delete_snapshot`, `cleanup_old_snapshots`, `is_auto_snapshot_enabled`
- [ ] Service is exported from `shuffify/services/__init__.py`
- [ ] Snapshot routes are in `shuffify/routes/snapshots.py` and registered in `__init__.py`
- [ ] Pydantic schema `ManualSnapshotRequest` validates track URIs
- [ ] Auto-snapshot hook in `shuffle.py` fires before `playlist_service.update_playlist_tracks()`
- [ ] Auto-snapshot hook in `workshop.py` fires before `playlist_service.update_playlist_tracks()`
- [ ] Auto-snapshot hook in `job_executor_service.py` fires before `api.update_playlist_tracks()` (shuffle) and before `api._sp.playlist_add_items()` (raid)
- [ ] All auto-snapshot hooks are wrapped in `try/except` and log warnings on failure
- [ ] All auto-snapshot hooks check `is_auto_snapshot_enabled()` before creating
- [ ] All auto-snapshot hooks check `is_db_available()` (routes only -- job executor already has DB context)
- [ ] Ownership checks prevent users from accessing other users' snapshots
- [ ] Retention limit is enforced after every `create_snapshot()`
- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes with all existing + new tests
- [ ] Alembic migration generates and applies cleanly
- [ ] CHANGELOG.md is updated under `## [Unreleased]`

---

## What NOT to Do

1. **Do NOT make auto-snapshots blocking.** If the snapshot creation fails, the original operation must proceed. Never let a snapshot error propagate up to the user as a failed shuffle/commit/raid.

2. **Do NOT call the Spotify API from `restore_snapshot()` in the service layer.** The service returns URIs; the route layer applies them. This maintains the separation of concerns.

3. **Do NOT use `spotify_id` (string) as the user identifier in `PlaylistSnapshotService`.** Use the internal `user_id` (integer) to match the FK and avoid extra lookups. The route layer resolves `user_id` from `get_db_user()`.

4. **Do NOT hard-code the max snapshot count in `create_snapshot()`.** Always read from `_get_max_snapshots()` which will read from `UserSettings` when Phase 3 is available.

5. **Do NOT create a separate Blueprint for snapshot routes.** Follow the existing pattern where all routes use the single `main` Blueprint split across feature modules.

6. **Do NOT add `track_uris_json` validation in the model layer.** The Pydantic schema handles URI validation at the request boundary. The model layer trusts that data has been validated.

7. **Do NOT modify the existing `StateService` or session-based undo system.** The snapshot system is a separate, persistent layer that complements (not replaces) the session-based undo stack.

8. **Do NOT snapshot when the operation won't actually change anything.** In the shuffle route, the snapshot happens before we check `ShuffleService.shuffle_changed_order()`. This is intentional -- we can't know if the order will change until after the shuffle algorithm runs. Snapshotting before is safer. The storage cost of occasional no-op snapshots is negligible given the retention limit.

---

## CHANGELOG Entry

```markdown
## [Unreleased]

### Added
- **Playlist Snapshots** - Persistent point-in-time capture of playlist track orderings
  - New `PlaylistSnapshot` database model with automatic retention management
  - Auto-snapshot before shuffle, workshop commit, and scheduled job execution
  - Manual snapshot creation via API endpoint
  - Snapshot restoration with pre-restore auto-snapshot for undo safety
  - Snapshot listing, viewing, and deletion with ownership checks
  - `SnapshotType` enum for categorizing snapshot triggers
  - Respects `UserSettings.auto_snapshot_enabled` and `max_snapshots_per_playlist`
  - New API endpoints: `GET/POST /playlist/<id>/snapshots`, `GET/DELETE /snapshots/<id>`, `POST /snapshots/<id>/restore`
```

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/models/db.py` - Add PlaylistSnapshot model (core data structure)
- `/Users/chris/Projects/shuffify/shuffify/services/playlist_snapshot_service.py` - New service with all CRUD + cleanup logic (create this file)
- `/Users/chris/Projects/shuffify/shuffify/services/job_executor_service.py` - Hook auto-snapshot into background scheduled operations
- `/Users/chris/Projects/shuffify/shuffify/routes/workshop.py` - Hook auto-snapshot before workshop commit
- `/Users/chris/Projects/shuffify/shuffify/services/workshop_session_service.py` - Reference pattern for ownership checks, CRUD, and limit enforcement
