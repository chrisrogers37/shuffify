"""
Tests for UserService.

Tests cover user upsert, lookup, and error handling.
"""

import pytest
from datetime import datetime, timezone

from shuffify.models.db import User
from shuffify.services.user_service import (
    UserService,
    UserServiceError,
    UpsertResult,
)


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

        result = UserService.upsert_from_spotify(user_data)

        assert result.user.spotify_id == "spotify_user_1"
        assert result.user.display_name == "Test User"
        assert result.user.email == "test@example.com"
        assert result.user.profile_image_url == (
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

    def test_upsert_no_images(self, app_ctx):
        """Should handle user data with no images."""
        user_data = {
            "id": "user_no_img",
            "display_name": "No Image User",
            "images": [],
        }
        result = UserService.upsert_from_spotify(user_data)
        assert result.user.profile_image_url is None

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
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        user_data = {
            "id": "login_time",
            "display_name": "Timed",
            "images": [],
        }

        result = UserService.upsert_from_spotify(user_data)
        after = datetime.now(timezone.utc).replace(tzinfo=None)

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

        second_result = UserService.upsert_from_spotify(
            user_data
        )

        assert (
            second_result.user.last_login_at >= first_login
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
        result = UserService.upsert_from_spotify({
            "id": "user_y",
            "display_name": "User Y",
            "images": [],
        })

        user = UserService.get_by_id(result.user.id)
        assert user is not None
        assert user.spotify_id == "user_y"

    def test_get_by_id_not_found(self, app_ctx):
        """Should return None for non-existent ID."""
        user = UserService.get_by_id(99999)
        assert user is None


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

        result = UserService.upsert_from_spotify(user_data)

        settings = UserSettings.query.filter_by(
            user_id=result.user.id
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

        result = UserService.upsert_from_spotify(user_data)

        # Verify settings exist
        count_before = UserSettings.query.filter_by(
            user_id=result.user.id
        ).count()
        assert count_before == 1

        # Re-login (upsert again)
        UserService.upsert_from_spotify(user_data)

        count_after = UserSettings.query.filter_by(
            user_id=result.user.id
        ).count()
        assert count_after == 1
