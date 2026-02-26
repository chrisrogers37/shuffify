"""
Tests for RaidSyncService.

Tests cover watch/unwatch operations, raid status retrieval,
schedule management, and the _find_raid_schedule helper.
"""

import pytest
from unittest.mock import patch, MagicMock

from shuffify.models.db import db, Schedule
from shuffify.services.user_service import UserService
from shuffify.services.upstream_source_service import (
    UpstreamSourceService,
    UpstreamSourceNotFoundError,
)
from shuffify.services.scheduler_service import (
    SchedulerService,
    ScheduleLimitError,
)
from shuffify.services.raid_sync_service import (
    RaidSyncService,
    RaidSyncError,
)
from shuffify.enums import JobType


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


# =============================================================================
# Watch Playlist
# =============================================================================


class TestWatchPlaylist:
    """Tests for RaidSyncService.watch_playlist."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_creates_source_and_schedule(
        self, mock_add_job, user
    ):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
            source_playlist_name="Source One",
        )
        assert result["source"] is not None
        assert result["schedule"] is not None
        assert result["source"]["source_playlist_id"] == "source1"

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_no_schedule(self, mock_add_job, user):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
            auto_schedule=False,
        )
        assert result["source"] is not None
        assert result["schedule"] is None

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_second_source_appends(
        self, mock_add_job, user
    ):
        RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
        )
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source2",
        )
        schedule = result["schedule"]
        assert "source1" in schedule["source_playlist_ids"]
        assert "source2" in schedule["source_playlist_ids"]

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_same_source_idempotent(
        self, mock_add_job, user
    ):
        RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
        )
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
        )
        schedule = result["schedule"]
        ids = schedule["source_playlist_ids"]
        assert ids.count("source1") == 1

    def test_watch_invalid_user_raises(self, db_app):
        with db_app.app_context():
            with pytest.raises(RaidSyncError, match="User not found"):
                RaidSyncService.watch_playlist(
                    spotify_id="nonexistent",
                    target_playlist_id="t1",
                    target_playlist_name="T",
                    source_playlist_id="s1",
                )

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_custom_schedule_value(
        self, mock_add_job, user
    ):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
            schedule_value="weekly",
        )
        assert (
            result["schedule"]["schedule_value"] == "weekly"
        )

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_watch_returns_expected_keys(
        self, mock_add_job, user
    ):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="My Playlist",
            source_playlist_id="source1",
        )
        assert "source" in result
        assert "schedule" in result


# =============================================================================
# Unwatch Playlist
# =============================================================================


class TestUnwatchPlaylist:
    """Tests for RaidSyncService.unwatch_playlist."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_unwatch_removes_source(
        self, mock_add_job, user
    ):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="T",
            source_playlist_id="source1",
        )
        source_id = result["source"]["id"]

        RaidSyncService.unwatch_playlist(
            spotify_id="user123",
            source_id=source_id,
            target_playlist_id="target1",
        )

        sources = UpstreamSourceService.list_sources(
            "user123", "target1"
        )
        assert len(sources) == 0

    @patch("shuffify.scheduler.add_job_for_schedule")
    @patch("shuffify.scheduler.remove_job_for_schedule")
    def test_unwatch_last_source_deletes_schedule(
        self, mock_remove, mock_add, user
    ):
        result = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="T",
            source_playlist_id="source1",
        )
        source_id = result["source"]["id"]

        RaidSyncService.unwatch_playlist(
            spotify_id="user123",
            source_id=source_id,
            target_playlist_id="target1",
        )

        schedule = RaidSyncService._find_raid_schedule(
            user.id, "target1"
        )
        assert schedule is None

    def test_unwatch_nonexistent_raises(self, user):
        with pytest.raises(UpstreamSourceNotFoundError):
            RaidSyncService.unwatch_playlist(
                spotify_id="user123",
                source_id=9999,
                target_playlist_id="target1",
            )

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_unwatch_updates_remaining(
        self, mock_add_job, user
    ):
        r1 = RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="T",
            source_playlist_id="source1",
        )
        RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="T",
            source_playlist_id="source2",
        )

        RaidSyncService.unwatch_playlist(
            spotify_id="user123",
            source_id=r1["source"]["id"],
            target_playlist_id="target1",
        )

        schedule = RaidSyncService._find_raid_schedule(
            user.id, "target1"
        )
        assert schedule is not None
        assert "source2" in schedule.source_playlist_ids
        assert "source1" not in schedule.source_playlist_ids


# =============================================================================
# Get Raid Status
# =============================================================================


class TestGetRaidStatus:
    """Tests for RaidSyncService.get_raid_status."""

    @patch("shuffify.scheduler.add_job_for_schedule")
    def test_status_with_sources_and_schedule(
        self, mock_add_job, user
    ):
        RaidSyncService.watch_playlist(
            spotify_id="user123",
            target_playlist_id="target1",
            target_playlist_name="T",
            source_playlist_id="source1",
        )

        status = RaidSyncService.get_raid_status(
            "user123", "target1"
        )
        assert status["source_count"] == 1
        assert status["has_schedule"] is True
        assert status["is_schedule_enabled"] is True
        assert len(status["sources"]) == 1

    def test_status_empty(self, user):
        status = RaidSyncService.get_raid_status(
            "user123", "target1"
        )
        assert status["source_count"] == 0
        assert status["has_schedule"] is False
        assert status["sources"] == []

    def test_status_unknown_user(self, db_app):
        with db_app.app_context():
            status = RaidSyncService.get_raid_status(
                "nonexistent", "target1"
            )
            assert status["source_count"] == 0
            assert status["has_schedule"] is False


# =============================================================================
# Raid Now
# =============================================================================


class TestRaidNow:
    """Tests for RaidSyncService.raid_now."""

    def test_raid_now_no_sources_raises(self, user):
        with pytest.raises(
            RaidSyncError,
            match="No sources configured"
        ):
            RaidSyncService.raid_now(
                "user123", "target1"
            )


# =============================================================================
# _find_raid_schedule
# =============================================================================


class TestFindRaidSchedule:
    """Tests for RaidSyncService._find_raid_schedule."""

    def test_finds_raid_schedule(self, user):
        SchedulerService.create_schedule(
            user_id=user.id,
            job_type=JobType.RAID,
            target_playlist_id="target1",
            target_playlist_name="T",
            schedule_type="interval",
            schedule_value="daily",
            source_playlist_ids=["s1"],
        )
        result = RaidSyncService._find_raid_schedule(
            user.id, "target1"
        )
        assert result is not None
        assert result.job_type == JobType.RAID

    def test_returns_none_when_no_schedule(self, user):
        result = RaidSyncService._find_raid_schedule(
            user.id, "nonexistent"
        )
        assert result is None
