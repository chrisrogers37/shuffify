# Phase 5: User Database & Persistence

**PR Title:** `feat: Add SQLite persistence with User, WorkshopSession, and UpstreamSource models`

**Risk Level:** Medium -- introduces a new dependency (SQLAlchemy) and modifies the app factory, but does not alter existing session-based features. The database is strictly additive; all existing routes and services continue to function without it.

**Estimated Effort:** 4-5 days for a mid-level engineer, 6-8 days for a junior engineer.

**Files Created:**
- `shuffify/models/__init__.py` -- Package init, exports db instance and all models
- `shuffify/models/db.py` -- SQLAlchemy models (User, WorkshopSession, UpstreamSource)
- `shuffify/services/user_service.py` -- User CRUD and upsert-on-login
- `shuffify/services/workshop_session_service.py` -- Save/load/delete workshop sessions
- `shuffify/services/upstream_source_service.py` -- CRUD for upstream source configs
- `tests/models/__init__.py` -- Test package init
- `tests/models/test_db_models.py` -- Model unit tests
- `tests/services/test_user_service.py` -- UserService tests
- `tests/services/test_workshop_session_service.py` -- WorkshopSessionService tests
- `tests/services/test_upstream_source_service.py` -- UpstreamSourceService tests

**Files Modified:**
- `shuffify/__init__.py` -- Add SQLAlchemy and Flask-Migrate initialization in app factory
- `config.py` -- Add `SQLALCHEMY_DATABASE_URI` and `SQLALCHEMY_TRACK_MODIFICATIONS` settings
- `shuffify/routes.py` -- Add workshop session save/load/delete routes and upstream source routes
- `shuffify/services/__init__.py` -- Export new services and exceptions
- `shuffify/error_handlers.py` -- Add handlers for new database exceptions
- `requirements/base.txt` -- Add Flask-SQLAlchemy and Flask-Migrate
- `CHANGELOG.md` -- Add entry under `[Unreleased]`

**Files Deleted:** None

---

## Context

Currently Shuffify stores all user-specific state in the Flask session (Redis or filesystem). This means:
- Workshop arrangements are lost when the session expires (1 hour `PERMANENT_SESSION_LIFETIME` in `/Users/chris/Projects/shuffify/config.py` line 28).
- There is no concept of a "user record" -- the app only knows about users via the Spotify API response held in `session['user_data']`.
- There is no way to persist upstream source configurations for Phase 6's scheduled operations.
- Undo state is ephemeral and tied to the browser session.

Phase 5 introduces a lightweight SQLite database to address these gaps while preserving the existing session-based architecture as the primary mechanism for real-time state.

The database stores three entity types:
1. **User** -- Created/updated automatically on each OAuth login (upsert pattern). Stores Spotify user ID, display name, email, profile image URL, and timestamps.
2. **WorkshopSession** -- Named save-points of a workshop's track arrangement. Users can save their current workshop state and resume later, even after session expiry.
3. **UpstreamSource** -- Persistent record of a source playlist configuration linked to a target playlist. Created during Phase 4's raiding UI but stored here for Phase 6's scheduled operations.

---

## Dependencies

**Prerequisites:**
- Phase 1 (Workshop Core) must be merged -- the WorkshopSession model persists workshop state, and the save/load routes are accessed from the workshop page.
- Phase 4 (External Raiding) should ideally be merged or in parallel -- the UpstreamSource model references Phase 4's concept of external playlist sources. However, the UpstreamSource model can be created now and the routes that populate it can land in Phase 4 or later.

**What this unlocks:**
- Phase 6: Scheduled Operations -- requires the database for job configs, user records, and upstream source references. Also needs refresh token storage (which Phase 6 adds as a column to the User model).

**New package dependencies:**
- `Flask-SQLAlchemy>=3.1` -- ORM layer on top of SQLite
- `Flask-Migrate>=4.0` -- Alembic-based schema migrations

---

## Detailed Implementation Plan

### Step 1: Add Dependencies to `requirements/base.txt`

**File:** `/Users/chris/Projects/shuffify/requirements/base.txt`

**Current state (11 lines):**
```
Flask>=3.1.0
Flask-Session>=0.8.0
spotipy==2.25.1
python-dotenv==1.1.1
gunicorn==23.0.0
numpy>=1.26.0
pydantic>=2.0.0
requests==2.32.5
python-jose>=3.4.0
cryptography>=43.0.1
redis>=5.0.0
```

**Add these two lines at the end:**
```
Flask-SQLAlchemy>=3.1.0
Flask-Migrate>=4.0.0
```

After adding, run `pip install -r requirements/dev.txt` to install.

---

### Step 2: Add Database Configuration to `config.py`

**File:** `/Users/chris/Projects/shuffify/config.py`

**2a. Add to the `Config` base class (after line 41, after the caching configuration block):**

```python
    # Database configuration
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL', 'sqlite:///shuffify.db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
```

The `sqlite:///shuffify.db` value is a relative URI. Flask-SQLAlchemy resolves relative SQLite paths relative to the Flask app's `instance_path`, which defaults to `<project_root>/instance/`. This means the database file will be created at `instance/shuffify.db`.

**2b. Add to the `ProdConfig` class (after line 67):**

```python
    # Production database - use explicit path or override via DATABASE_URL
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL', 'sqlite:///shuffify.db'
    )
```

**2c. Add to the `DevConfig` class (after line 79):**

```python
    # Development database
    SQLALCHEMY_DATABASE_URI = os.getenv(
        'DATABASE_URL', 'sqlite:///shuffify_dev.db'
    )
```

Using a separate filename (`shuffify_dev.db`) in development prevents accidental data crossover with production.

---

### Step 3: Create the SQLAlchemy Models

**File:** `/Users/chris/Projects/shuffify/shuffify/models/db.py` (NEW FILE)

This file defines the `db` SQLAlchemy instance and all three models.

```python
"""
SQLAlchemy database models for Shuffify.

Defines the User, WorkshopSession, and UpstreamSource models
for persistent storage in SQLite.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from flask_sqlalchemy import SQLAlchemy

logger = logging.getLogger(__name__)

# The SQLAlchemy instance. Initialized with the Flask app in create_app().
db = SQLAlchemy()


class User(db.Model):
    """
    Spotify user record.

    Created or updated on each OAuth login via the upsert pattern.
    Links to all user-specific data (workshop sessions, upstream sources).

    Attributes:
        id: Auto-incrementing primary key.
        spotify_id: The user's Spotify ID (unique, from Spotify API 'id' field).
        display_name: The user's Spotify display name.
        email: The user's Spotify email address (may be None if scope not granted).
        profile_image_url: URL to the user's Spotify profile image.
        created_at: Timestamp when the record was first created.
        updated_at: Timestamp of the most recent login/update.
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    spotify_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    profile_image_url = db.Column(db.String(1024), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    workshop_sessions = db.relationship(
        "WorkshopSession", back_populates="user", cascade="all, delete-orphan"
    )
    upstream_sources = db.relationship(
        "UpstreamSource", back_populates="user", cascade="all, delete-orphan"
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the User to a dictionary."""
        return {
            "id": self.id,
            "spotify_id": self.spotify_id,
            "display_name": self.display_name,
            "email": self.email,
            "profile_image_url": self.profile_image_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<User {self.spotify_id} ({self.display_name})>"


class WorkshopSession(db.Model):
    """
    Saved workshop state for a specific playlist.

    Stores the track URI ordering so users can save their workshop
    arrangement and resume later, even after the Flask session expires.

    Attributes:
        id: Auto-incrementing primary key.
        user_id: FK to the User who owns this session.
        playlist_id: The Spotify playlist ID this session is for.
        session_name: User-provided name for this saved session.
        track_uris_json: JSON-encoded list of track URIs in order.
        created_at: Timestamp when the session was first saved.
        updated_at: Timestamp of the most recent update.
    """

    __tablename__ = "workshop_sessions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    playlist_id = db.Column(db.String(255), nullable=False, index=True)
    session_name = db.Column(db.String(255), nullable=False)
    track_uris_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user = db.relationship("User", back_populates="workshop_sessions")

    # Composite index for efficient lookup: "all sessions for user X on playlist Y"
    __table_args__ = (
        db.Index("ix_workshop_user_playlist", "user_id", "playlist_id"),
    )

    @property
    def track_uris(self) -> List[str]:
        """Deserialize the stored JSON into a list of URI strings."""
        import json

        if not self.track_uris_json:
            return []
        try:
            return json.loads(self.track_uris_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                f"Failed to decode track_uris_json for WorkshopSession {self.id}"
            )
            return []

    @track_uris.setter
    def track_uris(self, uris: List[str]) -> None:
        """Serialize a list of URI strings to JSON for storage."""
        import json

        self.track_uris_json = json.dumps(uris)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the WorkshopSession to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "playlist_id": self.playlist_id,
            "session_name": self.session_name,
            "track_uris": self.track_uris,
            "track_count": len(self.track_uris),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<WorkshopSession {self.id}: '{self.session_name}' "
            f"for playlist {self.playlist_id}>"
        )


class UpstreamSource(db.Model):
    """
    Persistent record of an external playlist source configuration.

    Links a source playlist to a target playlist for a specific user.
    Created during Phase 4's raiding UI and persisted here for
    Phase 6's scheduled operations.

    Attributes:
        id: Auto-incrementing primary key.
        user_id: FK to the User who configured this source.
        target_playlist_id: The Spotify playlist ID being built/modified.
        source_playlist_id: The Spotify playlist ID to pull tracks from.
        source_url: The original URL used to find this source (if entered by URL).
        source_type: Either 'own' (user's own playlist) or 'external' (public playlist).
        source_name: Display name of the source playlist (for UI convenience).
        created_at: Timestamp when the source was first configured.
    """

    __tablename__ = "upstream_sources"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    target_playlist_id = db.Column(db.String(255), nullable=False, index=True)
    source_playlist_id = db.Column(db.String(255), nullable=False)
    source_url = db.Column(db.String(1024), nullable=True)
    source_type = db.Column(
        db.String(20), nullable=False, default="external"
    )  # 'own' or 'external'
    source_name = db.Column(db.String(255), nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    user = db.relationship("User", back_populates="upstream_sources")

    # Composite index: "all sources for user X targeting playlist Y"
    __table_args__ = (
        db.Index("ix_upstream_user_target", "user_id", "target_playlist_id"),
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the UpstreamSource to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "target_playlist_id": self.target_playlist_id,
            "source_playlist_id": self.source_playlist_id,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<UpstreamSource {self.id}: {self.source_type} "
            f"'{self.source_playlist_id}' -> '{self.target_playlist_id}'>"
        )
```

---

### Step 4: Create the Models Package Init

**File:** `/Users/chris/Projects/shuffify/shuffify/models/__init__.py` (NEW FILE)

Currently the `models/` directory has no `__init__.py`. Create one that exports the `db` instance and all models.

```python
"""
Shuffify Models Package.

Exports the SQLAlchemy database instance and all database models.
Also exports the existing Playlist dataclass for backward compatibility.

Usage:
    from shuffify.models import db, User, WorkshopSession, UpstreamSource
    from shuffify.models import Playlist  # existing dataclass
"""

from shuffify.models.db import db, User, WorkshopSession, UpstreamSource
from shuffify.models.playlist import Playlist

__all__ = [
    "db",
    "User",
    "WorkshopSession",
    "UpstreamSource",
    "Playlist",
]
```

---

### Step 5: Initialize SQLAlchemy in the App Factory

**File:** `/Users/chris/Projects/shuffify/shuffify/__init__.py`

**5a. Add imports at the top of the file (after line 6, after `import redis`):**

```python
from flask_migrate import Migrate
```

**5b. Add a module-level variable for the Migrate instance (after line 13, after `_redis_client`):**

```python
_migrate: Optional[Migrate] = None
```

**5c. Add database initialization in `create_app()`.** Insert the following block after the Flask-Session initialization (after line 141 `Session(app)`) and before the blueprint registration (before line 144 `from shuffify.routes import main`):

```python
    # Initialize SQLAlchemy database
    _db_initialized = False
    try:
        from shuffify.models.db import db

        db.init_app(app)

        global _migrate
        _migrate = Migrate(app, db)

        # Create tables if they don't exist (development convenience)
        # In production, use Flask-Migrate/Alembic for schema changes
        with app.app_context():
            db.create_all()

        _db_initialized = True
        logger.info("SQLAlchemy database initialized: %s",
                     app.config.get("SQLALCHEMY_DATABASE_URI", "not set"))
    except Exception as e:
        logger.warning(
            "Database initialization failed: %s. "
            "Persistence features will be unavailable.", e
        )
        _db_initialized = False
```

This pattern matches the existing graceful-degradation approach used for Redis (lines 114-138 in the current `__init__.py`). If the database fails to initialize, the app still starts -- only persistence-specific routes will return errors.

**5d. Add a helper function to check database availability (after the `get_spotify_cache` function, around line 76):**

```python
def is_db_available() -> bool:
    """
    Check if the SQLAlchemy database is initialized and available.

    Returns:
        True if database is available, False otherwise.
    """
    try:
        from shuffify.models.db import db
        from flask import current_app

        # Verify we're in app context and db is initialized
        if not current_app:
            return False
        # Quick test query
        db.session.execute(db.text("SELECT 1"))
        return True
    except Exception:
        return False
```

---

### Step 6: Verify `.gitignore` Already Ignores `instance/`

**File:** `/Users/chris/Projects/shuffify/.gitignore`

Looking at line 41 of the current `.gitignore`:
```
instance/
```

This is already present. No change needed. The SQLite database file (`instance/shuffify.db` and `instance/shuffify_dev.db`) will be properly ignored.

---

### Step 7: Create the UserService

**File:** `/Users/chris/Projects/shuffify/shuffify/services/user_service.py` (NEW FILE)

```python
"""
User service for managing user records in the database.

Handles user creation, retrieval, and the upsert-on-login pattern.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from shuffify.models.db import db, User

logger = logging.getLogger(__name__)


class UserServiceError(Exception):
    """Base exception for user service operations."""

    pass


class UserNotFoundError(UserServiceError):
    """Raised when a user cannot be found."""

    pass


class UserService:
    """Service for managing User records."""

    @staticmethod
    def upsert_from_spotify(user_data: Dict[str, Any]) -> User:
        """
        Create or update a User from Spotify profile data.

        Called on every successful OAuth login. If the user already exists
        (matched by spotify_id), update their profile fields and timestamp.
        If the user does not exist, create a new record.

        Args:
            user_data: The Spotify user profile dictionary. Expected keys:
                - 'id' (str): Spotify user ID (REQUIRED)
                - 'display_name' (str): Display name
                - 'email' (str): Email address
                - 'images' (list): List of image dicts with 'url' key

        Returns:
            The User instance (created or updated).

        Raises:
            UserServiceError: If the upsert fails.
        """
        spotify_id = user_data.get("id")
        if not spotify_id:
            raise UserServiceError("Spotify user data missing 'id' field")

        # Extract profile image URL (first image if available)
        images = user_data.get("images", [])
        profile_image_url = images[0].get("url") if images else None

        try:
            user = User.query.filter_by(spotify_id=spotify_id).first()

            if user:
                # Update existing user
                user.display_name = user_data.get("display_name")
                user.email = user_data.get("email")
                user.profile_image_url = profile_image_url
                user.updated_at = datetime.now(timezone.utc)
                logger.info(
                    f"Updated existing user: {spotify_id} "
                    f"({user_data.get('display_name', 'Unknown')})"
                )
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
            logger.error(f"Failed to upsert user {spotify_id}: {e}", exc_info=True)
            raise UserServiceError(f"Failed to save user record: {e}")

    @staticmethod
    def get_by_spotify_id(spotify_id: str) -> Optional[User]:
        """
        Look up a user by their Spotify ID.

        Args:
            spotify_id: The Spotify user ID.

        Returns:
            User instance if found, None otherwise.
        """
        if not spotify_id:
            return None
        return User.query.filter_by(spotify_id=spotify_id).first()

    @staticmethod
    def get_by_id(user_id: int) -> Optional[User]:
        """
        Look up a user by their internal database ID.

        Args:
            user_id: The internal auto-increment ID.

        Returns:
            User instance if found, None otherwise.
        """
        return db.session.get(User, user_id)
```

---

### Step 8: Create the WorkshopSessionService

**File:** `/Users/chris/Projects/shuffify/shuffify/services/workshop_session_service.py` (NEW FILE)

```python
"""
Workshop session service for saving and loading workshop state.

Handles CRUD operations for WorkshopSession records, enabling users
to save their track arrangements and resume later.
"""

import json
import logging
from typing import Dict, Any, List, Optional

from shuffify.models.db import db, WorkshopSession, User

logger = logging.getLogger(__name__)

# Maximum number of saved sessions per user per playlist
MAX_SESSIONS_PER_PLAYLIST = 10


class WorkshopSessionError(Exception):
    """Base exception for workshop session operations."""

    pass


class WorkshopSessionNotFoundError(WorkshopSessionError):
    """Raised when a workshop session cannot be found."""

    pass


class WorkshopSessionLimitError(WorkshopSessionError):
    """Raised when user has too many saved sessions for a playlist."""

    pass


class WorkshopSessionService:
    """Service for managing saved workshop sessions."""

    @staticmethod
    def save_session(
        spotify_id: str,
        playlist_id: str,
        session_name: str,
        track_uris: List[str],
    ) -> WorkshopSession:
        """
        Save a workshop session for a user.

        Creates a new WorkshopSession record with the given track order.

        Args:
            spotify_id: The Spotify user ID.
            playlist_id: The Spotify playlist ID.
            session_name: A user-provided name for this saved session.
            track_uris: Ordered list of track URIs.

        Returns:
            The created WorkshopSession instance.

        Raises:
            WorkshopSessionError: If the user does not exist in the database.
            WorkshopSessionLimitError: If the user already has the max
                number of sessions for this playlist.
        """
        if not session_name or not session_name.strip():
            raise WorkshopSessionError("Session name cannot be empty")

        session_name = session_name.strip()

        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise WorkshopSessionError(
                f"User not found for spotify_id: {spotify_id}. "
                f"User must be logged in first."
            )

        # Check session limit per playlist
        existing_count = WorkshopSession.query.filter_by(
            user_id=user.id, playlist_id=playlist_id
        ).count()

        if existing_count >= MAX_SESSIONS_PER_PLAYLIST:
            raise WorkshopSessionLimitError(
                f"Maximum of {MAX_SESSIONS_PER_PLAYLIST} saved sessions "
                f"per playlist reached. Delete an existing session first."
            )

        try:
            ws = WorkshopSession(
                user_id=user.id,
                playlist_id=playlist_id,
                session_name=session_name,
            )
            ws.track_uris = track_uris  # uses the property setter

            db.session.add(ws)
            db.session.commit()

            logger.info(
                f"Saved workshop session '{session_name}' for user "
                f"{spotify_id}, playlist {playlist_id} "
                f"({len(track_uris)} tracks)"
            )
            return ws

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to save workshop session: {e}", exc_info=True
            )
            raise WorkshopSessionError(f"Failed to save session: {e}")

    @staticmethod
    def list_sessions(
        spotify_id: str, playlist_id: str
    ) -> List[WorkshopSession]:
        """
        List all saved workshop sessions for a user and playlist.

        Args:
            spotify_id: The Spotify user ID.
            playlist_id: The Spotify playlist ID.

        Returns:
            List of WorkshopSession instances, ordered by most recent first.
        """
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            return []

        return (
            WorkshopSession.query.filter_by(
                user_id=user.id, playlist_id=playlist_id
            )
            .order_by(WorkshopSession.updated_at.desc())
            .all()
        )

    @staticmethod
    def get_session(
        session_id: int, spotify_id: str
    ) -> Optional[WorkshopSession]:
        """
        Get a specific workshop session by ID.

        Validates that the session belongs to the given user.

        Args:
            session_id: The workshop session database ID.
            spotify_id: The Spotify user ID (for ownership check).

        Returns:
            WorkshopSession if found and owned by user, None otherwise.

        Raises:
            WorkshopSessionNotFoundError: If session not found or not owned.
        """
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise WorkshopSessionNotFoundError("User not found")

        ws = db.session.get(WorkshopSession, session_id)
        if not ws or ws.user_id != user.id:
            raise WorkshopSessionNotFoundError(
                f"Workshop session {session_id} not found"
            )

        return ws

    @staticmethod
    def update_session(
        session_id: int,
        spotify_id: str,
        track_uris: List[str],
        session_name: Optional[str] = None,
    ) -> WorkshopSession:
        """
        Update an existing workshop session with a new track order.

        Args:
            session_id: The workshop session database ID.
            spotify_id: The Spotify user ID (for ownership check).
            track_uris: The new ordered list of track URIs.
            session_name: Optional new name for the session.

        Returns:
            The updated WorkshopSession instance.

        Raises:
            WorkshopSessionNotFoundError: If session not found or not owned.
            WorkshopSessionError: If the update fails.
        """
        ws = WorkshopSessionService.get_session(session_id, spotify_id)

        try:
            ws.track_uris = track_uris
            if session_name is not None:
                ws.session_name = session_name.strip()
            db.session.commit()

            logger.info(
                f"Updated workshop session {session_id}: "
                f"'{ws.session_name}' ({len(track_uris)} tracks)"
            )
            return ws

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to update workshop session {session_id}: {e}",
                exc_info=True,
            )
            raise WorkshopSessionError(f"Failed to update session: {e}")

    @staticmethod
    def delete_session(session_id: int, spotify_id: str) -> bool:
        """
        Delete a saved workshop session.

        Args:
            session_id: The workshop session database ID.
            spotify_id: The Spotify user ID (for ownership check).

        Returns:
            True if deleted successfully.

        Raises:
            WorkshopSessionNotFoundError: If session not found or not owned.
            WorkshopSessionError: If the deletion fails.
        """
        ws = WorkshopSessionService.get_session(session_id, spotify_id)

        try:
            db.session.delete(ws)
            db.session.commit()
            logger.info(f"Deleted workshop session {session_id}")
            return True

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to delete workshop session {session_id}: {e}",
                exc_info=True,
            )
            raise WorkshopSessionError(f"Failed to delete session: {e}")
```

---

### Step 9: Create the UpstreamSourceService

**File:** `/Users/chris/Projects/shuffify/shuffify/services/upstream_source_service.py` (NEW FILE)

```python
"""
Upstream source service for managing persistent source configurations.

Handles CRUD operations for UpstreamSource records, which link a source
playlist to a target playlist for a specific user.
"""

import logging
from typing import Dict, Any, List, Optional

from shuffify.models.db import db, UpstreamSource, User

logger = logging.getLogger(__name__)


class UpstreamSourceError(Exception):
    """Base exception for upstream source operations."""

    pass


class UpstreamSourceNotFoundError(UpstreamSourceError):
    """Raised when an upstream source cannot be found."""

    pass


class UpstreamSourceService:
    """Service for managing UpstreamSource records."""

    @staticmethod
    def add_source(
        spotify_id: str,
        target_playlist_id: str,
        source_playlist_id: str,
        source_type: str = "external",
        source_url: Optional[str] = None,
        source_name: Optional[str] = None,
    ) -> UpstreamSource:
        """
        Add an upstream source configuration.

        Args:
            spotify_id: The Spotify user ID.
            target_playlist_id: The playlist being built/modified.
            source_playlist_id: The playlist to pull tracks from.
            source_type: Either 'own' or 'external'.
            source_url: The original URL used to find this source.
            source_name: Display name of the source playlist.

        Returns:
            The created UpstreamSource instance.

        Raises:
            UpstreamSourceError: If the user does not exist or creation fails.
        """
        if source_type not in ("own", "external"):
            raise UpstreamSourceError(
                f"Invalid source_type: {source_type}. Must be 'own' or 'external'."
            )

        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise UpstreamSourceError(
                f"User not found for spotify_id: {spotify_id}"
            )

        # Check for duplicate: same user, target, and source
        existing = UpstreamSource.query.filter_by(
            user_id=user.id,
            target_playlist_id=target_playlist_id,
            source_playlist_id=source_playlist_id,
        ).first()

        if existing:
            logger.info(
                f"Upstream source already exists: {source_playlist_id} -> "
                f"{target_playlist_id} for user {spotify_id}"
            )
            return existing

        try:
            source = UpstreamSource(
                user_id=user.id,
                target_playlist_id=target_playlist_id,
                source_playlist_id=source_playlist_id,
                source_url=source_url,
                source_type=source_type,
                source_name=source_name,
            )
            db.session.add(source)
            db.session.commit()

            logger.info(
                f"Added upstream source: {source_playlist_id} -> "
                f"{target_playlist_id} for user {spotify_id} "
                f"(type={source_type})"
            )
            return source

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to add upstream source: {e}", exc_info=True
            )
            raise UpstreamSourceError(f"Failed to add source: {e}")

    @staticmethod
    def list_sources(
        spotify_id: str, target_playlist_id: str
    ) -> List[UpstreamSource]:
        """
        List all upstream sources for a user's target playlist.

        Args:
            spotify_id: The Spotify user ID.
            target_playlist_id: The target playlist ID.

        Returns:
            List of UpstreamSource instances.
        """
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            return []

        return (
            UpstreamSource.query.filter_by(
                user_id=user.id, target_playlist_id=target_playlist_id
            )
            .order_by(UpstreamSource.created_at.desc())
            .all()
        )

    @staticmethod
    def get_source(
        source_id: int, spotify_id: str
    ) -> UpstreamSource:
        """
        Get a specific upstream source by ID.

        Args:
            source_id: The upstream source database ID.
            spotify_id: The Spotify user ID (for ownership check).

        Returns:
            UpstreamSource instance.

        Raises:
            UpstreamSourceNotFoundError: If not found or not owned by user.
        """
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            raise UpstreamSourceNotFoundError("User not found")

        source = db.session.get(UpstreamSource, source_id)
        if not source or source.user_id != user.id:
            raise UpstreamSourceNotFoundError(
                f"Upstream source {source_id} not found"
            )
        return source

    @staticmethod
    def delete_source(source_id: int, spotify_id: str) -> bool:
        """
        Delete an upstream source configuration.

        Args:
            source_id: The upstream source database ID.
            spotify_id: The Spotify user ID (for ownership check).

        Returns:
            True if deleted successfully.

        Raises:
            UpstreamSourceNotFoundError: If not found or not owned.
            UpstreamSourceError: If deletion fails.
        """
        source = UpstreamSourceService.get_source(source_id, spotify_id)

        try:
            db.session.delete(source)
            db.session.commit()
            logger.info(f"Deleted upstream source {source_id}")
            return True

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to delete upstream source {source_id}: {e}",
                exc_info=True,
            )
            raise UpstreamSourceError(f"Failed to delete source: {e}")

    @staticmethod
    def list_all_sources_for_user(
        spotify_id: str,
    ) -> List[UpstreamSource]:
        """
        List ALL upstream sources for a user, across all target playlists.

        Args:
            spotify_id: The Spotify user ID.

        Returns:
            List of UpstreamSource instances.
        """
        user = User.query.filter_by(spotify_id=spotify_id).first()
        if not user:
            return []

        return (
            UpstreamSource.query.filter_by(user_id=user.id)
            .order_by(UpstreamSource.created_at.desc())
            .all()
        )
```

---

### Step 10: Update the Services Package Init

**File:** `/Users/chris/Projects/shuffify/shuffify/services/__init__.py`

Add imports for the three new services and their exceptions. Insert after the existing `StateService` imports (after line 56):

```python
# User Service
from shuffify.services.user_service import (
    UserService,
    UserServiceError,
    UserNotFoundError,
)

# Workshop Session Service
from shuffify.services.workshop_session_service import (
    WorkshopSessionService,
    WorkshopSessionError,
    WorkshopSessionNotFoundError,
    WorkshopSessionLimitError,
)

# Upstream Source Service
from shuffify.services.upstream_source_service import (
    UpstreamSourceService,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
)
```

Add the new names to the `__all__` list:

```python
    # User Service
    "UserService",
    "UserServiceError",
    "UserNotFoundError",
    # Workshop Session Service
    "WorkshopSessionService",
    "WorkshopSessionError",
    "WorkshopSessionNotFoundError",
    "WorkshopSessionLimitError",
    # Upstream Source Service
    "UpstreamSourceService",
    "UpstreamSourceError",
    "UpstreamSourceNotFoundError",
```

---

### Step 11: Add User Upsert to the OAuth Callback Route

**File:** `/Users/chris/Projects/shuffify/shuffify/routes.py`

**11a. Add import for UserService.** Update the import block at line 22-31. After the existing `StateService` import, add `UserService`:

```python
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    StateService,
    UserService,
    AuthenticationError,
    PlaylistError,
    PlaylistUpdateError,
)
```

**11b. Add user upsert in the `/callback` route.** In the `callback()` function (currently line 192-234), after line 222 (`session["user_data"] = user_data`) and before line 223 (`session.modified = True`), add:

```python
        # Upsert user record in database (non-blocking)
        try:
            UserService.upsert_from_spotify(user_data)
        except Exception as e:
            # Database failure should NOT block login
            logger.warning(
                f"Failed to upsert user to database: {e}. "
                f"Login continues without persistence."
            )
```

This is deliberately wrapped in a bare `except` with a warning log. The user can still log in and use all session-based features even if the database is down. Only persistence features (save/load workshop sessions) will be affected.

---

### Step 12: Add Workshop Session Routes

**File:** `/Users/chris/Projects/shuffify/shuffify/routes.py`

Add imports for the new services and the `is_db_available` helper. At the top of the file, add to the existing import block:

```python
from shuffify.services import (
    # ... existing imports ...
    WorkshopSessionService,
    WorkshopSessionError,
    WorkshopSessionNotFoundError,
    WorkshopSessionLimitError,
    UpstreamSourceService,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
)
from shuffify import is_db_available
```

Add the following route section at the end of the file (after the existing Workshop Routes section that Phase 1 adds):

```python
# =============================================================================
# Workshop Session Persistence Routes
# =============================================================================


@main.route("/workshop/<playlist_id>/sessions", methods=["GET"])
def list_workshop_sessions(playlist_id):
    """List all saved workshop sessions for a playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable. Cannot load saved sessions.", 503)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    sessions = WorkshopSessionService.list_sessions(spotify_id, playlist_id)
    return jsonify({
        "success": True,
        "sessions": [ws.to_dict() for ws in sessions],
    })


@main.route("/workshop/<playlist_id>/sessions", methods=["POST"])
def save_workshop_session(playlist_id):
    """Save the current workshop state as a named session."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable. Cannot save session.", 503)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    session_name = data.get("session_name", "").strip()
    track_uris = data.get("track_uris", [])

    if not session_name:
        return json_error("Session name is required.", 400)

    if not isinstance(track_uris, list):
        return json_error("track_uris must be a list.", 400)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        ws = WorkshopSessionService.save_session(
            spotify_id=spotify_id,
            playlist_id=playlist_id,
            session_name=session_name,
            track_uris=track_uris,
        )
        logger.info(
            f"User {spotify_id} saved workshop session "
            f"'{session_name}' for playlist {playlist_id}"
        )
        return json_success(
            f"Session '{session_name}' saved.",
            session=ws.to_dict(),
        )
    except WorkshopSessionLimitError as e:
        return json_error(str(e), 400)
    except WorkshopSessionError as e:
        return json_error(str(e), 500)


@main.route("/workshop/sessions/<int:session_id>", methods=["GET"])
def load_workshop_session(session_id):
    """Load a saved workshop session by ID."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable. Cannot load session.", 503)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        ws = WorkshopSessionService.get_session(session_id, spotify_id)
        return jsonify({
            "success": True,
            "session": ws.to_dict(),
        })
    except WorkshopSessionNotFoundError:
        return json_error("Saved session not found.", 404)


@main.route("/workshop/sessions/<int:session_id>", methods=["PUT"])
def update_workshop_session(session_id):
    """Update an existing saved workshop session."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable. Cannot update session.", 503)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    track_uris = data.get("track_uris")
    session_name = data.get("session_name")

    if track_uris is not None and not isinstance(track_uris, list):
        return json_error("track_uris must be a list.", 400)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        ws = WorkshopSessionService.update_session(
            session_id=session_id,
            spotify_id=spotify_id,
            track_uris=track_uris if track_uris is not None else [],
            session_name=session_name,
        )
        return json_success(
            f"Session '{ws.session_name}' updated.",
            session=ws.to_dict(),
        )
    except WorkshopSessionNotFoundError:
        return json_error("Saved session not found.", 404)
    except WorkshopSessionError as e:
        return json_error(str(e), 500)


@main.route("/workshop/sessions/<int:session_id>", methods=["DELETE"])
def delete_workshop_session(session_id):
    """Delete a saved workshop session."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable. Cannot delete session.", 503)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        WorkshopSessionService.delete_session(session_id, spotify_id)
        return json_success("Session deleted.")
    except WorkshopSessionNotFoundError:
        return json_error("Saved session not found.", 404)
    except WorkshopSessionError as e:
        return json_error(str(e), 500)


# =============================================================================
# Upstream Source Routes
# =============================================================================


@main.route("/playlist/<playlist_id>/upstream-sources", methods=["GET"])
def list_upstream_sources(playlist_id):
    """List all upstream sources for a target playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable.", 503)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    sources = UpstreamSourceService.list_sources(spotify_id, playlist_id)
    return jsonify({
        "success": True,
        "sources": [s.to_dict() for s in sources],
    })


@main.route("/playlist/<playlist_id>/upstream-sources", methods=["POST"])
def add_upstream_source(playlist_id):
    """Add an upstream source to a target playlist."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable.", 503)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    source_playlist_id = data.get("source_playlist_id")
    if not source_playlist_id:
        return json_error("source_playlist_id is required.", 400)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        source = UpstreamSourceService.add_source(
            spotify_id=spotify_id,
            target_playlist_id=playlist_id,
            source_playlist_id=source_playlist_id,
            source_type=data.get("source_type", "external"),
            source_url=data.get("source_url"),
            source_name=data.get("source_name"),
        )
        return json_success(
            "Source added.",
            source=source.to_dict(),
        )
    except UpstreamSourceError as e:
        return json_error(str(e), 400)


@main.route("/upstream-sources/<int:source_id>", methods=["DELETE"])
def delete_upstream_source(source_id):
    """Delete an upstream source configuration."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    if not is_db_available():
        return json_error("Database is unavailable.", 503)

    user_data = session.get("user_data", {})
    spotify_id = user_data.get("id")
    if not spotify_id:
        return json_error("User data not found in session.", 401)

    try:
        UpstreamSourceService.delete_source(source_id, spotify_id)
        return json_success("Source removed.")
    except UpstreamSourceNotFoundError:
        return json_error("Source not found.", 404)
    except UpstreamSourceError as e:
        return json_error(str(e), 500)
```

---

### Step 13: Add Error Handlers for New Exceptions

**File:** `/Users/chris/Projects/shuffify/shuffify/error_handlers.py`

**13a. Add imports** (after the existing `StateService` exception imports, around line 25):

```python
from shuffify.services import (
    # ... existing imports ...
    UserServiceError,
    UserNotFoundError,
    WorkshopSessionError,
    WorkshopSessionNotFoundError,
    WorkshopSessionLimitError,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
)
```

**13b. Add handlers** inside the `register_error_handlers` function. Add after the State Errors section (after line 162):

```python
    # =========================================================================
    # Database / Persistence Errors
    # =========================================================================

    @app.errorhandler(UserServiceError)
    def handle_user_service_error(error: UserServiceError):
        """Handle user service errors."""
        logger.error(f"User service error: {error}")
        return json_error_response("User operation failed.", 500)

    @app.errorhandler(UserNotFoundError)
    def handle_user_not_found(error: UserNotFoundError):
        """Handle user not found errors."""
        logger.info(f"User not found: {error}")
        return json_error_response("User not found.", 404)

    @app.errorhandler(WorkshopSessionNotFoundError)
    def handle_workshop_session_not_found(error: WorkshopSessionNotFoundError):
        """Handle workshop session not found."""
        logger.info(f"Workshop session not found: {error}")
        return json_error_response("Saved session not found.", 404)

    @app.errorhandler(WorkshopSessionLimitError)
    def handle_workshop_session_limit(error: WorkshopSessionLimitError):
        """Handle workshop session limit exceeded."""
        logger.warning(f"Workshop session limit: {error}")
        return json_error_response(str(error), 400)

    @app.errorhandler(WorkshopSessionError)
    def handle_workshop_session_error(error: WorkshopSessionError):
        """Handle general workshop session errors."""
        logger.error(f"Workshop session error: {error}")
        return json_error_response("Workshop session operation failed.", 500)

    @app.errorhandler(UpstreamSourceNotFoundError)
    def handle_upstream_source_not_found(error: UpstreamSourceNotFoundError):
        """Handle upstream source not found."""
        logger.info(f"Upstream source not found: {error}")
        return json_error_response("Source not found.", 404)

    @app.errorhandler(UpstreamSourceError)
    def handle_upstream_source_error(error: UpstreamSourceError):
        """Handle general upstream source errors."""
        logger.error(f"Upstream source error: {error}")
        return json_error_response("Source operation failed.", 500)
```

---

### Step 14: Set Up Flask-Migrate (Alembic)

After all code changes are in place, run the following commands to initialize Alembic migrations:

```bash
# Set up the migration directory (run once)
flask db init

# Create the initial migration
flask db migrate -m "Initial schema: User, WorkshopSession, UpstreamSource"

# Apply the migration
flask db upgrade
```

This creates a `migrations/` directory at the project root. Commit this directory to git. Future schema changes should use `flask db migrate -m "description"` and `flask db upgrade`.

For tests that create an in-memory database, `db.create_all()` is used directly (see Test Plan below).

---

## Test Plan

### Test File: `tests/models/__init__.py` (NEW FILE)

```python
"""Tests for Shuffify database models."""
```

### Test File: `tests/models/test_db_models.py` (NEW FILE)

```python
"""
Tests for SQLAlchemy database models.

Tests cover User, WorkshopSession, and UpstreamSource models
including creation, relationships, serialization, and constraints.
"""

import json
import pytest
from datetime import datetime, timezone

from shuffify.models.db import db, User, WorkshopSession, UpstreamSource


@pytest.fixture
def db_app():
    """Create a Flask app with an in-memory SQLite database for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True

    # Re-initialize db with in-memory URI
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def db_session(db_app):
    """Provide a database session within app context."""
    with db_app.app_context():
        yield db.session


class TestUserModel:
    """Tests for the User model."""

    def test_create_user(self, db_session):
        """Should create a user with required fields."""
        user = User(
            spotify_id="user123",
            display_name="Test User",
            email="test@example.com",
            profile_image_url="https://example.com/avatar.jpg",
        )
        db_session.add(user)
        db_session.commit()

        assert user.id is not None
        assert user.spotify_id == "user123"
        assert user.display_name == "Test User"
        assert user.created_at is not None
        assert user.updated_at is not None

    def test_spotify_id_is_unique(self, db_session):
        """Should not allow duplicate spotify_id values."""
        user1 = User(spotify_id="user123", display_name="User 1")
        db_session.add(user1)
        db_session.commit()

        user2 = User(spotify_id="user123", display_name="User 2")
        db_session.add(user2)

        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()
        db_session.rollback()

    def test_spotify_id_is_required(self, db_session):
        """Should not allow null spotify_id."""
        user = User(display_name="No ID User")
        db_session.add(user)

        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()
        db_session.rollback()

    def test_to_dict(self, db_session):
        """Should serialize all fields to a dictionary."""
        user = User(
            spotify_id="user123",
            display_name="Test User",
            email="test@example.com",
        )
        db_session.add(user)
        db_session.commit()

        d = user.to_dict()
        assert d["spotify_id"] == "user123"
        assert d["display_name"] == "Test User"
        assert d["email"] == "test@example.com"
        assert "created_at" in d
        assert "updated_at" in d

    def test_repr(self, db_session):
        """Should have a useful string representation."""
        user = User(spotify_id="user123", display_name="Test User")
        assert "user123" in repr(user)
        assert "Test User" in repr(user)

    def test_nullable_optional_fields(self, db_session):
        """Should allow null for optional fields."""
        user = User(spotify_id="minimal_user")
        db_session.add(user)
        db_session.commit()

        assert user.display_name is None
        assert user.email is None
        assert user.profile_image_url is None


class TestWorkshopSessionModel:
    """Tests for the WorkshopSession model."""

    @pytest.fixture
    def user(self, db_session):
        """Create a test user."""
        user = User(spotify_id="user123", display_name="Test User")
        db_session.add(user)
        db_session.commit()
        return user

    def test_create_workshop_session(self, db_session, user):
        """Should create a workshop session with track URIs."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="playlist456",
            session_name="My Arrangement",
        )
        ws.track_uris = ["spotify:track:a", "spotify:track:b", "spotify:track:c"]

        db_session.add(ws)
        db_session.commit()

        assert ws.id is not None
        assert ws.track_uris == ["spotify:track:a", "spotify:track:b", "spotify:track:c"]
        assert ws.session_name == "My Arrangement"

    def test_track_uris_property_getter(self, db_session, user):
        """Should deserialize JSON to list."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
            track_uris_json='["uri1", "uri2"]',
        )
        db_session.add(ws)
        db_session.commit()

        assert ws.track_uris == ["uri1", "uri2"]

    def test_track_uris_property_setter(self, db_session, user):
        """Should serialize list to JSON."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
        )
        ws.track_uris = ["a", "b", "c"]
        db_session.add(ws)
        db_session.commit()

        assert json.loads(ws.track_uris_json) == ["a", "b", "c"]

    def test_track_uris_empty_json(self, db_session, user):
        """Should return empty list for empty JSON."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
            track_uris_json="",
        )
        assert ws.track_uris == []

    def test_track_uris_invalid_json(self, db_session, user):
        """Should return empty list for invalid JSON."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
            track_uris_json="not valid json",
        )
        assert ws.track_uris == []

    def test_to_dict(self, db_session, user):
        """Should serialize with track count."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="My Session",
        )
        ws.track_uris = ["uri1", "uri2", "uri3"]
        db_session.add(ws)
        db_session.commit()

        d = ws.to_dict()
        assert d["session_name"] == "My Session"
        assert d["track_count"] == 3
        assert d["track_uris"] == ["uri1", "uri2", "uri3"]
        assert d["playlist_id"] == "p1"

    def test_user_relationship(self, db_session, user):
        """Should link to the parent User."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
            track_uris_json="[]",
        )
        db_session.add(ws)
        db_session.commit()

        assert ws.user.spotify_id == "user123"
        assert len(user.workshop_sessions) == 1

    def test_cascade_delete(self, db_session, user):
        """Deleting user should delete workshop sessions."""
        ws = WorkshopSession(
            user_id=user.id,
            playlist_id="p1",
            session_name="Test",
            track_uris_json="[]",
        )
        db_session.add(ws)
        db_session.commit()
        ws_id = ws.id

        db_session.delete(user)
        db_session.commit()

        assert db_session.get(WorkshopSession, ws_id) is None


class TestUpstreamSourceModel:
    """Tests for the UpstreamSource model."""

    @pytest.fixture
    def user(self, db_session):
        """Create a test user."""
        user = User(spotify_id="user123", display_name="Test User")
        db_session.add(user)
        db_session.commit()
        return user

    def test_create_upstream_source(self, db_session, user):
        """Should create an upstream source with required fields."""
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id="target_p1",
            source_playlist_id="source_p2",
            source_type="external",
            source_name="Cool Playlist",
        )
        db_session.add(source)
        db_session.commit()

        assert source.id is not None
        assert source.source_type == "external"
        assert source.source_name == "Cool Playlist"

    def test_to_dict(self, db_session, user):
        """Should serialize all fields."""
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id="target",
            source_playlist_id="source",
            source_type="own",
            source_url="https://open.spotify.com/playlist/source",
            source_name="My Source",
        )
        db_session.add(source)
        db_session.commit()

        d = source.to_dict()
        assert d["source_type"] == "own"
        assert d["source_url"] == "https://open.spotify.com/playlist/source"
        assert d["source_name"] == "My Source"

    def test_user_relationship(self, db_session, user):
        """Should link to parent User."""
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id="target",
            source_playlist_id="source",
            source_type="external",
        )
        db_session.add(source)
        db_session.commit()

        assert source.user.spotify_id == "user123"
        assert len(user.upstream_sources) == 1

    def test_cascade_delete(self, db_session, user):
        """Deleting user should delete upstream sources."""
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id="target",
            source_playlist_id="source",
            source_type="external",
        )
        db_session.add(source)
        db_session.commit()
        source_id = source.id

        db_session.delete(user)
        db_session.commit()

        assert db_session.get(UpstreamSource, source_id) is None

    def test_default_source_type(self, db_session, user):
        """Should default source_type to 'external'."""
        source = UpstreamSource(
            user_id=user.id,
            target_playlist_id="target",
            source_playlist_id="source",
        )
        db_session.add(source)
        db_session.commit()

        assert source.source_type == "external"
```

### Test File: `tests/services/test_user_service.py` (NEW FILE)

```python
"""
Tests for UserService.

Tests cover user upsert, lookup, and error handling.
"""

import pytest

from shuffify.models.db import db, User
from shuffify.services.user_service import (
    UserService,
    UserServiceError,
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


class TestUserServiceUpsert:
    """Tests for upsert_from_spotify."""

    def test_create_new_user(self, app_ctx):
        """Should create a new user from Spotify data."""
        user_data = {
            "id": "spotify_user_1",
            "display_name": "Test User",
            "email": "test@example.com",
            "images": [{"url": "https://example.com/img.jpg"}],
        }

        user = UserService.upsert_from_spotify(user_data)

        assert user.spotify_id == "spotify_user_1"
        assert user.display_name == "Test User"
        assert user.email == "test@example.com"
        assert user.profile_image_url == "https://example.com/img.jpg"

    def test_update_existing_user(self, app_ctx):
        """Should update existing user on re-login."""
        user_data = {
            "id": "spotify_user_1",
            "display_name": "Original Name",
            "email": "old@example.com",
            "images": [],
        }
        UserService.upsert_from_spotify(user_data)

        updated_data = {
            "id": "spotify_user_1",
            "display_name": "New Name",
            "email": "new@example.com",
            "images": [{"url": "https://example.com/new.jpg"}],
        }
        user = UserService.upsert_from_spotify(updated_data)

        assert user.display_name == "New Name"
        assert user.email == "new@example.com"
        assert user.profile_image_url == "https://example.com/new.jpg"

        # Verify only one user exists
        count = User.query.filter_by(spotify_id="spotify_user_1").count()
        assert count == 1

    def test_upsert_no_images(self, app_ctx):
        """Should handle user data with no images."""
        user_data = {
            "id": "user_no_img",
            "display_name": "No Image User",
            "images": [],
        }
        user = UserService.upsert_from_spotify(user_data)
        assert user.profile_image_url is None

    def test_upsert_missing_id_raises(self, app_ctx):
        """Should raise error when spotify ID is missing."""
        with pytest.raises(UserServiceError, match="missing 'id'"):
            UserService.upsert_from_spotify({"display_name": "No ID"})

    def test_upsert_empty_id_raises(self, app_ctx):
        """Should raise error when spotify ID is empty."""
        with pytest.raises(UserServiceError, match="missing 'id'"):
            UserService.upsert_from_spotify({"id": "", "display_name": "Empty"})


class TestUserServiceLookup:
    """Tests for get_by_spotify_id and get_by_id."""

    def test_get_by_spotify_id_found(self, app_ctx):
        """Should return user when found."""
        UserService.upsert_from_spotify({
            "id": "user_x", "display_name": "User X", "images": []
        })

        user = UserService.get_by_spotify_id("user_x")
        assert user is not None
        assert user.display_name == "User X"

    def test_get_by_spotify_id_not_found(self, app_ctx):
        """Should return None when not found."""
        user = UserService.get_by_spotify_id("nonexistent")
        assert user is None

    def test_get_by_spotify_id_empty(self, app_ctx):
        """Should return None for empty string."""
        user = UserService.get_by_spotify_id("")
        assert user is None

    def test_get_by_spotify_id_none(self, app_ctx):
        """Should return None for None."""
        user = UserService.get_by_spotify_id(None)
        assert user is None

    def test_get_by_id_found(self, app_ctx):
        """Should return user by internal ID."""
        created = UserService.upsert_from_spotify({
            "id": "user_y", "display_name": "User Y", "images": []
        })

        user = UserService.get_by_id(created.id)
        assert user is not None
        assert user.spotify_id == "user_y"

    def test_get_by_id_not_found(self, app_ctx):
        """Should return None for non-existent ID."""
        user = UserService.get_by_id(99999)
        assert user is None
```

### Test File: `tests/services/test_workshop_session_service.py` (NEW FILE)

```python
"""
Tests for WorkshopSessionService.

Tests cover save, list, get, update, delete, and limit enforcement.
"""

import pytest

from shuffify.models.db import db, User
from shuffify.services.user_service import UserService
from shuffify.services.workshop_session_service import (
    WorkshopSessionService,
    WorkshopSessionError,
    WorkshopSessionNotFoundError,
    WorkshopSessionLimitError,
    MAX_SESSIONS_PER_PLAYLIST,
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
    """Provide app context with a test user."""
    with db_app.app_context():
        UserService.upsert_from_spotify({
            "id": "user123", "display_name": "Test User", "images": []
        })
        yield


class TestWorkshopSessionServiceSave:
    """Tests for save_session."""

    def test_save_session(self, app_ctx):
        """Should save a new workshop session."""
        uris = ["spotify:track:a", "spotify:track:b"]
        ws = WorkshopSessionService.save_session(
            "user123", "playlist1", "My Save", uris
        )

        assert ws.id is not None
        assert ws.session_name == "My Save"
        assert ws.track_uris == uris
        assert ws.playlist_id == "playlist1"

    def test_save_session_strips_name(self, app_ctx):
        """Should strip whitespace from session name."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "  Padded Name  ", ["uri"]
        )
        assert ws.session_name == "Padded Name"

    def test_save_session_empty_name_raises(self, app_ctx):
        """Should reject empty session name."""
        with pytest.raises(WorkshopSessionError, match="cannot be empty"):
            WorkshopSessionService.save_session("user123", "p1", "", ["uri"])

    def test_save_session_whitespace_name_raises(self, app_ctx):
        """Should reject whitespace-only session name."""
        with pytest.raises(WorkshopSessionError, match="cannot be empty"):
            WorkshopSessionService.save_session("user123", "p1", "   ", ["uri"])

    def test_save_session_unknown_user_raises(self, app_ctx):
        """Should raise error when user not found."""
        with pytest.raises(WorkshopSessionError, match="User not found"):
            WorkshopSessionService.save_session(
                "nonexistent", "p1", "Test", ["uri"]
            )

    def test_save_session_limit_enforcement(self, app_ctx):
        """Should reject saves beyond the per-playlist limit."""
        for i in range(MAX_SESSIONS_PER_PLAYLIST):
            WorkshopSessionService.save_session(
                "user123", "p1", f"Session {i}", [f"uri{i}"]
            )

        with pytest.raises(WorkshopSessionLimitError, match="Maximum"):
            WorkshopSessionService.save_session(
                "user123", "p1", "One Too Many", ["uri"]
            )

    def test_save_session_limit_is_per_playlist(self, app_ctx):
        """Limit should be per-playlist, not global."""
        for i in range(MAX_SESSIONS_PER_PLAYLIST):
            WorkshopSessionService.save_session(
                "user123", "playlist_A", f"Session {i}", [f"uri{i}"]
            )

        # Should succeed on a different playlist
        ws = WorkshopSessionService.save_session(
            "user123", "playlist_B", "Session on B", ["uri"]
        )
        assert ws.id is not None

    def test_save_session_empty_uris(self, app_ctx):
        """Should allow saving with an empty track list."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Empty Session", []
        )
        assert ws.track_uris == []


class TestWorkshopSessionServiceList:
    """Tests for list_sessions."""

    def test_list_sessions_returns_sessions(self, app_ctx):
        """Should return all sessions for user and playlist."""
        WorkshopSessionService.save_session("user123", "p1", "A", ["uri_a"])
        WorkshopSessionService.save_session("user123", "p1", "B", ["uri_b"])

        sessions = WorkshopSessionService.list_sessions("user123", "p1")
        assert len(sessions) == 2

    def test_list_sessions_filters_by_playlist(self, app_ctx):
        """Should only return sessions for the specified playlist."""
        WorkshopSessionService.save_session("user123", "p1", "A", ["a"])
        WorkshopSessionService.save_session("user123", "p2", "B", ["b"])

        sessions = WorkshopSessionService.list_sessions("user123", "p1")
        assert len(sessions) == 1
        assert sessions[0].session_name == "A"

    def test_list_sessions_unknown_user_returns_empty(self, app_ctx):
        """Should return empty list for unknown user."""
        sessions = WorkshopSessionService.list_sessions("ghost", "p1")
        assert sessions == []

    def test_list_sessions_ordered_by_most_recent(self, app_ctx):
        """Should return most recent first."""
        WorkshopSessionService.save_session("user123", "p1", "First", ["a"])
        WorkshopSessionService.save_session("user123", "p1", "Second", ["b"])

        sessions = WorkshopSessionService.list_sessions("user123", "p1")
        # Most recent first (Second was created last)
        assert sessions[0].session_name == "Second"


class TestWorkshopSessionServiceGet:
    """Tests for get_session."""

    def test_get_session_success(self, app_ctx):
        """Should return the session by ID."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Test", ["uri"]
        )

        result = WorkshopSessionService.get_session(ws.id, "user123")
        assert result.session_name == "Test"

    def test_get_session_wrong_user_raises(self, app_ctx):
        """Should raise when session belongs to another user."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Test", ["uri"]
        )

        # Create another user
        UserService.upsert_from_spotify({
            "id": "other_user", "display_name": "Other", "images": []
        })

        with pytest.raises(WorkshopSessionNotFoundError):
            WorkshopSessionService.get_session(ws.id, "other_user")

    def test_get_session_nonexistent_id_raises(self, app_ctx):
        """Should raise for non-existent session ID."""
        with pytest.raises(WorkshopSessionNotFoundError):
            WorkshopSessionService.get_session(99999, "user123")


class TestWorkshopSessionServiceUpdate:
    """Tests for update_session."""

    def test_update_track_uris(self, app_ctx):
        """Should update the track URIs."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Original", ["old_uri"]
        )

        updated = WorkshopSessionService.update_session(
            ws.id, "user123", ["new_uri_a", "new_uri_b"]
        )
        assert updated.track_uris == ["new_uri_a", "new_uri_b"]

    def test_update_session_name(self, app_ctx):
        """Should update the session name."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Old Name", ["uri"]
        )

        updated = WorkshopSessionService.update_session(
            ws.id, "user123", ["uri"], session_name="New Name"
        )
        assert updated.session_name == "New Name"


class TestWorkshopSessionServiceDelete:
    """Tests for delete_session."""

    def test_delete_session(self, app_ctx):
        """Should delete the session."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "To Delete", ["uri"]
        )
        ws_id = ws.id

        result = WorkshopSessionService.delete_session(ws_id, "user123")
        assert result is True

        with pytest.raises(WorkshopSessionNotFoundError):
            WorkshopSessionService.get_session(ws_id, "user123")

    def test_delete_session_wrong_user_raises(self, app_ctx):
        """Should raise when session belongs to another user."""
        ws = WorkshopSessionService.save_session(
            "user123", "p1", "Test", ["uri"]
        )

        UserService.upsert_from_spotify({
            "id": "other_user", "display_name": "Other", "images": []
        })

        with pytest.raises(WorkshopSessionNotFoundError):
            WorkshopSessionService.delete_session(ws.id, "other_user")
```

### Test File: `tests/services/test_upstream_source_service.py` (NEW FILE)

```python
"""
Tests for UpstreamSourceService.

Tests cover add, list, get, delete, and duplicate detection.
"""

import pytest

from shuffify.models.db import db, User
from shuffify.services.user_service import UserService
from shuffify.services.upstream_source_service import (
    UpstreamSourceService,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
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
    """Provide app context with a test user."""
    with db_app.app_context():
        UserService.upsert_from_spotify({
            "id": "user123", "display_name": "Test User", "images": []
        })
        yield


class TestUpstreamSourceServiceAdd:
    """Tests for add_source."""

    def test_add_source(self, app_ctx):
        """Should create a new upstream source."""
        source = UpstreamSourceService.add_source(
            spotify_id="user123",
            target_playlist_id="target_1",
            source_playlist_id="source_1",
            source_type="external",
            source_name="Cool Playlist",
        )

        assert source.id is not None
        assert source.source_type == "external"
        assert source.source_name == "Cool Playlist"

    def test_add_duplicate_returns_existing(self, app_ctx):
        """Should return existing source instead of creating duplicate."""
        source1 = UpstreamSourceService.add_source(
            "user123", "target_1", "source_1"
        )
        source2 = UpstreamSourceService.add_source(
            "user123", "target_1", "source_1"
        )

        assert source1.id == source2.id

    def test_add_source_invalid_type_raises(self, app_ctx):
        """Should reject invalid source_type."""
        with pytest.raises(UpstreamSourceError, match="Invalid source_type"):
            UpstreamSourceService.add_source(
                "user123", "target", "source", source_type="invalid"
            )

    def test_add_source_unknown_user_raises(self, app_ctx):
        """Should raise for unknown user."""
        with pytest.raises(UpstreamSourceError, match="User not found"):
            UpstreamSourceService.add_source(
                "ghost", "target", "source"
            )

    def test_add_source_with_url(self, app_ctx):
        """Should store source URL."""
        source = UpstreamSourceService.add_source(
            "user123", "target", "source",
            source_url="https://open.spotify.com/playlist/source",
        )
        assert source.source_url == "https://open.spotify.com/playlist/source"


class TestUpstreamSourceServiceList:
    """Tests for list_sources."""

    def test_list_sources(self, app_ctx):
        """Should return sources for user and target."""
        UpstreamSourceService.add_source("user123", "target_1", "src_a")
        UpstreamSourceService.add_source("user123", "target_1", "src_b")

        sources = UpstreamSourceService.list_sources("user123", "target_1")
        assert len(sources) == 2

    def test_list_sources_filters_by_target(self, app_ctx):
        """Should only return sources for the specified target."""
        UpstreamSourceService.add_source("user123", "target_1", "src_a")
        UpstreamSourceService.add_source("user123", "target_2", "src_b")

        sources = UpstreamSourceService.list_sources("user123", "target_1")
        assert len(sources) == 1

    def test_list_sources_unknown_user_returns_empty(self, app_ctx):
        """Should return empty for unknown user."""
        sources = UpstreamSourceService.list_sources("ghost", "target")
        assert sources == []

    def test_list_all_sources_for_user(self, app_ctx):
        """Should return all sources across all targets."""
        UpstreamSourceService.add_source("user123", "t1", "s1")
        UpstreamSourceService.add_source("user123", "t2", "s2")

        all_sources = UpstreamSourceService.list_all_sources_for_user("user123")
        assert len(all_sources) == 2


class TestUpstreamSourceServiceDelete:
    """Tests for delete_source."""

    def test_delete_source(self, app_ctx):
        """Should delete the source."""
        source = UpstreamSourceService.add_source(
            "user123", "target", "source"
        )
        source_id = source.id

        result = UpstreamSourceService.delete_source(source_id, "user123")
        assert result is True

        with pytest.raises(UpstreamSourceNotFoundError):
            UpstreamSourceService.get_source(source_id, "user123")

    def test_delete_source_wrong_user_raises(self, app_ctx):
        """Should raise when source belongs to another user."""
        source = UpstreamSourceService.add_source(
            "user123", "target", "source"
        )

        UserService.upsert_from_spotify({
            "id": "other", "display_name": "Other", "images": []
        })

        with pytest.raises(UpstreamSourceNotFoundError):
            UpstreamSourceService.delete_source(source.id, "other")

    def test_delete_nonexistent_raises(self, app_ctx):
        """Should raise for non-existent source."""
        with pytest.raises(UpstreamSourceNotFoundError):
            UpstreamSourceService.delete_source(99999, "user123")
```

---

## Documentation Updates

**CHANGELOG.md** -- Add under `## [Unreleased]` / `### Added`:

```markdown
- **User Database & Persistence** - SQLite database with Flask-SQLAlchemy for persistent storage
  - User model: stores Spotify user ID, display name, email, profile image; auto-upserted on login
  - WorkshopSession model: save/load named workshop arrangements across browser sessions
  - UpstreamSource model: persist external playlist source configurations for scheduled operations
  - New routes: workshop session CRUD (`/workshop/<id>/sessions`), upstream source CRUD
  - Flask-Migrate (Alembic) integration for database schema versioning
  - Graceful degradation: core shuffle/undo features work without database; only persistence returns errors
  - New services: UserService, WorkshopSessionService, UpstreamSourceService
  - New dependencies: Flask-SQLAlchemy>=3.1.0, Flask-Migrate>=4.0.0
```

---

## Edge Cases

### 1. Database file does not exist on first run
- `db.create_all()` in the app factory creates the `instance/` directory and the SQLite file automatically.
- Flask's `instance_path` defaults to `<project_root>/instance/`, and Flask creates it if needed.
- No manual setup required.

### 2. Database is locked (SQLite concurrent access)
- SQLite supports multiple readers but only one writer at a time.
- With 25 max users and Flask's synchronous request handling (one thread per request in dev), lock contention is extremely unlikely.
- In production with Gunicorn workers, the default WAL journal mode in modern SQLite handles concurrent reads/writes well up to moderate concurrency.
- If a lock error occurs, SQLAlchemy raises `OperationalError` which is caught by the service `try/except` blocks and returned as a 500 error.

### 3. User logs in before database is available
- The `UserService.upsert_from_spotify()` call in the callback route is wrapped in a `try/except` that logs a warning but does NOT block login.
- The user's session still gets `session['user_data']` and `session['spotify_token']`.
- When the user tries to save a workshop session, `is_db_available()` returns `False` and the route returns 503.

### 4. Track URIs exceed typical JSON size for WorkshopSession
- A Spotify playlist can hold up to 10,000 tracks.
- Each URI is ~30 characters (e.g., `spotify:track:6rqhFgbbKwnb9MLmUQDhG6`).
- 10,000 URIs as JSON: approximately 340KB. SQLite's `TEXT` column can hold up to 1GB by default.
- This is well within limits.

### 5. User deletes their Spotify account
- The User record remains in the database (no automatic cleanup).
- The user cannot log in again because Spotify OAuth will fail.
- Orphan data is harmless for 25 users. A future admin cleanup script could prune inactive users.

### 6. Duplicate WorkshopSession names
- The schema does NOT enforce unique session names per playlist. Users can have multiple sessions with the same name.
- This is intentional -- names are for human identification, not programmatic lookup.

### 7. Session `user_data` is missing or stale
- All persistence routes check `session.get("user_data", {}).get("id")` before proceeding.
- If missing, they return 401 with a clear message.
- The `user_data` is refreshed on each login, so staleness is bounded by session lifetime.

### 8. Flask-Migrate `migrations/` directory conflicts
- `flask db init` creates a `migrations/` directory. This should be committed to git.
- If the directory already exists (from a previous failed init), `flask db init` will error. Delete the directory and re-run.

---

## Verification Checklist

```bash
# 1. Install new dependencies
pip install -r requirements/dev.txt

# 2. Lint check (REQUIRED)
flake8 shuffify/

# 3. All tests pass (REQUIRED)
pytest tests/ -v

# 4. New model tests pass specifically
pytest tests/models/test_db_models.py -v

# 5. New service tests pass specifically
pytest tests/services/test_user_service.py -v
pytest tests/services/test_workshop_session_service.py -v
pytest tests/services/test_upstream_source_service.py -v

# 6. Code formatting
black --check shuffify/

# 7. Quick combined check
flake8 shuffify/ && pytest tests/ -v && echo "Ready to push!"

# 8. Initialize migrations (run once after code is in place)
flask db init
flask db migrate -m "Initial schema: User, WorkshopSession, UpstreamSource"
flask db upgrade
```

Manual checks:
- [ ] App starts successfully with `python run.py` (database auto-created)
- [ ] `instance/shuffify_dev.db` file appears after startup
- [ ] Login via Spotify OAuth still works (session-based, no regression)
- [ ] After login, User record appears in SQLite (`flask shell` -> `User.query.all()`)
- [ ] Logging in again updates the User record (check `updated_at`)
- [ ] `GET /workshop/<id>/sessions` returns empty list for a new playlist
- [ ] `POST /workshop/<id>/sessions` saves a session and returns it
- [ ] `GET /workshop/sessions/<id>` loads the saved session with track URIs
- [ ] `DELETE /workshop/sessions/<id>` removes the session
- [ ] `POST /playlist/<id>/upstream-sources` creates an upstream source
- [ ] `GET /playlist/<id>/upstream-sources` lists sources for a playlist
- [ ] `DELETE /upstream-sources/<id>` removes a source
- [ ] Stopping Redis does NOT break the app (filesystem session fallback still works)
- [ ] All existing tests still pass (no regressions)

---

## What NOT To Do

1. **Do NOT modify the existing session-based undo system.** The `StateService` and `session['playlist_states']` remain the primary mechanism for real-time undo/redo. The database is for long-term persistence only. Do not attempt to merge these two systems in this phase.

2. **Do NOT store OAuth tokens in the database in this phase.** Refresh token encryption and storage is a Phase 6 concern. The User model intentionally has no `refresh_token` column yet. Phase 6 will add it with Fernet encryption.

3. **Do NOT use PostgreSQL or any external database.** SQLite is the deliberate choice for 25 users. Do not introduce connection pooling, database server configuration, or Docker database containers.

4. **Do NOT make the database a hard requirement for app startup.** The `try/except` block around `db.init_app()` / `db.create_all()` in the app factory is intentional. If the database fails, the app must still start and serve its core features.

5. **Do NOT create a separate `tracks` table for WorkshopSession track URIs.** Storing track URIs as a JSON text column is the correct choice for 25 users. A normalized tracks table would add complexity (join queries, migration headaches) with no benefit at this scale.

6. **Do NOT skip the ownership check in service methods.** Every `get_session`, `update_session`, `delete_session`, `get_source`, and `delete_source` method must verify that the record belongs to the requesting user via `user_id` comparison. Without this, users could access each other's data.

7. **Do NOT use `db.session.commit()` inside route handlers.** All database writes go through the service layer. Routes call service methods which handle commit/rollback internally. This preserves the separation of concerns.

8. **Do NOT add Flask-SQLAlchemy to `requirements/dev.txt` separately.** It is already inherited from `base.txt` via the `-r base.txt` directive on line 1 of `dev.txt`.

9. **Do NOT delete the `migrations/` directory from git.** Once created, the Alembic migrations directory must be version-controlled so that other developers (and production) can run `flask db upgrade` to apply schema changes.

10. **Do NOT use `db.create_all()` in production as the sole migration strategy.** It is included in the app factory as a development convenience, but production deployments should rely on `flask db upgrade` (Alembic) for reliable, versioned schema changes. `db.create_all()` cannot handle column additions, renames, or deletions.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/__init__.py` - Core modification: add SQLAlchemy/Migrate init to app factory with graceful degradation
- `/Users/chris/Projects/shuffify/shuffify/models/db.py` - New file: all three SQLAlchemy model definitions (User, WorkshopSession, UpstreamSource)
- `/Users/chris/Projects/shuffify/shuffify/services/user_service.py` - New file: upsert-on-login pattern, user lookup methods
- `/Users/chris/Projects/shuffify/shuffify/routes.py` - Significant modification: add user upsert in callback, add 8 new persistence routes
- `/Users/chris/Projects/shuffify/config.py` - Add SQLALCHEMY_DATABASE_URI and SQLALCHEMY_TRACK_MODIFICATIONS settings