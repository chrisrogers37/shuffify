"""
Tests for drip_executor.

Tests cover drip execution flow: selecting tracks from raid
playlist, adding to target, removing from raid, and updating
PendingRaidTrack status.
"""

import pytest
from unittest.mock import MagicMock, patch

from shuffify.models.db import (
    db, Schedule, RaidPlaylistLink, PendingRaidTrack,
)
from shuffify.services.user_service import UserService
from shuffify.services.raid_link_service import (
    RaidLinkService,
)
from shuffify.services.pending_raid_service import (
    PendingRaidService,
)
from shuffify.enums import (
    JobType, ScheduleType, IntervalValue,
    PendingRaidStatus,
)
from shuffify.services.executors.drip_executor import (
    execute_drip,
    _select_drip_tracks,
)


@pytest.fixture
def user(db_app):
    """Provide a test user."""
    with db_app.app_context():
        result = UserService.upsert_from_spotify({
            "id": "dripuser1",
            "display_name": "Drip User",
            "images": [],
        })
        yield result.user


@pytest.fixture
def raid_link(user):
    """Create a raid playlist link."""
    return RaidLinkService.create_link(
        user_id=user.id,
        target_playlist_id="target_drip",
        raid_playlist_id="raid_drip",
        target_playlist_name="My Target",
        raid_playlist_name="My Target [Raids]",
        drip_count=3,
        drip_enabled=True,
    )


@pytest.fixture
def schedule(user):
    """Create a drip schedule."""
    sched = Schedule(
        user_id=user.id,
        job_type=JobType.DRIP,
        target_playlist_id="target_drip",
        target_playlist_name="My Target",
        schedule_type=ScheduleType.INTERVAL,
        schedule_value=IntervalValue.DAILY,
        algorithm_params={"drip_count": 3},
        is_enabled=True,
    )
    db.session.add(sched)
    db.session.commit()
    return sched


@pytest.fixture
def mock_api():
    """Mock SpotifyAPI."""
    api = MagicMock()
    return api


class TestSelectDripTracks:
    """Tests for _select_drip_tracks."""

    def test_selects_up_to_count(self):
        uris = [f"uri:{i}" for i in range(10)]
        result = _select_drip_tracks(uris, 3)
        assert len(result) == 3
        for uri in result:
            assert uri in uris

    def test_returns_all_when_fewer(self):
        uris = ["uri:1", "uri:2"]
        result = _select_drip_tracks(uris, 5)
        assert len(result) == 2


class TestExecuteDrip:
    """Tests for execute_drip."""

    def test_drip_disabled_skips(
        self, user, schedule, mock_api
    ):
        """When drip_enabled=False, skip with reason."""
        RaidLinkService.create_link(
            user_id=user.id,
            target_playlist_id="target_drip",
            raid_playlist_id="raid_drip",
            drip_enabled=False,
        )
        mock_api.get_playlist_tracks.return_value = [
            {"uri": "uri:1"}
        ]

        result = execute_drip(schedule, mock_api)
        assert result["tracks_added"] == 0
        assert result.get("skipped_reason") == (
            "drip_disabled"
        )

    def test_empty_raid_playlist(
        self, user, raid_link, schedule, mock_api
    ):
        """When raid playlist is empty, nothing to drip."""
        mock_api.get_playlist_tracks.return_value = []

        result = execute_drip(schedule, mock_api)
        assert result["tracks_added"] == 0

    @patch(
        "shuffify.services.executors.drip_executor"
        "._auto_snapshot_before_drip"
    )
    def test_drip_moves_tracks(
        self, mock_snap,
        user, raid_link, schedule, mock_api,
    ):
        """Drip moves tracks from raid to target."""
        raid_tracks = [
            {"uri": f"spotify:track:{i}"}
            for i in range(5)
        ]
        target_tracks = [
            {"uri": "spotify:track:existing"}
        ]

        call_count = [0]

        def get_tracks_side_effect(pid):
            if pid == "raid_drip":
                return raid_tracks
            return target_tracks

        mock_api.get_playlist_tracks.side_effect = (
            get_tracks_side_effect
        )
        mock_api.playlist_add_items.return_value = None
        mock_api.playlist_remove_items.return_value = (
            True
        )

        result = execute_drip(schedule, mock_api)

        assert result["tracks_added"] == 3
        # Verify add was called with position=0
        add_call = (
            mock_api.playlist_add_items.call_args
        )
        assert add_call[0][0] == "target_drip"
        assert add_call[1].get("position") == 0
        # Verify remove from raid
        mock_api.playlist_remove_items.assert_called()

    def test_no_link_raises(self, user, mock_api):
        """When no link exists, raises error."""
        from shuffify.services.executors.base_executor import (  # noqa: E501
            JobExecutionError,
        )

        sched = Schedule(
            user_id=user.id,
            job_type=JobType.DRIP,
            target_playlist_id="no_link_target",
            schedule_type=ScheduleType.INTERVAL,
            schedule_value=IntervalValue.DAILY,
            is_enabled=True,
        )
        db.session.add(sched)
        db.session.commit()

        with pytest.raises(JobExecutionError):
            execute_drip(sched, mock_api)
