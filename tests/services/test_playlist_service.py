"""
Tests for PlaylistService.

Tests cover playlist retrieval, updates, and validation.
"""

import pytest
from unittest.mock import Mock, patch

from shuffify.services import (
    PlaylistService,
    PlaylistError,
    PlaylistNotFoundError,
    PlaylistUpdateError,
)
from shuffify.models.playlist import Playlist


class TestPlaylistServiceInit:
    """Tests for PlaylistService initialization."""

    def test_init_with_client(self, mock_spotify_client):
        """Should initialize with a SpotifyClient."""
        service = PlaylistService(mock_spotify_client)
        assert service._client == mock_spotify_client


class TestPlaylistServiceGetUserPlaylists:
    """Tests for get_user_playlists method."""

    def test_get_user_playlists_success(self, mock_spotify_client, sample_playlists):
        """Should return list of user playlists."""
        service = PlaylistService(mock_spotify_client)

        result = service.get_user_playlists()

        assert result == sample_playlists
        mock_spotify_client.get_user_playlists.assert_called_once()

    def test_get_user_playlists_empty(self, mock_spotify_client):
        """Should return empty list when user has no playlists."""
        mock_spotify_client.get_user_playlists.return_value = []
        service = PlaylistService(mock_spotify_client)

        result = service.get_user_playlists()

        assert result == []

    def test_get_user_playlists_failure(self, mock_spotify_client):
        """Should raise PlaylistError on API failure."""
        mock_spotify_client.get_user_playlists.side_effect = Exception("API error")
        service = PlaylistService(mock_spotify_client)

        with pytest.raises(PlaylistError) as exc_info:
            service.get_user_playlists()
        assert "Failed to fetch playlists" in str(exc_info.value)


class TestPlaylistServiceGetPlaylist:
    """Tests for get_playlist method."""

    def test_get_playlist_success(self, mock_spotify_client, sample_playlist_data, sample_tracks):
        """Should return Playlist model instance."""
        service = PlaylistService(mock_spotify_client)

        result = service.get_playlist('playlist123', include_features=False)

        assert isinstance(result, Playlist)
        assert result.id == 'playlist123'
        assert result.name == 'My Test Playlist'
        assert len(result.tracks) == len(sample_tracks)

    def test_get_playlist_with_features(self, mock_spotify_client, sample_audio_features):
        """Should include audio features when requested."""
        service = PlaylistService(mock_spotify_client)

        result = service.get_playlist('playlist123', include_features=True)

        assert result.has_features()
        mock_spotify_client.get_track_audio_features.assert_called_once()

    def test_get_playlist_without_features(self, mock_spotify_client):
        """Should not fetch features when not requested."""
        service = PlaylistService(mock_spotify_client)

        result = service.get_playlist('playlist123', include_features=False)

        assert not result.has_features()
        mock_spotify_client.get_track_audio_features.assert_not_called()

    def test_get_playlist_empty_id(self, mock_spotify_client):
        """Should raise PlaylistNotFoundError for empty ID."""
        service = PlaylistService(mock_spotify_client)

        with pytest.raises(PlaylistNotFoundError) as exc_info:
            service.get_playlist('')
        assert "Playlist ID is required" in str(exc_info.value)

    def test_get_playlist_none_id(self, mock_spotify_client):
        """Should raise PlaylistNotFoundError for None ID."""
        service = PlaylistService(mock_spotify_client)

        with pytest.raises(PlaylistNotFoundError) as exc_info:
            service.get_playlist(None)
        assert "Playlist ID is required" in str(exc_info.value)

    def test_get_playlist_api_failure(self, mock_spotify_client):
        """Should raise PlaylistError on API failure."""
        mock_spotify_client.get_playlist.side_effect = Exception("API error")
        service = PlaylistService(mock_spotify_client)

        with pytest.raises(PlaylistError) as exc_info:
            service.get_playlist('playlist123')
        assert "Failed to fetch playlist" in str(exc_info.value)


class TestPlaylistServiceGetPlaylistStats:
    """Tests for get_playlist_stats method."""

    def test_get_playlist_stats_success(self, mock_spotify_client, sample_audio_features):
        """Should return feature statistics."""
        service = PlaylistService(mock_spotify_client)

        stats = service.get_playlist_stats('playlist123')

        assert 'tempo' in stats
        assert 'energy' in stats
        assert 'valence' in stats
        assert 'danceability' in stats

    def test_get_playlist_stats_includes_min_max_avg(self, mock_spotify_client):
        """Stats should include min, max, and avg for each feature."""
        service = PlaylistService(mock_spotify_client)

        stats = service.get_playlist_stats('playlist123')

        for feature in ['tempo', 'energy', 'valence', 'danceability']:
            assert 'min' in stats[feature]
            assert 'max' in stats[feature]
            assert 'avg' in stats[feature]


class TestPlaylistServiceUpdatePlaylistTracks:
    """Tests for update_playlist_tracks method."""

    def test_update_playlist_tracks_success(self, mock_spotify_client, sample_track_uris):
        """Should update playlist and return True."""
        service = PlaylistService(mock_spotify_client)

        result = service.update_playlist_tracks('playlist123', sample_track_uris)

        assert result is True
        mock_spotify_client.update_playlist_tracks.assert_called_once_with(
            'playlist123', sample_track_uris
        )

    def test_update_playlist_tracks_empty_list(self, mock_spotify_client):
        """Should handle empty track list (clears playlist)."""
        service = PlaylistService(mock_spotify_client)

        result = service.update_playlist_tracks('playlist123', [])

        assert result is True
        mock_spotify_client.update_playlist_tracks.assert_called_once_with('playlist123', [])

    def test_update_playlist_tracks_empty_id(self, mock_spotify_client, sample_track_uris):
        """Should raise PlaylistUpdateError for empty ID."""
        service = PlaylistService(mock_spotify_client)

        with pytest.raises(PlaylistUpdateError) as exc_info:
            service.update_playlist_tracks('', sample_track_uris)
        assert "Playlist ID is required" in str(exc_info.value)

    def test_update_playlist_tracks_api_returns_false(self, mock_spotify_client, sample_track_uris):
        """Should raise PlaylistUpdateError when API returns False."""
        mock_spotify_client.update_playlist_tracks.return_value = False
        service = PlaylistService(mock_spotify_client)

        with pytest.raises(PlaylistUpdateError) as exc_info:
            service.update_playlist_tracks('playlist123', sample_track_uris)
        assert "Spotify API returned failure" in str(exc_info.value)

    def test_update_playlist_tracks_api_failure(self, mock_spotify_client, sample_track_uris):
        """Should raise PlaylistUpdateError on API exception."""
        mock_spotify_client.update_playlist_tracks.side_effect = Exception("API error")
        service = PlaylistService(mock_spotify_client)

        with pytest.raises(PlaylistUpdateError) as exc_info:
            service.update_playlist_tracks('playlist123', sample_track_uris)
        assert "Failed to update playlist" in str(exc_info.value)


class TestPlaylistServiceGetTrackUris:
    """Tests for get_track_uris method."""

    def test_get_track_uris_success(self, mock_spotify_client, sample_track_uris):
        """Should return list of track URIs."""
        service = PlaylistService(mock_spotify_client)

        result = service.get_track_uris('playlist123')

        assert result == sample_track_uris

    def test_get_track_uris_empty_playlist(self, mock_spotify_client):
        """Should return empty list for empty playlist."""
        mock_spotify_client.get_playlist_tracks.return_value = []
        service = PlaylistService(mock_spotify_client)

        result = service.get_track_uris('playlist123')

        assert result == []


class TestPlaylistServiceValidatePlaylistHasTracks:
    """Tests for validate_playlist_has_tracks method."""

    def test_validate_playlist_has_tracks_success(self, mock_spotify_client):
        """Should not raise for playlist with tracks."""
        service = PlaylistService(mock_spotify_client)
        playlist = service.get_playlist('playlist123')

        # Should not raise
        service.validate_playlist_has_tracks(playlist)

    def test_validate_playlist_has_tracks_empty(self, mock_spotify_client):
        """Should raise PlaylistError for empty playlist."""
        mock_spotify_client.get_playlist_tracks.return_value = []
        service = PlaylistService(mock_spotify_client)
        playlist = service.get_playlist('playlist123')

        with pytest.raises(PlaylistError) as exc_info:
            service.validate_playlist_has_tracks(playlist)
        assert "has no tracks" in str(exc_info.value)
