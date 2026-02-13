# Phase 3: User Settings / Preferences Model

## PR Title
`feat: Add UserSettings model, service, and settings page (#Phase-3)`

## Risk Level
**LOW** -- This phase adds a new model, service, route, and template. It touches one existing model (`User`) to add a relationship and one existing service (`UserService`) to auto-create settings on first login. No existing functionality is modified in a breaking way.

## Effort Estimate
**Medium** -- ~4-6 hours for an experienced developer, ~8-10 hours for a junior.

## Files to Create
| File | Purpose |
|------|---------|
| `shuffify/services/user_settings_service.py` | CRUD service for UserSettings |
| `shuffify/routes/settings.py` | `/settings` GET/POST route |
| `shuffify/templates/settings.html` | Settings form page |
| `shuffify/schemas/settings_requests.py` | Pydantic validation for settings updates |
| `tests/services/test_user_settings_service.py` | Service unit tests |
| `tests/test_settings_route.py` | Route integration tests |

## Files to Modify
| File | Change |
|------|--------|
| `shuffify/models/db.py` | Add `UserSettings` model class |
| `shuffify/services/user_service.py` | Auto-create `UserSettings` on new user creation |
| `shuffify/services/__init__.py` | Export `UserSettingsService` and exceptions |
| `shuffify/schemas/__init__.py` | Export `UserSettingsUpdateRequest` |
| `shuffify/routes/__init__.py` | Import the new `settings` route module |
| `shuffify/templates/dashboard.html` | Add "Settings" link to nav bar |

---

## Context & Dependencies

### Phase 0-2 Assumptions
This plan is written against the **current codebase state** (no Alembic, no PostgreSQL, no LoginHistory). The instructions below specify what to do in both scenarios:
- **If Phases 0-2 are already merged**: Generate an Alembic migration with `flask db migrate -m "add user_settings table"`.
- **If Phases 0-2 are NOT yet merged** (current state): The `db.create_all()` call in `shuffify/__init__.py` (line 192) will auto-create the table. An Alembic migration should be created later when Phase 0 introduces Alembic.

### Algorithm IDs
The valid algorithm IDs come from `ShuffleRegistry._algorithms` in `/Users/chris/Projects/shuffify/shuffify/shuffle_algorithms/registry.py` (line 15-23). The keys are:
```
"BasicShuffle", "BalancedShuffle", "PercentageShuffle", "StratifiedShuffle",
"ArtistSpacingShuffle", "AlbumSequenceShuffle", "TempoGradientShuffle"
```

### One-to-One Relationship Pattern
The `UserSettings` model uses a **one-to-one** relationship with `User`. This is enforced by:
1. A `unique=True` constraint on the `user_id` foreign key column
2. `uselist=False` on the relationship definition

---

## Detailed Implementation

### Step 1: Add `UserSettings` Model to `shuffify/models/db.py`

**Location**: After the `User` class (after line 86), before the `WorkshopSession` class.

**Add to imports at top (line 11)**: No new imports needed -- `json`, `logging`, `datetime`, `timezone`, `Dict`, `Any`, `List` are already imported.

**Add `UserSettings` class after line 86**:

```python
class UserSettings(db.Model):
    """
    User preferences and configuration.

    One-to-one relationship with User. Created automatically on
    first login via UserService.upsert_from_spotify().
    """

    __tablename__ = "user_settings"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Shuffle defaults
    default_algorithm = db.Column(
        db.String(64), nullable=True
    )
    default_algorithm_params = db.Column(
        db.JSON, nullable=True
    )

    # UI preferences
    theme = db.Column(
        db.String(10), nullable=False, default="system"
    )

    # Feature toggles
    notifications_enabled = db.Column(
        db.Boolean, nullable=False, default=False
    )
    auto_snapshot_enabled = db.Column(
        db.Boolean, nullable=False, default=True
    )
    max_snapshots_per_playlist = db.Column(
        db.Integer, nullable=False, default=10
    )

    # Dashboard preferences
    dashboard_show_recent_activity = db.Column(
        db.Boolean, nullable=False, default=True
    )

    # Extensible JSON field for future preferences
    extra = db.Column(db.JSON, nullable=True)

    # Timestamps
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
    user = db.relationship("User", back_populates="settings")

    # Valid theme choices (used by service layer for validation)
    VALID_THEMES = {"light", "dark", "system"}

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the UserSettings to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "default_algorithm": self.default_algorithm,
            "default_algorithm_params": (
                self.default_algorithm_params or {}
            ),
            "theme": self.theme,
            "notifications_enabled": self.notifications_enabled,
            "auto_snapshot_enabled": self.auto_snapshot_enabled,
            "max_snapshots_per_playlist": (
                self.max_snapshots_per_playlist
            ),
            "dashboard_show_recent_activity": (
                self.dashboard_show_recent_activity
            ),
            "extra": self.extra or {},
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
            f"<UserSettings user_id={self.user_id} "
            f"theme={self.theme}>"
        )
```

**Add relationship on `User` model** -- Insert after line 66 (after the `upstream_sources` relationship), before `to_dict`:

```python
    settings = db.relationship(
        "UserSettings",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
```

**BEFORE** (`shuffify/models/db.py` lines 56-67):
```python
    # Relationships
    workshop_sessions = db.relationship(
        "WorkshopSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    upstream_sources = db.relationship(
        "UpstreamSource",
        back_populates="user",
        cascade="all, delete-orphan",
    )
```

**AFTER**:
```python
    # Relationships
    workshop_sessions = db.relationship(
        "WorkshopSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    upstream_sources = db.relationship(
        "UpstreamSource",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    settings = db.relationship(
        "UserSettings",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
```

**Update the module docstring** (line 4): Change from:
```python
Defines the User, WorkshopSession, and UpstreamSource models
```
to:
```python
Defines the User, UserSettings, WorkshopSession, and UpstreamSource models
```

---

### Step 2: Create `shuffify/services/user_settings_service.py`

This service follows the exact same static-method pattern as `UserService` and `WorkshopSessionService`.

```python
"""
User settings service for managing user preferences.

Handles get-or-create, update, and convenience methods
for reading specific preference values.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from shuffify.models.db import db, UserSettings
from shuffify.shuffle_algorithms.registry import ShuffleRegistry

logger = logging.getLogger(__name__)

# Constraints
MAX_SNAPSHOTS_LIMIT = 50
MIN_SNAPSHOTS_LIMIT = 1


class UserSettingsError(Exception):
    """Base exception for user settings operations."""

    pass


class UserSettingsService:
    """Service for managing UserSettings records."""

    @staticmethod
    def get_or_create(user_id: int) -> UserSettings:
        """
        Get existing settings or create defaults for a user.

        Args:
            user_id: The internal database user ID.

        Returns:
            UserSettings instance (existing or newly created).

        Raises:
            UserSettingsError: If the operation fails.
        """
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
                f"Created default settings for user {user_id}"
            )
            return settings

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to get/create settings for user "
                f"{user_id}: {e}",
                exc_info=True,
            )
            raise UserSettingsError(
                f"Failed to get or create settings: {e}"
            )

    @staticmethod
    def update(
        user_id: int, **kwargs: Any
    ) -> UserSettings:
        """
        Update specific settings for a user.

        Only provided keyword arguments are updated. Unknown keys
        are silently ignored to allow forward compatibility.

        Args:
            user_id: The internal database user ID.
            **kwargs: Setting fields to update. Valid keys:
                - default_algorithm (str or None)
                - default_algorithm_params (dict or None)
                - theme (str)
                - notifications_enabled (bool)
                - auto_snapshot_enabled (bool)
                - max_snapshots_per_playlist (int)
                - dashboard_show_recent_activity (bool)
                - extra (dict or None)

        Returns:
            Updated UserSettings instance.

        Raises:
            UserSettingsError: If validation or update fails.
        """
        settings = UserSettingsService.get_or_create(user_id)

        # Define the set of updatable fields
        updatable_fields = {
            "default_algorithm",
            "default_algorithm_params",
            "theme",
            "notifications_enabled",
            "auto_snapshot_enabled",
            "max_snapshots_per_playlist",
            "dashboard_show_recent_activity",
            "extra",
        }

        try:
            for key, value in kwargs.items():
                if key not in updatable_fields:
                    continue

                # Validate specific fields
                if key == "default_algorithm" and value is not None:
                    valid = set(
                        ShuffleRegistry.get_available_algorithms().keys()
                    )
                    if value not in valid:
                        raise UserSettingsError(
                            f"Invalid algorithm '{value}'. "
                            f"Valid: {', '.join(sorted(valid))}"
                        )

                if key == "theme":
                    if value not in UserSettings.VALID_THEMES:
                        raise UserSettingsError(
                            f"Invalid theme '{value}'. "
                            f"Valid: {', '.join(sorted(UserSettings.VALID_THEMES))}"
                        )

                if key == "max_snapshots_per_playlist":
                    if not isinstance(value, int):
                        raise UserSettingsError(
                            "max_snapshots_per_playlist must be an integer"
                        )
                    if (
                        value < MIN_SNAPSHOTS_LIMIT
                        or value > MAX_SNAPSHOTS_LIMIT
                    ):
                        raise UserSettingsError(
                            f"max_snapshots_per_playlist must be "
                            f"between {MIN_SNAPSHOTS_LIMIT} and "
                            f"{MAX_SNAPSHOTS_LIMIT}"
                        )

                setattr(settings, key, value)

            settings.updated_at = datetime.now(timezone.utc)
            db.session.commit()

            logger.info(
                f"Updated settings for user {user_id}: "
                f"{list(kwargs.keys())}"
            )
            return settings

        except UserSettingsError:
            db.session.rollback()
            raise
        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to update settings for user "
                f"{user_id}: {e}",
                exc_info=True,
            )
            raise UserSettingsError(
                f"Failed to update settings: {e}"
            )

    @staticmethod
    def get_default_algorithm(
        user_id: int,
    ) -> Optional[str]:
        """
        Get the user's default shuffle algorithm name.

        Convenience method for shuffle routes that need
        to pre-select the user's preferred algorithm.

        Args:
            user_id: The internal database user ID.

        Returns:
            Algorithm class name string, or None if not set.
        """
        settings = UserSettings.query.filter_by(
            user_id=user_id
        ).first()

        if not settings:
            return None

        return settings.default_algorithm
```

---

### Step 3: Create `shuffify/schemas/settings_requests.py`

Pydantic schema for validating settings update form submissions.

```python
"""
Pydantic validation schemas for user settings requests.

Validates settings update payloads from the settings form.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator

from shuffify.shuffle_algorithms.registry import ShuffleRegistry


class UserSettingsUpdateRequest(BaseModel):
    """Schema for updating user settings via the settings form."""

    default_algorithm: Optional[str] = Field(default=None)
    theme: Optional[str] = Field(default=None)
    notifications_enabled: Optional[bool] = Field(
        default=None
    )
    auto_snapshot_enabled: Optional[bool] = Field(
        default=None
    )
    max_snapshots_per_playlist: Optional[int] = Field(
        default=None, ge=1, le=50
    )
    dashboard_show_recent_activity: Optional[bool] = Field(
        default=None
    )

    @field_validator("default_algorithm")
    @classmethod
    def validate_algorithm(
        cls, v: Optional[str]
    ) -> Optional[str]:
        """Validate algorithm name if provided."""
        if v is None or v == "":
            return None
        v = v.strip()
        if not v:
            return None
        valid = set(
            ShuffleRegistry.get_available_algorithms().keys()
        )
        if v not in valid:
            raise ValueError(
                f"Invalid algorithm '{v}'. Valid: "
                f"{', '.join(sorted(valid))}"
            )
        return v

    @field_validator("theme")
    @classmethod
    def validate_theme(
        cls, v: Optional[str]
    ) -> Optional[str]:
        """Validate theme choice."""
        if v is None:
            return None
        v = v.strip().lower()
        valid_themes = {"light", "dark", "system"}
        if v not in valid_themes:
            raise ValueError(
                f"Invalid theme '{v}'. Valid: "
                f"{', '.join(sorted(valid_themes))}"
            )
        return v

    class Config:
        extra = "ignore"
```

---

### Step 4: Modify `shuffify/services/user_service.py` -- Auto-Create Settings

**Location**: Inside `upsert_from_spotify()`, right after a new user is created and committed (after line 94, `db.session.commit()`), add logic to create default settings for new users.

**BEFORE** (lines 80-105):
```python
            else:
                # Create new user
                user = User(
                    spotify_id=spotify_id,
                    display_name=user_data.get("display_name"),
                    email=user_data.get("email"),
                    profile_image_url=profile_image_url,
                )
                db.session.add(user)
                logger.info(
                    f"Created new user: {spotify_id} "
                    f"({user_data.get('display_name', 'Unknown')})"
                )

            db.session.commit()
            return user

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to upsert user {spotify_id}: {e}",
                exc_info=True,
            )
            raise UserServiceError(
                f"Failed to save user record: {e}"
            )
```

**AFTER**:
```python
            else:
                # Create new user
                user = User(
                    spotify_id=spotify_id,
                    display_name=user_data.get("display_name"),
                    email=user_data.get("email"),
                    profile_image_url=profile_image_url,
                )
                db.session.add(user)
                is_new_user = True
                logger.info(
                    f"Created new user: {spotify_id} "
                    f"({user_data.get('display_name', 'Unknown')})"
                )

            db.session.commit()

            # Auto-create default settings for new users
            if is_new_user:
                try:
                    from shuffify.services.user_settings_service import (
                        UserSettingsService,
                    )

                    UserSettingsService.get_or_create(user.id)
                except Exception as settings_err:
                    # Settings creation failure should NOT block login
                    logger.warning(
                        f"Failed to create default settings "
                        f"for user {spotify_id}: {settings_err}"
                    )

            return user

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to upsert user {spotify_id}: {e}",
                exc_info=True,
            )
            raise UserServiceError(
                f"Failed to save user record: {e}"
            )
```

Also add `is_new_user = False` at the start of the try block (around line 66, right before the `user = User.query...` line):

```python
        try:
            is_new_user = False
            user = User.query.filter_by(
                spotify_id=spotify_id
            ).first()
```

**Important**: The import of `UserSettingsService` is done inside the function (lazy import) to avoid circular import issues, following the same pattern used in `shuffify/routes/core.py` line 207-209 where `TokenService` is imported inside the callback function.

---

### Step 5: Update `shuffify/services/__init__.py`

**Add after line 79** (after the `UpstreamSourceService` imports):

```python
# User Settings Service
from shuffify.services.user_settings_service import (
    UserSettingsService,
    UserSettingsError,
)
```

**Add to `__all__` list** (after `"UpstreamSourceNotFoundError"` on line 139):

```python
    # User Settings Service
    "UserSettingsService",
    "UserSettingsError",
```

---

### Step 6: Update `shuffify/schemas/__init__.py`

**Add after line 24** (after `ScheduleUpdateRequest` import):

```python
from .settings_requests import (
    UserSettingsUpdateRequest,
)
```

**Add to `__all__` list** (after `"ScheduleUpdateRequest"` on line 45):

```python
    # Settings schemas
    "UserSettingsUpdateRequest",
```

---

### Step 7: Create `shuffify/routes/settings.py`

This follows the exact pattern of `shuffify/routes/schedules.py` -- imports from the routes package, uses `main` blueprint, uses `is_authenticated()`, `require_auth()`, `get_db_user()`, etc.

```python
"""
Settings routes: view and update user preferences.
"""

import logging

from flask import (
    session,
    redirect,
    url_for,
    request,
    flash,
    render_template,
)
from pydantic import ValidationError

from shuffify.routes import (
    main,
    is_authenticated,
    get_db_user,
    clear_session_and_show_login,
    json_error,
    json_success,
)
from shuffify.services import (
    AuthService,
    ShuffleService,
    AuthenticationError,
    UserSettingsService,
    UserSettingsError,
)
from shuffify.schemas import UserSettingsUpdateRequest

logger = logging.getLogger(__name__)


@main.route("/settings")
def settings():
    """Render the user settings page."""
    if not is_authenticated():
        return redirect(url_for("main.index"))

    try:
        client = AuthService.get_authenticated_client(
            session["spotify_token"]
        )
        user = AuthService.get_user_data(client)

        db_user = get_db_user()
        if not db_user:
            flash(
                "Please log in again to access settings.",
                "error",
            )
            return redirect(url_for("main.index"))

        user_settings = UserSettingsService.get_or_create(
            db_user.id
        )
        algorithms = ShuffleService.list_algorithms()

        return render_template(
            "settings.html",
            user=user,
            settings=user_settings.to_dict(),
            algorithms=algorithms,
        )

    except AuthenticationError as e:
        logger.error(
            f"Error loading settings page: {e}"
        )
        return clear_session_and_show_login(
            "Your session has expired. "
            "Please log in again."
        )


@main.route("/settings", methods=["POST"])
def update_settings():
    """Update user settings from form submission."""
    if not is_authenticated():
        return json_error("Please log in first.", 401)

    db_user = get_db_user()
    if not db_user:
        return json_error(
            "User not found. Please log in again.", 401
        )

    # Handle both JSON and form-encoded data
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form.to_dict()

    # Convert checkbox values from form data
    # HTML forms send "on" for checked, nothing for unchecked
    bool_fields = [
        "notifications_enabled",
        "auto_snapshot_enabled",
        "dashboard_show_recent_activity",
    ]
    for field in bool_fields:
        if field in data:
            val = data[field]
            if isinstance(val, str):
                data[field] = val.lower() in (
                    "true",
                    "on",
                    "1",
                    "yes",
                )
        else:
            # Unchecked checkboxes are absent from form data
            # Only set to False if form was submitted
            # (the field would normally be present as hidden)
            if not request.is_json:
                data[field] = False

    # Convert max_snapshots_per_playlist to int
    if "max_snapshots_per_playlist" in data:
        try:
            data["max_snapshots_per_playlist"] = int(
                data["max_snapshots_per_playlist"]
            )
        except (ValueError, TypeError):
            return json_error(
                "Invalid value for max snapshots.", 400
            )

    # Handle empty algorithm as None (no default)
    if data.get("default_algorithm") == "":
        data["default_algorithm"] = None

    try:
        update_request = UserSettingsUpdateRequest(**data)
    except ValidationError as e:
        errors = e.errors()
        msg = errors[0]["msg"] if errors else "Invalid input"
        return json_error(str(msg), 400)

    # Build kwargs from non-None fields only
    update_kwargs = {
        k: v
        for k, v in update_request.model_dump().items()
        if v is not None
    }

    try:
        updated = UserSettingsService.update(
            db_user.id, **update_kwargs
        )
    except UserSettingsError as e:
        return json_error(str(e), 400)

    # For regular form submission, redirect with flash
    if not request.is_json:
        flash("Settings saved successfully.", "success")
        return redirect(url_for("main.settings"))

    # For AJAX, return JSON
    return json_success(
        "Settings saved successfully.",
        settings=updated.to_dict(),
    )
```

---

### Step 8: Update `shuffify/routes/__init__.py`

**Add `settings` to the import list** at the bottom (line 121-128).

**BEFORE** (line 121-128):
```python
from shuffify.routes import (  # noqa: E402, F401
    core,
    playlists,
    shuffle,
    workshop,
    upstream_sources,
    schedules,
)
```

**AFTER**:
```python
from shuffify.routes import (  # noqa: E402, F401
    core,
    playlists,
    shuffle,
    workshop,
    upstream_sources,
    schedules,
    settings,
)
```

---

### Step 9: Create `shuffify/templates/settings.html`

This template follows the established patterns from `schedules.html` -- extends `base.html`, uses the Spotify green gradient, includes a back-to-dashboard link, and uses Tailwind utility classes.

```html
{% extends "base.html" %}

{% block title %}Settings - Shuffify{% endblock %}

{% block content %}
<div class="min-h-screen bg-gradient-to-br from-spotify-green via-spotify-green/90 to-spotify-dark">
    <div class="absolute inset-0" style="background-image: url('/static/images/hero-pattern.svg'); opacity: 0.15; pointer-events: none;"></div>

    <!-- Header -->
    <div class="relative max-w-3xl mx-auto px-4 pt-8">
        <div class="p-6 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20">
            <div class="flex items-center">
                <a href="{{ url_for('main.index') }}"
                   class="mr-4 p-2 rounded-lg bg-white/10 hover:bg-white/20 transition duration-150 border border-white/20"
                   title="Back to Dashboard">
                    <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path>
                    </svg>
                </a>
                <div>
                    <h1 class="text-2xl font-bold text-white">Settings</h1>
                    <p class="text-white/70 text-sm">Customize your Shuffify experience</p>
                </div>
            </div>
        </div>
    </div>

    <!-- Settings Form -->
    <div class="relative max-w-3xl mx-auto px-4 py-6">
        <form id="settings-form" method="POST" action="{{ url_for('main.update_settings') }}" class="space-y-6">

            <!-- Shuffle Defaults Section -->
            <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-6">
                <h2 class="text-lg font-bold text-white mb-4">Shuffle Defaults</h2>

                <div class="space-y-4">
                    <!-- Default Algorithm -->
                    <div>
                        <label for="default_algorithm" class="block text-sm font-medium text-white/90 mb-1">
                            Default Shuffle Algorithm
                        </label>
                        <select id="default_algorithm" name="default_algorithm"
                                class="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white focus:ring-2 focus:ring-white/30 focus:border-transparent">
                            <option value="">No default (choose each time)</option>
                            {% for algo in algorithms %}
                            <option value="{{ algo.class_name }}"
                                    {% if settings.default_algorithm == algo.class_name %}selected{% endif %}>
                                {{ algo.name }}
                            </option>
                            {% endfor %}
                        </select>
                        <p class="mt-1 text-xs text-white/50">
                            Pre-selects this algorithm when shuffling playlists.
                        </p>
                    </div>
                </div>
            </div>

            <!-- Appearance Section -->
            <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-6">
                <h2 class="text-lg font-bold text-white mb-4">Appearance</h2>

                <div>
                    <label for="theme" class="block text-sm font-medium text-white/90 mb-1">
                        Theme
                    </label>
                    <select id="theme" name="theme"
                            class="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white focus:ring-2 focus:ring-white/30 focus:border-transparent">
                        <option value="system" {% if settings.theme == 'system' %}selected{% endif %}>
                            System (follow OS setting)
                        </option>
                        <option value="light" {% if settings.theme == 'light' %}selected{% endif %}>
                            Light
                        </option>
                        <option value="dark" {% if settings.theme == 'dark' %}selected{% endif %}>
                            Dark
                        </option>
                    </select>
                    <p class="mt-1 text-xs text-white/50">
                        Theme support is planned for a future release.
                    </p>
                </div>
            </div>

            <!-- Snapshots & History Section -->
            <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-6">
                <h2 class="text-lg font-bold text-white mb-4">Snapshots &amp; History</h2>

                <div class="space-y-4">
                    <!-- Auto Snapshot -->
                    <div class="flex items-center justify-between">
                        <div>
                            <label for="auto_snapshot_enabled" class="text-sm font-medium text-white/90">
                                Auto-snapshot before shuffle
                            </label>
                            <p class="text-xs text-white/50">
                                Automatically save playlist state before each shuffle.
                            </p>
                        </div>
                        <label class="relative inline-flex items-center cursor-pointer">
                            <input type="hidden" name="auto_snapshot_enabled" value="false">
                            <input type="checkbox" id="auto_snapshot_enabled" name="auto_snapshot_enabled"
                                   value="true" class="sr-only peer"
                                   {% if settings.auto_snapshot_enabled %}checked{% endif %}>
                            <div class="w-11 h-6 bg-white/20 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-white/30 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-spotify-green"></div>
                        </label>
                    </div>

                    <!-- Max Snapshots -->
                    <div>
                        <label for="max_snapshots_per_playlist" class="block text-sm font-medium text-white/90 mb-1">
                            Max snapshots per playlist
                        </label>
                        <input type="number" id="max_snapshots_per_playlist" name="max_snapshots_per_playlist"
                               value="{{ settings.max_snapshots_per_playlist }}"
                               min="1" max="50"
                               class="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white focus:ring-2 focus:ring-white/30 focus:border-transparent">
                        <p class="mt-1 text-xs text-white/50">
                            Oldest snapshots are removed when the limit is reached (1-50).
                        </p>
                    </div>
                </div>
            </div>

            <!-- Dashboard Section -->
            <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-6">
                <h2 class="text-lg font-bold text-white mb-4">Dashboard</h2>

                <div class="flex items-center justify-between">
                    <div>
                        <label for="dashboard_show_recent_activity" class="text-sm font-medium text-white/90">
                            Show recent activity
                        </label>
                        <p class="text-xs text-white/50">
                            Display recent shuffle and raid activity on the dashboard.
                        </p>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer">
                        <input type="hidden" name="dashboard_show_recent_activity" value="false">
                        <input type="checkbox" id="dashboard_show_recent_activity" name="dashboard_show_recent_activity"
                               value="true" class="sr-only peer"
                               {% if settings.dashboard_show_recent_activity %}checked{% endif %}>
                        <div class="w-11 h-6 bg-white/20 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-white/30 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-spotify-green"></div>
                    </label>
                </div>
            </div>

            <!-- Notifications Section -->
            <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-6">
                <h2 class="text-lg font-bold text-white mb-4">Notifications</h2>

                <div class="flex items-center justify-between">
                    <div>
                        <label for="notifications_enabled" class="text-sm font-medium text-white/90">
                            Enable notifications
                        </label>
                        <p class="text-xs text-white/50">
                            Receive notifications about scheduled operations (coming soon).
                        </p>
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer">
                        <input type="hidden" name="notifications_enabled" value="false">
                        <input type="checkbox" id="notifications_enabled" name="notifications_enabled"
                               value="true" class="sr-only peer"
                               {% if settings.notifications_enabled %}checked{% endif %}>
                        <div class="w-11 h-6 bg-white/20 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-white/30 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-spotify-green"></div>
                    </label>
                </div>
            </div>

            <!-- Save Button -->
            <div class="flex justify-end">
                <button type="submit"
                        class="px-8 py-3 rounded-lg bg-white text-spotify-dark font-bold transition duration-150 hover:bg-green-100 shadow-lg">
                    Save Settings
                </button>
            </div>
        </form>
    </div>
</div>

<script>
document.getElementById('settings-form').addEventListener('submit', function(e) {
    e.preventDefault();

    const form = this;
    const submitBtn = form.querySelector('button[type="submit"]');
    const originalText = submitBtn.textContent;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving...';

    const formData = new FormData(form);

    fetch(form.action, {
        method: 'POST',
        body: formData,
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        }
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(d => {
                throw new Error(d.message || 'Failed to save settings.');
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            showNotification(data.message, 'success');
        } else {
            showNotification(data.message || 'Save failed.', 'error');
        }
    })
    .catch(error => {
        showNotification(error.message, 'error');
    })
    .finally(() => {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
    });
});

function showNotification(message, type) {
    const notification = document.createElement('div');
    const bgColor = type === 'success' ? 'bg-green-500/90' :
                    type === 'info' ? 'bg-blue-500/90' : 'bg-red-500/90';
    notification.className = `fixed bottom-4 right-4 px-6 py-3 rounded-lg shadow-lg backdrop-blur-md ${bgColor} text-white font-semibold transform transition duration-300 translate-y-16 opacity-0 z-50`;
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
        notification.classList.remove('translate-y-16', 'opacity-0');
    }, 100);

    setTimeout(() => {
        notification.classList.add('translate-y-16', 'opacity-0');
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}
</script>
{% endblock %}
```

**Key design note on checkbox handling**: HTML forms do not submit unchecked checkboxes. The hidden input before each checkbox (with `value="false"`) ensures the field is always present in form data. When the checkbox IS checked, both values are sent, but the last one (`"true"`) wins in `request.form.to_dict()`. The route handler converts string values to proper booleans.

---

### Step 10: Add Settings Link to Dashboard

**Location**: `/Users/chris/Projects/shuffify/shuffify/templates/dashboard.html`, lines 26-53, inside the header buttons area.

**BEFORE** (lines 26-53, just the `<div class="flex items-center space-x-2">` section):
```html
                <div class="flex items-center space-x-2">
                    <!-- Schedules Link -->
                    <a href="{{ url_for('main.schedules') }}"
                       ...>
                        ...
                        Schedules
                    </a>
                    <!-- Refresh Playlists Button -->
                    ...
                    <!-- Logout Button -->
                    ...
                </div>
```

**AFTER** (insert the Settings link after the Schedules link and before the Refresh button, around line 35):
```html
                    <!-- Settings Link -->
                    <a href="{{ url_for('main.settings') }}"
                       class="inline-flex items-center px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white font-medium transition duration-150 border border-white/20 hover:border-white/30"
                       title="User Settings">
                        <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path>
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path>
                        </svg>
                        Settings
                    </a>
```

The SVG is the standard gear/cog icon from Heroicons, consistent with the existing icon pattern.

---

### Step 11: Tests

#### A. Create `tests/services/test_user_settings_service.py`

Follows the exact pattern from `tests/services/test_user_service.py`.

```python
"""
Tests for UserSettingsService.

Tests cover get-or-create, update, validation, and convenience methods.
"""

import pytest

from shuffify.models.db import db, User, UserSettings
from shuffify.services.user_settings_service import (
    UserSettingsService,
    UserSettingsError,
    MAX_SNAPSHOTS_LIMIT,
    MIN_SNAPSHOTS_LIMIT,
)


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
    """Provide app context."""
    with db_app.app_context():
        yield


@pytest.fixture
def test_user(app_ctx):
    """Create a test user in the database."""
    user = User(
        spotify_id="settings_test_user",
        display_name="Settings Test User",
    )
    db.session.add(user)
    db.session.commit()
    return user


class TestGetOrCreate:
    """Tests for get_or_create."""

    def test_creates_default_settings(
        self, app_ctx, test_user
    ):
        """Should create settings with defaults for new user."""
        settings = UserSettingsService.get_or_create(
            test_user.id
        )

        assert settings is not None
        assert settings.user_id == test_user.id
        assert settings.theme == "system"
        assert settings.default_algorithm is None
        assert settings.notifications_enabled is False
        assert settings.auto_snapshot_enabled is True
        assert settings.max_snapshots_per_playlist == 10
        assert settings.dashboard_show_recent_activity is True
        assert settings.extra is None

    def test_returns_existing_settings(
        self, app_ctx, test_user
    ):
        """Should return existing settings on second call."""
        first = UserSettingsService.get_or_create(
            test_user.id
        )
        first_id = first.id

        second = UserSettingsService.get_or_create(
            test_user.id
        )

        assert second.id == first_id
        # Only one record should exist
        count = UserSettings.query.filter_by(
            user_id=test_user.id
        ).count()
        assert count == 1

    def test_different_users_get_separate_settings(
        self, app_ctx, test_user
    ):
        """Each user gets their own settings record."""
        user2 = User(
            spotify_id="settings_test_user_2",
            display_name="User 2",
        )
        db.session.add(user2)
        db.session.commit()

        s1 = UserSettingsService.get_or_create(
            test_user.id
        )
        s2 = UserSettingsService.get_or_create(user2.id)

        assert s1.id != s2.id
        assert s1.user_id == test_user.id
        assert s2.user_id == user2.id


class TestUpdate:
    """Tests for update."""

    def test_update_theme(self, app_ctx, test_user):
        """Should update theme setting."""
        settings = UserSettingsService.update(
            test_user.id, theme="dark"
        )
        assert settings.theme == "dark"

    def test_update_default_algorithm(
        self, app_ctx, test_user
    ):
        """Should update default algorithm."""
        settings = UserSettingsService.update(
            test_user.id,
            default_algorithm="BalancedShuffle",
        )
        assert settings.default_algorithm == "BalancedShuffle"

    def test_update_multiple_fields(
        self, app_ctx, test_user
    ):
        """Should update multiple fields at once."""
        settings = UserSettingsService.update(
            test_user.id,
            theme="light",
            notifications_enabled=True,
            max_snapshots_per_playlist=20,
        )
        assert settings.theme == "light"
        assert settings.notifications_enabled is True
        assert settings.max_snapshots_per_playlist == 20

    def test_update_auto_creates_settings(
        self, app_ctx, test_user
    ):
        """Should create settings if they do not exist."""
        # No explicit get_or_create call first
        settings = UserSettingsService.update(
            test_user.id, theme="dark"
        )
        assert settings.theme == "dark"
        assert settings.user_id == test_user.id

    def test_update_clear_default_algorithm(
        self, app_ctx, test_user
    ):
        """Should allow clearing the default algorithm."""
        UserSettingsService.update(
            test_user.id,
            default_algorithm="BasicShuffle",
        )
        settings = UserSettingsService.update(
            test_user.id, default_algorithm=None
        )
        assert settings.default_algorithm is None

    def test_update_ignores_unknown_keys(
        self, app_ctx, test_user
    ):
        """Should silently ignore keys not in updatable set."""
        settings = UserSettingsService.update(
            test_user.id,
            theme="dark",
            nonexistent_field="value",
        )
        assert settings.theme == "dark"
        assert not hasattr(settings, "nonexistent_field")

    def test_update_invalid_theme_raises(
        self, app_ctx, test_user
    ):
        """Should raise for invalid theme value."""
        with pytest.raises(
            UserSettingsError, match="Invalid theme"
        ):
            UserSettingsService.update(
                test_user.id, theme="neon"
            )

    def test_update_invalid_algorithm_raises(
        self, app_ctx, test_user
    ):
        """Should raise for invalid algorithm name."""
        with pytest.raises(
            UserSettingsError, match="Invalid algorithm"
        ):
            UserSettingsService.update(
                test_user.id,
                default_algorithm="FakeAlgorithm",
            )

    def test_update_snapshots_too_high_raises(
        self, app_ctx, test_user
    ):
        """Should raise if max_snapshots exceeds limit."""
        with pytest.raises(
            UserSettingsError,
            match="max_snapshots_per_playlist must be",
        ):
            UserSettingsService.update(
                test_user.id,
                max_snapshots_per_playlist=999,
            )

    def test_update_snapshots_too_low_raises(
        self, app_ctx, test_user
    ):
        """Should raise if max_snapshots is below minimum."""
        with pytest.raises(
            UserSettingsError,
            match="max_snapshots_per_playlist must be",
        ):
            UserSettingsService.update(
                test_user.id,
                max_snapshots_per_playlist=0,
            )

    def test_update_snapshots_not_int_raises(
        self, app_ctx, test_user
    ):
        """Should raise if max_snapshots is not an integer."""
        with pytest.raises(
            UserSettingsError,
            match="must be an integer",
        ):
            UserSettingsService.update(
                test_user.id,
                max_snapshots_per_playlist="ten",
            )

    def test_update_boolean_fields(
        self, app_ctx, test_user
    ):
        """Should correctly update boolean fields."""
        settings = UserSettingsService.update(
            test_user.id,
            notifications_enabled=True,
            auto_snapshot_enabled=False,
            dashboard_show_recent_activity=False,
        )
        assert settings.notifications_enabled is True
        assert settings.auto_snapshot_enabled is False
        assert settings.dashboard_show_recent_activity is False

    def test_update_extra_json_field(
        self, app_ctx, test_user
    ):
        """Should store arbitrary JSON in extra field."""
        extra_data = {"beta_features": True, "layout": "grid"}
        settings = UserSettingsService.update(
            test_user.id, extra=extra_data
        )
        assert settings.extra == extra_data


class TestGetDefaultAlgorithm:
    """Tests for get_default_algorithm."""

    def test_returns_none_when_no_settings(
        self, app_ctx, test_user
    ):
        """Should return None when no settings exist."""
        result = UserSettingsService.get_default_algorithm(
            test_user.id
        )
        assert result is None

    def test_returns_none_when_not_set(
        self, app_ctx, test_user
    ):
        """Should return None when algorithm is not configured."""
        UserSettingsService.get_or_create(test_user.id)
        result = UserSettingsService.get_default_algorithm(
            test_user.id
        )
        assert result is None

    def test_returns_algorithm_when_set(
        self, app_ctx, test_user
    ):
        """Should return algorithm name when configured."""
        UserSettingsService.update(
            test_user.id,
            default_algorithm="ArtistSpacingShuffle",
        )
        result = UserSettingsService.get_default_algorithm(
            test_user.id
        )
        assert result == "ArtistSpacingShuffle"

    def test_returns_none_for_nonexistent_user(
        self, app_ctx
    ):
        """Should return None for a user ID with no settings."""
        result = UserSettingsService.get_default_algorithm(
            99999
        )
        assert result is None


class TestUserSettingsModel:
    """Tests for UserSettings model methods."""

    def test_to_dict(self, app_ctx, test_user):
        """Should serialize settings to dictionary."""
        settings = UserSettingsService.get_or_create(
            test_user.id
        )
        d = settings.to_dict()

        assert d["user_id"] == test_user.id
        assert d["theme"] == "system"
        assert d["default_algorithm"] is None
        assert d["default_algorithm_params"] == {}
        assert d["notifications_enabled"] is False
        assert d["auto_snapshot_enabled"] is True
        assert d["max_snapshots_per_playlist"] == 10
        assert d["dashboard_show_recent_activity"] is True
        assert d["extra"] == {}
        assert "created_at" in d
        assert "updated_at" in d

    def test_repr(self, app_ctx, test_user):
        """Should have readable repr."""
        settings = UserSettingsService.get_or_create(
            test_user.id
        )
        repr_str = repr(settings)
        assert "UserSettings" in repr_str
        assert str(test_user.id) in repr_str

    def test_cascade_delete(self, app_ctx, test_user):
        """Settings should be deleted when user is deleted."""
        UserSettingsService.get_or_create(test_user.id)
        assert (
            UserSettings.query.filter_by(
                user_id=test_user.id
            ).count()
            == 1
        )

        db.session.delete(test_user)
        db.session.commit()

        assert (
            UserSettings.query.filter_by(
                user_id=test_user.id
            ).count()
            == 0
        )

    def test_user_relationship(self, app_ctx, test_user):
        """User.settings should return the UserSettings instance."""
        UserSettingsService.get_or_create(test_user.id)
        # Refresh from DB
        db.session.expire(test_user)
        assert test_user.settings is not None
        assert test_user.settings.theme == "system"
```

#### B. Create `tests/test_settings_route.py`

```python
"""
Tests for the /settings routes.

Tests cover rendering the settings page and updating settings.
"""

import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db, User, UserSettings


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
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def test_user(db_app):
    """Create a test user."""
    with db_app.app_context():
        user = User(
            spotify_id="route_test_user",
            display_name="Route Test User",
        )
        db.session.add(user)
        db.session.commit()
        return user


@pytest.fixture
def auth_client(db_app, test_user):
    """Authenticated test client with mocked Spotify auth."""
    with db_app.test_client() as client:
        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "test_refresh",
            }
            sess["user_data"] = {
                "id": "route_test_user",
                "display_name": "Route Test User",
                "images": [],
            }
        yield client


class TestSettingsGetRoute:
    """Tests for GET /settings."""

    def test_redirects_when_not_authenticated(
        self, db_app
    ):
        """Should redirect unauthenticated users."""
        with db_app.test_client() as client:
            response = client.get("/settings")
            assert response.status_code == 302
            assert "index" in response.location or "/" in response.location

    @patch("shuffify.routes.settings.AuthService")
    def test_renders_settings_page(
        self, mock_auth, auth_client, test_user
    ):
        """Should render settings page for authenticated user."""
        mock_client = MagicMock()
        mock_auth.get_authenticated_client.return_value = (
            mock_client
        )
        mock_auth.get_user_data.return_value = {
            "id": "route_test_user",
            "display_name": "Route Test User",
            "images": [],
        }

        response = auth_client.get("/settings")
        assert response.status_code == 200
        assert b"Settings" in response.data


class TestSettingsPostRoute:
    """Tests for POST /settings."""

    @patch("shuffify.routes.settings.AuthService")
    def test_update_settings_via_form(
        self, mock_auth, auth_client, db_app, test_user
    ):
        """Should update settings from form submission."""
        mock_auth.validate_session_token.return_value = True
        mock_auth.get_authenticated_client.return_value = (
            MagicMock()
        )

        with db_app.app_context():
            response = auth_client.post(
                "/settings",
                data={
                    "theme": "dark",
                    "auto_snapshot_enabled": "true",
                    "notifications_enabled": "false",
                    "max_snapshots_per_playlist": "15",
                    "dashboard_show_recent_activity": "true",
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest"
                },
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data["success"] is True

    def test_update_settings_unauthenticated(
        self, db_app
    ):
        """Should reject unauthenticated settings updates."""
        with db_app.test_client() as client:
            response = client.post(
                "/settings",
                data={"theme": "dark"},
                headers={
                    "X-Requested-With": "XMLHttpRequest"
                },
            )
            assert response.status_code in (401, 302)
```

---

### Step 12: Update `shuffify/services/user_service.py` Tests

Add a test in `tests/services/test_user_service.py` to verify that `UserSettings` is auto-created for new users.

Add this test class after the existing `TestUserServiceLookup` class:

```python
class TestUserServiceSettingsAutoCreate:
    """Tests for automatic settings creation on new user."""

    def test_new_user_gets_default_settings(
        self, app_ctx
    ):
        """New user should have UserSettings auto-created."""
        from shuffify.models.db import UserSettings

        user_data = {
            "id": "auto_settings_user",
            "display_name": "Auto Settings User",
            "email": "auto@example.com",
            "images": [],
        }

        user = UserService.upsert_from_spotify(user_data)

        settings = UserSettings.query.filter_by(
            user_id=user.id
        ).first()
        assert settings is not None
        assert settings.theme == "system"

    def test_existing_user_keeps_settings(
        self, app_ctx
    ):
        """Existing user re-login should not create duplicate settings."""
        from shuffify.models.db import UserSettings

        user_data = {
            "id": "keep_settings_user",
            "display_name": "Keep User",
            "images": [],
        }

        user = UserService.upsert_from_spotify(user_data)

        # Verify settings exist
        count_before = UserSettings.query.filter_by(
            user_id=user.id
        ).count()
        assert count_before == 1

        # Re-login (upsert again)
        UserService.upsert_from_spotify(user_data)

        count_after = UserSettings.query.filter_by(
            user_id=user.id
        ).count()
        assert count_after == 1
```

---

## Edge Cases & Important Considerations

### 1. Checkbox Handling in HTML Forms
HTML forms do NOT submit unchecked checkboxes. The template uses a **hidden input trick**: a hidden `<input type="hidden" name="field" value="false">` precedes each checkbox. When the checkbox IS checked, both are submitted but `request.form.to_dict()` takes the last value. When unchecked, only the hidden input's `"false"` is submitted. The route handler converts these strings to booleans.

### 2. One-to-One Integrity
The `unique=True` constraint on `user_id` in the `UserSettings` model prevents duplicate settings. The `get_or_create` pattern ensures idempotent creation. Even if called concurrently, only one record per user will exist (the second insert would fail due to the unique constraint, and the service catches the exception, rolls back, and retries via the existing record).

### 3. Database Migration Strategy
- **Without Alembic** (current state): `db.create_all()` in `shuffify/__init__.py` will create the `user_settings` table automatically on next startup.
- **With Alembic** (after Phase 0): Generate migration: `flask db migrate -m "add user_settings table"` then `flask db upgrade`.

### 4. Lazy Import in UserService
The `UserSettingsService` import inside `upsert_from_spotify()` is intentionally a lazy import to avoid circular dependency issues. The `UserService` module imports from `models.db`, and `UserSettingsService` also imports from `models.db`. While this is not technically circular in this case, lazy imports are the established pattern (see `shuffify/routes/core.py` lines 207-209).

### 5. Settings Failure Does Not Block Login
In `user_service.py`, the settings auto-creation is wrapped in a try/except that logs a warning but allows login to proceed. This matches the existing pattern where database operations are non-blocking for the OAuth login flow (see `shuffify/routes/core.py` lines 195-202).

### 6. Form vs JSON API
The settings route supports both form-encoded (for the HTML form with progressive enhancement) and JSON (for potential future API use). The `request.is_json` check handles the distinction.

### 7. Default Algorithm for Existing Users
Existing users who log in after this feature is deployed will NOT have UserSettings records. The `get_or_create()` call on the settings page handles this gracefully by creating defaults on first access. The auto-create on login only applies to brand-new users.

---

## What NOT to Do

1. **DO NOT** add `UserSettings` fields directly to the `User` model. Use a separate table with a one-to-one relationship to keep the User model clean and allow independent evolution of settings.

2. **DO NOT** store settings in the Flask session. Settings must be persisted in the database so they survive session expiry and work across devices.

3. **DO NOT** make the settings page accessible without authentication. Always check `is_authenticated()` first.

4. **DO NOT** apply the default algorithm to shuffles in this phase. That integration is a separate concern -- this phase only stores the preference. The shuffle route can read `get_default_algorithm()` in a future PR to pre-select the algorithm dropdown.

5. **DO NOT** implement the theme switching logic (CSS changes) in this phase. The theme preference is stored but not yet applied. The template shows a note saying "Theme support is planned for a future release."

6. **DO NOT** create a separate blueprint for settings. All routes in Shuffify use the single `main` Blueprint (see `shuffify/routes/__init__.py`). The `settings.py` module registers routes on `main`, same as `schedules.py`, `shuffle.py`, etc.

7. **DO NOT** forget the hidden input trick for checkboxes. Without the hidden `value="false"` inputs, unchecked checkboxes would be absent from form data and the route would not know to set them to `False`.

8. **DO NOT** use `db.session.merge()` for the get-or-create pattern. Use an explicit query-then-create to match the existing codebase pattern (`user_service.py`).

---

## Verification Checklist

Before marking this phase complete:

- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes (all existing + new tests)
- [ ] `python run.py` starts without errors
- [ ] Navigate to `/settings` while logged in -- page renders with all form fields
- [ ] Change theme to "dark", save -- value persists on page reload
- [ ] Set default algorithm to "BalancedShuffle", save -- value persists
- [ ] Toggle all checkboxes off, save -- all show as unchecked on reload
- [ ] Toggle all checkboxes on, save -- all show as checked on reload
- [ ] Set max snapshots to 25, save -- value persists
- [ ] Set max snapshots to 0 -- rejected with error message
- [ ] Set max snapshots to 51 -- rejected with error message
- [ ] Visit `/settings` as unauthenticated user -- redirected to login
- [ ] New user signup creates `UserSettings` automatically
- [ ] Dashboard shows "Settings" gear icon link in the nav area
- [ ] Deleting a user cascades to delete their settings
- [ ] `CHANGELOG.md` updated under `[Unreleased] > ### Added`

---

## CHANGELOG Entry

```markdown
## [Unreleased]

### Added
- **User Settings** - Persistent user preferences with settings page
  - New `UserSettings` model with default algorithm, theme, snapshot, and notification preferences
  - Settings page accessible from dashboard with gear icon
  - Auto-creates default settings for new users on first login
  - Extensible `extra` JSON field for future preferences
  - Pydantic validation for settings updates
  - Full test coverage for service and routes
```

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/models/db.py` - Add UserSettings model and User.settings relationship
- `/Users/chris/Projects/shuffify/shuffify/services/user_service.py` - Modify upsert_from_spotify to auto-create settings for new users
- `/Users/chris/Projects/shuffify/shuffify/routes/schedules.py` - Pattern reference for creating the new settings route module
- `/Users/chris/Projects/shuffify/shuffify/services/workshop_session_service.py` - CRUD service pattern reference for UserSettingsService
- `/Users/chris/Projects/shuffify/shuffify/templates/schedules.html` - Template pattern reference for settings.html layout and styling
