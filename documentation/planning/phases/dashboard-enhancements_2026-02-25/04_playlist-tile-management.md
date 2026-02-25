# Phase 04: Playlist Tile Management (Reorder, Hide, Pin)

**Status:** ✅ COMPLETE
**Started:** 2026-02-25
**Completed:** 2026-02-25

## Header

| Field | Value |
|---|---|
| **PR Title** | Add playlist tile management with drag-and-drop reorder, hide, and pin |
| **Risk Level** | Medium |
| **Estimated Effort** | High (3-4 days) |
| **Files Modified** | 7 |
| **Files Created** | 5 |
| **Files Deleted** | 0 |

---

## Context

The dashboard currently renders playlists in whatever order the Spotify API returns them. Users have no control over which playlists appear first, which are hidden from view, or how tiles are arranged. For power users with dozens of playlists, this makes the dashboard cluttered and hard to navigate. This phase adds a persistent PlaylistPreference model, a service layer for CRUD operations, API routes for the frontend, and a management mode in the dashboard UI with drag-and-drop reordering, pin-to-top, and hide/show controls. All changes are immediately persisted via AJAX -- no explicit save button.

---

## Dependencies

- **Phase 03** (Shuffle Hover Overlay) must be completed first. Phase 03 rewrites the card tile structure in `dashboard.html`. This phase builds on top of that tile structure by adding management controls (drag handles, pin/hide icons). The exact HTML structure of the card tile from Phase 03 will be the base that this phase modifies.
- **Unlocks:** None (this is a leaf phase).

---

## Detailed Implementation Plan

### Step 1: Add PlaylistPreference Model to `shuffify/models/db.py`

**File:** `/Users/chris/Projects/shuffify/shuffify/models/db.py`

Add the new model class after the `PlaylistPair` class (after line 934). Follow the exact patterns established by `PlaylistPair` and other models in this file.

```python
class PlaylistPreference(db.Model):
    """
    Per-user playlist display preferences.

    Controls the ordering, visibility, and pinning of playlists
    on the dashboard. Created on-demand when a user customizes
    their playlist arrangement.
    """

    __tablename__ = "playlist_preferences"

    id = db.Column(
        db.Integer, primary_key=True, autoincrement=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    spotify_playlist_id = db.Column(
        db.String(255), nullable=False
    )
    sort_order = db.Column(
        db.Integer, nullable=False, default=0
    )
    is_hidden = db.Column(
        db.Boolean, nullable=False, default=False
    )
    is_pinned = db.Column(
        db.Boolean, nullable=False, default=False
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = db.relationship(
        "User",
        backref=db.backref(
            "playlist_preferences",
            lazy="dynamic",
            cascade="all, delete-orphan",
        ),
    )

    # Unique constraint: one preference per user per playlist
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "spotify_playlist_id",
            name="uq_user_spotify_playlist",
        ),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the PlaylistPreference to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "spotify_playlist_id": self.spotify_playlist_id,
            "sort_order": self.sort_order,
            "is_hidden": self.is_hidden,
            "is_pinned": self.is_pinned,
            "created_at": (
                self.created_at.isoformat()
                if self.created_at
                else None
            ),
            "updated_at": (
                self.updated_at.isoformat()
                if self.updated_at
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<PlaylistPreference {self.id}: "
            f"playlist={self.spotify_playlist_id} "
            f"order={self.sort_order} "
            f"{'hidden' if self.is_hidden else 'visible'} "
            f"{'pinned' if self.is_pinned else 'unpinned'}>"
        )
```

Also update the module docstring at the top of `db.py` to mention `PlaylistPreference`.

---

### Step 2: Create PlaylistPreferenceService

**New file:** `/Users/chris/Projects/shuffify/shuffify/services/playlist_preference_service.py`

Full content:

```python
"""
Service for managing per-user playlist display preferences.

Handles sort ordering, hide/show toggling, pin toggling, and
bulk order updates for the dashboard playlist grid.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from shuffify.models.db import db, PlaylistPreference
from shuffify.services.base import safe_commit

logger = logging.getLogger(__name__)


class PlaylistPreferenceError(Exception):
    """Base error for playlist preference operations."""
    pass


class PlaylistPreferenceNotFoundError(PlaylistPreferenceError):
    """Raised when a preference record is not found."""
    pass


class PlaylistPreferenceService:
    """Manages PlaylistPreference CRUD operations."""

    @staticmethod
    def get_user_preferences(
        user_id: int,
    ) -> Dict[str, PlaylistPreference]:
        """
        Get all playlist preferences for a user.

        Returns:
            Dict mapping spotify_playlist_id to PlaylistPreference.
        """
        prefs = PlaylistPreference.query.filter_by(
            user_id=user_id
        ).all()
        return {
            p.spotify_playlist_id: p for p in prefs
        }

    @staticmethod
    def get_preference(
        user_id: int,
        spotify_playlist_id: str,
    ) -> Optional[PlaylistPreference]:
        """Get a single preference record, or None."""
        return PlaylistPreference.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=spotify_playlist_id,
        ).first()

    @staticmethod
    def save_order(
        user_id: int,
        ordered_playlist_ids: List[str],
    ) -> int:
        """
        Bulk upsert sort_order for an ordered list of playlist IDs.

        Creates preference records for playlists that don't have one.
        Updates sort_order for existing records.

        Returns:
            Number of preferences updated/created.
        """
        existing = {
            p.spotify_playlist_id: p
            for p in PlaylistPreference.query.filter_by(
                user_id=user_id
            ).all()
        }

        count = 0
        for index, playlist_id in enumerate(
            ordered_playlist_ids
        ):
            pref = existing.get(playlist_id)
            if pref:
                pref.sort_order = index
                pref.updated_at = datetime.now(timezone.utc)
            else:
                pref = PlaylistPreference(
                    user_id=user_id,
                    spotify_playlist_id=playlist_id,
                    sort_order=index,
                )
                db.session.add(pref)
            count += 1

        safe_commit(
            f"save playlist order ({count} items) "
            f"for user {user_id}",
            PlaylistPreferenceError,
        )
        return count

    @staticmethod
    def toggle_hidden(
        user_id: int,
        spotify_playlist_id: str,
    ) -> bool:
        """
        Toggle the is_hidden flag for a playlist.
        Creates a preference record if one doesn't exist.

        Returns:
            The new is_hidden value.
        """
        pref = PlaylistPreference.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=spotify_playlist_id,
        ).first()

        if pref:
            pref.is_hidden = not pref.is_hidden
            pref.updated_at = datetime.now(timezone.utc)
        else:
            pref = PlaylistPreference(
                user_id=user_id,
                spotify_playlist_id=spotify_playlist_id,
                is_hidden=True,
            )
            db.session.add(pref)

        safe_commit(
            f"toggle hidden for playlist "
            f"{spotify_playlist_id} (user {user_id})",
            PlaylistPreferenceError,
        )
        return pref.is_hidden

    @staticmethod
    def toggle_pinned(
        user_id: int,
        spotify_playlist_id: str,
    ) -> bool:
        """
        Toggle the is_pinned flag for a playlist.
        Creates a preference record if one doesn't exist.

        Returns:
            The new is_pinned value.
        """
        pref = PlaylistPreference.query.filter_by(
            user_id=user_id,
            spotify_playlist_id=spotify_playlist_id,
        ).first()

        if pref:
            pref.is_pinned = not pref.is_pinned
            pref.updated_at = datetime.now(timezone.utc)
        else:
            pref = PlaylistPreference(
                user_id=user_id,
                spotify_playlist_id=spotify_playlist_id,
                is_pinned=True,
            )
            db.session.add(pref)

        safe_commit(
            f"toggle pinned for playlist "
            f"{spotify_playlist_id} (user {user_id})",
            PlaylistPreferenceError,
        )
        return pref.is_pinned

    @staticmethod
    def reset_preferences(user_id: int) -> int:
        """
        Delete all playlist preferences for a user.

        Returns:
            Number of records deleted.
        """
        count = PlaylistPreference.query.filter_by(
            user_id=user_id
        ).delete()
        safe_commit(
            f"reset all playlist preferences "
            f"({count} deleted) for user {user_id}",
            PlaylistPreferenceError,
        )
        return count

    @staticmethod
    def apply_preferences(playlists, preferences):
        """
        Apply user preferences to sort and filter a playlist list.

        Ordering logic:
        1. Pinned playlists first, sorted by sort_order
        2. Unpinned playlists second, sorted by sort_order
        3. Playlists without preferences last, in original order
        4. Hidden playlists excluded from result

        Returns:
            Tuple of (visible_playlists, hidden_playlists).
        """
        known = []
        unknown = []
        for pl in playlists:
            pl_id = pl.get("id") or pl.get("playlist_id", "")
            pref = preferences.get(pl_id)
            if pref:
                pl["_pref"] = pref
                known.append(pl)
            else:
                pl["_pref"] = None
                unknown.append(pl)

        visible_known = [
            p for p in known if not p["_pref"].is_hidden
        ]
        hidden = [
            p for p in known if p["_pref"].is_hidden
        ]

        visible_known.sort(
            key=lambda p: (
                not p["_pref"].is_pinned,
                p["_pref"].sort_order,
            )
        )

        visible = visible_known + unknown
        return visible, hidden
```

---

### Step 3: Register Service in `shuffify/services/__init__.py`

Add after the Raid Sync Service imports:

```python
# Playlist Preference Service
from shuffify.services.playlist_preference_service import (
    PlaylistPreferenceService,
    PlaylistPreferenceError,
    PlaylistPreferenceNotFoundError,
)
```

Add to `__all__` list.

---

### Step 4: Create Pydantic Schema for Request Validation

**New file:** `/Users/chris/Projects/shuffify/shuffify/schemas/playlist_preference_requests.py`

```python
"""
Pydantic schemas for playlist preference API endpoints.
"""

import re
from typing import List

from pydantic import BaseModel, field_validator


SPOTIFY_ID_PATTERN = re.compile(r"^[a-zA-Z0-9]{1,255}$")


class SaveOrderRequest(BaseModel):
    """Request to save playlist display order."""

    playlist_ids: List[str]

    @field_validator("playlist_ids")
    @classmethod
    def validate_playlist_ids(cls, v):
        if not v:
            raise ValueError("playlist_ids must not be empty")
        if len(v) > 500:
            raise ValueError(
                "playlist_ids cannot exceed 500 items"
            )
        for pid in v:
            if not SPOTIFY_ID_PATTERN.match(pid):
                raise ValueError(
                    f"Invalid playlist ID format: {pid}"
                )
        return v
```

Register in `shuffify/schemas/__init__.py`.

---

### Step 5: Create Route Module

**New file:** `/Users/chris/Projects/shuffify/shuffify/routes/playlist_preferences.py`

```python
"""
Playlist preference routes.

Manages per-user playlist display ordering, visibility, and pinning.
"""

import logging

from shuffify.routes import (
    main,
    require_auth_and_db,
    json_error,
    json_success,
    validate_json,
)
from shuffify.services.playlist_preference_service import (
    PlaylistPreferenceService,
    PlaylistPreferenceError,
)
from shuffify.schemas.playlist_preference_requests import (
    SaveOrderRequest,
)

logger = logging.getLogger(__name__)


@main.route(
    "/api/playlist-preferences/order",
    methods=["POST"],
)
@require_auth_and_db
def save_playlist_order(client=None, user=None):
    """Save reordered playlist IDs."""
    req, err = validate_json(SaveOrderRequest)
    if err:
        return err

    try:
        count = PlaylistPreferenceService.save_order(
            user.id, req.playlist_ids
        )
        return json_success(
            f"Saved order for {count} playlists",
            count=count,
        )
    except PlaylistPreferenceError as e:
        logger.error("Failed to save order: %s", e)
        return json_error(
            "Failed to save playlist order", 500
        )


@main.route(
    "/api/playlist-preferences/<playlist_id>/toggle-hidden",
    methods=["POST"],
)
@require_auth_and_db
def toggle_playlist_hidden(
    playlist_id, client=None, user=None
):
    """Toggle hidden state for a playlist."""
    try:
        is_hidden = PlaylistPreferenceService.toggle_hidden(
            user.id, playlist_id
        )
        action = "hidden" if is_hidden else "shown"
        return json_success(
            f"Playlist {action}",
            is_hidden=is_hidden,
        )
    except PlaylistPreferenceError as e:
        logger.error(
            "Failed to toggle hidden: %s", e
        )
        return json_error(
            "Failed to update visibility", 500
        )


@main.route(
    "/api/playlist-preferences/<playlist_id>/toggle-pinned",
    methods=["POST"],
)
@require_auth_and_db
def toggle_playlist_pinned(
    playlist_id, client=None, user=None
):
    """Toggle pinned state for a playlist."""
    try:
        is_pinned = PlaylistPreferenceService.toggle_pinned(
            user.id, playlist_id
        )
        action = "pinned" if is_pinned else "unpinned"
        return json_success(
            f"Playlist {action}",
            is_pinned=is_pinned,
        )
    except PlaylistPreferenceError as e:
        logger.error(
            "Failed to toggle pinned: %s", e
        )
        return json_error(
            "Failed to update pin state", 500
        )


@main.route(
    "/api/playlist-preferences/reset",
    methods=["POST"],
)
@require_auth_and_db
def reset_playlist_preferences(
    client=None, user=None
):
    """Reset all playlist preferences for the current user."""
    try:
        count = PlaylistPreferenceService.reset_preferences(
            user.id
        )
        return json_success(
            f"Reset {count} playlist preferences",
            count=count,
        )
    except PlaylistPreferenceError as e:
        logger.error(
            "Failed to reset preferences: %s", e
        )
        return json_error(
            "Failed to reset preferences", 500
        )
```

Register in `shuffify/routes/__init__.py` (add `playlist_preferences` to the import list).

---

### Step 6: Update Dashboard Route in `shuffify/routes/core.py`

Add import for `PlaylistPreferenceService`. Update the `index()` route to:
1. Load user preferences via `PlaylistPreferenceService.get_user_preferences(db_user.id)`
2. Apply preferences via `PlaylistPreferenceService.apply_preferences(playlists, prefs)`
3. Pass `visible_playlists`, `hidden_playlists`, and `preferences` dict to the template

**Before (in the index route, after fetching playlists):**
```python
            return render_template(
                "dashboard.html",
                playlists=playlists,
                user=user,
                algorithms=algorithms,
                dashboard=dashboard_data,
            )
```

**After:**
```python
            return render_template(
                "dashboard.html",
                playlists=visible_playlists,
                hidden_playlists=hidden_playlists,
                user=user,
                algorithms=algorithms,
                dashboard=dashboard_data,
                preferences=preferences,
            )
```

With safe defaults (`preferences = {}`, `visible_playlists = playlists`, `hidden_playlists = []`) set before the try block, and preference loading inside the existing try/except alongside dashboard_data.

---

### Step 7: Update Dashboard Template

**File:** `shuffify/templates/dashboard.html`

**7a. Add "Manage" button** to dashboard header (before the Logout button).

**7b. Add management toolbar** (hidden by default, shown in manage mode) with instructions, "Show Hidden" toggle, and "Reset All" button.

**7c. Update playlist grid** to add per-tile management controls:
- Pin button (bookmark icon, yellow when pinned)
- Hide button (eye icon)
- Drag handle (grip icon)
- Pinned badge (yellow pill, shown when pinned)
- `data-playlist-id`, `data-pinned`, `data-hidden` attributes
- `draggable` attribute toggled by JS

**7d. Add hidden playlists grid** (rendered from `hidden_playlists`, grayed out, with unhide button).

**7e. Add management JavaScript:**
- `toggleManageMode()` — show/hide controls, enable draggable
- `toggleShowHidden()` — reveal/hide the hidden playlists grid
- `togglePin(playlistId)` — AJAX POST to toggle-pinned, reload
- `toggleHide(playlistId)` — AJAX POST to toggle-hidden, reload
- `resetPreferences()` — confirm dialog, AJAX POST to reset, reload
- HTML5 Drag and Drop: `initDragAndDrop()` with dragstart/dragover/dragleave/drop/dragend handlers
- `saveCurrentOrder()` — collect tile order from DOM, POST to save-order

**7f. Add CSS for management mode:**
```css
.manage-mode-active { cursor: grab; }
.manage-mode-active:active { cursor: grabbing; }
.drag-over { border-color: rgba(255,255,255,0.6) !important; box-shadow: 0 0 0 2px rgba(29,185,84,0.5); transform: scale(1.02); }
.manage-mode-active:hover { transform: scale(1) !important; }
```

---

### Step 8: Database Migration

```bash
./venv/bin/python -m flask db migrate -m "Add playlist_preferences table"
./venv/bin/python -m flask db upgrade
```

The migration creates `playlist_preferences` table with columns: `id`, `user_id`, `spotify_playlist_id`, `sort_order`, `is_hidden`, `is_pinned`, `created_at`, `updated_at`, foreign key to `users.id`, unique constraint on `(user_id, spotify_playlist_id)`, and index on `user_id`.

---

## Test Plan

### New Test Files

**`tests/services/test_playlist_preference_service.py`** (~25-30 tests):
- `TestGetUserPreferences`: empty dict, keyed by spotify_id, doesn't leak across users
- `TestSaveOrder`: creates new, updates existing, preserves hidden/pinned, returns count, sequential ordering
- `TestToggleHidden`: creates on first toggle, toggles both directions, returns new value, isolated per user
- `TestTogglePinned`: creates on first toggle, toggles both directions, returns new value
- `TestResetPreferences`: deletes all, returns count, doesn't affect other users, returns 0 when empty
- `TestApplyPreferences`: pinned first, hidden excluded, unknown at end, sort_order respected, empty prefs returns original

**`tests/routes/test_playlist_preferences_routes.py`** (~20-25 tests):
- Auth required tests for all 4 endpoints
- Happy path tests for save order, toggle hidden, toggle pinned, reset
- Validation tests: empty body, empty playlist_ids, invalid format

**`tests/schemas/test_playlist_preference_requests.py`** (~5-8 tests):
- Valid request, empty list, over 500 items, invalid format

**`tests/models/test_playlist_preference_model.py`** (~5 tests):
- Create, unique constraint, to_dict, defaults, repr

### Existing tests — no modifications needed

---

## Documentation Updates

### CLAUDE.md
- Update model count from 10 to 11
- Add `playlist_preferences.py` to route modules table (4 routes)
- Update route total from 53 to 57
- Update service count from 17 to 18
- Update schema count from 6 to 7

### CHANGELOG.md

```markdown
### Added
- **Playlist Tile Management** - Reorder, hide, and pin playlists on the dashboard
  - New PlaylistPreference model for persistent per-user arrangement
  - Drag-and-drop reordering with HTML5 Drag and Drop API
  - Pin-to-top and hide/show controls with immediate AJAX persistence
  - Management mode toggle with toolbar and "Show Hidden" reveal
  - Reset button to restore default Spotify ordering
```

---

## Stress Testing & Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Deleted Spotify playlist | Preference exists but playlist missing from API response — simply doesn't appear |
| New Spotify playlist | No preference record — appended at end, visible, unpinned |
| 500+ playlists | Schema rejects save_order with >500 items (reasonable limit) |
| Rapid drag-and-drop | Each drop overwrites entire order; last save wins |
| Concurrent sessions | Last save wins — no conflict needed |
| Empty playlist list | Management mode works but no tiles to manage |
| Database unavailable | Graceful degradation — playlists show in Spotify default order |

---

## Verification Checklist

- [ ] `flake8 shuffify/` passes
- [ ] `pytest tests/ -v` passes (all existing + new tests)
- [ ] Migration generated and applied successfully
- [ ] Manual: playlists display normally (no preferences yet)
- [ ] Manual: "Manage" button shows toolbar and tile controls
- [ ] Manual: pin moves playlist to top after reload
- [ ] Manual: hide removes playlist after reload
- [ ] Manual: "Show Hidden" reveals hidden playlists grayed out
- [ ] Manual: unhide returns playlist to grid
- [ ] Manual: drag-and-drop reorders and persists
- [ ] Manual: "Reset All" restores default Spotify order
- [ ] Manual: "Done" hides management controls
- [ ] Hidden playlists still accessible in Workshop
- [ ] `flask routes` shows 4 new endpoints
- [ ] CLAUDE.md and CHANGELOG.md updated

---

## What NOT To Do

1. **Do NOT use SortableJS or any external drag-and-drop library.** Use vanilla HTML5 Drag and Drop API.

2. **Do NOT filter hidden playlists in the Spotify API call.** Filtering happens in `apply_preferences()` only. Hidden playlists must still be fetched for Workshop, schedules, etc.

3. **Do NOT use `db.func.now()` for timestamps.** Follow the existing pattern: `lambda: datetime.now(timezone.utc)`.

4. **Do NOT make management mode the default.** Dashboard loads in normal mode. Controls are hidden until the user clicks "Manage."

5. **Do NOT add a `PlaylistPreference` relationship on the `User` model class body.** Use `backref` on the `PlaylistPreference` side instead.

6. **Do NOT add the route import in alphabetical order.** Add `playlist_preferences` after `raid_panel` in `shuffify/routes/__init__.py`.

7. **Do NOT use `confirm()` dialogs for pin/hide actions.** Only "Reset All" gets a confirmation dialog. Pin and hide are immediately reversible.
