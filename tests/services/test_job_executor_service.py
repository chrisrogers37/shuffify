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
    api.token_info = Mock()
    api.token_info.refresh_token = "original_refresh"
    return api


class TestExecuteRaid:
    """Tests for the raid execution logic."""

    def test_raid_adds_new_tracks(
        self, mock_schedule, mock_api
    ):
        """Should add tracks from source not in target."""
        mock_schedule.job_type = "raid"

        # Target has tracks 1-5
        mock_api.get_playlist_tracks.side_effect = [
            # First call: target tracks
            [
                {
                    "id": f"track{i}",
                    "uri": f"spotify:track:track{i}",
                }
                for i in range(1, 6)
            ],
            # Second call: source tracks (some new)
            [
                {
                    "id": f"track{i}",
                    "uri": f"spotify:track:track{i}",
                }
                for i in range(4, 9)
            ],
        ]

        result = execute_raid(
            mock_schedule, mock_api
        )

        # Tracks 6, 7, 8 are new (4, 5 are duplicates)
        assert result["tracks_added"] == 3
        assert result["tracks_total"] == 8

    def test_raid_no_new_tracks(
        self, mock_schedule, mock_api
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
        mock_api.get_playlist_tracks.side_effect = [
            same_tracks,
            same_tracks,
        ]

        result = execute_raid(
            mock_schedule, mock_api
        )
        assert result["tracks_added"] == 0

    def test_raid_no_sources_skips(
        self, mock_schedule, mock_api
    ):
        """Should skip when no source playlists."""
        mock_schedule.source_playlist_ids = []
        result = execute_raid(
            mock_schedule, mock_api
        )
        assert result["tracks_added"] == 0


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
