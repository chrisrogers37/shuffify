"""
Tests for PlaylistPairService.

Tests cover CRUD operations for playlist pairs, archive/unarchive
track logic, archive playlist creation, and to_dict serialization.
"""

import pytest
from unittest.mock import MagicMock, call

from shuffify.models.db import db, PlaylistPair
from shuffify.services.user_service import UserService
from shuffify.services.playlist_pair_service import (
    PlaylistPairService,
    PlaylistPairExistsError,
    PlaylistPairNotFoundError,
)


@pytest.fixture
def user(db_app):
    """Provide a test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield result.user


@pytest.fixture
def user2(db_app):
    """Provide a second test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify({
            "id": "user456",
            "display_name": "Test User 2",
            "images": [],
        })
        yield result.user


@pytest.fixture
def mock_sp():
    """Mock Spotify API client."""
    sp = MagicMock()
    sp.playlist_add_items.return_value = None
    sp.playlist_remove_all_occurrences_of_items.return_value = None
    sp.user_playlist_create.return_value = {
        "id": "new_archive_id",
        "name": "My Playlist [Archive]",
    }
    return sp


# =============================================================================
# Create Pair
# =============================================================================


class TestCreatePair:
    """Tests for PlaylistPairService.create_pair."""

    def test_create_pair_success(self, user):
        pair = PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
            production_playlist_name="My Playlist",
            archive_playlist_name="My Playlist [Archive]",
        )
        assert pair.id is not None
        assert pair.production_playlist_id == "prod1"
        assert pair.archive_playlist_id == "arch1"
        assert pair.auto_archive_on_remove is True

    def test_create_pair_duplicate_raises(self, user):
        PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
        )
        with pytest.raises(PlaylistPairExistsError):
            PlaylistPairService.create_pair(
                user_id=user.id,
                production_playlist_id="prod1",
                archive_playlist_id="arch2",
            )

    def test_create_pair_different_playlists_ok(self, user):
        p1 = PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
        )
        p2 = PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod2",
            archive_playlist_id="arch2",
        )
        assert p1.id != p2.id

    def test_create_pair_different_users_same_playlist(
        self, user, user2
    ):
        p1 = PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
        )
        p2 = PlaylistPairService.create_pair(
            user_id=user2.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch_other",
        )
        assert p1.id != p2.id

    def test_create_pair_without_names(self, user):
        pair = PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
        )
        assert pair.production_playlist_name is None
        assert pair.archive_playlist_name is None


# =============================================================================
# Get Pair
# =============================================================================


class TestGetPair:
    """Tests for PlaylistPairService.get_pair_for_playlist."""

    def test_get_pair_found(self, user):
        PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
        )
        pair = PlaylistPairService.get_pair_for_playlist(
            user.id, "prod1"
        )
        assert pair is not None
        assert pair.production_playlist_id == "prod1"

    def test_get_pair_not_found(self, user):
        pair = PlaylistPairService.get_pair_for_playlist(
            user.id, "nonexistent"
        )
        assert pair is None

    def test_get_pair_wrong_user(self, user, user2):
        PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
        )
        pair = PlaylistPairService.get_pair_for_playlist(
            user2.id, "prod1"
        )
        assert pair is None


# =============================================================================
# List Pairs
# =============================================================================


class TestListPairs:
    """Tests for PlaylistPairService.get_pairs_for_user."""

    def test_list_multiple(self, user):
        PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
        )
        PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod2",
            archive_playlist_id="arch2",
        )
        pairs = PlaylistPairService.get_pairs_for_user(user.id)
        assert len(pairs) == 2

    def test_list_empty(self, user):
        pairs = PlaylistPairService.get_pairs_for_user(user.id)
        assert pairs == []

    def test_list_scoped_to_user(self, user, user2):
        PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
        )
        PlaylistPairService.create_pair(
            user_id=user2.id,
            production_playlist_id="prod2",
            archive_playlist_id="arch2",
        )
        pairs = PlaylistPairService.get_pairs_for_user(user.id)
        assert len(pairs) == 1
        assert pairs[0].production_playlist_id == "prod1"


# =============================================================================
# Delete Pair
# =============================================================================


class TestDeletePair:
    """Tests for PlaylistPairService.delete_pair."""

    def test_delete_success(self, user):
        PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
        )
        PlaylistPairService.delete_pair(user.id, "prod1")
        pair = PlaylistPairService.get_pair_for_playlist(
            user.id, "prod1"
        )
        assert pair is None

    def test_delete_nonexistent_raises(self, user):
        with pytest.raises(PlaylistPairNotFoundError):
            PlaylistPairService.delete_pair(
                user.id, "nonexistent"
            )

    def test_delete_wrong_user_raises(self, user, user2):
        PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
        )
        with pytest.raises(PlaylistPairNotFoundError):
            PlaylistPairService.delete_pair(
                user2.id, "prod1"
            )


# =============================================================================
# Archive Tracks
# =============================================================================


class TestArchiveTracks:
    """Tests for PlaylistPairService.archive_tracks."""

    def test_archive_success(self, mock_sp):
        uris = ["spotify:track:a1b2c3d4e5f6g7h8i9j0k1"]
        count = PlaylistPairService.archive_tracks(
            mock_sp, "arch1", uris
        )
        assert count == 1
        mock_sp.playlist_add_items.assert_called_once_with(
            "arch1", uris
        )

    def test_archive_empty_list(self, mock_sp):
        count = PlaylistPairService.archive_tracks(
            mock_sp, "arch1", []
        )
        assert count == 0
        mock_sp.playlist_add_items.assert_not_called()

    def test_archive_large_batch(self, mock_sp):
        uris = [
            f"spotify:track:{'x' * 22}"
            for _ in range(150)
        ]
        count = PlaylistPairService.archive_tracks(
            mock_sp, "arch1", uris
        )
        assert count == 150
        assert mock_sp.playlist_add_items.call_count == 2
        first_call = mock_sp.playlist_add_items.call_args_list[0]
        assert len(first_call[0][1]) == 100
        second_call = mock_sp.playlist_add_items.call_args_list[1]
        assert len(second_call[0][1]) == 50


# =============================================================================
# Unarchive Tracks
# =============================================================================


class TestUnarchiveTracks:
    """Tests for PlaylistPairService.unarchive_tracks."""

    def test_unarchive_success(self, mock_sp):
        uris = ["spotify:track:a1b2c3d4e5f6g7h8i9j0k1"]
        count = PlaylistPairService.unarchive_tracks(
            mock_sp, "prod1", "arch1", uris
        )
        assert count == 1
        mock_sp.playlist_add_items.assert_called_once_with(
            "prod1", uris
        )
        mock_sp.playlist_remove_all_occurrences_of_items \
            .assert_called_once_with("arch1", uris)

    def test_unarchive_empty_list(self, mock_sp):
        count = PlaylistPairService.unarchive_tracks(
            mock_sp, "prod1", "arch1", []
        )
        assert count == 0
        mock_sp.playlist_add_items.assert_not_called()


# =============================================================================
# Create Archive Playlist
# =============================================================================


class TestCreateArchivePlaylist:
    """Tests for PlaylistPairService.create_archive_playlist."""

    def test_create_success(self, mock_sp):
        pid, pname = PlaylistPairService.create_archive_playlist(
            mock_sp, "user123", "My Playlist"
        )
        assert pid == "new_archive_id"
        assert pname == "My Playlist [Archive]"
        mock_sp.user_playlist_create.assert_called_once_with(
            "user123",
            "My Playlist [Archive]",
            public=False,
            description="Archive playlist for removed tracks",
        )

    def test_create_api_error(self, mock_sp):
        mock_sp.user_playlist_create.side_effect = Exception(
            "API error"
        )
        with pytest.raises(Exception, match="API error"):
            PlaylistPairService.create_archive_playlist(
                mock_sp, "user123", "Test"
            )


# =============================================================================
# PlaylistPair.to_dict Serialization
# =============================================================================


class TestPlaylistPairToDict:
    """Tests for PlaylistPair.to_dict()."""

    def test_to_dict_contains_expected_keys(self, user):
        pair = PlaylistPairService.create_pair(
            user_id=user.id,
            production_playlist_id="prod1",
            archive_playlist_id="arch1",
            production_playlist_name="My Playlist",
            archive_playlist_name="My Playlist [Archive]",
        )
        d = pair.to_dict()
        assert d["id"] == pair.id
        assert d["production_playlist_id"] == "prod1"
        assert d["archive_playlist_id"] == "arch1"
        assert d["production_playlist_name"] == "My Playlist"
        assert d["archive_playlist_name"] == "My Playlist [Archive]"
        assert d["auto_archive_on_remove"] is True
        assert "created_at" in d
        assert "updated_at" in d
