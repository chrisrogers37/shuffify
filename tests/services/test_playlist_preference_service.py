"""
Tests for PlaylistPreferenceService.

Covers get_user_preferences, save_order, toggle_hidden,
toggle_pinned, reset_preferences, and apply_preferences.
"""

import pytest

from shuffify.models.db import (
    db,
    User,
    PlaylistPreference,
)
from shuffify.services.playlist_preference_service import (
    PlaylistPreferenceService,
    PlaylistPreferenceError,
)


@pytest.fixture
def test_user(app_ctx):
    """Create a test user."""
    user = User(
        spotify_id="pref_svc_user",
        display_name="Pref Svc User",
    )
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def other_user(app_ctx):
    """Create another test user for isolation tests."""
    user = User(
        spotify_id="other_pref_user",
        display_name="Other User",
    )
    db.session.add(user)
    db.session.commit()
    return user


class TestGetUserPreferences:
    """Tests for get_user_preferences."""

    def test_empty_dict_when_no_prefs(
        self, app_ctx, test_user
    ):
        """Should return empty dict for new user."""
        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        assert prefs == {}

    def test_keyed_by_spotify_id(
        self, app_ctx, test_user
    ):
        """Should return dict keyed by spotify_playlist_id."""
        pref = PlaylistPreference(
            user_id=test_user.id,
            spotify_playlist_id="pl1",
            sort_order=0,
        )
        db.session.add(pref)
        db.session.commit()

        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        assert "pl1" in prefs
        assert prefs["pl1"].sort_order == 0

    def test_doesnt_leak_across_users(
        self, app_ctx, test_user, other_user
    ):
        """Prefs from user A should not appear for user B."""
        pref = PlaylistPreference(
            user_id=test_user.id,
            spotify_playlist_id="pl1",
        )
        db.session.add(pref)
        db.session.commit()

        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(other_user.id)
        )
        assert prefs == {}


class TestSaveOrder:
    """Tests for save_order."""

    def test_creates_new_prefs(
        self, app_ctx, test_user
    ):
        """Should create prefs for playlists."""
        count = PlaylistPreferenceService.save_order(
            test_user.id, ["pl1", "pl2", "pl3"]
        )
        assert count == 3

        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        assert prefs["pl1"].sort_order == 0
        assert prefs["pl2"].sort_order == 1
        assert prefs["pl3"].sort_order == 2

    def test_updates_existing_prefs(
        self, app_ctx, test_user
    ):
        """Should update sort_order for existing."""
        PlaylistPreferenceService.save_order(
            test_user.id, ["pl1", "pl2"]
        )
        PlaylistPreferenceService.save_order(
            test_user.id, ["pl2", "pl1"]
        )

        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        assert prefs["pl2"].sort_order == 0
        assert prefs["pl1"].sort_order == 1

    def test_preserves_hidden_pinned(
        self, app_ctx, test_user
    ):
        """Should not reset hidden/pinned on reorder."""
        pref = PlaylistPreference(
            user_id=test_user.id,
            spotify_playlist_id="pl1",
            sort_order=0,
            is_hidden=True,
            is_pinned=True,
        )
        db.session.add(pref)
        db.session.commit()

        PlaylistPreferenceService.save_order(
            test_user.id, ["pl1"]
        )

        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        assert prefs["pl1"].is_hidden is True
        assert prefs["pl1"].is_pinned is True

    def test_returns_count(self, app_ctx, test_user):
        """Should return number of updated/created."""
        count = PlaylistPreferenceService.save_order(
            test_user.id, ["a", "b", "c", "d"]
        )
        assert count == 4

    def test_sequential_ordering(
        self, app_ctx, test_user
    ):
        """Sort orders should be sequential 0..N-1."""
        PlaylistPreferenceService.save_order(
            test_user.id,
            ["z", "y", "x", "w", "v"],
        )
        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        orders = sorted(
            p.sort_order for p in prefs.values()
        )
        assert orders == [0, 1, 2, 3, 4]


class TestToggleHidden:
    """Tests for toggle_hidden."""

    def test_creates_on_first_toggle(
        self, app_ctx, test_user
    ):
        """Should create pref and set hidden=True."""
        result = (
            PlaylistPreferenceService.toggle_hidden(
                test_user.id, "pl1"
            )
        )
        assert result is True

        pref = (
            PlaylistPreferenceService.get_preference(
                test_user.id, "pl1"
            )
        )
        assert pref is not None
        assert pref.is_hidden is True

    def test_toggles_both_directions(
        self, app_ctx, test_user
    ):
        """Should toggle True -> False."""
        PlaylistPreferenceService.toggle_hidden(
            test_user.id, "pl1"
        )
        result = (
            PlaylistPreferenceService.toggle_hidden(
                test_user.id, "pl1"
            )
        )
        assert result is False

    def test_returns_new_value(
        self, app_ctx, test_user
    ):
        """Return value should be the new is_hidden."""
        r1 = PlaylistPreferenceService.toggle_hidden(
            test_user.id, "pl1"
        )
        assert r1 is True
        r2 = PlaylistPreferenceService.toggle_hidden(
            test_user.id, "pl1"
        )
        assert r2 is False

    def test_isolated_per_user(
        self, app_ctx, test_user, other_user
    ):
        """Hiding for user A should not affect user B."""
        PlaylistPreferenceService.toggle_hidden(
            test_user.id, "pl1"
        )
        pref = (
            PlaylistPreferenceService.get_preference(
                other_user.id, "pl1"
            )
        )
        assert pref is None


class TestTogglePinned:
    """Tests for toggle_pinned."""

    def test_creates_on_first_toggle(
        self, app_ctx, test_user
    ):
        """Should create pref and set pinned=True."""
        result = (
            PlaylistPreferenceService.toggle_pinned(
                test_user.id, "pl1"
            )
        )
        assert result is True

    def test_toggles_both_directions(
        self, app_ctx, test_user
    ):
        """Should toggle True -> False."""
        PlaylistPreferenceService.toggle_pinned(
            test_user.id, "pl1"
        )
        result = (
            PlaylistPreferenceService.toggle_pinned(
                test_user.id, "pl1"
            )
        )
        assert result is False

    def test_returns_new_value(
        self, app_ctx, test_user
    ):
        """Return value should be the new is_pinned."""
        r1 = PlaylistPreferenceService.toggle_pinned(
            test_user.id, "pl1"
        )
        assert r1 is True


class TestResetPreferences:
    """Tests for reset_preferences."""

    def test_deletes_all(self, app_ctx, test_user):
        """Should delete all prefs for user."""
        PlaylistPreferenceService.save_order(
            test_user.id, ["pl1", "pl2", "pl3"]
        )
        count = (
            PlaylistPreferenceService
            .reset_preferences(test_user.id)
        )
        assert count == 3

        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        assert prefs == {}

    def test_returns_count(self, app_ctx, test_user):
        """Should return number deleted."""
        PlaylistPreferenceService.save_order(
            test_user.id, ["a", "b"]
        )
        count = (
            PlaylistPreferenceService
            .reset_preferences(test_user.id)
        )
        assert count == 2

    def test_doesnt_affect_other_users(
        self, app_ctx, test_user, other_user
    ):
        """Reset for user A should not affect user B."""
        PlaylistPreferenceService.save_order(
            test_user.id, ["pl1"]
        )
        PlaylistPreferenceService.save_order(
            other_user.id, ["pl2"]
        )

        PlaylistPreferenceService.reset_preferences(
            test_user.id
        )

        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(other_user.id)
        )
        assert "pl2" in prefs

    def test_returns_zero_when_empty(
        self, app_ctx, test_user
    ):
        """Should return 0 when no prefs exist."""
        count = (
            PlaylistPreferenceService
            .reset_preferences(test_user.id)
        )
        assert count == 0


class TestApplyPreferences:
    """Tests for apply_preferences (3-tuple return)."""

    def test_favorites_separated(
        self, app_ctx, test_user
    ):
        """Pinned playlists should appear in favorites."""
        playlists = [
            {"id": "pl1", "name": "A"},
            {"id": "pl2", "name": "B"},
            {"id": "pl3", "name": "C"},
        ]

        PlaylistPreferenceService.toggle_pinned(
            test_user.id, "pl3"
        )
        PlaylistPreferenceService.save_order(
            test_user.id, ["pl1", "pl2", "pl3"]
        )

        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        favorites, regular, hidden = (
            PlaylistPreferenceService
            .apply_preferences(playlists, prefs)
        )

        assert len(favorites) == 1
        assert favorites[0]["id"] == "pl3"
        assert len(regular) == 2
        assert "pl3" not in [p["id"] for p in regular]

    def test_hidden_excluded(self, app_ctx, test_user):
        """Hidden playlists not in visible lists."""
        playlists = [
            {"id": "pl1", "name": "A"},
            {"id": "pl2", "name": "B"},
        ]

        PlaylistPreferenceService.toggle_hidden(
            test_user.id, "pl2"
        )
        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        favorites, regular, hidden = (
            PlaylistPreferenceService
            .apply_preferences(playlists, prefs)
        )

        assert len(favorites) == 0
        assert len(regular) == 1
        assert regular[0]["id"] == "pl1"
        assert len(hidden) == 1
        assert hidden[0]["id"] == "pl2"

    def test_unknown_at_end(self, app_ctx, test_user):
        """Playlists without prefs appear after known."""
        playlists = [
            {"id": "pl1", "name": "A"},
            {"id": "pl2", "name": "B"},
            {"id": "pl3", "name": "C"},
        ]

        PlaylistPreferenceService.save_order(
            test_user.id, ["pl1"]
        )
        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        favorites, regular, hidden = (
            PlaylistPreferenceService
            .apply_preferences(playlists, prefs)
        )

        assert regular[0]["id"] == "pl1"
        assert regular[1]["id"] == "pl2"
        assert regular[2]["id"] == "pl3"

    def test_sort_order_respected(
        self, app_ctx, test_user
    ):
        """Sort order should determine position."""
        playlists = [
            {"id": "pl1", "name": "A"},
            {"id": "pl2", "name": "B"},
            {"id": "pl3", "name": "C"},
        ]

        PlaylistPreferenceService.save_order(
            test_user.id, ["pl3", "pl1", "pl2"]
        )
        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        favorites, regular, hidden = (
            PlaylistPreferenceService
            .apply_preferences(playlists, prefs)
        )

        ids = [p["id"] for p in regular]
        assert ids == ["pl3", "pl1", "pl2"]

    def test_empty_prefs_returns_original(self):
        """Empty prefs returns empty favorites."""
        playlists = [
            {"id": "pl1"},
            {"id": "pl2"},
        ]
        favorites, regular, hidden = (
            PlaylistPreferenceService
            .apply_preferences(playlists, {})
        )
        assert len(favorites) == 0
        assert len(regular) == 2
        assert len(hidden) == 0

    def test_hidden_pinned_goes_to_hidden(
        self, app_ctx, test_user
    ):
        """A playlist that is both pinned and hidden
        should go to hidden, not favorites."""
        playlists = [
            {"id": "pl1", "name": "A"},
        ]

        PlaylistPreferenceService.toggle_pinned(
            test_user.id, "pl1"
        )
        PlaylistPreferenceService.toggle_hidden(
            test_user.id, "pl1"
        )
        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        favorites, regular, hidden = (
            PlaylistPreferenceService
            .apply_preferences(playlists, prefs)
        )

        assert len(favorites) == 0
        assert len(regular) == 0
        assert len(hidden) == 1
        assert hidden[0]["id"] == "pl1"

    def test_multiple_favorites_sorted(
        self, app_ctx, test_user
    ):
        """Multiple favorites should be sorted by
        sort_order."""
        playlists = [
            {"id": "pl1", "name": "A"},
            {"id": "pl2", "name": "B"},
            {"id": "pl3", "name": "C"},
        ]

        PlaylistPreferenceService.toggle_pinned(
            test_user.id, "pl1"
        )
        PlaylistPreferenceService.toggle_pinned(
            test_user.id, "pl3"
        )
        PlaylistPreferenceService.save_order(
            test_user.id, ["pl3", "pl1", "pl2"]
        )

        prefs = (
            PlaylistPreferenceService
            .get_user_preferences(test_user.id)
        )
        favorites, regular, hidden = (
            PlaylistPreferenceService
            .apply_preferences(playlists, prefs)
        )

        assert len(favorites) == 2
        assert favorites[0]["id"] == "pl3"
        assert favorites[1]["id"] == "pl1"
        assert len(regular) == 1
        assert regular[0]["id"] == "pl2"
