"""
Tests for execute_rotate().

Tests cover all three rotation modes (archive_oldest, refresh,
swap), parameter validation, edge cases, and dispatch.
"""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from shuffify.services.executors import (
    JobExecutorService,
    JobExecutionError,
)
from shuffify.services.executors.rotate_executor import (
    execute_rotate,
)
from shuffify.enums import JobType, RotationMode


def _make_schedule(
    rotation_mode="archive_oldest",
    rotation_count=5,
    target_id="target1",
    user_id=1,
):
    """Create a mock schedule for rotation tests."""
    schedule = MagicMock()
    schedule.id = 42
    schedule.user_id = user_id
    schedule.job_type = JobType.ROTATE
    schedule.target_playlist_id = target_id
    schedule.target_playlist_name = "My Playlist"
    schedule.algorithm_params = {
        "rotation_mode": rotation_mode,
        "rotation_count": rotation_count,
    }
    return schedule


def _make_tracks(uris):
    """Create track dicts from a list of URI strings."""
    return [{"uri": u, "name": u} for u in uris]


def _make_api(prod_tracks=None, archive_tracks=None):
    """Create a mock SpotifyAPI."""
    api = MagicMock()
    api._sp = MagicMock()
    api._ensure_valid_token = MagicMock()

    def get_tracks(playlist_id):
        if playlist_id == "target1":
            return prod_tracks or []
        if playlist_id == "archive1":
            return archive_tracks or []
        return []

    api.get_playlist_tracks = MagicMock(
        side_effect=get_tracks
    )
    api.playlist_remove_items = MagicMock(
        return_value=True
    )
    return api


def _make_pair():
    """Create a mock PlaylistPair."""
    pair = MagicMock()
    pair.archive_playlist_id = "archive1"
    return pair


# =============================================================================
# ARCHIVE OLDEST
# =============================================================================


class TestExecuteRotateArchiveOldest:
    """Tests for archive_oldest rotation mode."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_archives_oldest_tracks(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(
            ["u1", "u2", "u3", "u4", "u5", "u6"]
        )
        api = _make_api(prod_tracks=prod)
        schedule = _make_schedule(
            rotation_count=3
        )

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_added"] == 0
        assert result["tracks_total"] == 3
        api._sp.playlist_add_items.assert_called_once_with(
            "archive1", ["u1", "u2", "u3"]
        )
        api.playlist_remove_items.assert_called_once_with(
            "target1", ["u1", "u2", "u3"]
        )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_clamps_count_to_playlist_size(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["u1", "u2"])
        api = _make_api(prod_tracks=prod)
        schedule = _make_schedule(
            rotation_count=10
        )

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_total"] == 0
        api.playlist_remove_items.assert_called_once_with(
            "target1", ["u1", "u2"]
        )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_empty_playlist_returns_zero(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        api = _make_api(prod_tracks=[])
        schedule = _make_schedule()

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_added"] == 0
        assert result["tracks_total"] == 0

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_auto_snapshot_created(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            True
        )

        prod = _make_tracks(["u1", "u2", "u3"])
        api = _make_api(prod_tracks=prod)
        schedule = _make_schedule(
            rotation_count=1
        )

        execute_rotate(
            schedule, api
        )

        mock_snap.create_snapshot.assert_called_once()

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_snapshot_skipped_when_disabled(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["u1", "u2", "u3"])
        api = _make_api(prod_tracks=prod)
        schedule = _make_schedule(
            rotation_count=1
        )

        execute_rotate(
            schedule, api
        )

        mock_snap.create_snapshot.assert_not_called()

    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_no_pair_raises(self, mock_pair):
        mock_pair.return_value = None

        api = _make_api()
        schedule = _make_schedule()

        with pytest.raises(
            JobExecutionError,
            match="No archive pair found",
        ):
            execute_rotate(
                schedule, api
            )


# =============================================================================
# REFRESH
# =============================================================================


class TestExecuteRotateRefresh:
    """Tests for refresh rotation mode."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_refresh_replaces_oldest(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(
            ["p1", "p2", "p3", "p4"]
        )
        archive = _make_tracks(
            ["a1", "a2", "a3"]
        )
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        schedule = _make_schedule(
            rotation_mode="refresh",
            rotation_count=2,
        )

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_added"] == 2
        assert result["tracks_total"] == 4
        api.playlist_remove_items.assert_called_once_with(
            "target1", ["p1", "p2"]
        )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_refresh_deduplicates(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "shared"])
        archive = _make_tracks(["shared", "a1"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        schedule = _make_schedule(
            rotation_mode="refresh",
            rotation_count=2,
        )

        result = execute_rotate(
            schedule, api
        )

        # Only a1 is available (shared is in prod)
        assert result["tracks_added"] == 1

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_refresh_empty_archive(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=[],
        )
        schedule = _make_schedule(
            rotation_mode="refresh",
            rotation_count=2,
        )

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_added"] == 0
        api.playlist_remove_items.assert_not_called()

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_refresh_partial(
        self, mock_pair, mock_snap
    ):
        """Archive has fewer unique tracks than requested."""
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        archive = _make_tracks(["a1"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        schedule = _make_schedule(
            rotation_mode="refresh",
            rotation_count=5,
        )

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_added"] == 1
        assert result["tracks_total"] == 3


# =============================================================================
# SWAP
# =============================================================================


class TestExecuteRotateSwap:
    """Tests for swap rotation mode."""

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_swap_exchanges_tracks(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2", "p3"])
        archive = _make_tracks(["a1", "a2", "a3"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        schedule = _make_schedule(
            rotation_mode="swap",
            rotation_count=2,
        )

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_added"] == 2
        assert result["tracks_total"] == 3

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_swap_deduplicates(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(
            ["p1", "p2", "shared"]
        )
        archive = _make_tracks(["shared", "a1"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=archive,
        )
        schedule = _make_schedule(
            rotation_mode="swap",
            rotation_count=3,
        )

        result = execute_rotate(
            schedule, api
        )

        # Only a1 is available; swap limited to 1
        assert result["tracks_added"] == 1

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_swap_empty_archive(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["p1", "p2"])
        api = _make_api(
            prod_tracks=prod,
            archive_tracks=[],
        )
        schedule = _make_schedule(
            rotation_mode="swap",
            rotation_count=2,
        )

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_added"] == 0


# =============================================================================
# VALIDATION AND DISPATCH
# =============================================================================


class TestExecuteRotateValidation:
    """Tests for validation and dispatch."""

    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_invalid_mode_raises(self, mock_pair):
        mock_pair.return_value = _make_pair()

        api = _make_api(
            prod_tracks=_make_tracks(["u1"])
        )
        schedule = _make_schedule()
        schedule.algorithm_params = {
            "rotation_mode": "invalid_mode",
        }

        with pytest.raises(
            JobExecutionError,
            match="Invalid rotation_mode",
        ):
            execute_rotate(
                schedule, api
            )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_missing_mode_defaults(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["u1", "u2"])
        api = _make_api(prod_tracks=prod)
        schedule = _make_schedule()
        schedule.algorithm_params = {}

        result = execute_rotate(
            schedule, api
        )

        # Defaults to archive_oldest with count=5
        # but only 2 tracks, so all archived
        assert result["tracks_total"] == 0

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_count_defaults_to_5(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        uris = [
            "u{}".format(i) for i in range(10)
        ]
        prod = _make_tracks(uris)
        api = _make_api(prod_tracks=prod)
        schedule = _make_schedule()
        schedule.algorithm_params = {
            "rotation_mode": "archive_oldest",
        }

        result = execute_rotate(
            schedule, api
        )

        assert result["tracks_total"] == 5

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_count_zero_clamped_to_1(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            False
        )

        prod = _make_tracks(["u1", "u2", "u3"])
        api = _make_api(prod_tracks=prod)
        schedule = _make_schedule(
            rotation_count=0
        )

        result = execute_rotate(
            schedule, api
        )

        # Clamped to 1, so 1 track archived
        assert result["tracks_total"] == 2

    def test_dispatch_routes_to_rotate(self):
        """_execute_job_type dispatches ROTATE."""
        schedule = _make_schedule()
        api = MagicMock()

        with patch(
            "shuffify.services.executors.rotate_executor"
            ".execute_rotate",
            return_value={"tracks_added": 0},
        ) as mock_rotate:
            JobExecutorService._execute_job_type(
                schedule, api
            )
            mock_rotate.assert_called_once_with(
                schedule, api
            )

    def test_unknown_job_type_raises(self):
        """Unknown job type still raises error."""
        schedule = MagicMock()
        schedule.job_type = "nonexistent"
        api = MagicMock()

        with pytest.raises(
            JobExecutionError,
            match="Unknown job type",
        ):
            JobExecutorService._execute_job_type(
                schedule, api
            )

    @patch(
        "shuffify.services.executors.rotate_executor"
        ".PlaylistSnapshotService"
    )
    @patch(
        "shuffify.services.playlist_pair_service"
        ".PlaylistPairService.get_pair_for_playlist"
    )
    def test_snapshot_failure_non_blocking(
        self, mock_pair, mock_snap
    ):
        mock_pair.return_value = _make_pair()
        mock_snap.is_auto_snapshot_enabled.return_value = (
            True
        )
        mock_snap.create_snapshot.side_effect = (
            Exception("snap fail")
        )

        prod = _make_tracks(["u1", "u2"])
        api = _make_api(prod_tracks=prod)
        schedule = _make_schedule(
            rotation_count=1
        )

        # Should not raise despite snapshot failure
        result = execute_rotate(
            schedule, api
        )
        assert result["tracks_total"] == 1
