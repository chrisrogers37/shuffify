# Phase 2: Login History Tracking

**PR Title:** `feat: Add LoginHistory model and service for sign-in event tracking (#XX)`

**Risk Level:** Low -- adds a new table and service without modifying any existing models or business logic. The only changes to existing code are two small additions to the callback and logout routes to record login/logout events, wrapped in try/except so failures do not block authentication.

**Estimated Effort:** 2-3 days for a mid-level engineer, 3-4 days for a junior engineer.

**Files Created:**
- `shuffify/services/login_history_service.py` -- LoginHistoryService with record_login, record_logout, get_recent_logins, get_login_stats
- `tests/services/test_login_history_service.py` -- Full test coverage for LoginHistoryService

**Files Modified:**
- `shuffify/models/db.py` -- Add LoginHistory model class
- `shuffify/models/__init__.py` -- Export LoginHistory
- `shuffify/services/__init__.py` -- Export LoginHistoryService and exceptions
- `shuffify/routes/core.py` -- Add record_login call in callback, record_logout call in logout
- `tests/models/test_db_models.py` -- Add TestLoginHistoryModel test class
- `CHANGELOG.md` -- Add entry under `[Unreleased]`

**Files Deleted:** None

---

## Context

Shuffify currently has no record of when users sign in or out. The `User` model captures profile data but not login frequency, session duration, or access patterns. Phase 1 of this enhancement plan adds `last_login_at` and `login_count` fields to the User model, but those are summary fields -- they do not provide a history of individual login events.

Phase 2 introduces a `LoginHistory` table that records every discrete sign-in event, including:
- When the login occurred
- When the user logged out (or the session expired)
- IP address and user agent (for security auditing)
- Flask session ID (for correlating with server-side session data)
- Login type (oauth_initial vs oauth_refresh vs session_resume)

This data serves multiple future purposes:
- **Security auditing**: Users can review their login history for suspicious activity
- **Analytics**: Understand usage patterns, session durations, peak hours
- **Support**: Debug user-reported issues by correlating with login events
- **Phase 3+ features**: Building blocks for activity feeds, admin dashboards

---

## Dependencies

**Prerequisites:**
- Phase 0 (PostgreSQL + Alembic) -- Phase 2 requires Alembic to generate a migration for the new table. If Phase 0 is not yet merged, the model can still be created (tables are auto-created via `db.create_all()` in development), but the migration generation step should be deferred.
- Phase 1 (Enhanced User model) -- Phase 2 adds a relationship on the User model. The `login_history` relationship is additive and does not conflict with Phase 1's `last_login_at` / `login_count` fields, but ideally Phase 1 merges first so the User model has all its new columns before Phase 2 adds the relationship.

**What this unlocks:**
- Phase 3+: Admin dashboard with login analytics
- Phase 3+: Security page where users can review their login history
- Phase 3+: Session management (view active sessions, revoke sessions)

**New package dependencies:** None

---

## Detailed Implementation Plan

### Step 1: Add LoginHistory Model to `shuffify/models/db.py`

**File:** `/Users/chris/Projects/shuffify/shuffify/models/db.py`

**Where to add:** After the `JobExecution` class (after line 417), add the new `LoginHistory` class.

**Add this code at the end of the file (after line 417):**

```python
class LoginHistory(db.Model):
    """
    Record of a single user login event.

    Created on each OAuth callback. The logged_out_at field is updated
    when the user explicitly logs out or when the session expires.
    """

    __tablename__ = "login_history"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    logged_in_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    logged_out_at = db.Column(db.DateTime, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    session_id = db.Column(db.String(255), nullable=True)
    login_type = db.Column(db.String(20), nullable=False)

    # Relationships
    user = db.relationship(
        "User", back_populates="login_history"
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the LoginHistory to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "logged_in_at": (
                self.logged_in_at.isoformat()
                if self.logged_in_at
                else None
            ),
            "logged_out_at": (
                self.logged_out_at.isoformat()
                if self.logged_out_at
                else None
            ),
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "session_id": self.session_id,
            "login_type": self.login_type,
        }

    def __repr__(self) -> str:
        return (
            f"<LoginHistory {self.id}: user={self.user_id} "
            f"type={self.login_type} "
            f"at={self.logged_in_at}>"
        )
```

**Design decisions:**
- `ip_address` is `String(45)` to accommodate IPv6 addresses (max 45 characters for a full IPv6 representation)
- `user_agent` is `String(512)` -- user agent strings can be long, but 512 is sufficient for practical use and avoids unbounded storage
- `login_type` is `String(20)` to hold values like `'oauth_initial'`, `'oauth_refresh'`, `'session_resume'` (longest is 15 characters, 20 gives headroom)
- No composite index needed -- queries will primarily be by `user_id` (already indexed via FK) ordered by `logged_in_at`

---

### Step 2: Add `login_history` Relationship to User Model

**File:** `/Users/chris/Projects/shuffify/shuffify/models/db.py`

**Where to modify:** Inside the `User` class, after the existing `upstream_sources` relationship (lines 62-66), add the new relationship.

**BEFORE (lines 56-66):**
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

**AFTER:**
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
    login_history = db.relationship(
        "LoginHistory",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="LoginHistory.logged_in_at.desc()",
    )
```

**Note:** The `order_by` parameter means accessing `user.login_history` always returns records most-recent-first, which is the natural access pattern for login history.

---

### Step 3: Export LoginHistory from `shuffify/models/__init__.py`

**File:** `/Users/chris/Projects/shuffify/shuffify/models/__init__.py`

**BEFORE:**
```python
from shuffify.models.db import (
    db,
    User,
    WorkshopSession,
    UpstreamSource,
    Schedule,
    JobExecution,
)
from shuffify.models.playlist import Playlist

__all__ = [
    "db",
    "User",
    "WorkshopSession",
    "UpstreamSource",
    "Schedule",
    "JobExecution",
    "Playlist",
]
```

**AFTER:**
```python
from shuffify.models.db import (
    db,
    User,
    WorkshopSession,
    UpstreamSource,
    Schedule,
    JobExecution,
    LoginHistory,
)
from shuffify.models.playlist import Playlist

__all__ = [
    "db",
    "User",
    "WorkshopSession",
    "UpstreamSource",
    "Schedule",
    "JobExecution",
    "LoginHistory",
    "Playlist",
]
```

---

### Step 4: Create LoginHistoryService

**File:** `/Users/chris/Projects/shuffify/shuffify/services/login_history_service.py` (new file)

This service follows the same patterns as `UserService` and `WorkshopSessionService`: static methods, explicit error handling with rollback, dedicated exception classes, structured logging.

```python
"""
Login history service for recording and querying sign-in events.

Handles creation of login records on OAuth callback, updating logout
timestamps, and querying login history for auditing and analytics.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from flask import Request

from shuffify.models.db import db, LoginHistory, User

logger = logging.getLogger(__name__)


class LoginHistoryError(Exception):
    """Base exception for login history operations."""

    pass


class LoginHistoryNotFoundError(LoginHistoryError):
    """Raised when a login history record cannot be found."""

    pass


class LoginHistoryService:
    """Service for managing login history records."""

    @staticmethod
    def record_login(
        user_id: int,
        request: Request,
        session_id: Optional[str] = None,
        login_type: str = "oauth_initial",
    ) -> LoginHistory:
        """
        Record a new login event.

        Creates a LoginHistory record capturing the login timestamp,
        IP address, user agent, and session ID.

        Args:
            user_id: The internal database user ID.
            request: The Flask request object (used for IP and UA).
            session_id: The Flask session ID for correlation.
            login_type: One of 'oauth_initial', 'oauth_refresh',
                'session_resume'.

        Returns:
            The created LoginHistory instance.

        Raises:
            LoginHistoryError: If recording fails.
        """
        # Extract IP address, preferring X-Forwarded-For for proxied requests
        ip_address = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr
        )

        # Extract and truncate user agent to fit column length
        user_agent = request.headers.get("User-Agent", "")
        if len(user_agent) > 512:
            user_agent = user_agent[:512]

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
            logger.error(
                f"Failed to record login for user_id="
                f"{user_id}: {e}",
                exc_info=True,
            )
            raise LoginHistoryError(
                f"Failed to record login: {e}"
            )

    @staticmethod
    def record_logout(
        user_id: int,
        session_id: Optional[str] = None,
    ) -> bool:
        """
        Update the most recent login record with a logout timestamp.

        Finds the most recent LoginHistory record for the user
        (optionally filtered by session_id) that has no logged_out_at
        value, and sets it to the current UTC time.

        Args:
            user_id: The internal database user ID.
            session_id: Optional Flask session ID to match a specific
                login record.

        Returns:
            True if a record was updated, False if no matching record
            was found.

        Raises:
            LoginHistoryError: If the update fails.
        """
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

    @staticmethod
    def get_recent_logins(
        user_id: int, limit: int = 10
    ) -> List[LoginHistory]:
        """
        Get the most recent login events for a user.

        Args:
            user_id: The internal database user ID.
            limit: Maximum number of records to return (default 10).

        Returns:
            List of LoginHistory instances, most recent first.
        """
        return (
            LoginHistory.query.filter_by(user_id=user_id)
            .order_by(LoginHistory.logged_in_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_login_stats(user_id: int) -> Dict[str, Any]:
        """
        Get summary statistics for a user's login history.

        Returns total logins, average session duration (for completed
        sessions), the most recent login timestamp, and a count of
        logins by type.

        Args:
            user_id: The internal database user ID.

        Returns:
            Dictionary with keys:
                - total_logins (int)
                - avg_session_duration_seconds (float or None)
                - last_login_at (str ISO format or None)
                - logins_by_type (dict of type -> count)
        """
        all_entries = LoginHistory.query.filter_by(
            user_id=user_id
        ).all()

        if not all_entries:
            return {
                "total_logins": 0,
                "avg_session_duration_seconds": None,
                "last_login_at": None,
                "logins_by_type": {},
            }

        total_logins = len(all_entries)

        # Calculate average session duration for completed sessions
        completed = [
            e for e in all_entries if e.logged_out_at is not None
        ]
        avg_duration = None
        if completed:
            durations = [
                (e.logged_out_at - e.logged_in_at).total_seconds()
                for e in completed
            ]
            avg_duration = sum(durations) / len(durations)

        # Most recent login
        most_recent = max(
            all_entries, key=lambda e: e.logged_in_at
        )

        # Count by login type
        logins_by_type: Dict[str, int] = {}
        for e in all_entries:
            logins_by_type[e.login_type] = (
                logins_by_type.get(e.login_type, 0) + 1
            )

        return {
            "total_logins": total_logins,
            "avg_session_duration_seconds": avg_duration,
            "last_login_at": (
                most_recent.logged_in_at.isoformat()
                if most_recent.logged_in_at
                else None
            ),
            "logins_by_type": logins_by_type,
        }
```

---

### Step 5: Export LoginHistoryService from `shuffify/services/__init__.py`

**File:** `/Users/chris/Projects/shuffify/shuffify/services/__init__.py`

**Add after the JobExecutorService import block (after line 99), before the `__all__` list:**

```python
# Login History Service
from shuffify.services.login_history_service import (
    LoginHistoryService,
    LoginHistoryError,
    LoginHistoryNotFoundError,
)
```

**Add to `__all__` list (after `"JobExecutionError"` on line 149):**

```python
    # Login History Service
    "LoginHistoryService",
    "LoginHistoryError",
    "LoginHistoryNotFoundError",
```

---

### Step 6: Update Callback Route to Record Login

**File:** `/Users/chris/Projects/shuffify/shuffify/routes/core.py`

**6a. Update imports (line 24-31):**

**BEFORE:**
```python
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    UserService,
    AuthenticationError,
    PlaylistError,
)
```

**AFTER:**
```python
from shuffify.services import (
    AuthService,
    PlaylistService,
    ShuffleService,
    UserService,
    LoginHistoryService,
    AuthenticationError,
    PlaylistError,
)
```

**6b. Add login recording in the callback route.** Insert a new try/except block after the "Store encrypted refresh token" block (after line 233, before `session.modified = True` on line 235).

**Add this code block before `session.modified = True`:**

```python
        # Record login event (non-blocking)
        try:
            db_user = (
                db_user
                if db_user
                else UserService.get_by_spotify_id(
                    user_data["id"]
                )
            )
            if db_user:
                # Get Flask session ID if available
                flask_session_id = getattr(
                    session, "sid", None
                )
                LoginHistoryService.record_login(
                    user_id=db_user.id,
                    request=request,
                    session_id=flask_session_id,
                    login_type="oauth_initial",
                )
        except Exception as e:
            # Login history failure should NOT block login
            logger.warning(
                f"Failed to record login history: {e}. "
                f"Login continues without history tracking."
            )
```

**Important note on `db_user` variable:** In the current callback code, `db_user` is defined inside a nested try block (line 212). It may not be defined if that block fails. The code above handles this by using a fallback lookup. An alternative approach is to hoist the `db_user` variable declaration to the outer scope. The implementation should ensure `db_user` is accessible; the conditional `if db_user` plus the fallback `UserService.get_by_spotify_id()` call makes this robust.

---

### Step 7: Update Logout Route to Record Logout

**File:** `/Users/chris/Projects/shuffify/shuffify/routes/core.py`

**BEFORE (lines 256-260):**
```python
@main.route("/logout")
def logout():
    """Clear session and log out."""
    session.clear()
    return redirect(url_for("main.index"))
```

**AFTER:**
```python
@main.route("/logout")
def logout():
    """Clear session and log out."""
    # Record logout event before clearing session
    try:
        user_data = session.get("user_data")
        if user_data and user_data.get("id"):
            db_user = UserService.get_by_spotify_id(
                user_data["id"]
            )
            if db_user:
                flask_session_id = getattr(
                    session, "sid", None
                )
                LoginHistoryService.record_logout(
                    user_id=db_user.id,
                    session_id=flask_session_id,
                )
    except Exception as e:
        logger.warning(
            f"Failed to record logout: {e}. "
            f"Logout continues."
        )

    session.clear()
    return redirect(url_for("main.index"))
```

**Critical ordering:** The logout recording MUST happen BEFORE `session.clear()` because we need `session['user_data']` to identify the user and `session.sid` for the session ID correlation. Once `session.clear()` is called, all that data is gone.

---

### Step 8: Generate Alembic Migration

**Precondition:** Phase 0 must be merged so that `flask db` commands work.

Run the following command to auto-generate a migration for the new table:

```bash
source venv/bin/activate
flask db migrate -m "Add login_history table"
```

This will generate a migration file in `migrations/versions/` with an `upgrade()` function that creates the `login_history` table, and a `downgrade()` function that drops it.

**Verify the generated migration** contains:
- `op.create_table('login_history', ...)` with all 7 columns
- An index on `user_id`
- Proper foreign key constraint to `users.id`

Then apply:

```bash
flask db upgrade
```

**If Phase 0 is NOT yet merged:** Skip this step. The table will be created automatically by `db.create_all()` in development mode (see `/Users/chris/Projects/shuffify/shuffify/__init__.py` line 192). Add a TODO comment in the PR description to generate the migration after Phase 0 merges.

---

### Step 9: Add Model Tests

**File:** `/Users/chris/Projects/shuffify/tests/models/test_db_models.py`

**Add this test class after the existing `TestUpstreamSourceModel` class (after line 361):**

```python
class TestLoginHistoryModel:
    """Tests for the LoginHistory model."""

    @pytest.fixture
    def user(self, db_session):
        """Create a test user."""
        user = User(
            spotify_id="user123", display_name="Test User"
        )
        db_session.add(user)
        db_session.commit()
        return user

    def test_create_login_history(self, db_session, user):
        """Should create a login history record."""
        entry = LoginHistory(
            user_id=user.id,
            ip_address="192.168.1.1",
            user_agent="TestBrowser/1.0",
            session_id="sess_abc123",
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        assert entry.id is not None
        assert entry.user_id == user.id
        assert entry.ip_address == "192.168.1.1"
        assert entry.user_agent == "TestBrowser/1.0"
        assert entry.session_id == "sess_abc123"
        assert entry.login_type == "oauth_initial"
        assert entry.logged_in_at is not None
        assert entry.logged_out_at is None

    def test_login_type_required(self, db_session, user):
        """Should require login_type field."""
        entry = LoginHistory(
            user_id=user.id,
        )
        db_session.add(entry)

        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()
        db_session.rollback()

    def test_nullable_fields(self, db_session, user):
        """Should allow null for optional fields."""
        entry = LoginHistory(
            user_id=user.id,
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        assert entry.ip_address is None
        assert entry.user_agent is None
        assert entry.session_id is None
        assert entry.logged_out_at is None

    def test_logged_out_at_update(self, db_session, user):
        """Should allow setting logged_out_at after creation."""
        entry = LoginHistory(
            user_id=user.id,
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        entry.logged_out_at = datetime.now(timezone.utc)
        db_session.commit()

        assert entry.logged_out_at is not None

    def test_to_dict(self, db_session, user):
        """Should serialize all fields."""
        entry = LoginHistory(
            user_id=user.id,
            ip_address="10.0.0.1",
            user_agent="Chrome/100",
            session_id="sess_xyz",
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        d = entry.to_dict()
        assert d["user_id"] == user.id
        assert d["ip_address"] == "10.0.0.1"
        assert d["user_agent"] == "Chrome/100"
        assert d["session_id"] == "sess_xyz"
        assert d["login_type"] == "oauth_initial"
        assert d["logged_in_at"] is not None
        assert d["logged_out_at"] is None

    def test_repr(self, db_session, user):
        """Should have a useful string representation."""
        entry = LoginHistory(
            user_id=user.id,
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        r = repr(entry)
        assert "LoginHistory" in r
        assert "oauth_initial" in r

    def test_user_relationship(self, db_session, user):
        """Should link to parent User."""
        entry = LoginHistory(
            user_id=user.id,
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()

        assert entry.user.spotify_id == "user123"
        assert len(user.login_history) == 1

    def test_cascade_delete(self, db_session, user):
        """Deleting user should delete login history."""
        entry = LoginHistory(
            user_id=user.id,
            login_type="oauth_initial",
        )
        db_session.add(entry)
        db_session.commit()
        entry_id = entry.id

        db_session.delete(user)
        db_session.commit()

        assert db_session.get(LoginHistory, entry_id) is None

    def test_multiple_login_entries(self, db_session, user):
        """Should support multiple login records per user."""
        for i in range(5):
            entry = LoginHistory(
                user_id=user.id,
                login_type="oauth_initial",
                ip_address=f"10.0.0.{i}",
            )
            db_session.add(entry)
        db_session.commit()

        assert len(user.login_history) == 5
```

**Add the import** for `LoginHistory` at the top of the file (line 11):

**BEFORE:**
```python
from shuffify.models.db import db, User, WorkshopSession, UpstreamSource
```

**AFTER:**
```python
from shuffify.models.db import (
    db, User, WorkshopSession, UpstreamSource, LoginHistory
)
```

Also add the `datetime` import at the top:
```python
from datetime import datetime, timezone
```

---

### Step 10: Add Service Tests

**File:** `/Users/chris/Projects/shuffify/tests/services/test_login_history_service.py` (new file)

```python
"""
Tests for LoginHistoryService.

Tests cover login recording, logout recording, recent logins
retrieval, and login statistics computation.
"""

import pytest
from unittest.mock import Mock
from datetime import datetime, timezone, timedelta

from shuffify.models.db import db, LoginHistory
from shuffify.services.user_service import UserService
from shuffify.services.login_history_service import (
    LoginHistoryService,
    LoginHistoryError,
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
        user = UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield user


@pytest.fixture
def mock_request():
    """Create a mock Flask request object."""
    req = Mock()
    req.remote_addr = "192.168.1.100"
    req.headers = {
        "User-Agent": "Mozilla/5.0 TestBrowser",
    }
    req.headers.get = lambda key, default="": {
        "X-Forwarded-For": "",
        "User-Agent": "Mozilla/5.0 TestBrowser",
    }.get(key, default)
    return req


@pytest.fixture
def mock_request_with_proxy():
    """Create a mock request with X-Forwarded-For header."""
    req = Mock()
    req.remote_addr = "10.0.0.1"
    req.headers = {
        "X-Forwarded-For": "203.0.113.50, 70.41.3.18",
        "User-Agent": "Chrome/100",
    }
    req.headers.get = lambda key, default="": {
        "X-Forwarded-For": "203.0.113.50, 70.41.3.18",
        "User-Agent": "Chrome/100",
    }.get(key, default)
    return req


class TestRecordLogin:
    """Tests for record_login."""

    def test_record_login_basic(
        self, app_ctx, mock_request
    ):
        """Should create a login history record."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            session_id="sess_abc",
            login_type="oauth_initial",
        )

        assert entry.id is not None
        assert entry.user_id == app_ctx.id
        assert entry.ip_address == "192.168.1.100"
        assert entry.user_agent == "Mozilla/5.0 TestBrowser"
        assert entry.session_id == "sess_abc"
        assert entry.login_type == "oauth_initial"
        assert entry.logged_in_at is not None
        assert entry.logged_out_at is None

    def test_record_login_uses_forwarded_ip(
        self, app_ctx, mock_request_with_proxy
    ):
        """Should prefer X-Forwarded-For over remote_addr."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request_with_proxy,
            login_type="oauth_initial",
        )

        assert entry.ip_address == "203.0.113.50"

    def test_record_login_falls_back_to_remote_addr(
        self, app_ctx, mock_request
    ):
        """Should use remote_addr when X-Forwarded-For is empty."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )

        assert entry.ip_address == "192.168.1.100"

    def test_record_login_truncates_long_user_agent(
        self, app_ctx
    ):
        """Should truncate user agent strings longer than 512."""
        req = Mock()
        req.remote_addr = "1.2.3.4"
        long_ua = "A" * 600
        req.headers = Mock()
        req.headers.get = lambda key, default="": {
            "X-Forwarded-For": "",
            "User-Agent": long_ua,
        }.get(key, default)

        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=req,
            login_type="oauth_initial",
        )

        assert len(entry.user_agent) == 512

    def test_record_login_no_session_id(
        self, app_ctx, mock_request
    ):
        """Should allow None session_id."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )

        assert entry.session_id is None

    def test_record_login_different_types(
        self, app_ctx, mock_request
    ):
        """Should record different login types."""
        for login_type in [
            "oauth_initial",
            "oauth_refresh",
            "session_resume",
        ]:
            entry = LoginHistoryService.record_login(
                user_id=app_ctx.id,
                request=mock_request,
                login_type=login_type,
            )
            assert entry.login_type == login_type


class TestRecordLogout:
    """Tests for record_logout."""

    def test_record_logout_updates_most_recent(
        self, app_ctx, mock_request
    ):
        """Should set logged_out_at on the most recent open record."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            session_id="sess_1",
            login_type="oauth_initial",
        )

        result = LoginHistoryService.record_logout(
            user_id=app_ctx.id,
            session_id="sess_1",
        )

        assert result is True

        # Refresh from DB
        updated = db.session.get(LoginHistory, entry.id)
        assert updated.logged_out_at is not None

    def test_record_logout_no_matching_record(
        self, app_ctx
    ):
        """Should return False when no open record exists."""
        result = LoginHistoryService.record_logout(
            user_id=app_ctx.id,
            session_id="nonexistent",
        )

        assert result is False

    def test_record_logout_without_session_id(
        self, app_ctx, mock_request
    ):
        """Should match any open record when session_id is None."""
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )

        result = LoginHistoryService.record_logout(
            user_id=app_ctx.id,
        )

        assert result is True

    def test_record_logout_only_updates_open_record(
        self, app_ctx, mock_request
    ):
        """Should not re-update an already logged-out record."""
        # First login and logout
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            session_id="sess_1",
            login_type="oauth_initial",
        )
        LoginHistoryService.record_logout(
            user_id=app_ctx.id,
            session_id="sess_1",
        )

        # Try to logout again -- no open record
        result = LoginHistoryService.record_logout(
            user_id=app_ctx.id,
            session_id="sess_1",
        )
        assert result is False

    def test_record_logout_correct_session(
        self, app_ctx, mock_request
    ):
        """Should only logout the matching session."""
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            session_id="sess_1",
            login_type="oauth_initial",
        )
        entry2 = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            session_id="sess_2",
            login_type="oauth_initial",
        )

        LoginHistoryService.record_logout(
            user_id=app_ctx.id,
            session_id="sess_1",
        )

        # sess_2 should still be open
        updated2 = db.session.get(LoginHistory, entry2.id)
        assert updated2.logged_out_at is None


class TestGetRecentLogins:
    """Tests for get_recent_logins."""

    def test_get_recent_logins_returns_records(
        self, app_ctx, mock_request
    ):
        """Should return login records in descending order."""
        for _ in range(5):
            LoginHistoryService.record_login(
                user_id=app_ctx.id,
                request=mock_request,
                login_type="oauth_initial",
            )

        results = LoginHistoryService.get_recent_logins(
            app_ctx.id
        )
        assert len(results) == 5

    def test_get_recent_logins_respects_limit(
        self, app_ctx, mock_request
    ):
        """Should respect the limit parameter."""
        for _ in range(10):
            LoginHistoryService.record_login(
                user_id=app_ctx.id,
                request=mock_request,
                login_type="oauth_initial",
            )

        results = LoginHistoryService.get_recent_logins(
            app_ctx.id, limit=3
        )
        assert len(results) == 3

    def test_get_recent_logins_empty_for_unknown_user(
        self, app_ctx
    ):
        """Should return empty list for user with no logins."""
        results = LoginHistoryService.get_recent_logins(
            99999
        )
        assert results == []

    def test_get_recent_logins_ordered_by_most_recent(
        self, app_ctx, mock_request
    ):
        """Should return most recent login first."""
        entry1 = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )
        entry2 = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_refresh",
        )

        results = LoginHistoryService.get_recent_logins(
            app_ctx.id
        )
        assert results[0].id == entry2.id
        assert results[1].id == entry1.id


class TestGetLoginStats:
    """Tests for get_login_stats."""

    def test_get_login_stats_no_logins(self, app_ctx):
        """Should return zero stats for user with no logins."""
        stats = LoginHistoryService.get_login_stats(
            app_ctx.id
        )

        assert stats["total_logins"] == 0
        assert stats["avg_session_duration_seconds"] is None
        assert stats["last_login_at"] is None
        assert stats["logins_by_type"] == {}

    def test_get_login_stats_total_logins(
        self, app_ctx, mock_request
    ):
        """Should count total logins."""
        for _ in range(3):
            LoginHistoryService.record_login(
                user_id=app_ctx.id,
                request=mock_request,
                login_type="oauth_initial",
            )

        stats = LoginHistoryService.get_login_stats(
            app_ctx.id
        )
        assert stats["total_logins"] == 3

    def test_get_login_stats_logins_by_type(
        self, app_ctx, mock_request
    ):
        """Should break down logins by type."""
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_refresh",
        )

        stats = LoginHistoryService.get_login_stats(
            app_ctx.id
        )
        assert stats["logins_by_type"]["oauth_initial"] == 2
        assert stats["logins_by_type"]["oauth_refresh"] == 1

    def test_get_login_stats_avg_duration(
        self, app_ctx, mock_request
    ):
        """Should compute average session duration for
        completed sessions."""
        entry = LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )
        # Manually set timestamps for predictable duration
        entry.logged_in_at = datetime(
            2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc
        )
        entry.logged_out_at = datetime(
            2026, 1, 1, 13, 0, 0, tzinfo=timezone.utc
        )
        db.session.commit()

        stats = LoginHistoryService.get_login_stats(
            app_ctx.id
        )
        assert stats["avg_session_duration_seconds"] == 3600.0

    def test_get_login_stats_no_completed_sessions(
        self, app_ctx, mock_request
    ):
        """Should return None avg duration when no sessions are
        completed."""
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )

        stats = LoginHistoryService.get_login_stats(
            app_ctx.id
        )
        assert stats["avg_session_duration_seconds"] is None

    def test_get_login_stats_last_login_at(
        self, app_ctx, mock_request
    ):
        """Should return the most recent login timestamp."""
        LoginHistoryService.record_login(
            user_id=app_ctx.id,
            request=mock_request,
            login_type="oauth_initial",
        )

        stats = LoginHistoryService.get_login_stats(
            app_ctx.id
        )
        assert stats["last_login_at"] is not None
```

---

### Step 11: Update CHANGELOG.md

**File:** `/Users/chris/Projects/shuffify/CHANGELOG.md`

**Add under `## [Unreleased]`:**

```markdown
### Added
- **Login History Tracking** - New `LoginHistory` model records every sign-in event
  - Captures IP address, user agent, session ID, and login type
  - `LoginHistoryService` with `record_login()`, `record_logout()`, `get_recent_logins()`, and `get_login_stats()`
  - Login events recorded automatically during OAuth callback
  - Logout timestamps recorded during explicit logout
  - Cascade delete ensures login history is removed when user is deleted
```

---

## Edge Cases and Error Handling

1. **Database failure during login recording**: The `record_login` call in the callback route is wrapped in try/except. If the database is down, login proceeds normally -- the user just will not have a history entry for that session. This mirrors the existing pattern for `UserService.upsert_from_spotify` (see `/Users/chris/Projects/shuffify/shuffify/routes/core.py` lines 195-202).

2. **Database failure during logout recording**: Same pattern -- wrapped in try/except in the logout route. The user is still logged out normally.

3. **`session.sid` availability**: Flask-Session (both Redis and filesystem backends) exposes `session.sid` as the server-side session identifier. However, this attribute may not exist on all session implementations (e.g., the default cookie-based session). The code uses `getattr(session, "sid", None)` to safely handle this case. If `sid` is not available, `session_id` will be stored as `None` -- this is acceptable as it is a nullable field used only for correlation.

4. **X-Forwarded-For with multiple IPs**: When behind multiple proxies, `X-Forwarded-For` contains a comma-separated list. The code takes the first entry (`split(",")[0].strip()`), which is the original client IP. This is the standard convention.

5. **Very long user agent strings**: Some user agents (especially with browser extensions) can exceed 512 characters. The service truncates to 512 to prevent database errors.

6. **Multiple open login records**: If a user has multiple open login records (e.g., from different browsers/devices), `record_logout` with a `session_id` will only close the matching one. Without a `session_id`, it closes the most recent open record. This is the correct behavior -- a user logging out from one browser should not close login records from other sessions.

7. **User record does not exist**: If the User upsert failed (database issue during callback), the login history recording will also fail because `db_user` will be `None`. The try/except ensures this does not block authentication.

8. **Session expiry without explicit logout**: The `logged_out_at` field will remain `None` for sessions that expire without an explicit `/logout` call. A future cleanup task could set `logged_out_at` for records older than `PERMANENT_SESSION_LIFETIME` that still have `logged_out_at = None`. This is explicitly out of scope for Phase 2.

---

## What NOT To Do

1. **Do NOT add a `login_type` enum to `shuffify/enums.py`** -- The login_type values are specific to this model and unlikely to be used across schemas, routes, and services the way JobType/ScheduleType are. A simple string field is sufficient. If login types proliferate in later phases, an enum can be extracted then.

2. **Do NOT add routes for viewing login history in this phase** -- The service methods `get_recent_logins` and `get_login_stats` are building blocks for future phases (security page, admin dashboard). Adding UI routes in this phase would scope-creep beyond the data layer.

3. **Do NOT hash or encrypt IP addresses** -- IP addresses are needed in plaintext for security auditing (e.g., "was this login from a suspicious location?"). If GDPR compliance requires it in the future, a separate privacy-focused phase should handle PII masking.

4. **Do NOT add a periodic cleanup job for old login history records** -- This is a future concern. The table will grow slowly (one record per login), and even active users will only generate a few records per day.

5. **Do NOT modify the `UserService.upsert_from_spotify` method** -- Login history recording is the responsibility of the route layer (or a new service), not the user service. The user service handles user record management; the login history service handles event recording. Keep separation of concerns clean.

6. **Do NOT block the authentication flow if login recording fails** -- Both the callback and logout routes wrap login history calls in try/except to ensure authentication continues even if the database is unavailable.

7. **Do NOT store the full request object in the database** -- Only extract the specific fields needed (IP, user agent). The request object contains sensitive data (cookies, headers) that should not be persisted.

---

## Verification Checklist

Before creating the PR:

- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes all tests (existing + new)
- [ ] New model `LoginHistory` has all 7 columns: `id`, `user_id`, `logged_in_at`, `logged_out_at`, `ip_address`, `user_agent`, `session_id`, `login_type`
- [ ] `User.login_history` relationship works with `cascade="all, delete-orphan"`
- [ ] `LoginHistory` is exported from `shuffify/models/__init__.py`
- [ ] `LoginHistoryService` is exported from `shuffify/services/__init__.py`
- [ ] Callback route records login on successful OAuth
- [ ] Logout route records logout BEFORE clearing session
- [ ] Both route modifications are wrapped in try/except (non-blocking)
- [ ] All new code follows existing patterns (static methods, rollback on error, structured logging)
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] Alembic migration generated (or TODO noted if Phase 0 not merged)

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/models/db.py` - Core file: add LoginHistory model class and User.login_history relationship
- `/Users/chris/Projects/shuffify/shuffify/services/login_history_service.py` - New file: the entire LoginHistoryService with all 4 methods
- `/Users/chris/Projects/shuffify/shuffify/routes/core.py` - Modify callback and logout routes to record login/logout events
- `/Users/chris/Projects/shuffify/shuffify/services/__init__.py` - Register new service exports (pattern reference and modification target)
- `/Users/chris/Projects/shuffify/tests/services/test_login_history_service.py` - New file: comprehensive test suite (~25 tests)
