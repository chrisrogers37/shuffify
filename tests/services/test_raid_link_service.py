"""
Tests for RaidLinkService.

Tests cover CRUD operations for raid playlist links
and to_dict serialization.
"""

from unittest.mock import patch

import pytest

from shuffify.models.db import db
from shuffify.services.raid_link_service import (
    RaidLinkExistsError,
    RaidLinkNotFoundError,
    RaidLinkService,
)
from shuffify.services.user_service import UserService


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


class TestStagedWrites:
    """commit=False flushes, lets errors propagate and leaves the transaction to the caller."""

    def test_staged_create_and_update_flush_without_committing(self, user):
        with patch("shuffify.services.base.safe_commit") as commit:
            link = RaidLinkService.create_link(user.id, "t1", "r1", commit=False)
            RaidLinkService.update_link(user.id, "t1", raid_playlist_id="r2", drip_enabled=True, commit=False)
        commit.assert_not_called()
        assert link.id is not None
        staged = RaidLinkService.get_link_for_playlist(user.id, "t1")
        assert (staged.raid_playlist_id, staged.drip_enabled, staged.drip_count) == ("r2", True, 3)
        db.session.rollback()
        assert RaidLinkService.get_link_for_playlist(user.id, "t1") is None

    def test_a_staged_delete_flushes_before_a_relink_and_errors_do_not_roll_back(self, user):
        RaidLinkService.create_link(user.id, "t1", "r1")
        with patch("shuffify.services.base.safe_commit") as commit:
            RaidLinkService.delete_link(user.id, "t1", commit=False)
            assert RaidLinkService.get_link_for_playlist(user.id, "t1") is None
            RaidLinkService.create_link(user.id, "t1", "r2", commit=False)
            with pytest.raises(RaidLinkExistsError):
                RaidLinkService.create_link(user.id, "t1", "r3", commit=False)
            with pytest.raises(RaidLinkNotFoundError):
                RaidLinkService.update_link(user.id, "t2", drip_count=4, commit=False)
        commit.assert_not_called()
        assert RaidLinkService.get_link_for_playlist(user.id, "t1").raid_playlist_id == "r2"
        db.session.rollback()
        assert RaidLinkService.get_link_for_playlist(user.id, "t1").raid_playlist_id == "r1"
