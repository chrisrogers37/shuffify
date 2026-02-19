"""
Tests for user settings request validation schemas.

Tests UserSettingsUpdateRequest Pydantic model for settings
form payload validation.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.settings_requests import (
    UserSettingsUpdateRequest,
)


# =============================================================================
# UserSettingsUpdateRequest — Valid inputs
# =============================================================================


class TestUserSettingsUpdateRequestValid:
    """Tests for valid UserSettingsUpdateRequest payloads."""

    def test_all_fields_provided(self):
        """Should accept a full settings update."""
        req = UserSettingsUpdateRequest(
            default_algorithm="BasicShuffle",
            theme="dark",
            notifications_enabled=True,
            auto_snapshot_enabled=True,
            max_snapshots_per_playlist=10,
            dashboard_show_recent_activity=False,
        )
        assert req.default_algorithm == "BasicShuffle"
        assert req.theme == "dark"
        assert req.notifications_enabled is True
        assert req.auto_snapshot_enabled is True
        assert req.max_snapshots_per_playlist == 10
        assert req.dashboard_show_recent_activity is False

    def test_partial_update_algorithm_only(self):
        """Should accept partial update with just algorithm."""
        req = UserSettingsUpdateRequest(
            default_algorithm="BalancedShuffle",
        )
        assert req.default_algorithm == "BalancedShuffle"
        assert req.theme is None
        assert req.notifications_enabled is None

    def test_partial_update_theme_only(self):
        """Should accept partial update with just theme."""
        req = UserSettingsUpdateRequest(theme="light")
        assert req.theme == "light"
        assert req.default_algorithm is None

    def test_partial_update_booleans_only(self):
        """Should accept partial update with boolean fields."""
        req = UserSettingsUpdateRequest(
            notifications_enabled=False,
            auto_snapshot_enabled=True,
        )
        assert req.notifications_enabled is False
        assert req.auto_snapshot_enabled is True

    def test_empty_request_all_none(self):
        """Should accept request with no fields set."""
        req = UserSettingsUpdateRequest()
        assert req.default_algorithm is None
        assert req.theme is None
        assert req.notifications_enabled is None
        assert req.auto_snapshot_enabled is None
        assert req.max_snapshots_per_playlist is None
        assert req.dashboard_show_recent_activity is None

    def test_all_valid_algorithms(self):
        """Should accept every registered algorithm name."""
        valid_names = [
            "BasicShuffle",
            "BalancedShuffle",
            "PercentageShuffle",
            "StratifiedShuffle",
            "ArtistSpacingShuffle",
            "AlbumSequenceShuffle",
            "TempoGradientShuffle",
        ]
        for name in valid_names:
            req = UserSettingsUpdateRequest(
                default_algorithm=name
            )
            assert req.default_algorithm == name

    def test_all_valid_themes(self):
        """Should accept all valid theme choices."""
        for theme in ["light", "dark", "system"]:
            req = UserSettingsUpdateRequest(theme=theme)
            assert req.theme == theme

    def test_theme_case_insensitive(self):
        """Should normalize theme to lowercase."""
        req = UserSettingsUpdateRequest(theme="DARK")
        assert req.theme == "dark"

        req = UserSettingsUpdateRequest(theme="Light")
        assert req.theme == "light"

    def test_theme_stripped(self):
        """Should strip whitespace from theme."""
        req = UserSettingsUpdateRequest(theme="  dark  ")
        assert req.theme == "dark"

    def test_algorithm_none_is_valid(self):
        """Should accept None as algorithm (no default)."""
        req = UserSettingsUpdateRequest(
            default_algorithm=None
        )
        assert req.default_algorithm is None

    def test_algorithm_empty_string_becomes_none(self):
        """Should normalize empty string algorithm to None."""
        req = UserSettingsUpdateRequest(
            default_algorithm=""
        )
        assert req.default_algorithm is None

    def test_algorithm_whitespace_only_becomes_none(self):
        """Should normalize whitespace-only algorithm to None."""
        req = UserSettingsUpdateRequest(
            default_algorithm="   "
        )
        assert req.default_algorithm is None

    def test_max_snapshots_boundary_min(self):
        """Should accept minimum value of 1."""
        req = UserSettingsUpdateRequest(
            max_snapshots_per_playlist=1
        )
        assert req.max_snapshots_per_playlist == 1

    def test_max_snapshots_boundary_max(self):
        """Should accept maximum value of 50."""
        req = UserSettingsUpdateRequest(
            max_snapshots_per_playlist=50
        )
        assert req.max_snapshots_per_playlist == 50

    def test_extra_fields_ignored(self):
        """Should ignore unknown fields."""
        req = UserSettingsUpdateRequest(
            theme="dark",
            unknown_field="should be ignored",
        )
        assert req.theme == "dark"
        assert not hasattr(req, "unknown_field")


# =============================================================================
# UserSettingsUpdateRequest — Invalid inputs
# =============================================================================


class TestUserSettingsUpdateRequestInvalid:
    """Tests for invalid UserSettingsUpdateRequest payloads."""

    def test_invalid_algorithm_name(self):
        """Should reject unknown algorithm names."""
        with pytest.raises(ValidationError) as exc_info:
            UserSettingsUpdateRequest(
                default_algorithm="NonexistentShuffle"
            )
        assert "Invalid algorithm" in str(exc_info.value)
        assert "NonexistentShuffle" in str(exc_info.value)

    def test_invalid_theme(self):
        """Should reject invalid theme choices."""
        with pytest.raises(ValidationError) as exc_info:
            UserSettingsUpdateRequest(theme="blue")
        assert "Invalid theme" in str(exc_info.value)
        assert "blue" in str(exc_info.value)

    def test_max_snapshots_below_min(self):
        """Should reject max_snapshots below 1."""
        with pytest.raises(ValidationError) as exc_info:
            UserSettingsUpdateRequest(
                max_snapshots_per_playlist=0
            )
        assert (
            "greater than or equal to 1"
            in str(exc_info.value).lower()
        )

    def test_max_snapshots_above_max(self):
        """Should reject max_snapshots above 50."""
        with pytest.raises(ValidationError) as exc_info:
            UserSettingsUpdateRequest(
                max_snapshots_per_playlist=51
            )
        assert (
            "less than or equal to 50"
            in str(exc_info.value).lower()
        )

    def test_max_snapshots_negative(self):
        """Should reject negative max_snapshots."""
        with pytest.raises(ValidationError):
            UserSettingsUpdateRequest(
                max_snapshots_per_playlist=-5
            )

    def test_theme_empty_string(self):
        """Should reject empty string theme after strip."""
        with pytest.raises(ValidationError) as exc_info:
            UserSettingsUpdateRequest(theme="")
        assert "Invalid theme" in str(exc_info.value)
