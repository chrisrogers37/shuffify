# Phase 05: Wire Up Unused ActivityType Enums

`📋 PENDING`

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `feat: Wire up activity logging for snapshots, workshop sessions, and settings` |
| **Risk Level** | Low |
| **Estimated Effort** | Low (1-2 hours) |
| **Dependencies** | None |
| **Blocks** | Nothing |

### Files Modified

| File | Change Type |
|------|-------------|
| `shuffify/routes/snapshots.py` | Add `log_activity()` calls for create/restore/delete |
| `shuffify/routes/settings.py` | Add `log_activity()` call for settings change |
| `shuffify/routes/workshop.py` | Add `log_activity()` calls for session save/delete |
| Route test files (3) | Verify logging occurs |

---

## Problem

6 `ActivityType` enum values in `shuffify/enums.py` (lines 55-74) are defined but never used:

| Enum Value | Defined At | Expected Usage |
|-----------|-----------|---------------|
| `WORKSHOP_SESSION_SAVE` | line 55 | When a workshop session is saved |
| `WORKSHOP_SESSION_DELETE` | line 56 | When a workshop session is deleted |
| `SNAPSHOT_CREATE` | line 64 | When a snapshot is manually created |
| `SNAPSHOT_RESTORE` | line 65 | When a snapshot is restored |
| `SNAPSHOT_DELETE` | line 66 | When a snapshot is deleted |
| `SETTINGS_CHANGE` | line 74 | When user settings are updated |

### Existing `log_activity()` Pattern

The shared helper at `shuffify/routes/__init__.py:194` wraps `ActivityLogService.log()`:

```python
def log_activity(user_id, activity_type, description, **kwargs):
    """Log a user activity. Non-blocking convenience wrapper."""
    try:
        ActivityLogService.log(
            user_id=user_id,
            activity_type=activity_type,
            description=description,
            **kwargs,
        )
    except Exception:
        pass  # Non-blocking
```

All existing call sites follow this pattern:
```python
log_activity(
    user_id=db_user.id,
    activity_type=ActivityType.SHUFFLE,
    description="Shuffled playlist with BasicShuffle",
    playlist_id="abc123",
    playlist_name="My Playlist",
)
```

---

## Step-by-Step Implementation

### Step 1: Wire up `SNAPSHOT_CREATE` in `shuffify/routes/snapshots.py`

Find the `create_snapshot` route handler. After the successful snapshot creation, add:

```python
log_activity(
    user_id=db_user.id,
    activity_type=ActivityType.SNAPSHOT_CREATE,
    description=f"Created manual snapshot for {playlist_name}",
    playlist_id=playlist_id,
    playlist_name=playlist_name,
)
```

Ensure `log_activity` and `ActivityType` are imported (check existing imports — `log_activity` is likely already imported from `shuffify.routes`; `ActivityType` from `shuffify.enums`).

### Step 2: Wire up `SNAPSHOT_RESTORE` in `shuffify/routes/snapshots.py`

Find the `restore_snapshot` route handler (line 108). After successful restoration:

```python
log_activity(
    user_id=db_user.id,
    activity_type=ActivityType.SNAPSHOT_RESTORE,
    description=f"Restored snapshot for {playlist_name}",
    playlist_id=playlist_id,
    playlist_name=playlist_name,
)
```

### Step 3: Wire up `SNAPSHOT_DELETE` in `shuffify/routes/snapshots.py`

Find the `delete_snapshot` route handler (line 202). After successful deletion:

```python
log_activity(
    user_id=db_user.id,
    activity_type=ActivityType.SNAPSHOT_DELETE,
    description=f"Deleted snapshot for {playlist_name}",
    playlist_id=playlist_id,
    playlist_name=playlist_name,
)
```

### Step 4: Wire up `SETTINGS_CHANGE` in `shuffify/routes/settings.py`

Find the `update_settings` route handler (line 112). After successful settings update:

```python
log_activity(
    user_id=db_user.id,
    activity_type=ActivityType.SETTINGS_CHANGE,
    description="Updated user settings",
)
```

Ensure `log_activity` and `ActivityType` are imported. Add imports if missing:
```python
from shuffify.routes import log_activity
from shuffify.enums import ActivityType
```

### Step 5: Wire up `WORKSHOP_SESSION_SAVE` in `shuffify/routes/workshop.py`

Find the workshop session save handler. After successful session save:

```python
log_activity(
    user_id=db_user.id,
    activity_type=ActivityType.WORKSHOP_SESSION_SAVE,
    description=f"Saved workshop session '{session_name}'",
)
```

`log_activity` and `ActivityType` are already imported in `workshop.py` (line 17).

### Step 6: Wire up `WORKSHOP_SESSION_DELETE` in `shuffify/routes/workshop.py`

Find the workshop session delete handler. After successful deletion:

```python
log_activity(
    user_id=db_user.id,
    activity_type=ActivityType.WORKSHOP_SESSION_DELETE,
    description=f"Deleted workshop session '{session_name}'",
)
```

### Step 7: Update tests

For each route file modified, add or update a test that verifies `log_activity` is called with the correct `ActivityType`. Use `@patch("shuffify.routes.<module>.log_activity")` and assert it was called.

---

## Verification Checklist

```bash
# 1. Lint
./venv/bin/python -m flake8 shuffify/routes/snapshots.py shuffify/routes/settings.py shuffify/routes/workshop.py

# 2. Route tests
./venv/bin/python -m pytest tests/routes/test_snapshot_routes.py tests/routes/test_settings_routes.py tests/routes/test_workshop_routes.py -v

# 3. Full test suite
./venv/bin/python -m pytest tests/ -v

# 4. Verify no unused ActivityType enums remain
# Search for each enum value in the codebase — all 6 should now have at least one usage site
```

---

## What NOT To Do

1. **Do NOT modify `ActivityLogService` or `ActivityType` enum.** Only add `log_activity()` calls in route handlers.
2. **Do NOT add logging in service layer.** Keep activity logging at the route level, consistent with all existing call sites.
3. **Do NOT make activity logging blocking.** The `log_activity()` wrapper already handles this — just use it.
4. **Do NOT log duplicate activities.** Check that each action point doesn't already have a `log_activity` call before adding one.
5. **Do NOT add activity logging to error paths.** Only log on successful operations.
