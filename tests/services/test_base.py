"""
Tests for shared service base utilities.

Tests cover safe_commit, get_user_or_raise, and get_owned_entity.
"""

import pytest

from shuffify.models.db import (
    db,
    User,
    WorkshopSession,
)
from shuffify.services.base import (
    safe_commit,
    get_user_or_raise,
    get_owned_entity,
)


class FakeError(Exception):
    """Fake exception class for verifying exception behavior."""

    pass


class FakeNotFoundError(Exception):
    """Fake exception for not-found scenarios."""

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
    app.config["SCHEDULER_ENABLED"] = False

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

    def test_success(self, app_ctx, test_user):
        """Should commit and persist changes."""
        user2 = User(
            spotify_id="new_user",
            display_name="New User",
        )
        db.session.add(user2)
        safe_commit("create test user", FakeError)

        found = User.query.filter_by(
            spotify_id="new_user"
        ).first()
        assert found is not None
        assert found.display_name == "New User"

    def test_rollback_on_failure(
        self, app_ctx, test_user
    ):
        """Should rollback and raise on commit failure."""
        duplicate = User(
            spotify_id="base_test_user",
            display_name="Duplicate",
        )
        db.session.add(duplicate)

        with pytest.raises(FakeError, match="Failed to"):
            safe_commit("create duplicate user", FakeError)

    def test_session_clean_after_rollback(
        self, app_ctx, test_user
    ):
        """After failed safe_commit, session should be clean."""
        duplicate = User(
            spotify_id="base_test_user",
            display_name="Duplicate",
        )
        db.session.add(duplicate)

        with pytest.raises(FakeError):
            safe_commit("create duplicate", FakeError)

        # Session should be usable after rollback
        user2 = User(
            spotify_id="after_rollback",
            display_name="After Rollback",
        )
        db.session.add(user2)
        db.session.commit()
        found = User.query.filter_by(
            spotify_id="after_rollback"
        ).first()
        assert found is not None

    def test_default_exception_class(
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

    def test_custom_exception_class(
        self, app_ctx, test_user
    ):
        """Should raise the specified custom exception."""
        duplicate = User(
            spotify_id="base_test_user",
            display_name="Duplicate",
        )
        db.session.add(duplicate)

        with pytest.raises(FakeError):
            safe_commit("create duplicate", FakeError)


class TestGetUserOrRaise:
    """Tests for get_user_or_raise utility."""

    def test_returns_user_when_found(
        self, app_ctx, test_user
    ):
        """Should return the user when found."""
        user = get_user_or_raise(
            "base_test_user", FakeError
        )
        assert user is not None
        assert user.spotify_id == "base_test_user"

    def test_raises_when_not_found(self, app_ctx):
        """Should raise when user not found."""
        with pytest.raises(
            FakeError, match="User not found"
        ):
            get_user_or_raise("ghost", FakeError)

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
            FakeError, match="nonexistent_user"
        ):
            get_user_or_raise(
                "nonexistent_user", FakeError
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
            FakeNotFoundError,
        )
        assert result.id == ws.id

    def test_raises_when_not_found(
        self, app_ctx, test_user
    ):
        """Should raise when entity does not exist."""
        with pytest.raises(FakeNotFoundError):
            get_owned_entity(
                WorkshopSession,
                99999,
                test_user.id,
                FakeNotFoundError,
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

        with pytest.raises(FakeNotFoundError):
            get_owned_entity(
                WorkshopSession,
                ws.id,
                99999,
                FakeNotFoundError,
            )

    def test_exception_message_contains_class_name(
        self, app_ctx, test_user
    ):
        """Should include entity class name in message."""
        with pytest.raises(
            FakeNotFoundError,
            match="WorkshopSession",
        ):
            get_owned_entity(
                WorkshopSession,
                99999,
                test_user.id,
                FakeNotFoundError,
            )
