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
        with pytest.raises(
            UserServiceError, match="missing 'id'"
        ):
            UserService.upsert_from_spotify(
                {"display_name": "No ID"}
            )

    def test_upsert_empty_id_raises(self, app_ctx):
        """Should raise error when spotify ID is empty."""
        with pytest.raises(
            UserServiceError, match="missing 'id'"
        ):
            UserService.upsert_from_spotify(
                {"id": "", "display_name": "Empty"}
            )


class TestUserServiceLookup:
    """Tests for get_by_spotify_id and get_by_id."""

    def test_get_by_spotify_id_found(self, app_ctx):
        """Should return user when found."""
        UserService.upsert_from_spotify({
            "id": "user_x",
            "display_name": "User X",
            "images": [],
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
            "id": "user_y",
            "display_name": "User Y",
            "images": [],
        })

        user = UserService.get_by_id(created.id)
        assert user is not None
        assert user.spotify_id == "user_y"

    def test_get_by_id_not_found(self, app_ctx):
        """Should return None for non-existent ID."""
        user = UserService.get_by_id(99999)
        assert user is None
