# Phase 1: User Dimension Table Enhancement

**PR Title:** `feat: Enrich User model with login tracking, Spotify profile fields, and create/update distinction`

**Risk Level:** Low -- adds new nullable columns to an existing model and enriches the upsert method. No existing functionality is altered; all new columns default to safe values. The migration is additive-only (no column renames, no drops).

**Estimated Effort:** 1-2 days for a mid-level engineer, 2-3 days for a junior engineer.

**Files Modified:**
- `shuffify/models/db.py` -- Add 6 new columns to User model, update `to_dict()`
- `shuffify/services/user_service.py` -- Add `UpsertResult` dataclass, update `upsert_from_spotify()` return type and logic
- `shuffify/routes/core.py` -- Store `is_new_user` flag in session from upsert result
- `tests/conftest.py` -- Add `uri` field to `sample_user` fixture
- `tests/models/test_db_models.py` -- Add tests for new User fields and defaults
- `tests/services/test_user_service.py` -- Add tests for login tracking, new fields, `UpsertResult`
- `tests/test_integration.py` -- Add `country`, `product`, `uri` to local `sample_user` fixture
- `shuffify/services/__init__.py` -- Export `UpsertResult` 
- `CHANGELOG.md` -- Add entry under `[Unreleased]`

**Files Created:**
- One Alembic migration file (auto-generated via `flask db migrate`)

**Files Deleted:** None

---

## Context

The current `User` model (`/Users/chris/Projects/shuffify/shuffify/models/db.py`, lines 22-85) serves primarily as a "token locker" -- it stores the Spotify ID, display name, email, profile image, and an encrypted refresh token. It has no concept of login history, account status, or extended Spotify profile data.

Transforming it into a proper **user dimension table** is a prerequisite for future phases that need:
- **Analytics**: knowing when users last logged in, how often they use the app
- **User segmentation**: distinguishing free vs. premium users, geography-based features
- **Onboarding flows**: knowing whether this is a first-time or returning user
- **Soft-delete / deactivation**: an `is_active` flag for GDPR compliance and account management

This phase is intentionally narrow: it only adds columns and enriches the upsert logic. It does NOT build any UI for these fields -- that comes in later phases.

---

## Dependencies

**Prerequisites:**
- Phase 0 (PostgreSQL migration) must be merged. This phase generates an Alembic migration, which assumes Flask-Migrate is configured and pointing at a real database.
- If Phase 0 is NOT yet merged, the Alembic migration step can be deferred and done manually after Phase 0 lands. The model and service changes work fine with SQLite in development.

**What this unlocks:**
- Future phases can query `last_login_at`, `login_count`, `is_active`, `country`, `spotify_product` for user analytics dashboards, targeted features, or admin tools.
- The `is_new_user` flag in session enables future onboarding flows.

**New package dependencies:** None.

---

## Detailed Implementation Plan

### Step 1: Add New Columns to User Model

**File:** `/Users/chris/Projects/shuffify/shuffify/models/db.py`

**What to change:** Add 6 new columns to the `User` class (after `encrypted_refresh_token`, before `created_at`) and update `to_dict()`.

**BEFORE (lines 22-85):**
```python
class User(db.Model):
    """
    Spotify user record.

    Created or updated on each OAuth login via the upsert pattern.
    Links to all user-specific data (workshop sessions, upstream sources).
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    spotify_id = db.Column(
        db.String(255), unique=True, nullable=False, index=True
    )
    display_name = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    profile_image_url = db.Column(db.String(1024), nullable=True)
    # Encrypted Spotify refresh token for background job execution.
    # Encrypted with Fernet using a key derived from SECRET_KEY.
    # Set during OAuth callback, updated on token refresh.
    encrypted_refresh_token = db.Column(db.Text, nullable=True)

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

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the User to a dictionary."""
        return {
            "id": self.id,
            "spotify_id": self.spotify_id,
            "display_name": self.display_name,
            "email": self.email,
            "profile_image_url": self.profile_image_url,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "updated_at": (
                self.updated_at.isoformat() if self.updated_at else None
            ),
        }

    def __repr__(self) -> str:
        return f"<User {self.spotify_id} ({self.display_name})>"
```

**AFTER:**
```python
class User(db.Model):
    """
    Spotify user record.

    Created or updated on each OAuth login via the upsert pattern.
    Links to all user-specific data (workshop sessions, upstream sources).
    Serves as the user dimension table with login tracking and
    extended Spotify profile fields.
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    spotify_id = db.Column(
        db.String(255), unique=True, nullable=False, index=True
    )
    display_name = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    profile_image_url = db.Column(db.String(1024), nullable=True)
    # Encrypted Spotify refresh token for background job execution.
    # Encrypted with Fernet using a key derived from SECRET_KEY.
    # Set during OAuth callback, updated on token refresh.
    encrypted_refresh_token = db.Column(db.Text, nullable=True)

    # Login tracking
    last_login_at = db.Column(db.DateTime, nullable=True)
    login_count = db.Column(
        db.Integer, nullable=False, default=0
    )

    # Account status
    is_active = db.Column(
        db.Boolean, nullable=False, default=True
    )

    # Extended Spotify profile fields
    country = db.Column(db.String(10), nullable=True)
    spotify_product = db.Column(db.String(50), nullable=True)
    spotify_uri = db.Column(db.String(255), nullable=True)

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

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the User to a dictionary."""
        return {
            "id": self.id,
            "spotify_id": self.spotify_id,
            "display_name": self.display_name,
            "email": self.email,
            "profile_image_url": self.profile_image_url,
            "last_login_at": (
                self.last_login_at.isoformat()
                if self.last_login_at
                else None
            ),
            "login_count": self.login_count,
            "is_active": self.is_active,
            "country": self.country,
            "spotify_product": self.spotify_product,
            "spotify_uri": self.spotify_uri,
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
        return f"<User {self.spotify_id} ({self.display_name})>"
```

**Key design decisions:**
- `last_login_at` is nullable (null for users who existed before this migration)
- `login_count` defaults to 0 (for pre-existing users; new users get set to 1 on create)
- `is_active` defaults to True (all users are active by default)
- `country` is `String(10)` -- Spotify returns ISO 3166-1 alpha-2 codes (e.g., "US"), 10 chars is generous
- `spotify_product` is `String(50)` -- Spotify returns "free", "premium", "open", etc.
- `spotify_uri` is `String(255)` -- format is `spotify:user:USERID`

---

### Step 2: Add `UpsertResult` Dataclass and Update `UserService`

**File:** `/Users/chris/Projects/shuffify/shuffify/services/user_service.py`

This is the most significant change. The `upsert_from_spotify()` method needs to:
1. Extract the new Spotify profile fields (`country`, `product`, `uri`)
2. Set `login_count=1` and `last_login_at=now` on CREATE
3. Increment `login_count` and update `last_login_at` on UPDATE
4. Return an `UpsertResult` instead of a bare `User` (so the caller knows if it was a create or update)

**BEFORE (full file, lines 1-134):**
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
            raise UserServiceError(
                "Spotify user data missing 'id' field"
            )

        # Extract profile image URL (first image if available)
        images = user_data.get("images", [])
        profile_image_url = (
            images[0].get("url") if images else None
        )

        try:
            user = User.query.filter_by(
                spotify_id=spotify_id
            ).first()

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
            logger.error(
                f"Failed to upsert user {spotify_id}: {e}",
                exc_info=True,
            )
            raise UserServiceError(
                f"Failed to save user record: {e}"
            )

    @staticmethod
    def get_by_spotify_id(spotify_id: str) -> Optional[User]:
        ...

    @staticmethod
    def get_by_id(user_id: int) -> Optional[User]:
        ...
```

**AFTER (full file):**
```python
"""
User service for managing user records in the database.

Handles user creation, retrieval, and the upsert-on-login pattern.
"""

import logging
from dataclasses import dataclass
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


@dataclass
class UpsertResult:
    """Result of a user upsert operation."""

    user: User
    is_new: bool


class UserService:
    """Service for managing User records."""

    @staticmethod
    def upsert_from_spotify(
        user_data: Dict[str, Any],
    ) -> UpsertResult:
        """
        Create or update a User from Spotify profile data.

        Called on every successful OAuth login. If the user already
        exists (matched by spotify_id), update their profile fields,
        increment login_count, and set last_login_at. If the user
        does not exist, create a new record with login_count=1.

        Args:
            user_data: The Spotify user profile dictionary.
                Expected keys:
                - 'id' (str): Spotify user ID (REQUIRED)
                - 'display_name' (str): Display name
                - 'email' (str): Email address
                - 'images' (list): List of image dicts with
                    'url' key
                - 'country' (str): ISO 3166-1 alpha-2 country
                    code (optional)
                - 'product' (str): Spotify subscription level
                    (optional)
                - 'uri' (str): Spotify URI for the user
                    (optional)

        Returns:
            UpsertResult with the User instance and whether
            it was a new user.

        Raises:
            UserServiceError: If the upsert fails.
        """
        spotify_id = user_data.get("id")
        if not spotify_id:
            raise UserServiceError(
                "Spotify user data missing 'id' field"
            )

        # Extract profile image URL (first image if available)
        images = user_data.get("images", [])
        profile_image_url = (
            images[0].get("url") if images else None
        )

        now = datetime.now(timezone.utc)

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
                user.login_count = (user.login_count or 0) + 1
                user.updated_at = now
                is_new = False
                logger.info(
                    f"Updated existing user: {spotify_id} "
                    f"({user_data.get('display_name', 'Unknown')})"
                    f" — login #{user.login_count}"
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
                    f"Created new user: {spotify_id} "
                    f"({user_data.get('display_name', 'Unknown')})"
                )

            db.session.commit()
            return UpsertResult(user=user, is_new=is_new)

        except Exception as e:
            db.session.rollback()
            logger.error(
                f"Failed to upsert user {spotify_id}: {e}",
                exc_info=True,
            )
            raise UserServiceError(
                f"Failed to save user record: {e}"
            )

    @staticmethod
    def get_by_spotify_id(
        spotify_id: str,
    ) -> Optional[User]:
        """
        Look up a user by their Spotify ID.

        Args:
            spotify_id: The Spotify user ID.

        Returns:
            User instance if found, None otherwise.
        """
        if not spotify_id:
            return None
        return User.query.filter_by(
            spotify_id=spotify_id
        ).first()

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

**Key design decisions:**
- `UpsertResult` is a `@dataclass` (consistent with `PlaylistState` in `/Users/chris/Projects/shuffify/shuffify/services/state_service.py` line 18)
- `login_count` increment uses `(user.login_count or 0) + 1` to safely handle pre-existing rows where `login_count` is 0 or None (from the migration default)
- `user_data.get("product")` maps to `spotify_product` column (the Spotify API returns the field as `"product"` but we name the column `spotify_product` to be unambiguous)
- `user_data.get("uri")` maps to `spotify_uri` column (similarly unambiguous)

---

### Step 3: Export `UpsertResult` from Services Package

**File:** `/Users/chris/Projects/shuffify/shuffify/services/__init__.py`

**Change the UserService import block (lines 60-64) from:**
```python
# User Service
from shuffify.services.user_service import (
    UserService,
    UserServiceError,
    UserNotFoundError,
)
```

**To:**
```python
# User Service
from shuffify.services.user_service import (
    UserService,
    UserServiceError,
    UserNotFoundError,
    UpsertResult,
)
```

**Also add `"UpsertResult"` to the `__all__` list (after line 131, after `"UserNotFoundError"`):**
```python
    "UserNotFoundError",
    "UpsertResult",
```

---

### Step 4: Update Callback Route to Use `UpsertResult`

**File:** `/Users/chris/Projects/shuffify/shuffify/routes/core.py`

The callback route currently calls `UserService.upsert_from_spotify(user_data)` and ignores the return value. We need to:
1. Capture the `UpsertResult`
2. Store `is_new_user` in the Flask session

**BEFORE (lines 194-202):**
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

**AFTER:**
```python
        # Upsert user record in database (non-blocking)
        is_new_user = False
        try:
            result = UserService.upsert_from_spotify(user_data)
            is_new_user = result.is_new
        except Exception as e:
            # Database failure should NOT block login
            logger.warning(
                f"Failed to upsert user to database: {e}. "
                f"Login continues without persistence."
            )

        session["is_new_user"] = is_new_user
```

**Important:** The `session["is_new_user"] = is_new_user` line must go AFTER the try/except block (not inside the try). If the DB fails, `is_new_user` defaults to `False`, which is the safe fallback -- we treat all users as returning if we can't determine their status.

The `session["is_new_user"]` line should be placed BEFORE the existing `session.modified = True` line (which is at line 235 in the current file). The full block from the upsert through session.modified will look like:

```python
        # Upsert user record in database (non-blocking)
        is_new_user = False
        try:
            result = UserService.upsert_from_spotify(user_data)
            is_new_user = result.is_new
        except Exception as e:
            # Database failure should NOT block login
            logger.warning(
                f"Failed to upsert user to database: {e}. "
                f"Login continues without persistence."
            )

        session["is_new_user"] = is_new_user

        # Store encrypted refresh token for scheduled ops
        if token_data.get("refresh_token"):
            ... # (existing token storage code unchanged)

        session.modified = True
```

No new imports are needed in `core.py` -- `UserService` is already imported via the routes `__init__.py`.

---

### Step 5: Update Test Fixtures

#### 5a: Update `conftest.py` sample_user fixture

**File:** `/Users/chris/Projects/shuffify/tests/conftest.py`

The `sample_user` fixture (lines 52-61) already includes `country` and `product` but is missing `uri`. Add it.

**BEFORE (lines 52-61):**
```python
@pytest.fixture
def sample_user():
    """Sample Spotify user data."""
    return {
        'id': 'user123',
        'display_name': 'Test User',
        'email': 'test@example.com',
        'images': [{'url': 'https://example.com/avatar.jpg'}],
        'country': 'US',
        'product': 'premium'
    }
```

**AFTER:**
```python
@pytest.fixture
def sample_user():
    """Sample Spotify user data."""
    return {
        'id': 'user123',
        'display_name': 'Test User',
        'email': 'test@example.com',
        'images': [{'url': 'https://example.com/avatar.jpg'}],
        'country': 'US',
        'product': 'premium',
        'uri': 'spotify:user:user123',
    }
```

#### 5b: Update `test_integration.py` local sample_user fixture

**File:** `/Users/chris/Projects/shuffify/tests/test_integration.py`

This file has its own local `sample_user` fixture (lines 54-61) that is missing `country`, `product`, and `uri`. Align it with the global one.

**BEFORE (lines 54-61):**
```python
@pytest.fixture
def sample_user():
    """Sample user data."""
    return {
        'id': 'user123',
        'display_name': 'Test User',
        'email': 'test@example.com'
    }
```

**AFTER:**
```python
@pytest.fixture
def sample_user():
    """Sample user data."""
    return {
        'id': 'user123',
        'display_name': 'Test User',
        'email': 'test@example.com',
        'images': [{'url': 'https://example.com/avatar.jpg'}],
        'country': 'US',
        'product': 'premium',
        'uri': 'spotify:user:user123',
    }
```

---

### Step 6: Add Model Tests for New Fields

**File:** `/Users/chris/Projects/shuffify/tests/models/test_db_models.py`

Add new test methods to the existing `TestUserModel` class. Insert these after the existing `test_nullable_optional_fields` method (after line 124).

```python
    def test_new_fields_defaults(self, db_session):
        """Should have correct defaults for new fields."""
        user = User(spotify_id="default_user")
        db_session.add(user)
        db_session.commit()

        assert user.login_count == 0
        assert user.is_active is True
        assert user.last_login_at is None
        assert user.country is None
        assert user.spotify_product is None
        assert user.spotify_uri is None

    def test_create_user_with_all_fields(self, db_session):
        """Should create a user with all new fields populated."""
        now = datetime.now(timezone.utc)
        user = User(
            spotify_id="full_user",
            display_name="Full User",
            email="full@example.com",
            profile_image_url="https://example.com/img.jpg",
            last_login_at=now,
            login_count=5,
            is_active=True,
            country="US",
            spotify_product="premium",
            spotify_uri="spotify:user:full_user",
        )
        db_session.add(user)
        db_session.commit()

        assert user.last_login_at == now
        assert user.login_count == 5
        assert user.is_active is True
        assert user.country == "US"
        assert user.spotify_product == "premium"
        assert user.spotify_uri == "spotify:user:full_user"

    def test_to_dict_includes_new_fields(self, db_session):
        """Should include new fields in serialized dict."""
        now = datetime.now(timezone.utc)
        user = User(
            spotify_id="dict_user",
            last_login_at=now,
            login_count=3,
            is_active=True,
            country="GB",
            spotify_product="free",
            spotify_uri="spotify:user:dict_user",
        )
        db_session.add(user)
        db_session.commit()

        d = user.to_dict()
        assert d["last_login_at"] == now.isoformat()
        assert d["login_count"] == 3
        assert d["is_active"] is True
        assert d["country"] == "GB"
        assert d["spotify_product"] == "free"
        assert d["spotify_uri"] == "spotify:user:dict_user"

    def test_to_dict_null_new_fields(self, db_session):
        """Should serialize None for unpopulated new fields."""
        user = User(spotify_id="null_fields_user")
        db_session.add(user)
        db_session.commit()

        d = user.to_dict()
        assert d["last_login_at"] is None
        assert d["login_count"] == 0
        assert d["is_active"] is True
        assert d["country"] is None
        assert d["spotify_product"] is None
        assert d["spotify_uri"] is None

    def test_is_active_can_be_set_false(self, db_session):
        """Should allow setting is_active to False."""
        user = User(
            spotify_id="inactive_user",
            is_active=False,
        )
        db_session.add(user)
        db_session.commit()

        fetched = User.query.filter_by(
            spotify_id="inactive_user"
        ).first()
        assert fetched.is_active is False
```

**Note:** Add the required import at the top of the test file. Currently `/Users/chris/Projects/shuffify/tests/models/test_db_models.py` imports `db, User, WorkshopSession, UpstreamSource` from `shuffify.models.db` (line 11). Add `datetime` and `timezone` imports:

```python
from datetime import datetime, timezone
```

---

### Step 7: Add Service Tests for Enhanced Upsert

**File:** `/Users/chris/Projects/shuffify/tests/services/test_user_service.py`

Add the `UpsertResult` import and new test methods. 

**Update the imports (lines 3-5) from:**
```python
from shuffify.models.db import db, User
from shuffify.services.user_service import (
    UserService,
    UserServiceError,
)
```

**To:**
```python
from datetime import datetime, timezone

from shuffify.models.db import db, User
from shuffify.services.user_service import (
    UserService,
    UserServiceError,
    UpsertResult,
)
```

**Add these new test methods to `TestUserServiceUpsert` (after `test_upsert_empty_id_raises`, after line 127):**

```python
    def test_create_returns_upsert_result(self, app_ctx):
        """Should return UpsertResult with is_new=True."""
        user_data = {
            "id": "new_user",
            "display_name": "New User",
            "email": "new@example.com",
            "images": [],
        }

        result = UserService.upsert_from_spotify(user_data)

        assert isinstance(result, UpsertResult)
        assert result.is_new is True
        assert result.user.spotify_id == "new_user"

    def test_update_returns_upsert_result(self, app_ctx):
        """Should return UpsertResult with is_new=False."""
        user_data = {
            "id": "returning_user",
            "display_name": "Returning",
            "images": [],
        }
        UserService.upsert_from_spotify(user_data)

        result = UserService.upsert_from_spotify(user_data)

        assert isinstance(result, UpsertResult)
        assert result.is_new is False

    def test_create_sets_login_count_to_one(self, app_ctx):
        """Should set login_count to 1 on first login."""
        user_data = {
            "id": "first_login",
            "display_name": "First",
            "images": [],
        }

        result = UserService.upsert_from_spotify(user_data)

        assert result.user.login_count == 1

    def test_update_increments_login_count(self, app_ctx):
        """Should increment login_count on each login."""
        user_data = {
            "id": "multi_login",
            "display_name": "Multi",
            "images": [],
        }

        UserService.upsert_from_spotify(user_data)
        UserService.upsert_from_spotify(user_data)
        result = UserService.upsert_from_spotify(user_data)

        assert result.user.login_count == 3

    def test_create_sets_last_login_at(self, app_ctx):
        """Should set last_login_at on first login."""
        before = datetime.now(timezone.utc)
        user_data = {
            "id": "login_time",
            "display_name": "Timed",
            "images": [],
        }

        result = UserService.upsert_from_spotify(user_data)
        after = datetime.now(timezone.utc)

        assert result.user.last_login_at is not None
        assert before <= result.user.last_login_at <= after

    def test_update_refreshes_last_login_at(self, app_ctx):
        """Should update last_login_at on re-login."""
        user_data = {
            "id": "refresh_time",
            "display_name": "Refreshed",
            "images": [],
        }

        first_result = UserService.upsert_from_spotify(
            user_data
        )
        first_login = first_result.user.last_login_at

        # Second login
        second_result = UserService.upsert_from_spotify(
            user_data
        )

        assert (
            second_result.user.last_login_at
            >= first_login
        )

    def test_create_extracts_country(self, app_ctx):
        """Should extract country from Spotify data."""
        user_data = {
            "id": "country_user",
            "display_name": "Country User",
            "images": [],
            "country": "DE",
        }

        result = UserService.upsert_from_spotify(user_data)

        assert result.user.country == "DE"

    def test_create_extracts_product(self, app_ctx):
        """Should extract product as spotify_product."""
        user_data = {
            "id": "product_user",
            "display_name": "Product User",
            "images": [],
            "product": "premium",
        }

        result = UserService.upsert_from_spotify(user_data)

        assert result.user.spotify_product == "premium"

    def test_create_extracts_uri(self, app_ctx):
        """Should extract uri as spotify_uri."""
        user_data = {
            "id": "uri_user",
            "display_name": "URI User",
            "images": [],
            "uri": "spotify:user:uri_user",
        }

        result = UserService.upsert_from_spotify(user_data)

        assert result.user.spotify_uri == (
            "spotify:user:uri_user"
        )

    def test_update_refreshes_spotify_fields(self, app_ctx):
        """Should update country/product/uri on re-login."""
        user_data = {
            "id": "update_fields",
            "display_name": "Fields",
            "images": [],
            "country": "US",
            "product": "free",
            "uri": "spotify:user:update_fields",
        }
        UserService.upsert_from_spotify(user_data)

        user_data["country"] = "GB"
        user_data["product"] = "premium"
        result = UserService.upsert_from_spotify(user_data)

        assert result.user.country == "GB"
        assert result.user.spotify_product == "premium"

    def test_missing_optional_fields_default_none(
        self, app_ctx
    ):
        """Should default to None when optional fields absent."""
        user_data = {
            "id": "minimal_user",
            "display_name": "Minimal",
            "images": [],
        }

        result = UserService.upsert_from_spotify(user_data)

        assert result.user.country is None
        assert result.user.spotify_product is None
        assert result.user.spotify_uri is None
```

**Also update the two existing tests that check the return value of `upsert_from_spotify` to unwrap `UpsertResult`:**

**`test_create_new_user` (line 48) -- BEFORE:**
```python
    def test_create_new_user(self, app_ctx):
        """Should create a new user from Spotify data."""
        user_data = {
            "id": "spotify_user_1",
            "display_name": "Test User",
            "email": "test@example.com",
            "images": [
                {"url": "https://example.com/img.jpg"}
            ],
        }

        user = UserService.upsert_from_spotify(user_data)

        assert user.spotify_id == "spotify_user_1"
        assert user.display_name == "Test User"
        assert user.email == "test@example.com"
        assert user.profile_image_url == (
            "https://example.com/img.jpg"
        )
```

**AFTER:**
```python
    def test_create_new_user(self, app_ctx):
        """Should create a new user from Spotify data."""
        user_data = {
            "id": "spotify_user_1",
            "display_name": "Test User",
            "email": "test@example.com",
            "images": [
                {"url": "https://example.com/img.jpg"}
            ],
        }

        result = UserService.upsert_from_spotify(user_data)

        assert result.user.spotify_id == "spotify_user_1"
        assert result.user.display_name == "Test User"
        assert result.user.email == "test@example.com"
        assert result.user.profile_image_url == (
            "https://example.com/img.jpg"
        )
```

**`test_update_existing_user` (line 68) -- BEFORE:**
```python
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
            "images": [
                {"url": "https://example.com/new.jpg"}
            ],
        }
        user = UserService.upsert_from_spotify(updated_data)

        assert user.display_name == "New Name"
        assert user.email == "new@example.com"
        assert user.profile_image_url == (
            "https://example.com/new.jpg"
        )

        # Verify only one user exists
        count = User.query.filter_by(
            spotify_id="spotify_user_1"
        ).count()
        assert count == 1
```

**AFTER:**
```python
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
            "images": [
                {"url": "https://example.com/new.jpg"}
            ],
        }
        result = UserService.upsert_from_spotify(
            updated_data
        )

        assert result.user.display_name == "New Name"
        assert result.user.email == "new@example.com"
        assert result.user.profile_image_url == (
            "https://example.com/new.jpg"
        )

        # Verify only one user exists
        count = User.query.filter_by(
            spotify_id="spotify_user_1"
        ).count()
        assert count == 1
```

**`test_upsert_no_images` (line 100) -- BEFORE:**
```python
    def test_upsert_no_images(self, app_ctx):
        """Should handle user data with no images."""
        user_data = {
            "id": "user_no_img",
            "display_name": "No Image User",
            "images": [],
        }
        user = UserService.upsert_from_spotify(user_data)
        assert user.profile_image_url is None
```

**AFTER:**
```python
    def test_upsert_no_images(self, app_ctx):
        """Should handle user data with no images."""
        user_data = {
            "id": "user_no_img",
            "display_name": "No Image User",
            "images": [],
        }
        result = UserService.upsert_from_spotify(user_data)
        assert result.user.profile_image_url is None
```

---

### Step 8: Generate Alembic Migration

**Prerequisite:** Phase 0 (PostgreSQL + Alembic setup) must be merged.

Run this command from the project root with the virtual environment activated:

```bash
flask db migrate -m "Add login tracking and Spotify profile fields to User model"
```

This auto-generates a migration in `migrations/versions/` that adds:
- `last_login_at` (DateTime, nullable)
- `login_count` (Integer, not null, default 0)
- `is_active` (Boolean, not null, default True)
- `country` (String(10), nullable)
- `spotify_product` (String(50), nullable)
- `spotify_uri` (String(255), nullable)

**Review the generated migration** to ensure:
1. All 6 columns are present in `upgrade()`
2. All 6 columns have corresponding `drop_column()` calls in `downgrade()`
3. `login_count` has `server_default='0'` (not just a Python default)
4. `is_active` has `server_default='true'` (or `sa.text('true')`)

If Alembic does not automatically set `server_default`, manually add it:
```python
op.add_column('users', sa.Column(
    'login_count', sa.Integer(),
    nullable=False, server_default='0'
))
op.add_column('users', sa.Column(
    'is_active', sa.Boolean(),
    nullable=False, server_default=sa.text('true')
))
```

This ensures existing rows get proper values without a data migration.

Then apply:
```bash
flask db upgrade
```

**If Phase 0 is NOT yet merged:** Skip this step. The model changes work fine with `db.create_all()` in development (the `app` fixture in conftest.py uses `sqlite:///:memory:` and calls `db.create_all()`). Generate the migration after Phase 0 lands.

---

### Step 9: Update CHANGELOG.md

**File:** `/Users/chris/Projects/shuffify/CHANGELOG.md`

Add under the existing `## [Unreleased]` section, in the `### Added` block:

```markdown
- **User Dimension Table Enhancement** - Enriched User model with login tracking and Spotify profile fields
  - New fields: `last_login_at`, `login_count`, `is_active`, `country`, `spotify_product`, `spotify_uri`
  - `upsert_from_spotify()` now returns `UpsertResult` with `is_new` flag for create/update distinction
  - Login count auto-increments on each OAuth login
  - `is_new_user` flag stored in Flask session for future onboarding flows
  - Alembic migration for schema changes
```

---

## Edge Cases

1. **Pre-existing users (migration):** Users created before this migration will have `login_count=0`, `last_login_at=None`, `is_active=True`, and null for `country`/`spotify_product`/`spotify_uri`. On their next login, `login_count` becomes 1 and all fields are populated. The `(user.login_count or 0) + 1` expression handles both 0 and None safely.

2. **Spotify profile without `country`/`product`/`uri`:** The Spotify API may not return these fields depending on the OAuth scope. The `user-read-private` scope is needed for `country` and `product`. If absent, `user_data.get("country")` returns `None`, which is fine since all three columns are nullable.

3. **Database failure during upsert:** The callback route already handles this with a try/except. The `is_new_user` variable defaults to `False` before the try block, so if the DB fails, the session gets `is_new_user=False` (safe fallback: treat as returning user).

4. **Concurrent logins (same user, two tabs):** Both requests will read the same `login_count`, increment it, and write. This is a benign race condition -- one increment may be lost. For a login counter, this is acceptable. If exact accuracy were needed, we would use SQL `UPDATE users SET login_count = login_count + 1` via raw SQL, but that is overkill for this use case.

5. **Very long Spotify URIs:** The `spotify_uri` column is `String(255)`. Spotify user URIs follow the format `spotify:user:USERNAME` where usernames are max ~30 characters. 255 is more than sufficient.

6. **Empty string vs None:** `user_data.get("country")` returns `None` if the key is absent, but could return `""` if Spotify sends an empty string. Both are fine for a nullable column. No special handling needed.

---

## Test Plan

**New test count:** ~17 new tests (5 model tests + 12 service tests)

**Run all tests:**
```bash
pytest tests/ -v
```

**Run only affected tests:**
```bash
pytest tests/models/test_db_models.py tests/services/test_user_service.py -v
```

**Manual verification:**
1. Start the dev server: `python run.py`
2. Log in via Spotify
3. Check the database: `flask shell` then `User.query.first().to_dict()` -- verify new fields are populated
4. Log in again -- verify `login_count` incremented and `last_login_at` updated
5. Check Flask session for `is_new_user` key

---

## Verification Checklist

- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes (all existing + new tests)
- [ ] `User.to_dict()` includes all 6 new fields
- [ ] `UpsertResult` is importable from `shuffify.services`
- [ ] New user creation sets `login_count=1`, `last_login_at` to current time
- [ ] Returning user login increments `login_count`, updates `last_login_at`
- [ ] `country`, `spotify_product`, `spotify_uri` are extracted from Spotify data
- [ ] Missing Spotify fields default to `None` (no crash)
- [ ] `session["is_new_user"]` is set in the callback route
- [ ] DB failure in callback still allows login (non-blocking behavior preserved)
- [ ] Alembic migration applies cleanly (if Phase 0 is available)
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`
- [ ] No changes to existing functionality (all pre-existing tests still pass)

---

## What NOT to Do

- **Do NOT change the `User.__tablename__`** -- it must remain `"users"`
- **Do NOT remove or rename any existing columns** -- this is additive only
- **Do NOT change the return type of `get_by_spotify_id()` or `get_by_id()`** -- only `upsert_from_spotify()` changes its return type
- **Do NOT build UI for the new fields** -- that comes in future phases
- **Do NOT add new OAuth scopes** -- `country` and `product` require `user-read-private`, which should already be requested. If it is not, note it but do NOT change the scope in this PR
- **Do NOT add a `server_default` on the model column definitions** -- SQLAlchemy `default=` is sufficient for new inserts. The `server_default` is only needed in the Alembic migration for existing rows
- **Do NOT use `db.session.execute(text("UPDATE ..."))` for login_count** -- the ORM-level increment is sufficient for this use case
- **Do NOT modify the `WorkshopSession`, `UpstreamSource`, `Schedule`, or `JobExecution` models** -- this phase only touches `User`

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/models/db.py` - Core change: add 6 new columns to User model and update to_dict()
- `/Users/chris/Projects/shuffify/shuffify/services/user_service.py` - Core change: add UpsertResult dataclass, update upsert logic with login tracking and new field extraction
- `/Users/chris/Projects/shuffify/shuffify/routes/core.py` - Update callback to capture UpsertResult and store is_new_user in session
- `/Users/chris/Projects/shuffify/tests/services/test_user_service.py` - Most test changes: update existing tests for new return type, add ~12 new tests
- `/Users/chris/Projects/shuffify/tests/models/test_db_models.py` - Add ~5 new tests for model field defaults and serialization
