"""
Tests for RaidLinkService.

Tests cover CRUD operations for raid playlist links,
raid playlist creation, and to_dict serialization.
"""

import pytest
from unittest.mock import MagicMock

from shuffify.models.db import db, RaidPlaylistLink
from shuffify.services.user_service import UserService
from shuffify.services.raid_link_service import (
    RaidLinkService,
    RaidLinkExistsError,
    RaidLinkNotFoundError,
)


@pytest.fixture
def user(db_app):
    """Provide a test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify({
            "id": "raiduser1",
            "display_name": "Raid Test User",
            "images": [],
        })
        yield result.user


@pytest.fixture
def user2(db_app):
    """Provide a second test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify({
            "id": "raiduser2",
            "display_name": "Raid Test User 2",
            "images": [],
        })
        yield result.user


@pytest.fixture
def mock_api():
    """Mock SpotifyAPI client."""
    api = MagicMock()
    api.create_user_playlist.return_value = {
        "id": "new_raid_playlist_id",
        "name": "My Playlist [Raids]",
    }
    api.update_playlist_details.return_value = None
    return api


# =========================================================
# Create Link
# =========================================================


class TestCreateLink:
    """Tests for RaidLinkService.create_link."""

    def test_create_link_success(self, user):
        link = RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="target1",
            raid_playlist_id="raid1",
            target_playlist_name="My Playlist",
            raid_playlist_name="My Playlist [Raids]",
        )
        assert link.id is not None
        assert link.target_playlist_id == "target1"
        assert link.raid_playlist_id == "raid1"
        assert link.drip_count == 3
        assert link.drip_enabled is False

    def test_create_link_custom_drip(self, user):
        link = RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="target2",
            raid_playlist_id="raid2",
            drip_count=5,
            drip_enabled=True,
        )
        assert link.drip_count == 5
        assert link.drip_enabled is True

    def test_create_link_duplicate_raises(self, user):
        RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="dup1",
            raid_playlist_id="raid_dup1",
        )
        with pytest.raises(RaidLinkExistsError):
            RaidLinkService.create_link(
                user_id=user.id,
                target_playlist_id="dup1",
                raid_playlist_id="raid_dup2",
            )

    def test_create_link_different_users_ok(
        self, user, user2
    ):
        RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="shared1",
            raid_playlist_id="raid_u1",
        )
        link2 = RaidLinkService.create_link(
            user_id=user2.id,
            target_playlist_id="shared1",
            raid_playlist_id="raid_u2",
        )
        assert link2.id is not None


# =========================================================
# Get Link
# =========================================================


class TestGetLink:
    """Tests for RaidLinkService.get_link_for_playlist."""

    def test_get_existing_link(self, user):
        RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="get1",
            raid_playlist_id="raid_get1",
        )
        link = RaidLinkService.get_link_for_playlist(
            user.id, "get1"
        )
        assert link is not None
        assert link.raid_playlist_id == "raid_get1"

    def test_get_nonexistent_returns_none(self, user):
        link = RaidLinkService.get_link_for_playlist(
            user.id, "nonexistent"
        )
        assert link is None

    def test_get_links_for_user(self, user):
        RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="list1",
            raid_playlist_id="raid_l1",
        )
        RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="list2",
            raid_playlist_id="raid_l2",
        )
        links = RaidLinkService.get_links_for_user(
            user.id
        )
        assert len(links) == 2


# =========================================================
# Update Link
# =========================================================


class TestUpdateLink:
    """Tests for RaidLinkService.update_link."""

    def test_update_drip_count(self, user):
        RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="upd1",
            raid_playlist_id="raid_upd1",
        )
        link = RaidLinkService.update_link(
            user.id, "upd1", drip_count=10
        )
        assert link.drip_count == 10

    def test_update_drip_enabled(self, user):
        RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="upd2",
            raid_playlist_id="raid_upd2",
        )
        link = RaidLinkService.update_link(
            user.id, "upd2", drip_enabled=True
        )
        assert link.drip_enabled is True

    def test_update_nonexistent_raises(self, user):
        with pytest.raises(RaidLinkNotFoundError):
            RaidLinkService.update_link(
                user.id, "nonexistent",
                drip_count=5,
            )


# =========================================================
# Delete Link
# =========================================================


class TestDeleteLink:
    """Tests for RaidLinkService.delete_link."""

    def test_delete_link(self, user):
        RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="del1",
            raid_playlist_id="raid_del1",
        )
        RaidLinkService.delete_link(user.id, "del1")
        link = RaidLinkService.get_link_for_playlist(
            user.id, "del1"
        )
        assert link is None

    def test_delete_nonexistent_raises(self, user):
        with pytest.raises(RaidLinkNotFoundError):
            RaidLinkService.delete_link(
                user.id, "nonexistent"
            )


# =========================================================
# Create Raid Playlist
# =========================================================


class TestCreateRaidPlaylist:
    """Tests for RaidLinkService.create_raid_playlist."""

    def test_creates_playlist_with_raids_suffix(
        self, mock_api
    ):
        pid, name = RaidLinkService.create_raid_playlist(
            mock_api, "user123", "My Playlist"
        )
        assert pid == "new_raid_playlist_id"
        assert name == "My Playlist [Raids]"
        mock_api.create_user_playlist.assert_called_once_with(
            "user123",
            "My Playlist [Raids]",
            public=False,
            description=(
                "Raid staging playlist for incoming tracks"
            ),
        )
        mock_api.update_playlist_details.assert_called_once_with(
            "new_raid_playlist_id", public=False
        )


# =========================================================
# to_dict
# =========================================================


class TestToDict:
    """Tests for RaidPlaylistLink.to_dict."""

    def test_to_dict(self, user):
        link = RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="dict1",
            raid_playlist_id="raid_dict1",
            target_playlist_name="Target",
            raid_playlist_name="Target [Raids]",
            drip_count=5,
            drip_enabled=True,
        )
        d = link.to_dict()
        assert d["target_playlist_id"] == "dict1"
        assert d["raid_playlist_id"] == "raid_dict1"
        assert d["drip_count"] == 5
        assert d["drip_enabled"] is True
        assert "created_at" in d
        assert "updated_at" in d
