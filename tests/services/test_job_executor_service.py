"""
Tests for JobExecutorService.

Tests the job execution logic with mocked Spotify API calls.
"""

import pytest
from unittest.mock import patch, Mock

from shuffify.services.executors import (
    JobExecutorService,
    JobExecutionError,
)
from shuffify.services.executors.raid_executor import (
    execute_raid,
)
from shuffify.services.executors.shuffle_executor import (
    execute_shuffle,
)


# Executor paths touch TrackLockService (db.session) even in
# their except-branch fallbacks, so every test in this module
# needs the shared app_ctx fixture (defined in tests/conftest.py).
pytestmark = pytest.mark.usefixtures("app_ctx")


@pytest.fixture
def mock_schedule():
    """Create a mock Schedule model instance."""
    schedule = Mock()
    schedule.id = 1
    schedule.user_id = 1
    schedule.job_type = "shuffle"
    schedule.target_playlist_id = "target_pl"
    schedule.target_playlist_name = "My Playlist"
    schedule.source_playlist_ids = ["source_1"]
    schedule.algorithm_name = "BasicShuffle"
    schedule.algorithm_params = {"keep_first": 0}
    schedule.is_enabled = True
    schedule.last_run_at = None
    schedule.last_status = None
    schedule.last_error = None
    return schedule


@pytest.fixture
def mock_user():
    """Create a mock User model instance."""
    user = Mock()
    user.id = 1
    user.spotify_id = "test_user"
    user.encrypted_refresh_token = "encrypted_token_data"
    return user


@pytest.fixture
def mock_api():
    """Create a mock SpotifyAPI instance."""
    api = Mock()
    api.get_playlist_tracks.return_value = [
        {
            "id": f"track{i}",
            "name": f"Track {i}",
            "uri": f"spotify:track:track{i}",
            "artists": [{"name": f"Artist {i}"}],
            "album": {"name": f"Album {i}"},
        }
        for i in range(1, 6)
    ]
    api.update_playlist_tracks.return_value = True
    api.playlist_add_items.return_value = None
    api.get_tracks.return_value = []
    api.token_info = Mock()
    api.token_info.refresh_token = "original_refresh"
    return api


class TestExecuteRaid:
    """Tests for the raid execution logic."""

    @patch(
        "shuffify.services.executors.raid_executor"
        "._add_to_raid_playlist"
    )
    @patch(
        "shuffify.services.executors.raid_executor"
        ".PendingRaidService"
    )
    def test_raid_stages_new_tracks(
        self, mock_pending, mock_add_raid,
        mock_schedule, mock_api,
    ):
        """Should stage tracks from source not in target."""
        mock_schedule.job_type = "raid"
        mock_pending.stage_tracks.return_value = 3

        target_tracks = [
            {
                "id": f"track{i}",
                "uri": f"spotify:track:track{i}",
            }
            for i in range(1, 6)
        ]
        source_tracks = [
            {
                "id": f"track{i}",
                "uri": f"spotify:track:track{i}",
            }
            for i in range(4, 9)
        ]

        def get_tracks(pid):
            if pid == "target_pl":
                return target_tracks
            return source_tracks

        mock_api.get_playlist_tracks.side_effect = (
            get_tracks
        )

        result = execute_raid(
            mock_schedule, mock_api
        )

        # Tracks 6, 7, 8 are new (staged, not added)
        assert result["tracks_added"] == 3
        mock_pending.stage_tracks.assert_called_once()

    @patch(
        "shuffify.services.executors.raid_executor"
        ".PendingRaidService"
    )
    def test_raid_no_new_tracks(
        self, mock_pending, mock_schedule, mock_api
    ):
        """Should report 0 additions when all duplicates."""
        mock_schedule.job_type = "raid"

        same_tracks = [
            {
                "id": f"track{i}",
                "uri": f"spotify:track:track{i}",
            }
            for i in range(1, 4)
        ]
        mock_api.get_playlist_tracks.return_value = (
            same_tracks
        )

        result = execute_raid(
            mock_schedule, mock_api
        )
        assert result["tracks_added"] == 0
        mock_pending.stage_tracks.assert_not_called()

    def test_raid_no_sources_skips(
        self, mock_schedule, mock_api
    ):
        """Should skip when no source playlists."""
        mock_schedule.source_playlist_ids = []
        result = execute_raid(
            mock_schedule, mock_api
        )
        assert result["tracks_added"] == 0


class TestRaidSourceFailureLogging:
    """Issue #316: ResolveResult.error_message must surface
    as a structured log line and an ActivityLog entry instead
    of being silently discarded by the raid executor."""

    @patch(
        "shuffify.services.executors.raid_executor"
        "._log_failed_source_activity"
    )
    @patch(
        "shuffify.services.executors.raid_executor"
        ".SourceResolver"
    )
    @patch(
        "shuffify.services.executors.raid_executor"
        ".PendingRaidService"
    )
    def test_failed_resolve_emits_structured_warning(
        self,
        mock_pending,
        mock_resolver_cls,
        mock_activity_helper,
        mock_schedule,
        mock_api,
        caplog,
    ):
        """A source whose ResolveResult has success=False must
        produce a logger.warning carrying source_id /
        source_playlist_id / source_name / pathway / partial /
        error_message."""
        import logging
        from shuffify.services.source_resolver.base import (
            ResolveAllResult,
            ResolveResult,
        )

        mock_schedule.job_type = "raid"
        mock_schedule.source_playlist_ids = ["src_bad"]
        mock_pending.stage_tracks.return_value = 0

        target_tracks = [
            {"id": "t1", "uri": "spotify:track:t1"}
        ]
        mock_api.get_playlist_tracks.return_value = (
            target_tracks
        )

        failing_source = Mock(
            id=42,
            source_playlist_id="src_bad",
            source_name="Bad Source Playlist",
            source_type="external",
            raid_count=5,
        )
        failed = ResolveResult(
            track_uris=[],
            pathway_name="public_scraper",
            success=False,
            partial=False,
            error_message="HTTP 429 from scraper",
        )
        mock_resolver_cls.return_value.resolve_all.return_value = (  # noqa: E501
            ResolveAllResult(
                new_uris=[],
                source_results=[(failing_source, failed)],
            )
        )

        with caplog.at_level(
            logging.WARNING,
            logger=(
                "shuffify.services.executors.raid_executor"
            ),
        ):
            execute_raid(mock_schedule, mock_api)

        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "Raid source resolution failed"
            in r.getMessage()
        ]
        assert warning_records, (
            "Expected a structured warning for the failed "
            "source resolve; got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        msg = warning_records[0].getMessage()
        assert "src_bad" in msg
        assert "Bad Source Playlist" in msg
        assert "public_scraper" in msg
        assert "HTTP 429 from scraper" in msg

        # Activity-log helper should have fired exactly once
        # with this source + result.
        mock_activity_helper.assert_called_once()
        kwargs = mock_activity_helper.call_args.kwargs
        assert kwargs["user_id"] == mock_schedule.user_id
        assert kwargs["source"] is failing_source
        assert kwargs["result"] is failed

    @patch(
        "shuffify.services.executors.raid_executor"
        "._log_failed_source_activity"
    )
    @patch(
        "shuffify.services.executors.raid_executor"
        ".SourceResolver"
    )
    @patch(
        "shuffify.services.executors.raid_executor"
        ".PendingRaidService"
    )
    def test_successful_resolve_does_not_warn(
        self,
        mock_pending,
        mock_resolver_cls,
        mock_activity_helper,
        mock_schedule,
        mock_api,
        caplog,
    ):
        """Happy path: a successful resolve must NOT emit the
        failure warning or activity entry."""
        import logging
        from shuffify.services.source_resolver.base import (
            ResolveAllResult,
            ResolveResult,
        )

        mock_schedule.job_type = "raid"
        mock_schedule.source_playlist_ids = ["src_ok"]
        mock_pending.stage_tracks.return_value = 1

        target_tracks = [
            {"id": "t1", "uri": "spotify:track:t1"}
        ]

        def get_tracks(pid):
            if pid == "target_pl":
                return target_tracks
            return []

        mock_api.get_playlist_tracks.side_effect = get_tracks

        ok_source = Mock(
            id=99,
            source_playlist_id="src_ok",
            source_name="Good Source",
            source_type="external",
            raid_count=5,
        )
        ok = ResolveResult(
            track_uris=["spotify:track:new1"],
            pathway_name="direct_api",
            success=True,
        )
        mock_resolver_cls.return_value.resolve_all.return_value = (  # noqa: E501
            ResolveAllResult(
                new_uris=["spotify:track:new1"],
                source_results=[(ok_source, ok)],
            )
        )

        with caplog.at_level(
            logging.WARNING,
            logger=(
                "shuffify.services.executors.raid_executor"
            ),
        ):
            execute_raid(mock_schedule, mock_api)

        assert not any(
            "Raid source resolution failed" in r.getMessage()
            for r in caplog.records
        ), "Happy path must not produce failure log spam"
        mock_activity_helper.assert_not_called()


class TestExecuteShuffle:
    """Tests for the shuffle execution logic."""

    def test_shuffle_reorders_playlist(
        self, mock_schedule, mock_api
    ):
        """Should call update_playlist_tracks."""
        result = execute_shuffle(
            mock_schedule, mock_api
        )

        assert result["tracks_total"] == 5
        mock_api.update_playlist_tracks.assert_called_once()
        call_args = (
            mock_api.update_playlist_tracks.call_args
        )
        assert call_args[0][0] == "target_pl"
        assert len(call_args[0][1]) == 5

    def test_shuffle_no_algorithm_raises(
        self, mock_schedule, mock_api
    ):
        """Should raise when algorithm_name is not set."""
        mock_schedule.algorithm_name = None

        with pytest.raises(
            JobExecutionError,
            match="no algorithm configured",
        ):
            execute_shuffle(
                mock_schedule, mock_api
            )

    def test_shuffle_empty_playlist_returns_zero(
        self, mock_schedule, mock_api
    ):
        """Should handle empty playlists gracefully."""
        mock_api.get_playlist_tracks.return_value = []
        result = execute_shuffle(
            mock_schedule, mock_api
        )
        assert result["tracks_total"] == 0


class TestGetSpotifyApi:
    """Tests for _get_spotify_api token management."""

    @patch(
        "shuffify.services.executors.base_executor"
        ".TokenService"
    )
    @patch(
        "shuffify.services.executors.base_executor"
        ".SpotifyAPI"
    )
    @patch(
        "shuffify.services.executors.base_executor"
        ".SpotifyAuthManager"
    )
    @patch(
        "shuffify.services.executors.base_executor"
        ".SpotifyCredentials"
    )
    def test_creates_api_with_decrypted_token(
        self,
        mock_creds,
        mock_auth,
        mock_api_class,
        mock_token_svc,
        mock_user,
        app_context,
    ):
        """Should decrypt token and create SpotifyAPI."""
        mock_token_svc.decrypt_token.return_value = (
            "decrypted_refresh"
        )
        mock_api_instance = Mock()
        mock_api_instance.token_info.refresh_token = (
            "decrypted_refresh"
        )
        mock_api_class.return_value = mock_api_instance

        result = JobExecutorService._get_spotify_api(
            mock_user
        )
        mock_token_svc.decrypt_token.assert_called_once_with(
            "encrypted_token_data"
        )
        assert result == mock_api_instance

    def test_no_refresh_token_raises(self, mock_user):
        """Should raise when user has no stored token."""
        mock_user.encrypted_refresh_token = None

        with pytest.raises(
            JobExecutionError,
            match="no stored refresh token",
        ):
            JobExecutorService._get_spotify_api(mock_user)


class TestExecuteLocking:
    """JobExecutorService.execute must skip cleanly when the playlist
    lock times out so two schedules racing on the same target don't
    interleave reads and writes."""

    @staticmethod
    def _fake_lock(acquired: bool):
        """Build a context manager that yields the given bool."""
        from contextlib import contextmanager

        @contextmanager
        def _ctx(_pid, **_kw):
            yield acquired

        return _ctx

    def test_skips_work_when_lock_not_acquired(self, mock_schedule):
        """Lock returns False → no execution record, no API calls."""
        with patch(
            "shuffify.services.executors.base_executor.db"
        ) as fake_db, patch(
            "shuffify.services.executors.base_executor.playlist_lock",
            self._fake_lock(False),
        ), patch.object(
            JobExecutorService, "_create_execution_record",
        ) as create_record, patch.object(
            JobExecutorService, "_get_spotify_api"
        ) as get_api:
            fake_db.session.get.return_value = mock_schedule

            JobExecutorService.execute(mock_schedule.id)

            create_record.assert_not_called()
            get_api.assert_not_called()

    def test_proceeds_when_lock_acquired(self, mock_schedule, mock_user):
        """Lock returns True → executor proceeds through the
        full happy path."""
        with patch(
            "shuffify.services.executors.base_executor.db"
        ) as fake_db, patch(
            "shuffify.services.executors.base_executor.playlist_lock",
            self._fake_lock(True),
        ), patch.object(
            JobExecutorService, "_create_execution_record",
        ) as create_record, patch.object(
            JobExecutorService, "_get_spotify_api"
        ) as get_api, patch.object(
            JobExecutorService, "_execute_job_type",
            return_value={"tracks_added": 0, "tracks_total": 10},
        ), patch.object(
            JobExecutorService, "_record_success"
        ) as record_success:
            fake_db.session.get.side_effect = [mock_schedule, mock_user]
            create_record.return_value = Mock()

            JobExecutorService.execute(mock_schedule.id)

            create_record.assert_called_once_with(mock_schedule.id)
            get_api.assert_called_once_with(mock_user)
            record_success.assert_called_once()
