# Phase 2: Service Layer Deduplication -- Detailed Remediation Plan

## PR Metadata

- **PR title**: "Refactor: Extract shared service utilities to reduce CRUD duplication"
- **Branch name**: `implement/service-layer-deduplication`
- **Risk level**: Medium (changes internal implementation of 6+ services)
- **Estimated effort**: 2-3 hours
- **Dependencies**: None
- **Blocks**: Phase 3 (cleaner services make decomposition easier)
- **Files created**: `shuffify/services/base.py`, `tests/services/test_base.py`
- **Files modified**: `shuffify/services/upstream_source_service.py`, `shuffify/services/playlist_snapshot_service.py`, `shuffify/services/user_settings_service.py`, `shuffify/services/user_service.py`, `shuffify/services/login_history_service.py`, `shuffify/services/workshop_session_service.py`, `shuffify/services/__init__.py`

---

## 1. Complete Inventory of Duplicated Patterns

### Pattern A: safe_commit (try/commit/rollback/log/raise)

This pattern appears in the following exact locations. Each occurrence wraps a `db.session.commit()` call in a try/except that rolls back, logs, and re-raises as a service-specific exception.

| # | File | Method | Lines | Exception Class Raised |
|---|------|--------|-------|----------------------|
| 1 | `shuffify/services/upstream_source_service.py` | `add_source` | 84-112 | `UpstreamSourceError` |
| 2 | `shuffify/services/upstream_source_service.py` | `delete_source` | 191-208 | `UpstreamSourceError` |
| 3 | `shuffify/services/playlist_snapshot_service.py` | `create_snapshot` | 67-107 | `PlaylistSnapshotError` |
| 4 | `shuffify/services/playlist_snapshot_service.py` | `delete_snapshot` | 216-231 | `PlaylistSnapshotError` |
| 5 | `shuffify/services/playlist_snapshot_service.py` | `cleanup_old_snapshots` | 267-285 | (silently returns 0) |
| 6 | `shuffify/services/user_settings_service.py` | `get_or_create` | 45-74 | `UserSettingsError` |
| 7 | `shuffify/services/user_settings_service.py` | `update` | 110-181 | `UserSettingsError` |
| 8 | `shuffify/services/user_service.py` | `upsert_from_spotify` | 84-171 | `UserServiceError` |
| 9 | `shuffify/services/login_history_service.py` | `record_login` | 74-100 | `LoginHistoryError` |
| 10 | `shuffify/services/login_history_service.py` | `record_logout` | 126-166 | `LoginHistoryError` |
| 11 | `shuffify/services/workshop_session_service.py` | `save_session` | 90-116 | `WorkshopSessionError` |
| 12 | `shuffify/services/workshop_session_service.py` | `update_session` | 200-221 | `WorkshopSessionError` |
| 13 | `shuffify/services/workshop_session_service.py` | `delete_session` | 245-262 | `WorkshopSessionError` |

**NOTE on scope**: Not every occurrence above is a clean candidate for `safe_commit`. Some have additional logic inside the try block (e.g., `user_service.py` lines 84-171 has branching logic for new vs existing users, and auto-creates settings for new users). The utility should only replace the **commit/rollback/log/raise** portion, not the pre-commit business logic. The following occurrences are clean, direct candidates:

- **Direct replacements** (commit + log + return inside try, rollback + log + raise in except): #1, #2, #3, #4, #6, #9, #11, #12, #13
- **Partial replacements** (more complex logic in try block -- only the error-handling portion maps to safe_commit): #7, #8, #10
- **Different error handling** (returns 0 instead of raising on failure): #5

### Pattern B: get_user_or_raise (User lookup by spotify_id)

| # | File | Method | Lines | Exception on not-found | Behavior if not found |
|---|------|--------|-------|----------------------|----------------------|
| 1 | `upstream_source_service.py` | `add_source` | 63-67 | `UpstreamSourceError` | raises |
| 2 | `upstream_source_service.py` | `list_sources` | 128-129 | N/A | returns `[]` |
| 3 | `upstream_source_service.py` | `get_source` | 158-160 | `UpstreamSourceNotFoundError` | raises |
| 4 | `upstream_source_service.py` | `count_sources_for_target` | 215-219 | N/A | returns `0` |
| 5 | `upstream_source_service.py` | `list_all_sources_for_user` | 238-240 | N/A | returns `[]` |
| 6 | `workshop_session_service.py` | `save_session` | 71-76 | `WorkshopSessionError` | raises |
| 7 | `workshop_session_service.py` | `list_sessions` | 132-134 | N/A | returns `[]` |
| 8 | `workshop_session_service.py` | `get_session` | 161-163 | `WorkshopSessionNotFoundError` | raises |
| 9 | `raid_sync_service.py` | `watch_playlist` | 48-52 | `RaidSyncError` | raises |
| 10 | `raid_sync_service.py` | `unwatch_playlist` | 142-146 | N/A | returns `True` |
| 11 | `raid_sync_service.py` | `get_raid_status` | 191-203 | N/A | returns default dict |
| 12 | `raid_sync_service.py` | `raid_now` | 251-255 | `RaidSyncError` | raises |

The utility must support two modes:
1. **Raise mode** (default): look up user, raise exception if not found
2. **Return-None mode**: look up user, return `None` if not found (caller handles gracefully)

### Pattern C: get_owned_entity (fetch by ID + ownership check)

| # | File | Method | Lines | Entity Class | Exception on not-found |
|---|------|--------|-------|-------------|----------------------|
| 1 | `upstream_source_service.py` | `get_source` | 162-167 | `UpstreamSource` | `UpstreamSourceNotFoundError` |
| 2 | `playlist_snapshot_service.py` | `get_snapshot` | 153-160 | `PlaylistSnapshot` | `PlaylistSnapshotNotFoundError` |
| 3 | `workshop_session_service.py` | `get_session` | 165-171 | `WorkshopSession` | `WorkshopSessionNotFoundError` |

Note: `scheduler_service.py` lines 64-74 (`get_schedule`) uses a different pattern -- it queries with `filter_by(id=schedule_id, user_id=user_id)` rather than `db.session.get()` + ownership check. This is structurally different and should NOT be changed in this phase.

---

## 2. Utility Function Specifications

### 2a. `safe_commit(operation_name, exception_class)`

```python
# shuffify/services/base.py

import logging
from typing import Type, Optional

from shuffify.models.db import db, User

logger = logging.getLogger(__name__)


def safe_commit(
    operation_name: str,
    exception_class: Type[Exception] = Exception,
) -> None:
    """
    Commit the current database session with rollback on failure.

    Wraps db.session.commit() in a try/except. On success, logs an
    info message. On failure, rolls back, logs the error with
    exc_info, and raises the specified exception class.

    Args:
        operation_name: Human-readable description of the operation
            (used in log messages and exception text).
        exception_class: The exception class to raise on failure.
            Defaults to Exception.

    Raises:
        The specified exception_class with a message describing
        the failure.
    """
    try:
        db.session.commit()
        logger.info(f"Success: {operation_name}")
    except Exception as e:
        db.session.rollback()
        logger.error(
            f"Failed to {operation_name}: {e}",
            exc_info=True,
        )
        raise exception_class(
            f"Failed to {operation_name}: {e}"
        )
```

**Design decision**: This function ONLY handles commit/rollback/log/raise. It does NOT call `db.session.add()`. The caller is responsible for adding objects to the session before calling `safe_commit()`. This keeps the utility narrow and composable.

### 2b. `get_user_or_raise(spotify_id, exception_class=None)`

```python
def get_user_or_raise(
    spotify_id: str,
    exception_class: Optional[Type[Exception]] = None,
) -> Optional[User]:
    """
    Look up a User by spotify_id.

    If exception_class is provided, raises it when the user is not
    found. If exception_class is None, returns None when not found.

    Args:
        spotify_id: The Spotify user ID to look up.
        exception_class: Optional exception class to raise if user
            not found. If None, returns None instead of raising.

    Returns:
        The User instance, or None if not found and no
        exception_class was specified.

    Raises:
        The specified exception_class if user is not found and
        exception_class is not None.
    """
    user = User.query.filter_by(spotify_id=spotify_id).first()
    if not user and exception_class is not None:
        raise exception_class(
            f"User not found for spotify_id: {spotify_id}"
        )
    return user
```

**Design decision**: The dual-mode behavior (raise vs return None) is controlled by whether `exception_class` is passed. This matches both usage patterns found in the codebase: some callers raise on missing user, others return empty results.

### 2c. `get_owned_entity(entity_class, entity_id, user_id, exception_class)`

```python
def get_owned_entity(
    entity_class,
    entity_id: int,
    user_id: int,
    exception_class: Type[Exception],
):
    """
    Fetch an entity by primary key and verify ownership.

    Uses db.session.get() to fetch the entity, then checks that
    entity.user_id matches the provided user_id.

    Args:
        entity_class: The SQLAlchemy model class to query.
        entity_id: The primary key ID of the entity.
        user_id: The expected owner's internal database user ID.
        exception_class: The exception class to raise if the entity
            is not found or ownership does not match.

    Returns:
        The entity instance.

    Raises:
        The specified exception_class if entity is not found or
        user_id does not match.
    """
    entity = db.session.get(entity_class, entity_id)
    if not entity or entity.user_id != user_id:
        raise exception_class(
            f"{entity_class.__name__} {entity_id} not found"
        )
    return entity
```

**Design decision**: This function assumes all entities have a `user_id` attribute. All three candidates (UpstreamSource, PlaylistSnapshot, WorkshopSession) do.

---

## 3. Step-by-Step Implementation Instructions

### Step 1: Create `shuffify/services/base.py`

Create a new file at `/Users/chris/Projects/shuffify/shuffify/services/base.py` containing the three utility functions defined in Section 2 above, plus the necessary imports.

The complete file contents:

```python
"""
Shared service utilities to reduce CRUD boilerplate.

Provides common patterns for database commit safety, user lookup,
and entity ownership verification. Used across all service modules.
"""

import logging
from typing import Type, Optional

from shuffify.models.db import db, User

logger = logging.getLogger(__name__)


def safe_commit(
    operation_name: str,
    exception_class: Type[Exception] = Exception,
) -> None:
    """
    Commit the current database session with rollback on failure.

    Wraps db.session.commit() in a try/except. On success, logs an
    info message. On failure, rolls back, logs the error with
    exc_info, and raises the specified exception class.

    Args:
        operation_name: Human-readable description of the operation
            (used in log messages and exception text).
        exception_class: The exception class to raise on failure.
            Defaults to Exception.

    Raises:
        The specified exception_class with a message describing
        the failure.
    """
    try:
        db.session.commit()
        logger.info("Success: %s", operation_name)
    except Exception as e:
        db.session.rollback()
        logger.error(
            "Failed to %s: %s",
            operation_name,
            e,
            exc_info=True,
        )
        raise exception_class(
            f"Failed to {operation_name}: {e}"
        )


def get_user_or_raise(
    spotify_id: str,
    exception_class: Optional[Type[Exception]] = None,
) -> Optional[User]:
    """
    Look up a User by spotify_id.

    If exception_class is provided, raises it when the user is not
    found. If exception_class is None, returns None when not found.

    Args:
        spotify_id: The Spotify user ID to look up.
        exception_class: Optional exception class to raise if user
            not found. If None, returns None instead of raising.

    Returns:
        The User instance, or None if not found and no
        exception_class was specified.

    Raises:
        The specified exception_class if user is not found and
        exception_class is not None.
    """
    user = User.query.filter_by(spotify_id=spotify_id).first()
    if not user and exception_class is not None:
        raise exception_class(
            f"User not found for spotify_id: {spotify_id}"
        )
    return user


def get_owned_entity(
    entity_class,
    entity_id: int,
    user_id: int,
    exception_class: Type[Exception],
):
    """
    Fetch an entity by primary key and verify ownership.

    Uses db.session.get() to fetch the entity, then checks that
    entity.user_id matches the provided user_id.

    Args:
        entity_class: The SQLAlchemy model class to query.
        entity_id: The primary key ID of the entity.
        user_id: The expected owner's internal database user ID.
        exception_class: The exception class to raise if the entity
            is not found or ownership does not match.

    Returns:
        The entity instance.

    Raises:
        The specified exception_class if entity is not found or
        user_id does not match.
    """
    entity = db.session.get(entity_class, entity_id)
    if not entity or entity.user_id != user_id:
        raise exception_class(
            f"{entity_class.__name__} {entity_id} not found"
        )
    return entity
```

### Step 2: Update `shuffify/services/__init__.py`

Add exports for the new base utilities at the top of the file, after the docstring and before the existing imports. Add the following block:

```python
# Base utilities
from shuffify.services.base import (
    safe_commit,
    get_user_or_raise,
    get_owned_entity,
)
```

Also add to the `__all__` list:

```python
"safe_commit",
"get_user_or_raise",
"get_owned_entity",
```

### Step 3: Refactor `upstream_source_service.py`

**File**: `/Users/chris/Projects/shuffify/shuffify/services/upstream_source_service.py`

**3a. Add import** (line 11, alongside existing imports):

```python
from shuffify.services.base import (
    safe_commit,
    get_user_or_raise,
    get_owned_entity,
)
```

Remove `User` from the `from shuffify.models.db import db, UpstreamSource, User` import on line 11, since `get_user_or_raise` handles that internally. The line becomes:

```python
from shuffify.models.db import db, UpstreamSource
```

Note: `User` is still needed if it is referenced directly anywhere else. Check: `User` is only used via `User.query.filter_by(spotify_id=...)` lookups, all of which will be replaced. So `User` can be removed from the import.

**3b. Refactor `add_source`** (currently lines 63-112):

BEFORE (lines 63-112):
```python
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise UpstreamSourceError(
                f"User not found for spotify_id: {spotify_id}"
            )

        # Check for duplicate: same user, target, and source
        existing = UpstreamSource.query.filter_by(
            user_id=user.id,
            ...
        ).first()

        if existing:
            ...
            return existing

        try:
            source = UpstreamSource(...)
            db.session.add(source)
            db.session.commit()
            logger.info(...)
            return source
        except Exception as e:
            db.session.rollback()
            logger.error(...)
            raise UpstreamSourceError(...)
```

AFTER:
```python
        user = get_user_or_raise(
            spotify_id, UpstreamSourceError
        )

        # Check for duplicate: same user, target, and source
        existing = UpstreamSource.query.filter_by(
            user_id=user.id,
            target_playlist_id=target_playlist_id,
            source_playlist_id=source_playlist_id,
        ).first()

        if existing:
            logger.info(
                f"Upstream source already exists: "
                f"{source_playlist_id} -> "
                f"{target_playlist_id} for user {spotify_id}"
            )
            return existing

        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id=target_playlist_id,
            source_playlist_id=source_playlist_id,
            source_url=source_url,
            source_type=source_type,
            source_name=source_name,
        )
        db.session.add(source)
        safe_commit(
            f"add upstream source: {source_playlist_id} -> "
            f"{target_playlist_id} for user {spotify_id} "
            f"(type={source_type})",
            UpstreamSourceError,
        )
        return source
```

**3c. Refactor `list_sources`** (currently lines 128-139):

BEFORE (lines 128-129):
```python
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            return []
```

AFTER:
```python
        user = get_user_or_raise(spotify_id)
        if not user:
            return []
```

**3d. Refactor `get_source`** (currently lines 158-167):

BEFORE:
```python
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise UpstreamSourceNotFoundError("User not found")

        source = db.session.get(UpstreamSource, source_id)
        if not source or source.user_id != user.id:
            raise UpstreamSourceNotFoundError(
                f"Upstream source {source_id} not found"
            )
        return source
```

AFTER:
```python
        user = get_user_or_raise(
            spotify_id, UpstreamSourceNotFoundError
        )
        return get_owned_entity(
            UpstreamSource,
            source_id,
            user.id,
            UpstreamSourceNotFoundError,
        )
```

**3e. Refactor `delete_source`** (currently lines 191-208):

BEFORE:
```python
        source = UpstreamSourceService.get_source(
            source_id, spotify_id
        )

        try:
            db.session.delete(source)
            db.session.commit()
            logger.info(
                f"Deleted upstream source {source_id}"
            )
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(...)
            raise UpstreamSourceError(...)
```

AFTER:
```python
        source = UpstreamSourceService.get_source(
            source_id, spotify_id
        )

        db.session.delete(source)
        safe_commit(
            f"delete upstream source {source_id}",
            UpstreamSourceError,
        )
        return True
```

**3f. Refactor `count_sources_for_target`** (currently lines 215-223):

BEFORE:
```python
        user = User.query.filter_by(
            spotify_id=spotify_id
        ).first()
        if not user:
            return 0
```

AFTER:
```python
        user = get_user_or_raise(spotify_id)
        if not user:
            return 0
```

**3g. Refactor `list_all_sources_for_user`** (currently lines 238-246):

BEFORE:
```python
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            return []
```

AFTER:
```python
        user = get_user_or_raise(spotify_id)
        if not user:
            return []
```

### Step 4: Refactor `playlist_snapshot_service.py`

**File**: `/Users/chris/Projects/shuffify/shuffify/services/playlist_snapshot_service.py`

**4a. Add import** (after line 12):

```python
from shuffify.services.base import (
    safe_commit,
    get_owned_entity,
)
```

**4b. Refactor `create_snapshot`** (currently lines 67-107):

BEFORE:
```python
        try:
            snapshot = PlaylistSnapshot(...)
            snapshot.track_uris = track_uris

            db.session.add(snapshot)
            db.session.commit()

            logger.info(...)

            # Enforce retention limit
            max_snapshots = (...)
            PlaylistSnapshotService.cleanup_old_snapshots(...)

            return snapshot

        except Exception as e:
            db.session.rollback()
            logger.error(...)
            raise PlaylistSnapshotError(...)
```

AFTER:
```python
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
        safe_commit(
            f"create {snapshot_type} snapshot for user "
            f"{user_id}, playlist {playlist_id} "
            f"({len(track_uris)} tracks)",
            PlaylistSnapshotError,
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
```

**IMPORTANT BEHAVIORAL NOTE**: In the original code, the retention cleanup runs inside the same try/except as the commit. In the refactored version, `safe_commit` handles the commit failure, and the cleanup runs after a successful commit. This is actually **more correct** -- if the commit succeeds but cleanup fails, the snapshot was still created. The original code would have raised `PlaylistSnapshotError` even if the snapshot was committed but cleanup failed, which would have been misleading. However, verify this behavior change is acceptable in testing.

**4c. Refactor `get_snapshot`** (currently lines 153-160):

BEFORE:
```python
        snapshot = db.session.get(
            PlaylistSnapshot, snapshot_id
        )
        if not snapshot or snapshot.user_id != user_id:
            raise PlaylistSnapshotNotFoundError(
                f"Snapshot {snapshot_id} not found"
            )
        return snapshot
```

AFTER:
```python
        return get_owned_entity(
            PlaylistSnapshot,
            snapshot_id,
            user_id,
            PlaylistSnapshotNotFoundError,
        )
```

**4d. Refactor `delete_snapshot`** (currently lines 216-231):

BEFORE:
```python
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user_id
        )

        try:
            db.session.delete(snapshot)
            db.session.commit()
            logger.info(f"Deleted snapshot {snapshot_id}")
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(...)
            raise PlaylistSnapshotError(...)
```

AFTER:
```python
        snapshot = PlaylistSnapshotService.get_snapshot(
            snapshot_id, user_id
        )

        db.session.delete(snapshot)
        safe_commit(
            f"delete snapshot {snapshot_id}",
            PlaylistSnapshotError,
        )
        return True
```

**4e. `cleanup_old_snapshots`** (currently lines 267-285): **DO NOT REFACTOR**. This method has a unique error-handling pattern: it catches exceptions and returns `0` instead of raising. It also operates in a batch loop. The `safe_commit` utility does not match this pattern. Leave as-is.

### Step 5: Refactor `user_settings_service.py`

**File**: `/Users/chris/Projects/shuffify/shuffify/services/user_settings_service.py`

**5a. Add import** (after line 12):

```python
from shuffify.services.base import safe_commit
```

**5b. Refactor `get_or_create`** (currently lines 45-74):

BEFORE:
```python
        try:
            settings = UserSettings.query.filter_by(
                user_id=user_id
            ).first()

            if settings:
                return settings

            settings = UserSettings(user_id=user_id)
            db.session.add(settings)
            db.session.commit()

            logger.info(
                "Created default settings for user %d",
                user_id,
            )
            return settings

        except Exception as e:
            db.session.rollback()
            logger.error(...)
            raise UserSettingsError(...)
```

AFTER:
```python
        settings = UserSettings.query.filter_by(
            user_id=user_id
        ).first()

        if settings:
            return settings

        settings = UserSettings(user_id=user_id)
        db.session.add(settings)
        safe_commit(
            f"create default settings for user {user_id}",
            UserSettingsError,
        )
        return settings
```

**5c. Refactor `update`** (currently lines 110-181): **PARTIAL REFACTOR ONLY**. This method has complex logic: it validates fields in a loop, then commits. The validation raises `UserSettingsError` directly (not via commit failure). Only the commit/rollback portion maps to `safe_commit`.

BEFORE (lines 157-181):
```python
            settings.updated_at = datetime.now(timezone.utc)
            db.session.commit()

            logger.info(
                "Updated settings for user %d: %s",
                user_id,
                list(kwargs.keys()),
            )
            return settings

        except UserSettingsError:
            db.session.rollback()
            raise
        except Exception as e:
            db.session.rollback()
            logger.error(...)
            raise UserSettingsError(...)
```

AFTER:
```python
            settings.updated_at = datetime.now(timezone.utc)

            try:
                safe_commit(
                    f"update settings for user {user_id}: "
                    f"{list(kwargs.keys())}",
                    UserSettingsError,
                )
            except UserSettingsError:
                raise

            return settings

        except UserSettingsError:
            raise
```

**WAIT** -- this is getting convoluted. A simpler approach for `update`: keep the existing try/except structure for validation errors, and replace ONLY the `except Exception` branch's rollback+log+raise with `safe_commit`. Actually, the cleanest approach is:

AFTER (replace the entire try block from line 110 to 181):
```python
        try:
            for key, value in kwargs.items():
                if key not in updatable_fields:
                    continue

                # Validate specific fields
                if (
                    key == "default_algorithm"
                    and value is not None
                ):
                    valid = set(
                        ShuffleRegistry.get_available_algorithms().keys()
                    )
                    if value not in valid:
                        raise UserSettingsError(
                            f"Invalid algorithm '{value}'. "
                            f"Valid: "
                            f"{', '.join(sorted(valid))}"
                        )

                if key == "theme":
                    if value not in UserSettings.VALID_THEMES:
                        raise UserSettingsError(
                            f"Invalid theme '{value}'. "
                            f"Valid: "
                            f"{', '.join(sorted(UserSettings.VALID_THEMES))}"
                        )

                if key == "max_snapshots_per_playlist":
                    if not isinstance(value, int):
                        raise UserSettingsError(
                            "max_snapshots_per_playlist "
                            "must be an integer"
                        )
                    if (
                        value < MIN_SNAPSHOTS_LIMIT
                        or value > MAX_SNAPSHOTS_LIMIT
                    ):
                        raise UserSettingsError(
                            "max_snapshots_per_playlist "
                            f"must be between "
                            f"{MIN_SNAPSHOTS_LIMIT} and "
                            f"{MAX_SNAPSHOTS_LIMIT}"
                        )

                setattr(settings, key, value)

            settings.updated_at = datetime.now(timezone.utc)
            safe_commit(
                f"update settings for user {user_id}: "
                f"{list(kwargs.keys())}",
                UserSettingsError,
            )
            return settings

        except UserSettingsError:
            db.session.rollback()
            raise
```

This preserves the exact same behavior: validation errors cause rollback and re-raise, while unexpected commit errors are handled by `safe_commit`. The `except UserSettingsError: db.session.rollback(); raise` block catches both validation errors (raised before commit) and errors raised by `safe_commit` (which are also `UserSettingsError`). For `safe_commit` errors this means a double-rollback, which is harmless in SQLAlchemy.

### Step 6: Refactor `user_service.py`

**File**: `/Users/chris/Projects/shuffify/shuffify/services/user_service.py`

**6a. DO NOT REFACTOR `upsert_from_spotify`**. This method (lines 84-171) has complex branching logic:
- It checks if a user exists, then either updates or creates
- It commits, then auto-creates settings for new users in a separate try/except
- The error handling wraps the entire complex block

Forcing `safe_commit` here would make the code harder to read, not simpler. The upsert pattern is fundamentally different from simple CRUD operations. **Skip this service for this phase**.

**6b. Note**: `user_service.py` also has `get_by_spotify_id` (lines 173-190), which is a simple lookup. However, it returns `Optional[User]` and does NOT raise, so it does not match the `get_user_or_raise` pattern. **Skip this service entirely**.

### Step 7: Refactor `login_history_service.py`

**File**: `/Users/chris/Projects/shuffify/shuffify/services/login_history_service.py`

**7a. Add import** (after line 14):

```python
from shuffify.services.base import safe_commit
```

**7b. Refactor `record_login`** (currently lines 74-100):

BEFORE:
```python
        try:
            entry = LoginHistory(
                user_id=user_id,
                ip_address=ip_address,
                user_agent=user_agent,
                session_id=session_id,
                login_type=login_type,
            )
            db.session.add(entry)
            db.session.commit()

            logger.info(
                f"Recorded login for user_id={user_id}, "
                f"type={login_type}, ip={ip_address}"
            )
            return entry

        except Exception as e:
            db.session.rollback()
            logger.error(...)
            raise LoginHistoryError(...)
```

AFTER:
```python
        entry = LoginHistory(
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            session_id=session_id,
            login_type=login_type,
        )
        db.session.add(entry)
        safe_commit(
            f"record login for user_id={user_id}, "
            f"type={login_type}, ip={ip_address}",
            LoginHistoryError,
        )
        return entry
```

**7c. Refactor `record_logout`** (currently lines 126-166): **DO NOT REFACTOR**. This method has non-trivial query logic inside the try block (query building, conditional filtering, checking if entry exists) and returns `False` in the no-match case. The commit is only one step among many. The try/except wraps the entire operation. Forcing `safe_commit` would require splitting this into multiple try blocks, making it worse. **Leave as-is**.

### Step 8: Refactor `workshop_session_service.py`

**File**: `/Users/chris/Projects/shuffify/shuffify/services/workshop_session_service.py`

**8a. Add imports** (after line 11):

```python
from shuffify.services.base import (
    safe_commit,
    get_user_or_raise,
    get_owned_entity,
)
```

Remove `User` from the line 11 import. It becomes:

```python
from shuffify.models.db import db, WorkshopSession
```

**8b. Refactor `save_session`** (currently lines 71-116):

BEFORE (lines 71-76):
```python
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise WorkshopSessionError(
                f"User not found for spotify_id: {spotify_id}. "
                f"User must be logged in first."
            )
```

AFTER:
```python
        user = get_user_or_raise(
            spotify_id, WorkshopSessionError
        )
```

**Note on error message**: The original has a custom message "User not found for spotify_id: {spotify_id}. User must be logged in first." The utility uses a standardized message "User not found for spotify_id: {spotify_id}". The extra " User must be logged in first." will be lost. This is acceptable -- the error message is consumed by callers that flash a user-friendly message anyway, and the specific detail was not shown to end users.

BEFORE (lines 90-116):
```python
        try:
            ws = WorkshopSession(...)
            ws.track_uris = track_uris

            db.session.add(ws)
            db.session.commit()

            logger.info(...)
            return ws

        except Exception as e:
            db.session.rollback()
            logger.error(...)
            raise WorkshopSessionError(...)
```

AFTER:
```python
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id=playlist_id,
            session_name=session_name,
        )
        ws.track_uris = track_uris

        db.session.add(ws)
        safe_commit(
            f"save workshop session '{session_name}' for "
            f"user {spotify_id}, playlist {playlist_id} "
            f"({len(track_uris)} tracks)",
            WorkshopSessionError,
        )
        return ws
```

**8c. Refactor `list_sessions`** (currently lines 132-142):

BEFORE:
```python
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            return []
```

AFTER:
```python
        user = get_user_or_raise(spotify_id)
        if not user:
            return []
```

**8d. Refactor `get_session`** (currently lines 161-171):

BEFORE:
```python
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise WorkshopSessionNotFoundError("User not found")

        ws = db.session.get(WorkshopSession, session_id)
        if not ws or ws.user_id != user.id:
            raise WorkshopSessionNotFoundError(
                f"Workshop session {session_id} not found"
            )

        return ws
```

AFTER:
```python
        user = get_user_or_raise(
            spotify_id, WorkshopSessionNotFoundError
        )
        return get_owned_entity(
            WorkshopSession,
            session_id,
            user.id,
            WorkshopSessionNotFoundError,
        )
```

**8e. Refactor `update_session`** (currently lines 200-221):

BEFORE:
```python
        ws = WorkshopSessionService.get_session(
            session_id, spotify_id
        )

        try:
            ws.track_uris = track_uris
            if session_name is not None:
                ws.session_name = session_name.strip()
            db.session.commit()

            logger.info(...)
            return ws

        except Exception as e:
            db.session.rollback()
            logger.error(...)
            raise WorkshopSessionError(...)
```

AFTER:
```python
        ws = WorkshopSessionService.get_session(
            session_id, spotify_id
        )

        ws.track_uris = track_uris
        if session_name is not None:
            ws.session_name = session_name.strip()
        safe_commit(
            f"update workshop session {session_id}: "
            f"'{ws.session_name}' ({len(track_uris)} tracks)",
            WorkshopSessionError,
        )
        return ws
```

**8f. Refactor `delete_session`** (currently lines 245-262):

BEFORE:
```python
        ws = WorkshopSessionService.get_session(
            session_id, spotify_id
        )

        try:
            db.session.delete(ws)
            db.session.commit()
            logger.info(...)
            return True

        except Exception as e:
            db.session.rollback()
            logger.error(...)
            raise WorkshopSessionError(...)
```

AFTER:
```python
        ws = WorkshopSessionService.get_session(
            session_id, spotify_id
        )

        db.session.delete(ws)
        safe_commit(
            f"delete workshop session {session_id}",
            WorkshopSessionError,
        )
        return True
```

### Step 9: Services NOT being refactored (and why)

| Service | Reason |
|---------|--------|
| `user_service.py` | `upsert_from_spotify` has complex branching and post-commit logic. `get_by_spotify_id` returns Optional, does not raise. Not a good candidate. |
| `scheduler_service.py` | Uses `filter_by(id=..., user_id=...)` for ownership check (not `db.session.get` + field compare). Has `ScheduleLimitError` re-raise pattern in `create_schedule`. Different enough to skip. |
| `activity_log_service.py` | `log()` method intentionally swallows exceptions (returns `None` on failure). This is the opposite of `safe_commit`'s behavior. All other methods also swallow exceptions. Not a candidate. |
| `playlist_pair_service.py` | Uses `user_id` directly (not `spotify_id`), no try/except on most commits (lines 58-59 commit without error handling). Different pattern. |
| `raid_sync_service.py` | Orchestration service that calls other services. Its `User.query.filter_by` calls could use `get_user_or_raise`, but modifying an orchestration service adds risk. **Optional**: could refactor the user lookups, but recommend deferring to keep the PR focused. |
| `job_executor_service.py` | Complex execution flow with status tracking. Not a candidate. |

### Step 10: Create `tests/services/test_base.py`

Create a new test file at `/Users/chris/Projects/shuffify/tests/services/test_base.py` following the same fixture patterns used in existing test files (each test file creates its own `db_app` fixture with in-memory SQLite).

The test file should cover:

**For `safe_commit`**:
1. `test_safe_commit_success` -- Add an entity, call `safe_commit`, verify the entity is persisted.
2. `test_safe_commit_rollback_on_failure` -- Force a commit failure (e.g., unique constraint violation), verify `safe_commit` raises the specified exception class with the correct message.
3. `test_safe_commit_rolls_back_session` -- After a failed `safe_commit`, verify the session is clean (no pending changes).
4. `test_safe_commit_default_exception_class` -- Call `safe_commit` without specifying exception_class; on failure, it should raise plain `Exception`.
5. `test_safe_commit_custom_exception_class` -- Verify that the raised exception is an instance of the specified custom class.

**For `get_user_or_raise`**:
1. `test_get_user_or_raise_returns_user` -- Create a user, call `get_user_or_raise`, verify it returns the user.
2. `test_get_user_or_raise_raises_when_not_found` -- Call with a non-existent spotify_id and an exception class; verify it raises.
3. `test_get_user_or_raise_returns_none_when_no_exception_class` -- Call with a non-existent spotify_id and no exception class; verify it returns `None`.
4. `test_get_user_or_raise_exception_message_contains_spotify_id` -- Verify the exception message includes the spotify_id.

**For `get_owned_entity`**:
1. `test_get_owned_entity_returns_entity` -- Create an entity, verify it returns correctly.
2. `test_get_owned_entity_raises_when_not_found` -- Call with non-existent ID; verify it raises.
3. `test_get_owned_entity_raises_when_wrong_owner` -- Create entity for user A, try to get as user B; verify it raises.
4. `test_get_owned_entity_exception_message_contains_class_name` -- Verify the error message includes the entity class name and ID.

Here is the complete test file structure:

```python
"""
Tests for shared service base utilities.

Tests cover safe_commit, get_user_or_raise, and get_owned_entity.
"""

import pytest

from shuffify.models.db import (
    db,
    User,
    WorkshopSession,
    UpstreamSource,
)
from shuffify.services.base import (
    safe_commit,
    get_user_or_raise,
    get_owned_entity,
)


class TestError(Exception):
    """Test exception class for verifying exception behavior."""
    pass


class TestNotFoundError(Exception):
    """Test exception for not-found scenarios."""
    pass


@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"
    os.environ.pop("DATABASE_URL", None)

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///:memory:"
    )
    app.config["TESTING"] = True

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def app_ctx(db_app):
    """Provide app context."""
    with db_app.app_context():
        yield


@pytest.fixture
def test_user(app_ctx):
    """Create a test user in the database."""
    user = User(
        spotify_id="base_test_user",
        display_name="Base Test User",
    )
    db.session.add(user)
    db.session.commit()
    return user


class TestSafeCommit:
    """Tests for safe_commit utility."""

    def test_safe_commit_success(self, app_ctx, test_user):
        """Should commit and persist changes."""
        user2 = User(
            spotify_id="new_user",
            display_name="New User",
        )
        db.session.add(user2)
        safe_commit("create test user", TestError)

        found = User.query.filter_by(
            spotify_id="new_user"
        ).first()
        assert found is not None
        assert found.display_name == "New User"

    def test_safe_commit_rollback_on_failure(
        self, app_ctx, test_user
    ):
        """Should rollback and raise on commit failure."""
        # Trigger a unique constraint violation
        duplicate = User(
            spotify_id="base_test_user",
            display_name="Duplicate",
        )
        db.session.add(duplicate)

        with pytest.raises(TestError, match="Failed to"):
            safe_commit("create duplicate user", TestError)

    def test_safe_commit_default_exception_class(
        self, app_ctx, test_user
    ):
        """Should raise plain Exception when no class given."""
        duplicate = User(
            spotify_id="base_test_user",
            display_name="Duplicate",
        )
        db.session.add(duplicate)

        with pytest.raises(Exception, match="Failed to"):
            safe_commit("create duplicate user")

    def test_safe_commit_custom_exception_class(
        self, app_ctx, test_user
    ):
        """Should raise the specified custom exception."""
        duplicate = User(
            spotify_id="base_test_user",
            display_name="Duplicate",
        )
        db.session.add(duplicate)

        with pytest.raises(TestError):
            safe_commit("create duplicate", TestError)


class TestGetUserOrRaise:
    """Tests for get_user_or_raise utility."""

    def test_returns_user_when_found(
        self, app_ctx, test_user
    ):
        """Should return the user when found."""
        user = get_user_or_raise(
            "base_test_user", TestError
        )
        assert user is not None
        assert user.spotify_id == "base_test_user"

    def test_raises_when_not_found(self, app_ctx):
        """Should raise when user not found."""
        with pytest.raises(
            TestError, match="User not found"
        ):
            get_user_or_raise("ghost", TestError)

    def test_returns_none_when_no_exception_class(
        self, app_ctx
    ):
        """Should return None when no exception class."""
        result = get_user_or_raise("ghost")
        assert result is None

    def test_exception_message_contains_spotify_id(
        self, app_ctx
    ):
        """Should include spotify_id in error message."""
        with pytest.raises(
            TestError, match="nonexistent_user"
        ):
            get_user_or_raise(
                "nonexistent_user", TestError
            )

    def test_returns_user_without_exception_class(
        self, app_ctx, test_user
    ):
        """Should return user even when no exception class."""
        user = get_user_or_raise("base_test_user")
        assert user is not None
        assert user.spotify_id == "base_test_user"


class TestGetOwnedEntity:
    """Tests for get_owned_entity utility."""

    def test_returns_entity_when_owned(
        self, app_ctx, test_user
    ):
        """Should return entity when ownership matches."""
        ws = WorkshopSession(
            user_id=test_user.id,
            playlist_id="p1",
            session_name="Test",
        )
        ws.track_uris = ["spotify:track:a"]
        db.session.add(ws)
        db.session.commit()

        result = get_owned_entity(
            WorkshopSession,
            ws.id,
            test_user.id,
            TestNotFoundError,
        )
        assert result.id == ws.id

    def test_raises_when_not_found(
        self, app_ctx, test_user
    ):
        """Should raise when entity does not exist."""
        with pytest.raises(TestNotFoundError):
            get_owned_entity(
                WorkshopSession,
                99999,
                test_user.id,
                TestNotFoundError,
            )

    def test_raises_when_wrong_owner(
        self, app_ctx, test_user
    ):
        """Should raise when user_id does not match."""
        ws = WorkshopSession(
            user_id=test_user.id,
            playlist_id="p1",
            session_name="Test",
        )
        ws.track_uris = ["spotify:track:a"]
        db.session.add(ws)
        db.session.commit()

        with pytest.raises(TestNotFoundError):
            get_owned_entity(
                WorkshopSession,
                ws.id,
                99999,
                TestNotFoundError,
            )

    def test_exception_message_contains_class_name(
        self, app_ctx, test_user
    ):
        """Should include entity class name in message."""
        with pytest.raises(
            TestNotFoundError,
            match="WorkshopSession",
        ):
            get_owned_entity(
                WorkshopSession,
                99999,
                test_user.id,
                TestNotFoundError,
            )
```

### Step 11: Run Verification

After all changes:

```bash
# Run linting
flake8 shuffify/

# Run ALL tests (not just the new ones)
pytest tests/ -v

# Quick sanity check that the new base module is importable
python -c "from shuffify.services.base import safe_commit, get_user_or_raise, get_owned_entity; print('OK')"
```

---

## 4. Verification Checklist

- [ ] `flake8 shuffify/` returns 0 errors
- [ ] `pytest tests/ -v` -- all 1081+ tests pass (no regressions)
- [ ] `pytest tests/services/test_base.py -v` -- all new tests pass
- [ ] `pytest tests/services/test_upstream_source_service.py -v` -- all existing tests pass
- [ ] `pytest tests/services/test_workshop_session_service.py -v` -- all existing tests pass
- [ ] `pytest tests/services/test_playlist_snapshot_service.py -v` -- all existing tests pass
- [ ] `pytest tests/services/test_user_settings_service.py -v` -- all existing tests pass
- [ ] `pytest tests/services/test_login_history_service.py -v` -- all existing tests pass
- [ ] Error messages in tests still match (check `pytest.raises(match=...)` patterns)
- [ ] No new imports of `User` appear in refactored services that removed it
- [ ] `shuffify/services/__init__.py` exports the new utilities
- [ ] CHANGELOG.md updated with entry under `## [Unreleased]` / `### Changed`

---

## 5. What NOT To Do

1. **DO NOT refactor `activity_log_service.py`**. Its `log()` method intentionally swallows exceptions and returns `None`. This is a deliberate design choice for non-blocking audit logging. Forcing `safe_commit` here would break the "never raises" contract.

2. **DO NOT refactor `user_service.py`'s `upsert_from_spotify`**. It has complex post-commit logic (auto-creating settings for new users). Splitting this into `safe_commit` would require restructuring the entire method and could introduce bugs around the settings-creation fallback behavior.

3. **DO NOT refactor `scheduler_service.py`**. Its `get_schedule` uses `filter_by(id=..., user_id=...)` (a single query) rather than `db.session.get()` + ownership check (two operations). These are semantically different. Forcing `get_owned_entity` would change the query pattern.

4. **DO NOT refactor `login_history_service.py`'s `record_logout`**. It has a complex query chain with conditional filtering and returns `False` (not raises) when no matching record is found.

5. **DO NOT refactor `playlist_snapshot_service.py`'s `cleanup_old_snapshots`**. It catches exceptions and returns `0` instead of raising -- a fundamentally different error contract from `safe_commit`.

6. **DO NOT refactor `raid_sync_service.py`**. It is an orchestration service that composes other services. Its `User.query.filter_by` calls could theoretically use `get_user_or_raise`, but the risk of modifying an orchestration layer in the same PR as the utilities it depends on is unnecessary. Defer to a future cleanup pass.

7. **DO NOT change exception messages** that are tested with `pytest.raises(match=...)`. The test files check for specific strings like `"User not found"`, `"cannot be empty"`, etc. The `get_user_or_raise` utility produces `"User not found for spotify_id: {id}"` which still matches `"User not found"`. But if you change other error message strings in the service methods, verify the test `match` patterns still hold.

8. **DO NOT add `base.py` utilities to the `shuffify/services/base.py` __all__**. The file does not need an `__all__` -- it is a utilities module, not a package.

9. **DO NOT create circular imports**. The `base.py` module imports from `shuffify.models.db` only. Service modules import from `shuffify.services.base`. This is a one-way dependency. Do NOT import any service module from `base.py`.

10. **DO NOT change the behavior of `safe_commit`'s success logging**. The utility uses `logger.info("Success: %s", operation_name)`. Some original methods used `logger.info(f"...")` with more specific messages. The refactored services will produce slightly different info-level log messages. This is acceptable and expected, but DO NOT try to make the log messages identical -- that defeats the purpose of the utility.

11. **DO NOT change `playlist_pair_service.py`'s `create_pair` and `delete_pair`**. These methods call `db.session.commit()` without any try/except wrapping (lines 58-59 and 97-98). While they COULD benefit from `safe_commit`, adding error handling where none existed is a behavioral change, not a refactor. That belongs in a different phase.

---

## 6. CHANGELOG Entry

Add under `## [Unreleased]`:

```markdown
### Changed
- **Service Layer Deduplication** - Extracted shared CRUD utilities to `shuffify/services/base.py`
  - `safe_commit()` wraps db commit/rollback/log pattern (replaced 9 occurrences across 5 services)
  - `get_user_or_raise()` standardizes User lookup by spotify_id (replaced 8 occurrences across 2 services)
  - `get_owned_entity()` standardizes entity fetch + ownership check (replaced 3 occurrences across 3 services)
  - No behavioral changes -- purely internal refactoring
```

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/services/base.py` - New file: all three utility functions to create
- `/Users/chris/Projects/shuffify/shuffify/services/upstream_source_service.py` - Heaviest refactoring: 5 methods use all 3 utilities
- `/Users/chris/Projects/shuffify/shuffify/services/workshop_session_service.py` - Second heaviest: 5 methods use all 3 utilities
- `/Users/chris/Projects/shuffify/shuffify/services/playlist_snapshot_service.py` - 3 methods use safe_commit and get_owned_entity
- `/Users/chris/Projects/shuffify/tests/services/test_base.py` - New test file: ~15 tests for the three utilities
