# Phase 01: Standardize DB Commit Handling
**Status:** 🔧 IN PROGRESS
**Started:** 2026-02-23

## Header

| Field | Value |
|-------|-------|
| **PR Title** | Standardize all DB commits to use `safe_commit()` helper |
| **Risk Level** | Low |
| **Effort** | Low (1-2 hours) |
| **Files Modified** | 6 |
| **Files Created** | 0 |
| **Files Deleted** | 0 |
| **Dependencies** | None |
| **Blocks** | Phase 03, Phase 06 |

### Files Modified

| File | Change Type |
|------|-------------|
| `shuffify/services/user_service.py` | Replace manual try/except commit with `safe_commit()` |
| `shuffify/services/activity_log_service.py` | Replace manual try/except commit with `safe_commit()` (special: non-blocking) |
| `shuffify/services/login_history_service.py` | Replace manual try/except in `record_logout()` with `safe_commit()` |
| `shuffify/services/scheduler_service.py` | Replace 4 commit patterns with `safe_commit()` |
| `shuffify/services/playlist_pair_service.py` | Replace 2 bare commits with `safe_commit()` |
| `shuffify/services/playlist_snapshot_service.py` | Replace 1 bare commit in `cleanup_old_snapshots()` with `safe_commit()` |

### Files Explicitly NOT Modified

| File | Reason |
|------|--------|
| `shuffify/services/job_executor_service.py` | All 4 commit sites (`_create_execution_record`, `_record_success`, `_record_failure`, token rotation) will be refactored in Phase 03. Do NOT touch. |
| `shuffify/routes/core.py` | The bare commit on line 265 is inside `job_executor_service`'s token-rotation flow invoked from the OAuth callback. This commit is tightly coupled to the token storage logic that Phase 03 will refactor. Do NOT touch. |

---

## Context

The codebase has a well-designed `safe_commit()` helper in `shuffify/services/base.py` (lines 16-50) that wraps `db.session.commit()` with:
- Automatic rollback on failure
- Structured logging (info on success, error with `exc_info` on failure)
- Re-raising a caller-specified exception class

Five service files already use `safe_commit()` correctly. However, 7 other files still use manual `try/except/rollback` blocks or bare `db.session.commit()` calls. This inconsistency means:
1. Some commit failures silently pass without rollback (bare commits)
2. Error logging format varies across files
3. More code to maintain with duplicated patterns

This phase standardizes all eligible commit sites to use `safe_commit()`, reducing code duplication and ensuring consistent error handling.

---

## Dependencies

- **Requires**: Nothing. This is a standalone refactor.
- **Blocks**: Phase 03 (Job Executor refactoring relies on these services having clean commit patterns), Phase 06 (final audit assumes all commits are standardized).

---

## Detailed Implementation Plan

### File 1: `shuffify/services/user_service.py`

**What changes**: The `upsert_from_spotify()` method (lines 84-171) has a manual try/except wrapping the entire method body. The commit on line 137 and the rollback/re-raise on lines 162-171 should be replaced with `safe_commit()`.

**Why this is safe**: `safe_commit()` raises the specified exception class on failure, which matches the existing behavior of raising `UserServiceError`. The outer try/except currently catches ALL exceptions (including non-commit ones like the query on line 85). After refactoring, the query and field-setting code stays outside any try/except (it doesn't need protection), and only the commit is wrapped via `safe_commit()`.

**Important note**: Lines 139-157 (auto-create default settings for new users) must remain AFTER the commit and OUTSIDE `safe_commit()`'s scope. The existing logic is: commit user first, then try to create settings. This ordering must be preserved.

#### Import change

**Before** (line 12):
```python
from shuffify.models.db import db, User
```

**After** (line 12):
```python
from shuffify.models.db import db, User
from shuffify.services.base import safe_commit
```

Add the import on a new line 13 (after the existing `db, User` import, before the blank line).

#### Method change

**Before** (`upsert_from_spotify`, lines 84-171):
```python
        try:
            user = User.query.filter_by(
                spotify_id=spotify_id
            ).first()

            if user:
                # Update existing user
                user.display_name = user_data.get(
                    "display_name"
                )
                user.email = user_data.get("email")
                user.profile_image_url = profile_image_url
                user.country = user_data.get("country")
                user.spotify_product = user_data.get(
                    "product"
                )
                user.spotify_uri = user_data.get("uri")
                user.last_login_at = now
                user.login_count = (
                    user.login_count or 0
                ) + 1
                user.updated_at = now
                is_new = False
                logger.info(
                    "Updated existing user: %s (%s)"
                    " — login #%d",
                    spotify_id,
                    user_data.get("display_name", "Unknown"),
                    user.login_count,
                )
            else:
                # Create new user
                user = User(
                    spotify_id=spotify_id,
                    display_name=user_data.get(
                        "display_name"
                    ),
                    email=user_data.get("email"),
                    profile_image_url=profile_image_url,
                    country=user_data.get("country"),
                    spotify_product=user_data.get("product"),
                    spotify_uri=user_data.get("uri"),
                    last_login_at=now,
                    login_count=1,
                )
                db.session.add(user)
                is_new = True
                logger.info(
                    "Created new user: %s (%s)",
                    spotify_id,
                    user_data.get("display_name", "Unknown"),
                )

            db.session.commit()

            # Auto-create default settings for new users
            if is_new:
                try:
                    from shuffify.services.user_settings_service import (
                        UserSettingsService,
                    )

                    UserSettingsService.get_or_create(
                        user.id
                    )
                except Exception as settings_err:
                    # Settings creation failure should NOT
                    # block login
                    logger.warning(
                        "Failed to create default settings "
                        "for user %s: %s",
                        spotify_id,
                        settings_err,
                    )

            return UpsertResult(user=user, is_new=is_new)

        except Exception as e:
            db.session.rollback()
            logger.error(
                "Failed to upsert user %s: %s",
                spotify_id,
                e,
                exc_info=True,
            )
            raise UserServiceError(
                f"Failed to save user record: {e}"
            )
```

**After**:
```python
        user = User.query.filter_by(
            spotify_id=spotify_id
        ).first()

        if user:
            # Update existing user
            user.display_name = user_data.get(
                "display_name"
            )
            user.email = user_data.get("email")
            user.profile_image_url = profile_image_url
            user.country = user_data.get("country")
            user.spotify_product = user_data.get(
                "product"
            )
            user.spotify_uri = user_data.get("uri")
            user.last_login_at = now
            user.login_count = (
                user.login_count or 0
            ) + 1
            user.updated_at = now
            is_new = False
            logger.info(
                "Updated existing user: %s (%s)"
                " — login #%d",
                spotify_id,
                user_data.get("display_name", "Unknown"),
                user.login_count,
            )
        else:
            # Create new user
            user = User(
                spotify_id=spotify_id,
                display_name=user_data.get(
                    "display_name"
                ),
                email=user_data.get("email"),
                profile_image_url=profile_image_url,
                country=user_data.get("country"),
                spotify_product=user_data.get("product"),
                spotify_uri=user_data.get("uri"),
                last_login_at=now,
                login_count=1,
            )
            db.session.add(user)
            is_new = True
            logger.info(
                "Created new user: %s (%s)",
                spotify_id,
                user_data.get("display_name", "Unknown"),
            )

        safe_commit(
            f"upsert user {spotify_id}",
            UserServiceError,
        )

        # Auto-create default settings for new users
        if is_new:
            try:
                from shuffify.services.user_settings_service import (
                    UserSettingsService,
                )

                UserSettingsService.get_or_create(
                    user.id
                )
            except Exception as settings_err:
                # Settings creation failure should NOT
                # block login
                logger.warning(
                    "Failed to create default settings "
                    "for user %s: %s",
                    spotify_id,
                    settings_err,
                )

        return UpsertResult(user=user, is_new=is_new)
```

**Key behavioral differences**: None. `safe_commit()` raises `UserServiceError` on failure (same as before). The rollback and logging are handled by `safe_commit()`. The only visible difference is that the success log message will say `"Success: upsert user <id>"` instead of the existing info log for the commit (which was implicit — the existing code had no explicit success log for the commit itself, just for the user update/create above it).

---

### File 2: `shuffify/services/activity_log_service.py`

**What changes**: The `log()` method (lines 51-77) has a manual try/except that returns `None` on failure instead of raising. This is intentional — the module docstring on lines 1-6 explicitly states all logging methods are non-blocking. `safe_commit()` raises on failure by default, so we **cannot** use it directly. Instead, we wrap `safe_commit()` in a try/except that catches the re-raised exception and returns `None`.

**Why this approach**: The alternative would be to add a `suppress=True` parameter to `safe_commit()`, but that changes the shared helper's API for a single caller. Wrapping is simpler and preserves the existing non-blocking contract.

#### Import change

**Before** (line 13):
```python
from shuffify.models.db import db, ActivityLog
```

**After** (line 13):
```python
from shuffify.models.db import db, ActivityLog
from shuffify.services.base import safe_commit
```

Add the import on a new line 14 (after the existing `db, ActivityLog` import, before the blank line).

#### Method change

**Before** (`log`, lines 51-77):
```python
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
                "Activity logged: %s for user %s",
                activity_type,
                user_id,
            )
            return activity
        except Exception as e:
            db.session.rollback()
            logger.warning(
                "Failed to log activity "
                "(%s for user %s): %s",
                activity_type,
                user_id,
                e,
            )
            return None
```

**After**:
```python
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
            safe_commit(
                f"log activity {activity_type} "
                f"for user {user_id}",
                ActivityLogError,
            )
            return activity
        except Exception:
            # Non-blocking: activity logging must never
            # propagate errors to callers
            return None
```

**Key behavioral differences**: The warning-level log message for failures is now produced by `safe_commit()` at ERROR level (since `safe_commit` uses `logger.error`). This is acceptable because:
1. `safe_commit()` logs with `exc_info=True`, which provides better diagnostics
2. The caller-specific context ("Failed to log activity...") is replaced by `safe_commit()`'s generic but still descriptive message
3. The outer `except Exception` catches the re-raised `ActivityLogError` and silently returns `None`, preserving the non-blocking contract

**Note**: The `logger.debug("Activity logged: ...")` line is removed because `safe_commit()` already logs `"Success: log activity {activity_type} for user {user_id}"` at INFO level on success.

---

### File 3: `shuffify/services/login_history_service.py`

**What changes**: The `record_logout()` method (lines 114-154) has a manual try/except around its commit. The file already imports `safe_commit` (line 15) but does not use it in `record_logout()`.

#### Import change

None needed. `safe_commit` is already imported on line 15.

#### Method change

**Before** (`record_logout`, lines 113-154):
```python
        try:
            query = LoginHistory.query.filter_by(
                user_id=user_id
            ).filter(
                LoginHistory.logged_out_at.is_(None)
            )

            if session_id:
                query = query.filter_by(session_id=session_id)

            # Get the most recent open login record
            entry = query.order_by(
                LoginHistory.logged_in_at.desc()
            ).first()

            if not entry:
                logger.debug(
                    f"No open login record found for "
                    f"user_id={user_id} to mark as logged out"
                )
                return False

            entry.logged_out_at = datetime.now(timezone.utc)
            db.session.commit()

            logger.info(
                f"Recorded logout for user_id={user_id}, "
                f"login_history_id={entry.id}"
            )
            return True

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to record logout for user_id="
                f"{user_id}: {e}",
                exc_info=True,
            )
            raise LoginHistoryError(
                f"Failed to record logout: {e}"
            )
```

**After**:
```python
        query = LoginHistory.query.filter_by(
            user_id=user_id
        ).filter(
            LoginHistory.logged_out_at.is_(None)
        )

        if session_id:
            query = query.filter_by(session_id=session_id)

        # Get the most recent open login record
        entry = query.order_by(
            LoginHistory.logged_in_at.desc()
        ).first()

        if not entry:
            logger.debug(
                f"No open login record found for "
                f"user_id={user_id} to mark as logged out"
            )
            return False

        entry.logged_out_at = datetime.now(timezone.utc)
        safe_commit(
            f"record logout for user_id={user_id}",
            LoginHistoryError,
        )
        return True
```

**Key behavioral differences**: None. `safe_commit()` raises `LoginHistoryError` on failure (same as before). The manual rollback, error logging, and re-raise are all handled by `safe_commit()`. The explicit info log for success (`"Recorded logout for user_id=..."`) is replaced by `safe_commit()`'s `"Success: record logout for user_id=..."` — functionally equivalent.

---

### File 4: `shuffify/services/scheduler_service.py`

**What changes**: Four methods have commit patterns that need standardization:
1. `create_schedule()` (line 123) — bare commit inside try/except
2. `update_schedule()` (line 181) — bare commit inside try/except
3. `delete_schedule()` (line 219) — bare commit inside try/except
4. `toggle_schedule()` (line 250) — bare commit inside try/except

#### Import change

**Before** (line 12):
```python
from shuffify.models.db import db, Schedule
```

**After** (line 12):
```python
from shuffify.models.db import db, Schedule
from shuffify.services.base import safe_commit
```

Add the import on a new line 13 (after the existing `db, Schedule` import, before the blank line).

#### Method 1: `create_schedule` (lines 108-142)

**Before**:
```python
        try:
            schedule = Schedule(
                user_id=user_id,
                job_type=job_type,
                target_playlist_id=target_playlist_id,
                target_playlist_name=target_playlist_name,
                source_playlist_ids=source_playlist_ids or [],
                algorithm_name=algorithm_name,
                algorithm_params=algorithm_params or {},
                schedule_type=schedule_type,
                schedule_value=schedule_value,
                is_enabled=True,
            )

            db.session.add(schedule)
            db.session.commit()

            logger.info(
                f"Created schedule {schedule.id} for user "
                f"{user_id}: {job_type} on "
                f"{target_playlist_name}"
            )
            return schedule

        except ScheduleLimitError:
            raise
        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to create schedule: {e}",
                exc_info=True,
            )
            raise ScheduleError(
                f"Failed to create schedule: {e}"
            )
```

**After**:
```python
        schedule = Schedule(
            user_id=user_id,
            job_type=job_type,
            target_playlist_id=target_playlist_id,
            target_playlist_name=target_playlist_name,
            source_playlist_ids=source_playlist_ids or [],
            algorithm_name=algorithm_name,
            algorithm_params=algorithm_params or {},
            schedule_type=schedule_type,
            schedule_value=schedule_value,
            is_enabled=True,
        )

        db.session.add(schedule)
        safe_commit(
            f"create schedule for user {user_id}: "
            f"{job_type} on {target_playlist_name}",
            ScheduleError,
        )
        return schedule
```

**Note**: The `except ScheduleLimitError: raise` clause on lines 132-133 is no longer needed because `ScheduleLimitError` is raised on line 102 (before the commit block), so it is outside the refactored scope. The try/except that previously caught it was wrapping the entire creation block unnecessarily.

#### Method 2: `update_schedule` (lines 176-194)

**Before**:
```python
        try:
            for key, value in kwargs.items():
                if key in allowed_fields:
                    setattr(schedule, key, value)

            db.session.commit()
            logger.info(f"Updated schedule {schedule_id}")
            return schedule

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to update schedule "
                f"{schedule_id}: {e}",
                exc_info=True,
            )
            raise ScheduleError(
                f"Failed to update schedule: {e}"
            )
```

**After**:
```python
        for key, value in kwargs.items():
            if key in allowed_fields:
                setattr(schedule, key, value)

        safe_commit(
            f"update schedule {schedule_id}",
            ScheduleError,
        )
        return schedule
```

#### Method 3: `delete_schedule` (lines 211-232)

**Before**:
```python
        try:
            from shuffify.models.db import JobExecution

            JobExecution.query.filter_by(
                schedule_id=schedule_id
            ).delete()

            db.session.delete(schedule)
            db.session.commit()

            logger.info(f"Deleted schedule {schedule_id}")

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to delete schedule "
                f"{schedule_id}: {e}",
                exc_info=True,
            )
            raise ScheduleError(
                f"Failed to delete schedule: {e}"
            )
```

**After**:
```python
        from shuffify.models.db import JobExecution

        JobExecution.query.filter_by(
            schedule_id=schedule_id
        ).delete()

        db.session.delete(schedule)
        safe_commit(
            f"delete schedule {schedule_id}",
            ScheduleError,
        )
```

#### Method 4: `toggle_schedule` (lines 249-264)

**Before**:
```python
        schedule.is_enabled = not schedule.is_enabled

        try:
            db.session.commit()
            logger.info(
                f"Schedule {schedule_id} "
                f"{'enabled' if schedule.is_enabled else 'disabled'}"
            )
            return schedule
        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to toggle schedule "
                f"{schedule_id}: {e}"
            )
            raise ScheduleError(
                f"Failed to toggle schedule: {e}"
            )
```

**After**:
```python
        schedule.is_enabled = not schedule.is_enabled
        safe_commit(
            f"toggle schedule {schedule_id} to "
            f"{'enabled' if schedule.is_enabled else 'disabled'}",
            ScheduleError,
        )
        return schedule
```

---

### File 5: `shuffify/services/playlist_pair_service.py`

**What changes**: Two bare `db.session.commit()` calls with no error handling:
1. `create_pair()` (line 59) — bare commit
2. `delete_pair()` (line 98) — bare commit

#### Import change

**Before** (line 7):
```python
from shuffify.models.db import db, PlaylistPair
```

**After** (line 7):
```python
from shuffify.models.db import db, PlaylistPair
from shuffify.services.base import safe_commit
```

Add the import on a new line 8 (after the existing `db, PlaylistPair` import, before the blank line).

#### Method 1: `create_pair` (lines 58-66)

**Before**:
```python
        db.session.add(pair)
        db.session.commit()
        logger.info(
            "Created playlist pair %s -> %s for user %s",
            production_playlist_id,
            archive_playlist_id,
            user_id,
        )
        return pair
```

**After**:
```python
        db.session.add(pair)
        safe_commit(
            f"create playlist pair "
            f"{production_playlist_id} -> "
            f"{archive_playlist_id} for user {user_id}",
            PlaylistPairError,
        )
        return pair
```

**Note**: The explicit `logger.info(...)` call is removed because `safe_commit()` logs `"Success: create playlist pair ..."` at INFO level.

#### Method 2: `delete_pair` (lines 97-103)

**Before**:
```python
        db.session.delete(pair)
        db.session.commit()
        logger.info(
            "Deleted playlist pair for %s (user %s)",
            production_playlist_id,
            user_id,
        )
```

**After**:
```python
        db.session.delete(pair)
        safe_commit(
            f"delete playlist pair for "
            f"{production_playlist_id} (user {user_id})",
            PlaylistPairError,
        )
```

---

### File 6: `shuffify/services/playlist_snapshot_service.py`

**What changes**: The `cleanup_old_snapshots()` method (lines 244-262) has a bare `db.session.commit()` inside a try/except that returns 0 on failure. This file already imports `safe_commit` on line 14.

#### Import change

None needed. `safe_commit` is already imported on line 14.

#### Method change: `cleanup_old_snapshots` (lines 244-262)

**Before**:
```python
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
```

**After**:
```python
        for snapshot in to_delete:
            db.session.delete(snapshot)
            deleted_count += 1

        try:
            safe_commit(
                f"cleanup {deleted_count} old snapshots "
                f"for user {user_id}, "
                f"playlist {playlist_id}",
                PlaylistSnapshotError,
            )
        except PlaylistSnapshotError:
            return 0

        return deleted_count
```

**Key behavioral difference**: The method currently returns 0 on failure, which is a non-blocking pattern (cleanup is best-effort). We wrap `safe_commit()` in a try/except that catches the `PlaylistSnapshotError` it raises, and returns 0 — preserving the existing non-blocking behavior. The `safe_commit()` call handles the rollback and error logging internally before re-raising, so both rollback and logging happen before we catch and suppress.

---

## Test Plan

### Existing Tests

All 1220 existing tests should pass unchanged. The behavioral contracts of every method are preserved:
- Methods that raised specific exceptions on failure still raise those same exceptions
- Methods that returned `None` or `0` on failure still return those values
- Methods that logged on success still log (via `safe_commit()` instead of manual calls)

### Verification Command

```bash
pytest tests/ -v
```

All tests must pass with 0 failures. No new tests are needed because:
1. No new behavior is introduced
2. No method signatures change
3. No return types change
4. No exception types change

### Manual Verification

After implementation, grep for any remaining bare `db.session.commit()` calls in the modified files to confirm none were missed:

```bash
grep -n "db.session.commit()" \
  shuffify/services/user_service.py \
  shuffify/services/activity_log_service.py \
  shuffify/services/login_history_service.py \
  shuffify/services/scheduler_service.py \
  shuffify/services/playlist_pair_service.py \
  shuffify/services/playlist_snapshot_service.py
```

Expected output: **no matches**. Every commit in these 6 files should now go through `safe_commit()`.

---

## Documentation Updates

### CHANGELOG.md

Add under `## [Unreleased]`:

```markdown
### Changed
- **DB Commit Standardization** - Replaced 11 manual commit patterns across 6 service files with the shared `safe_commit()` helper for consistent error handling, rollback, and logging
```

### Inline Comments

No inline comment changes needed. The code is self-documenting with `safe_commit()` calls that include descriptive operation names.

---

## Stress Testing & Edge Cases

### Edge Case 1: Activity log non-blocking contract

The `activity_log_service.py` `log()` method must NEVER propagate exceptions. After refactoring, `safe_commit()` raises `ActivityLogError`, which is caught by the outer `except Exception` and returns `None`. Verify this by confirming the outer try/except is still present.

### Edge Case 2: Snapshot cleanup non-blocking contract

The `playlist_snapshot_service.py` `cleanup_old_snapshots()` method must return `0` on failure, not raise. After refactoring, `safe_commit()` raises `PlaylistSnapshotError`, which is caught and returns `0`. Verify this by confirming the try/except around `safe_commit()` is present.

### Edge Case 3: Scheduler `create_schedule` and `ScheduleLimitError`

The `ScheduleLimitError` is raised on line 102, before the commit block. Removing the `except ScheduleLimitError: raise` clause (lines 132-133) is safe because that exception is raised outside the refactored scope. Verify by reading the control flow: limit check happens first (raises if exceeded), then Schedule is created and committed.

### Edge Case 4: Login history `record_logout` query failure

The existing code wraps the query + commit in a single try/except. After refactoring, the query runs outside any try/except, and only the commit is wrapped by `safe_commit()`. If the query itself fails (e.g., database connection error), the exception will propagate as an unhandled `SQLAlchemyError`. This matches the pattern used by `record_login()` (which also lets query errors propagate). The calling code in `routes/core.py` (line 349) already wraps `record_logout()` in a try/except, so this is safe.

---

## Verification Checklist

1. [ ] Import `safe_commit` added to `user_service.py`, `activity_log_service.py`, `scheduler_service.py`, `playlist_pair_service.py`
2. [ ] Import NOT needed for `login_history_service.py` and `playlist_snapshot_service.py` (already imported)
3. [ ] `user_service.py`: outer try/except removed, `safe_commit()` replaces `db.session.commit()` + manual rollback
4. [ ] `activity_log_service.py`: `safe_commit()` inside existing try/except, outer except still returns `None`
5. [ ] `login_history_service.py`: outer try/except removed from `record_logout()`, `safe_commit()` replaces commit + rollback
6. [ ] `scheduler_service.py`: all 4 methods (`create`, `update`, `delete`, `toggle`) use `safe_commit()`, all manual try/except removed
7. [ ] `playlist_pair_service.py`: both methods (`create_pair`, `delete_pair`) use `safe_commit()`, manual `logger.info` calls removed
8. [ ] `playlist_snapshot_service.py`: `cleanup_old_snapshots()` uses `safe_commit()` inside try/except that returns 0 on failure
9. [ ] `job_executor_service.py` is NOT modified (reserved for Phase 03)
10. [ ] `routes/core.py` is NOT modified (reserved for Phase 03)
11. [ ] Run `flake8 shuffify/` — 0 errors
12. [ ] Run `pytest tests/ -v` — all tests pass
13. [ ] Run grep to confirm no bare `db.session.commit()` in modified files

---

## "What NOT To Do" Section

1. **Do NOT modify `job_executor_service.py`**. Its 4 commit sites (`_create_execution_record`, `_record_success`, `_record_failure`, token rotation in `_get_spotify_api`) are part of a tightly coupled execution flow that Phase 03 will refactor holistically. Touching them here creates merge conflicts and risks breaking the execution pipeline.

2. **Do NOT modify `routes/core.py`**. The bare commit on line 265 (`_db.session.commit()`) is for token storage during OAuth callback. This is coupled to the token service flow that Phase 03 addresses. It also uses a local alias `_db` (not the module-level `db`), making it a different import pattern.

3. **Do NOT add a `suppress=True` parameter to `safe_commit()`**. The two non-blocking callers (activity log, snapshot cleanup) are better served by wrapping `safe_commit()` in their own try/except. Changing the shared helper's API adds complexity for a marginal benefit.

4. **Do NOT remove the `except Exception` in `activity_log_service.py`'s `log()` method**. The method's contract is non-blocking — it must never raise. The outer try/except is essential.

5. **Do NOT change the log level of `safe_commit()`**. It logs at INFO on success and ERROR on failure. Some callers previously used WARNING or DEBUG, but standardizing on INFO/ERROR is the correct choice for a database commit utility.

6. **Do NOT reorder the settings-creation code in `user_service.py`**. The auto-create block (lines 139-157) must remain AFTER the `safe_commit()` call. Moving it before the commit would mean settings are created for a user that hasn't been committed yet.

7. **Do NOT wrap query operations in `safe_commit()`**. Only `db.session.commit()` calls should be replaced. Query failures are a different category of error with different handling requirements.
