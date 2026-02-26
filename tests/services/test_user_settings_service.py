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
        assert (
            settings.dashboard_show_recent_activity is True
        )
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
        assert (
            settings.default_algorithm == "BalancedShuffle"
        )

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
        assert (
            settings.dashboard_show_recent_activity is False
        )

    def test_update_extra_json_field(
        self, app_ctx, test_user
    ):
        """Should store arbitrary JSON in extra field."""
        extra_data = {
            "beta_features": True,
            "layout": "grid",
        }
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
