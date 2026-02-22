# Phase 1: Route Infrastructure & Boilerplate Cleanup -- Detailed Remediation Plan

**Status**: ✅ COMPLETE
**Started**: 2026-02-19
**Completed**: 2026-02-19
**PR**: #83

## PR Metadata

| Field | Value |
|-------|-------|
| **PR Title** | `Refactor: Route infrastructure decorators and boilerplate cleanup` |
| **Branch Name** | `implement/route-infrastructure-cleanup` |
| **Risk Level** | Medium -- touches many route files but changes are mechanical and testable |
| **Estimated Effort** | 3-4 hours |
| **Files Modified** | `shuffify/routes/__init__.py` + 7 route modules (of 10 total) |
| **Dependencies** | None (this is Phase 1) |
| **Blocks** | Phase 4 (route tests should test cleaned-up routes) |

---

## Part A: Create `@require_auth_and_db` Decorator

### Goal

Eliminate the repeated 3-check boilerplate (auth, DB availability, DB user lookup) that appears **28 times** across 7 route files. Replace it with a single decorator that injects `client` and `user` as keyword arguments.

### Current Pattern (Repeated 28 times)

There are three variant forms of this boilerplate in the codebase:

**Variant 1 -- Full 3-check with `get_db_user()` (used in snapshots.py, playlist_pairs.py, raid_panel.py line 273)**

Found at: `/Users/chris/Projects/shuffify/shuffify/routes/snapshots.py` lines 37-52, 71-101, 131-144, 164-177, 270-282; `/Users/chris/Projects/shuffify/shuffify/routes/playlist_pairs.py` lines 43-52, 72-81, 150-159, 182-191, 239-248, 301-310; `/Users/chris/Projects/shuffify/shuffify/routes/raid_panel.py` lines 266-275.

```python
client = require_auth()
if not client:
    return json_error("Please log in first.", 401)

if not is_db_available():
    return json_error("Database is unavailable.", 503)

user = get_db_user()
if not user:
    return json_error("User data not found in session.", 401)
```

**Variant 2 -- Full 3-check with `session.get("user_data")` (used in upstream_sources.py, raid_panel.py lines 49-60, 72-83, 144-155, 201-212)**

Found at: `/Users/chris/Projects/shuffify/shuffify/routes/upstream_sources.py` lines 29-41, 58-80, 136-148; `/Users/chris/Projects/shuffify/shuffify/routes/raid_panel.py` lines 51-60, 74-83, 146-155, 203-212.

```python
sp = require_auth()
if not sp:
    return json_error("Authentication required", 401)

if not is_db_available():
    return json_error("Database unavailable", 503)

user_data = session.get("user_data")
if not user_data or "id" not in user_data:
    return json_error("User not found", 404)
```

**Variant 3 -- Auth + DB user check, NO `is_db_available()` check (used in schedules.py)**

Found at: `/Users/chris/Projects/shuffify/shuffify/routes/schedules.py` lines 105-113, 203-211, 283-291, 325-333, 392-400, 431-439, 453-462.

```python
client = require_auth()
if not client:
    return json_error("Please log in first.", 401)

db_user = get_db_user()
if not db_user:
    return json_error("User not found. Please log in again.", 401)
```

### Where to Write the Decorator

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/__init__.py`
**Insert after**: Line 99 (after the `json_success()` function definition, before `get_db_user()`)

### Exact Code to Add

```python
import functools

def require_auth_and_db(f):
    """
    Decorator that enforces authentication and database availability.

    Checks performed in order:
    1. require_auth() -- returns 401 if not authenticated
    2. is_db_available() -- returns 503 if DB is down
    3. get_db_user() -- returns 401 if user not found in DB

    Injects `client` (SpotifyClient) and `user` (User model)
    as keyword arguments to the wrapped function.

    Usage:
        @main.route("/endpoint")
        @require_auth_and_db
        def my_route(client=None, user=None):
            # client and user are guaranteed non-None here
            ...
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        client = require_auth()
        if not client:
            return json_error("Please log in first.", 401)

        from shuffify import is_db_available
        if not is_db_available():
            return json_error(
                "Database is unavailable.", 503
            )

        user = get_db_user()
        if not user:
            return json_error("User not found.", 401)

        kwargs["client"] = client
        kwargs["user"] = user
        return f(*args, **kwargs)

    return decorated_function
```

### Import Changes Required in `__init__.py`

**Line 1 area**: Add `import functools` to the top of the file. Place it right after the existing `import logging` on line 19. Since `functools` is a stdlib module, it goes with the other stdlib imports.

Note: `is_db_available` is imported lazily inside the decorator (as `from shuffify import is_db_available`) to avoid circular imports. The existing route modules already do this same lazy import pattern (see `/Users/chris/Projects/shuffify/shuffify/routes/snapshots.py` line 25: `from shuffify import is_db_available`).

### Updated Exports

The decorator needs to be importable by route modules. It is automatically accessible since route modules import directly from the `shuffify.routes` package via `from shuffify.routes import ...`. No changes to `__all__` are needed because the package does not define `__all__`.

### Key Design Decision: Why Import `is_db_available` Inside the Decorator

The `is_db_available` function lives in `shuffify/__init__.py`. If you import it at the top of `shuffify/routes/__init__.py`, you create a circular import because `shuffify/__init__.py` imports the routes package at app creation time. The existing routes already import it lazily (e.g., `from shuffify import is_db_available` at the module top level in the route modules, which works because by the time the route functions execute, the app is fully initialized). Inside the decorator, a deferred import is the cleanest approach.

---

## Part B: Create `log_activity_nonblocking()` Helper

### Goal

Replace the repeated try/except-wrapped activity logging pattern that appears **~15 times** across route modules. The current pattern has two sub-variants:

**Sub-variant 1 -- Routes that already have `user` (or `db_user`) from `get_db_user()`:**

Found in: `schedules.py` (lines 169-190, 251-270, 303-314, 361-379, 407-420), `raid_panel.py` (lines 309-320), `playlist_pairs.py` (lines 118-126, 165-170, 212-220, 272-280).

```python
try:
    ActivityLogService.log(
        user_id=db_user.id,
        activity_type=ActivityType.SOME_ACTION,
        description="...",
    )
except Exception:
    pass
```

**Sub-variant 2 -- Routes that look up the user manually via `UserService.get_by_spotify_id()`:**

Found in: `upstream_sources.py` (lines 95-120, 156-173), `workshop.py` (lines 218-244, 570-592, 728-745), `shuffle.py` (lines 136-156), `raid_panel.py` (lines 108-124, 173-185, 227-245), `core.py` (lines 301-314, 359-374).

```python
try:
    db_user = UserService.get_by_spotify_id(spotify_id)
    if db_user:
        ActivityLogService.log(
            user_id=db_user.id,
            activity_type=ActivityType.SOME_ACTION,
            description="...",
        )
except Exception:
    pass
```

### Where to Write the Helper

**File**: `/Users/chris/Projects/shuffify/shuffify/routes/__init__.py`
**Insert after**: The `get_db_user()` function (currently ending at line 113), before the route module import block that starts at line 116.

### Exact Code to Add

```python
def log_activity(
    user_id: int,
    activity_type,
    description: str,
    **kwargs,
) -> None:
    """
    Log a user activity. Never raises -- failures are logged as warnings.

    This is a convenience wrapper around ActivityLogService.log() that
    silences exceptions so activity logging never disrupts route handlers.

    Args:
        user_id: The internal database user ID.
        activity_type: An ActivityType enum value.
        description: Human-readable description of the action.
        **kwargs: Additional keyword args passed to ActivityLogService.log()
            (e.g., playlist_id, playlist_name, metadata).
    """
    try:
        from shuffify.services.activity_log_service import (
            ActivityLogService,
        )
        ActivityLogService.log(
            user_id=user_id,
            activity_type=activity_type,
            description=description,
            **kwargs,
        )
    except Exception as e:
        logger.warning("Activity logging failed: %s", e)
```

### Design Decisions

1. **Name**: `log_activity` (not `log_activity_nonblocking`) -- shorter, and the docstring communicates the non-raising contract. The term "nonblocking" might imply async, which this is not.

2. **Accepts `user_id` directly, not `spotify_id`**: Since Part A's decorator already resolves the DB user, all routes that adopt `@require_auth_and_db` will have `user.id` available. Routes that still need to look up by spotify_id (like `core.py`'s callback/logout) can continue to do their own lookups and pass `user_id` to this helper.

3. **Lazy import of `ActivityLogService`**: To avoid expanding the import block at the top of `__init__.py` and to keep the import structure consistent. The `ActivityLogService` import from `shuffify.services` could create import-time coupling if placed at the module top.

4. **Logs warnings instead of silent `pass`**: The current pattern uses bare `except Exception: pass`, which swallows errors invisibly. The new helper logs a warning, improving debuggability while remaining non-disruptive.

---

## Part C: Apply `@require_auth_and_db` Decorator Across Route Modules

### Summary of Changes by File

For each file below, the specific routes to convert are listed with their line numbers. "Lines removed" counts the boilerplate lines being deleted. "Signature change" shows the new function signature.

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/snapshots.py`

**5 routes to convert.** All 5 use Variant 1 (full 3-check with `get_db_user()`).

**Import changes** (line 11-17):
- ADD: `require_auth_and_db` to the import from `shuffify.routes`
- REMOVE: `require_auth`, `get_db_user` (no longer used directly)
- REMOVE: `from shuffify import is_db_available` (line 25 -- no longer needed)

```python
# BEFORE (lines 11-17, 25):
from shuffify.routes import (
    main,
    require_auth,
    json_error,
    json_success,
    get_db_user,
)
from shuffify import is_db_available

# AFTER:
from shuffify.routes import (
    main,
    require_auth_and_db,
    json_error,
    json_success,
)
```

**Route 1: `list_snapshots`** (lines 32-63)

```python
# BEFORE (lines 35-53):
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
    ...

# AFTER:
@main.route(
    "/playlist/<playlist_id>/snapshots", methods=["GET"]
)
@require_auth_and_db
def list_snapshots(playlist_id, client=None, user=None):
    """List all snapshots for a playlist."""
    limit = request.args.get("limit", 20, type=int)
    ...
```

**Route 2: `create_manual_snapshot`** (lines 66-123)
- Remove lines 71-81 (auth/db boilerplate) and lines 97-101 (second `get_db_user()` call)
- Signature: `def create_manual_snapshot(playlist_id, client=None, user=None):`

**Route 3: `view_snapshot`** (lines 126-155)
- Remove lines 131-144 (auth/db boilerplate)
- Signature: `def view_snapshot(snapshot_id, client=None, user=None):`

**Route 4: `restore_snapshot`** (lines 158-262)
- Remove lines 164-177 (auth/db boilerplate)
- Signature: `def restore_snapshot(snapshot_id, client=None, user=None):`

**Route 5: `delete_snapshot`** (lines 265-293)
- Remove lines 270-282 (auth/db boilerplate)
- Signature: `def delete_snapshot(snapshot_id, client=None, user=None):`

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/playlist_pairs.py`

**6 routes to convert.** All use Variant 1 pattern with variable name `sp` instead of `client`.

**Import changes** (lines 13-19):
- ADD: `require_auth_and_db`
- REMOVE: `require_auth`, `get_db_user`
- REMOVE: `from shuffify import is_db_available` (line 12)

**Route 1: `get_pair`** (lines 38-64)
- Remove lines 43-52. Note: the variable was called `sp`, now it is `client`.
- Signature: `def get_pair(playlist_id, client=None, user=None):`

**Route 2: `create_pair`** (lines 67-142)
- Remove lines 72-81.
- IMPORTANT: Line 99 uses `sp._sp`. After conversion, this becomes `client._sp`. Search for all `sp._sp` in this file and replace with `client._sp`.
- Signature: `def create_pair(playlist_id, client=None, user=None):`

**Route 3: `delete_pair`** (lines 145-173)
- Remove lines 150-159.
- Signature: `def delete_pair(playlist_id, client=None, user=None):`

**Route 4: `archive_tracks`** (lines 176-230)
- Remove lines 182-191.
- Line 210 uses `sp._sp` -- becomes `client._sp`.
- Signature: `def archive_tracks(playlist_id, client=None, user=None):`

**Route 5: `unarchive_tracks`** (lines 233-292)
- Remove lines 239-248.
- Line 267 uses `sp._sp` -- becomes `client._sp`.
- Signature: `def unarchive_tracks(playlist_id, client=None, user=None):`

**Route 6: `list_archive_tracks`** (lines 295-363)
- Remove lines 301-310.
- Line 319 uses `sp._sp` -- becomes `client._sp`.
- Signature: `def list_archive_tracks(playlist_id, client=None, user=None):`

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/upstream_sources.py`

**3 routes to convert.** All use Variant 2 (session-based `spotify_id` lookup instead of `get_db_user()`).

**IMPORTANT subtlety**: The upstream_sources routes use `spotify_id` (not `user.id`) when calling `UpstreamSourceService.list_sources()` and `UpstreamSourceService.add_source()`. With the decorator, you have `user` which has a `spotify_id` attribute. Replace `spotify_id` local variable with `user.spotify_id`.

**Import changes** (lines 9, 18):
- ADD: `require_auth_and_db` to import from `shuffify.routes`
- REMOVE: `require_auth` from import
- REMOVE: `from flask import session` (session no longer used in routes 1 and 3; route 2 no longer uses session for user_data)
- REMOVE: `from shuffify import is_db_available` (line 18)
- REMOVE: `UserService` from `shuffify.services` import (line 15) -- only used for activity logging lookups, which will now use `user.id` directly

```python
# BEFORE (line 9):
from flask import session, request, jsonify

# AFTER:
from flask import request, jsonify
```

**Route 1: `list_upstream_sources`** (lines 23-49)
- Remove lines 29-41 (auth + db_available + session-based spotify_id)
- Replace `spotify_id` with `user.spotify_id` on line 44
- Signature: `def list_upstream_sources(playlist_id, client=None, user=None):`

**Route 2: `add_upstream_source`** (lines 52-127)
- Remove lines 58-63 (auth + db_available) and lines 75-80 (session spotify_id extraction)
- Replace `spotify_id` with `user.spotify_id` on line 83
- Signature: `def add_upstream_source(playlist_id, client=None, user=None):`

**Route 3: `delete_upstream_source`** (lines 130-179)
- Remove lines 136-148 (auth + db_available + session spotify_id)
- Replace `spotify_id` with `user.spotify_id` on line 152
- Signature: `def delete_upstream_source(source_id, client=None, user=None):`

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/raid_panel.py`

**5 routes to convert.** Routes use a mix of Variant 2 (session-based) and Variant 1 (`get_db_user`).

**Import changes** (lines 14-20):
- ADD: `require_auth_and_db`
- REMOVE: `require_auth`, `get_db_user`
- REMOVE: `from flask import request, session` -- change to `from flask import request` (session no longer used in 4 of 5 routes; the 5th, `raid_schedule_toggle`, already uses `get_db_user` not session)
- REMOVE: `from shuffify import is_db_available` (line 13)

**Route 1: `raid_status`** (lines 45-65)
- Remove lines 51-60. Note: this route uses `user_data["id"]` for `RaidSyncService.get_raid_status()`. After conversion, use `user.spotify_id`.
- Signature: `def raid_status(playlist_id, client=None, user=None):`

**Route 2: `raid_watch`** (lines 68-137)
- Remove lines 74-83 (auth + db + session user_data check).
- Line 96: `user_data["id"]` becomes `user.spotify_id`.
- Lines 108-124: The nested `try: user = get_db_user()` block for activity logging already has `user` from the decorator. Simplify to direct `log_activity()` call.
- Signature: `def raid_watch(playlist_id, client=None, user=None):`

**Route 3: `raid_unwatch`** (lines 140-194)
- Remove lines 146-155.
- Line 167: `user_data["id"]` becomes `user.spotify_id`.
- Lines 173-185: Simplify activity logging.
- Signature: `def raid_unwatch(playlist_id, client=None, user=None):`

**Route 4: `raid_now`** (lines 197-257)
- Remove lines 203-212.
- Line 221: `user_data["id"]` becomes `user.spotify_id`.
- Lines 227-245: Simplify activity logging.
- Signature: `def raid_now(playlist_id, client=None, user=None):`

**Route 5: `raid_schedule_toggle`** (lines 260-330)
- Remove lines 266-275 (already uses `get_db_user()`).
- Signature: `def raid_schedule_toggle(playlist_id, client=None, user=None):`

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/schedules.py`

**7 routes to convert** (of 8 total; the `schedules` page-rendering route at line 50 uses `is_authenticated()` + redirect pattern for HTML pages, NOT the JSON API pattern, so leave it alone).

**IMPORTANT**: `schedules.py` routes use Variant 3 -- they do NOT check `is_db_available()`. After converting to `@require_auth_and_db`, these routes will gain the DB availability check. This is a **behavior change** but a desirable one: if the database is down, the schedule routes should return 503 instead of crashing when `get_db_user()` fails in some unpredictable way.

**Import changes** (lines 18-26):
- ADD: `require_auth_and_db` to import from `shuffify.routes`
- REMOVE: `require_auth` (no longer used directly in JSON routes)
- KEEP: `is_authenticated`, `clear_session_and_show_login`, `get_db_user` (still used by the HTML `schedules()` route at line 50)
- Actually: `get_db_user` is still used in the HTML `schedules()` route on line 62. KEEP it.
- `json_error`, `json_success` -- KEEP

**Route 1: `create_schedule`** (lines 102-195)
- Remove lines 105-113. Note: line 115 checks `db_user.encrypted_refresh_token` -- now use `user.encrypted_refresh_token`.
- Rename `db_user` to `user` throughout the function body (lines 115, 129, 163, 170-188).
- Signature: `def create_schedule(client=None, user=None):`

**Route 2: `update_schedule`** (lines 198-275)
- Remove lines 203-211.
- Rename `db_user` to `user` throughout.
- Signature: `def update_schedule(schedule_id, client=None, user=None):`

**Route 3: `delete_schedule`** (lines 278-316)
- Remove lines 283-291.
- Rename `db_user` to `user` throughout.
- Signature: `def delete_schedule(schedule_id, client=None, user=None):`

**Route 4: `toggle_schedule`** (lines 319-384)
- Remove lines 325-333.
- Rename `db_user` to `user` throughout.
- Signature: `def toggle_schedule(schedule_id, client=None, user=None):`

**Route 5: `run_schedule_now`** (lines 387-425)
- Remove lines 392-400.
- Rename `db_user` to `user` throughout.
- Signature: `def run_schedule_now(schedule_id, client=None, user=None):`

**Route 6: `schedule_history`** (lines 428-445)
- Remove lines 431-439.
- Rename `db_user` to `user` throughout.
- Signature: `def schedule_history(schedule_id, client=None, user=None):`

**Route 7: `rotation_status`** (lines 448-496)
- Remove lines 453-462.
- Rename `db_user` to `user` throughout.
- Signature: `def rotation_status(playlist_id, client=None, user=None):`

---

### Files NOT converted (and why)

| File | Reason |
|------|--------|
| `/Users/chris/Projects/shuffify/shuffify/routes/core.py` | Routes are HTML page renders (redirect-based auth), OAuth callbacks, or the health endpoint. None follow the JSON API auth+db pattern. |
| `/Users/chris/Projects/shuffify/shuffify/routes/playlists.py` | Routes only check `require_auth()` -- they do NOT require database access. |
| `/Users/chris/Projects/shuffify/shuffify/routes/shuffle.py` | The `shuffle` route uses `is_db_available()` conditionally (for auto-snapshots), not as a gate. The `undo` route only checks auth. |
| `/Users/chris/Projects/shuffify/shuffify/routes/settings.py` | The GET route uses `is_authenticated()` + redirect. The POST route uses `is_authenticated()` without DB check. |
| `/Users/chris/Projects/shuffify/shuffify/routes/workshop.py` | Mixed patterns: the workshop page uses `is_authenticated()` + redirect; workshop_preview_shuffle/search/load-external only need auth; session persistence routes use session-based `spotify_id` and `WorkshopSessionService` with `spotify_id` (not `user.id`). These would need a different decorator or manual conversion. Leave for a future phase. |

---

## Part D: Apply `log_activity()` Helper

### Routes Where the Helper Applies

Replace the verbose try/except activity logging blocks with the `log_activity()` helper.

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/upstream_sources.py`

**2 occurrences.**

**Occurrence 1** -- `add_upstream_source` (lines 95-120):
```python
# BEFORE:
        # Log activity (non-blocking)
        try:
            db_user = UserService.get_by_spotify_id(
                spotify_id
            )
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
                        "source_playlist_id": (
                            source_playlist_id
                        ),
                        "source_type": data.get(
                            "source_type", "external"
                        ),
                    },
                )
        except Exception:
            pass

# AFTER (with decorator providing `user`):
        log_activity(
            user_id=user.id,
            activity_type=ActivityType.UPSTREAM_SOURCE_ADD,
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
            },
        )
```

**Occurrence 2** -- `delete_upstream_source` (lines 156-173): Same pattern.

**Import change**: Add `log_activity` to imports from `shuffify.routes`. Remove `ActivityLogService`, `UserService` from imports since they are no longer used directly.

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/playlist_pairs.py`

**4 occurrences** (lines 118-126, 165-170, 212-220, 272-280).

These routes already have `user.id` (from `get_db_user()` currently, and from the decorator after Part C). They call `ActivityLogService.log()` directly without the `UserService.get_by_spotify_id()` lookup -- but they lack the try/except wrapper in some places. Convert them all to use `log_activity()` for consistency and safety.

```python
# BEFORE (create_pair, line 118):
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.PAIR_CREATE,
            description=(
                f"Paired with archive '{archive_name}'"
            ),
            playlist_id=playlist_id,
            playlist_name=req.production_playlist_name,
        )

# AFTER:
        log_activity(
            user_id=user.id,
            activity_type=ActivityType.PAIR_CREATE,
            description=(
                f"Paired with archive '{archive_name}'"
            ),
            playlist_id=playlist_id,
            playlist_name=req.production_playlist_name,
        )
```

**Import change**: Add `log_activity` to imports. Remove `from shuffify.services.activity_log_service import ActivityLogService` (line 26-27) since it is no longer used directly.

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/schedules.py`

**5 occurrences** (lines 169-190, 251-270, 303-314, 361-379, 407-420).

All already have `db_user.id` (which becomes `user.id` after Part C).

```python
# BEFORE (create_schedule, lines 168-190):
    # Log activity (non-blocking)
    try:
        ActivityLogService.log(
            user_id=db_user.id,
            activity_type=ActivityType.SCHEDULE_CREATE,
            description=(
                f"Created {schedule.job_type} schedule "
                f"for '{schedule.target_playlist_name}'"
            ),
            playlist_id=schedule.target_playlist_id,
            playlist_name=(
                schedule.target_playlist_name
            ),
            metadata={
                "schedule_id": schedule.id,
                "job_type": schedule.job_type,
                "schedule_value": (
                    schedule.schedule_value
                ),
            },
        )
    except Exception:
        pass

# AFTER:
    log_activity(
        user_id=user.id,
        activity_type=ActivityType.SCHEDULE_CREATE,
        description=(
            f"Created {schedule.job_type} schedule "
            f"for '{schedule.target_playlist_name}'"
        ),
        playlist_id=schedule.target_playlist_id,
        playlist_name=schedule.target_playlist_name,
        metadata={
            "schedule_id": schedule.id,
            "job_type": schedule.job_type,
            "schedule_value": schedule.schedule_value,
        },
    )
```

**Import change**: Add `log_activity` to imports from `shuffify.routes`. Remove `from shuffify.services import ActivityLogService` (line 44).

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/raid_panel.py`

**4 occurrences** (lines 108-124, 173-185, 227-245, 309-320).

```python
# BEFORE (raid_watch, lines 108-124):
        try:
            user = get_db_user()
            if user:
                ActivityLogService.log(
                    user_id=user.id,
                    activity_type=ActivityType.RAID_WATCH_ADD,
                    description=(
                        "Watching '{}'"
                        .format(
                            req.source_playlist_name
                            or req.source_playlist_id
                        )
                    ),
                    playlist_id=playlist_id,
                )
        except Exception:
            pass

# AFTER (with decorator providing `user`):
        log_activity(
            user_id=user.id,
            activity_type=ActivityType.RAID_WATCH_ADD,
            description=(
                f"Watching '"
                f"{req.source_playlist_name or req.source_playlist_id}'"
            ),
            playlist_id=playlist_id,
        )
```

**Import change**: Add `log_activity` to imports from `shuffify.routes`. Remove `from shuffify.services.activity_log_service import ActivityLogService` (lines 32-34).

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/core.py`

**2 occurrences** in routes that are NOT converted to `@require_auth_and_db` (callback line 301-314 and logout lines 359-374). These routes operate before/during/after authentication, so they must manually look up the user. Convert only the try/except wrapper:

```python
# BEFORE (callback, lines 300-314):
        # Log login activity (non-blocking)
        try:
            db_user = UserService.get_by_spotify_id(
                user_data["id"]
            )
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    activity_type=ActivityType.LOGIN,
                    description=(
                        "Logged in via Spotify OAuth"
                    ),
                )
        except Exception:
            pass

# AFTER:
        # Log login activity (non-blocking)
        try:
            db_user = UserService.get_by_spotify_id(
                user_data["id"]
            )
            if db_user:
                log_activity(
                    user_id=db_user.id,
                    activity_type=ActivityType.LOGIN,
                    description="Logged in via Spotify OAuth",
                )
        except Exception:
            pass
```

Note: For `core.py`, the benefit is smaller since the user lookup still needs to happen. But using `log_activity` ensures that even if `ActivityLogService.log` itself raises internally, it is caught cleanly. The outer try/except for the `UserService.get_by_spotify_id` call remains.

**Import change**: Add `log_activity` to imports from `shuffify.routes` (line 19-24). `ActivityLogService` can be removed from the import (line 34) since `log_activity` handles it. However, `UserService` must remain since it is used for upsert and user lookup.

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/shuffle.py`

**1 occurrence** (lines 136-156):

```python
# BEFORE:
    # Log activity (non-blocking)
    try:
        db_user = get_db_user()
        if db_user:
            ActivityLogService.log(
                user_id=db_user.id,
                ...
            )
    except Exception:
        pass  # Activity logging must never break shuffle

# AFTER:
    # Log activity (non-blocking)
    db_user = get_db_user()
    if db_user:
        log_activity(
            user_id=db_user.id,
            ...
        )
```

**Import change**: Add `log_activity` to imports from `shuffify.routes`. Remove `ActivityLogService` from the services import (line 22).

---

### File: `/Users/chris/Projects/shuffify/shuffify/routes/workshop.py`

**2 occurrences** in `workshop_commit` (lines 218-244) and `save_workshop_session` (lines 570-592) and `delete_workshop_session` (lines 728-745).

The `workshop_commit` route does a manual `UserService.get_by_spotify_id()` lookup. Since the workshop routes are NOT being converted to the decorator in this phase, the conversion is partial:

```python
# BEFORE (workshop_commit, lines 217-244):
    try:
        user_data = session.get("user_data", {})
        spotify_id = user_data.get("id")
        if spotify_id:
            db_user = UserService.get_by_spotify_id(
                spotify_id
            )
            if db_user:
                ActivityLogService.log(
                    user_id=db_user.id,
                    ...
                )
    except Exception:
        pass

# AFTER:
    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if spotify_id:
        db_user = UserService.get_by_spotify_id(spotify_id)
        if db_user:
            log_activity(
                user_id=db_user.id,
                ...
            )
```

**Import change**: Add `log_activity` to imports from `shuffify.routes`. Remove `ActivityLogService` from services import but KEEP `UserService` (still used for spotify_id lookups in unconverted routes).

---

## Part E: Enforce `json_success()` / `json_error()` Helpers

### Goal

Replace raw `jsonify({"success": True, ...})` calls with appropriate helpers. There are two categories:

**Category 1: Calls that include a `"message"` key** -- these can directly use `json_success()`.

| File | Line | Current | Replacement |
|------|------|---------|-------------|
| `playlist_pairs.py` | 128-132 | `jsonify({"success": True, "message": "Archive pair created", "pair": pair.to_dict()})` | `json_success("Archive pair created", pair=pair.to_dict())` |
| `playlist_pairs.py` | 221-225 | `jsonify({"success": True, "message": f"Archived {count} tracks", "archived_count": count})` | `json_success(f"Archived {count} tracks", archived_count=count)` |
| `playlist_pairs.py` | 281-285 | `jsonify({"success": True, "message": f"Unarchived {count} tracks", "unarchived_count": count})` | `json_success(f"Unarchived {count} tracks", unarchived_count=count)` |

**Category 2: Calls that do NOT include a `"message"` key** -- these are data-only responses. The existing `json_success()` helper requires a `message` argument and adds `"category": "success"`. These data-only endpoints should NOT be forced into the `json_success()` pattern because:
- Adding a message would change the API contract for frontend consumers
- Adding `"category": "success"` would be noise in data responses

**Recommendation**: Add a new `json_data()` helper for data-only success responses, OR leave these raw `jsonify` calls as-is for now. The cleanest approach for Phase 1 is to leave data-only `jsonify` calls untouched and only convert the 3 calls in Category 1 above.

Data-only calls to leave unchanged:

| File | Line | Shape |
|------|------|-------|
| `snapshots.py` | 60 | `jsonify({"success": True, "snapshots": [...]})` |
| `snapshots.py` | 150 | `jsonify({"success": True, "snapshot": ...})` |
| `playlist_pairs.py` | 58 | `jsonify({"success": True, "paired": False})` |
| `playlist_pairs.py` | 60 | `jsonify({"success": True, "paired": True, "pair": ...})` |
| `playlist_pairs.py` | 352 | `jsonify({"success": True, "tracks": [...], "total": ...})` |
| `upstream_sources.py` | 46 | `jsonify({"success": True, "sources": [...]})` |
| `schedules.py` | 445 | `jsonify({"success": True, "history": history})` |
| `schedules.py` | 488 | `jsonify({"success": True, "has_pair": ..., ...})` |
| `playlists.py` | 101 | `jsonify({"success": True, "playlists": result})` |
| `workshop.py` | 128, 306, 351, 428, 464, 515, 631 | Various data-only shapes |
| `shuffle.py` | 110 | `jsonify({"success": False, "message": ..., "category": "info"})` -- special case, uses `"info"` category |

The `shuffle.py` line 110 case is a special "not-really-an-error" response where `success` is False but `category` is `"info"`. Leave this as-is.

---

## Step-by-Step Implementation Instructions

### Order of Operations

1. **Step 1**: Add `import functools` to `/Users/chris/Projects/shuffify/shuffify/routes/__init__.py`
2. **Step 2**: Add `require_auth_and_db` decorator to `/Users/chris/Projects/shuffify/shuffify/routes/__init__.py`
3. **Step 3**: Add `log_activity` helper to `/Users/chris/Projects/shuffify/shuffify/routes/__init__.py`
4. **Step 4**: Run `flake8 shuffify/routes/__init__.py` to verify no lint errors
5. **Step 5**: Convert `snapshots.py` (simplest file, 5 uniform routes)
6. **Step 6**: Run `pytest tests/test_snapshot_routes.py -v` -- expect some test failures due to changed mock targets (see Testing section below)
7. **Step 7**: Fix `tests/test_snapshot_routes.py` mock paths (change `@patch("shuffify.routes.snapshots.require_auth")` to `@patch("shuffify.routes.require_auth")` or better, patch the decorator)
8. **Step 8**: Convert `playlist_pairs.py`
9. **Step 9**: Convert `upstream_sources.py`
10. **Step 10**: Convert `raid_panel.py`
11. **Step 11**: Run `pytest tests/test_raid_panel_routes.py -v` and fix mocks
12. **Step 12**: Convert `schedules.py` (JSON routes only, leave HTML `schedules()` route untouched)
13. **Step 13**: Apply `log_activity` to `core.py`, `shuffle.py`, `workshop.py`
14. **Step 14**: Apply `json_success()` to 3 calls in `playlist_pairs.py` (Part E, Category 1)
15. **Step 15**: Run full test suite: `flake8 shuffify/ && pytest tests/ -v`
16. **Step 16**: Fix any remaining test failures

### Testing Impact

**Critical**: Existing tests patch `require_auth` at the route module level (e.g., `@patch("shuffify.routes.snapshots.require_auth")`). After conversion, the decorator is applied at import time, so the mock target changes. There are two strategies:

**Strategy A (Recommended)**: Patch `require_auth_and_db` instead:
```python
@patch("shuffify.routes.snapshots.require_auth_and_db",
       lambda f: f)  # Disable the decorator
```
Then inject `client` and `user` kwargs in test calls. This is cleaner but requires more test changes.

**Strategy B (Simpler)**: Patch the underlying functions that the decorator calls:
```python
@patch("shuffify.routes.require_auth")
@patch("shuffify.routes.is_db_available", return_value=True)  # Note: lazy import
@patch("shuffify.routes.get_db_user")
```
But this requires patching `is_db_available` correctly since it is imported lazily inside the decorator.

**Strategy C (Most Reliable)**: Patch at the source:
```python
@patch("shuffify.services.auth_service.AuthService.validate_session_token", return_value=True)
@patch("shuffify.services.auth_service.AuthService.get_authenticated_client")
```

Review the existing test files to determine which strategy minimizes changes. The test files at `/Users/chris/Projects/shuffify/tests/test_snapshot_routes.py` and `/Users/chris/Projects/shuffify/tests/test_raid_panel_routes.py` patch `require_auth` at the route module level -- those patches must be updated.

---

## Verification Checklist

Run these commands after implementation and confirm all pass:

```bash
# 1. Lint check (REQUIRED)
flake8 shuffify/

# 2. Full test suite (REQUIRED)
pytest tests/ -v

# 3. Specifically test the converted route modules
pytest tests/test_snapshot_routes.py tests/test_raid_panel_routes.py tests/test_settings_route.py -v

# 4. Grep to confirm no remaining boilerplate (should return 0 hits in converted files)
# Search for the old 3-check pattern in converted files:
grep -n "require_auth()" shuffify/routes/snapshots.py
grep -n "require_auth()" shuffify/routes/playlist_pairs.py
grep -n "require_auth()" shuffify/routes/upstream_sources.py
grep -n "require_auth()" shuffify/routes/raid_panel.py
# (schedules.py will still have require_auth in the HTML route)

# 5. Confirm decorator is properly imported in all converted modules
grep -n "require_auth_and_db" shuffify/routes/snapshots.py
grep -n "require_auth_and_db" shuffify/routes/playlist_pairs.py
grep -n "require_auth_and_db" shuffify/routes/upstream_sources.py
grep -n "require_auth_and_db" shuffify/routes/raid_panel.py
grep -n "require_auth_and_db" shuffify/routes/schedules.py

# 6. Confirm log_activity is used (should find at least 14 hits across routes)
grep -rn "log_activity(" shuffify/routes/

# 7. Confirm no bare ActivityLogService.log calls remain in converted files
grep -n "ActivityLogService.log" shuffify/routes/upstream_sources.py  # Should be 0
grep -n "ActivityLogService.log" shuffify/routes/raid_panel.py       # Should be 0
grep -n "ActivityLogService.log" shuffify/routes/schedules.py        # Should be 0

# 8. Quick pre-push
flake8 shuffify/ && pytest tests/ -v && echo "Ready to push!"
```

---

## What NOT To Do

1. **Do NOT convert HTML-rendering routes** (`index`, `workshop`, `schedules` page, `settings` page). These use `is_authenticated()` + `redirect()` for unauthenticated users -- a fundamentally different pattern from JSON API routes. The decorator returns JSON errors, which would break HTML pages.

2. **Do NOT convert routes that only need auth but NOT database access** (`playlists.py`, `shuffle.py`, `workshop_preview_shuffle`, `workshop_search`). These routes deliberately work without database availability. Forcing them through `@require_auth_and_db` would degrade availability.

3. **Do NOT change the `json_success()` function signature**. The existing `json_success(message: str, **extra)` signature works well. Do not make `message` optional -- that would muddy the distinction between "action response with message" and "data query response without message."

4. **Do NOT add a `json_data()` helper in this phase**. The data-only `jsonify({"success": True, ...})` calls are fine as-is. Adding another abstraction increases cognitive load without clear benefit.

5. **Do NOT convert workshop session routes** (`list_workshop_sessions`, `save_workshop_session`, `load_workshop_session`, `update_workshop_session`, `delete_workshop_session`). These use `spotify_id` directly with `WorkshopSessionService` (not `user.id`). Converting them would require changing how `WorkshopSessionService` identifies users. This is a separate refactor.

6. **Do NOT remove `get_db_user()` from `__init__.py`**. It is still used by unconverted routes (`settings.py`, `schedules.py` HTML route, `shuffle.py`, `workshop.py`, `core.py`).

7. **Do NOT remove `require_auth()` from `__init__.py`**. It is still used by unconverted routes and is called internally by the decorator.

8. **Do NOT make the decorator async or truly non-blocking**. The term "non-blocking" in the codebase comments means "does not raise exceptions that break the flow." All operations are synchronous.

9. **Do NOT change error message text in the decorator to match per-file variations** (e.g., "Authentication required" vs "Please log in first."). Standardize on the messages in the decorator: "Please log in first." (401), "Database is unavailable." (503), "User not found." (401). Some existing tests may assert on specific error messages -- update those tests.

10. **Do NOT forget `@functools.wraps(f)`** in the decorator. Without it, Flask will see all decorated routes as having the same function name (`decorated_function`), causing route registration conflicts.

---

## Total Lines of Code Impact (Estimated)

| Category | Lines Removed | Lines Added | Net |
|----------|---------------|-------------|-----|
| Decorator definition | 0 | ~30 | +30 |
| `log_activity` helper | 0 | ~20 | +20 |
| Boilerplate removal (5 files, 26 routes) | ~260 | 0 | -260 |
| Activity logging simplification (~15 occurrences) | ~150 | ~50 | -100 |
| `json_success()` conversions (3 calls) | ~12 | ~6 | -6 |
| Import line changes | ~30 | ~20 | -10 |
| Test file updates | ~20 | ~30 | +10 |
| **Total** | **~472** | **~156** | **~-316** |

Net reduction of approximately 316 lines of repetitive code.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/routes/__init__.py` - Core file to modify: add decorator and helper function
- `/Users/chris/Projects/shuffify/shuffify/routes/snapshots.py` - Cleanest conversion target (5 uniform routes), implement first as template for others
- `/Users/chris/Projects/shuffify/shuffify/routes/schedules.py` - Most routes to convert (7), most activity logging occurrences (5), and adds new DB availability check behavior
- `/Users/chris/Projects/shuffify/shuffify/routes/upstream_sources.py` - Unique pattern: uses `spotify_id` instead of `user.id`, tests the decorator's user object access
- `/Users/chris/Projects/shuffify/tests/test_snapshot_routes.py` - Must update mock targets after decorator adoption, establishes test pattern for remaining files
