"""
Tests for PlaylistService.

Tests cover playlist retrieval and updates.
"""


import pytest

from shuffify.models.playlist import Playlist
from shuffify.services import (
    PlaylistAccessError,
    PlaylistError,
    PlaylistNotFoundError,
    PlaylistService,
    PlaylistUpdateError,
)


class TestPlaylistServiceAssertUserCanEdit:
    """Tests for validate_user_can_edit (the defense-in-depth ownership guard)."""

    def test_allows_owner(self, mock_spotify_api):
        """The playlist's owner may edit it."""
        mock_spotify_api.get_playlist.return_value = {
            "id": "p1",
            "owner": {"id": "alice"},
            "collaborative": False,
        }
        PlaylistService(mock_spotify_api).validate_user_can_edit(
            "p1", "alice"
        )

    def test_allows_collaborative_non_owner(self, mock_spotify_api):
        """A collaborative playlist is editable even by a non-owner."""
        mock_spotify_api.get_playlist.return_value = {
            "id": "p1",
            "owner": {"id": "bob"},
            "collaborative": True,
        }
        PlaylistService(mock_spotify_api).validate_user_can_edit(
            "p1", "alice"
        )

    def test_rejects_non_owner_non_collaborative(self, mock_spotify_api):
        """A non-owner, non-collaborative playlist must be rejected."""
        mock_spotify_api.get_playlist.return_value = {
            "id": "p1",
            "owner": {"id": "bob"},
            "collaborative": False,
        }
        with pytest.raises(PlaylistAccessError):
            PlaylistService(mock_spotify_api).validate_user_can_edit(
                "p1", "alice"
            )

    def test_rejects_when_playlist_not_accessible(self, mock_spotify_api):
        """A playlist the user can't even fetch (404/private) is rejected."""
        from shuffify.spotify.exceptions import SpotifyNotFoundError

        mock_spotify_api.get_playlist.side_effect = SpotifyNotFoundError(
            "not found"
        )
        with pytest.raises(PlaylistAccessError):
            PlaylistService(mock_spotify_api).validate_user_can_edit(
                "p1", "alice"
            )


class TestPlaylistServiceLeanEditability:
    """validate_user_can_edit(lean=True): one uncached summary read, the same 403 and 404 handling."""

    @staticmethod
    def check(summary=None, error=None, user="alice"):
        from unittest.mock import Mock

        from shuffify.spotify.api import SpotifyAPI

        api = Mock(spec=SpotifyAPI)
        api.get_playlist_summary.return_value = summary
        api.get_playlist_summary.side_effect = error
        try:
            return PlaylistService(api).validate_user_can_edit("p1", user, lean=True)
        finally:
            api.get_playlist_summary.assert_called_once_with("p1")
            api.get_playlist.assert_not_called()
            api.get_current_user.assert_not_called()

    def test_allows_the_owner_and_returns_the_summary(self):
        summary = {"id": "p1", "name": "Mine", "owner": {"id": "alice"}, "collaborative": False}
        assert self.check(summary) == summary

    def test_allows_a_collaborative_non_owner(self):
        summary = {"id": "p1", "name": "Shared", "owner": {"id": "bob"}, "collaborative": True}
        assert self.check(summary) == summary

    def test_rejects_a_foreign_playlist(self):
        with pytest.raises(PlaylistAccessError, match="permission") as error:
            self.check({"id": "p1", "name": "Bob's", "owner": {"id": "bob"}, "collaborative": False})
        assert error.value.__cause__ is None

    @pytest.mark.parametrize("failure", ["SpotifyNotFoundError", "SpotifyForbiddenError"])
    def test_a_missing_or_forbidden_playlist_is_not_accessible(self, failure):
        from shuffify.spotify import exceptions

        cause = getattr(exceptions, failure)("API error")
        with pytest.raises(PlaylistAccessError, match="not found or not accessible") as error:
            self.check(error=cause)
        assert error.value.__cause__ is cause


class TestPlaylistServiceInit:
    """Tests for PlaylistService initialization."""

    def test_init_with_api(self, mock_spotify_api):
        """Should initialize with a SpotifyAPI."""
        service = PlaylistService(mock_spotify_api)
        assert service._api == mock_spotify_api


class TestPlaylistServiceGetUserPlaylists:
    """Tests for get_user_playlists method."""

    def test_get_user_playlists_success(self, mock_spotify_api, sample_playlists):
        """Should return list of user playlists."""
        service = PlaylistService(mock_spotify_api)

        result = service.get_user_playlists()

        assert result == sample_playlists
        mock_spotify_api.get_user_playlists.assert_called_once()

    def test_get_user_playlists_empty(self, mock_spotify_api):
        """Should return empty list when user has no playlists."""
        mock_spotify_api.get_user_playlists.return_value = []
        service = PlaylistService(mock_spotify_api)

        result = service.get_user_playlists()

        assert result == []

    def test_get_user_playlists_failure(self, mock_spotify_api):
        """Should raise PlaylistError on API failure."""
        mock_spotify_api.get_user_playlists.side_effect = Exception("API error")
        service = PlaylistService(mock_spotify_api)

        with pytest.raises(PlaylistError) as exc_info:
            service.get_user_playlists()
        assert "Failed to fetch playlists" in str(exc_info.value)


class TestPlaylistServiceGetPlaylist:
    """Tests for get_playlist method."""

    def test_get_playlist_success(self, mock_spotify_api, sample_playlist_data, sample_tracks):
        """Should return Playlist model instance."""
        service = PlaylistService(mock_spotify_api)

        result = service.get_playlist('playlist123', include_features=False)

        assert isinstance(result, Playlist)
        assert result.id == 'playlist123'
        assert result.name == 'My Test Playlist'
        assert len(result.tracks) == len(sample_tracks)

    def test_get_playlist_with_features(self, mock_spotify_api, sample_audio_features):
        """Should include audio features when requested."""
        service = PlaylistService(mock_spotify_api)

        result = service.get_playlist('playlist123', include_features=True)

        assert result.has_features()
        mock_spotify_api.get_audio_features.assert_called_once()

    def test_get_playlist_without_features(self, mock_spotify_api):
        """Should not fetch features when not requested."""
        service = PlaylistService(mock_spotify_api)

        result = service.get_playlist('playlist123', include_features=False)

        assert not result.has_features()
        mock_spotify_api.get_audio_features.assert_not_called()

    def test_get_playlist_empty_id(self, mock_spotify_api):
        """Should raise PlaylistNotFoundError for empty ID."""
        service = PlaylistService(mock_spotify_api)

        with pytest.raises(PlaylistNotFoundError) as exc_info:
            service.get_playlist('')
        assert "Playlist ID is required" in str(exc_info.value)

    def test_get_playlist_none_id(self, mock_spotify_api):
        """Should raise PlaylistNotFoundError for None ID."""
        service = PlaylistService(mock_spotify_api)

        with pytest.raises(PlaylistNotFoundError) as exc_info:
            service.get_playlist(None)
        assert "Playlist ID is required" in str(exc_info.value)

    def test_get_playlist_api_failure(self, mock_spotify_api):
        """Should raise PlaylistError on API failure."""
        mock_spotify_api.get_playlist.side_effect = Exception("API error")
        service = PlaylistService(mock_spotify_api)

        with pytest.raises(PlaylistError) as exc_info:
            service.get_playlist('playlist123')
        assert "Failed to fetch playlist" in str(exc_info.value)

    def test_get_playlist_withheld_tracks_raise_access_error(self, mock_spotify_api):
        """Spotify answers another user's playlist items with 403."""
        from shuffify.spotify.exceptions import SpotifyForbiddenError

        mock_spotify_api.get_playlist_tracks.side_effect = SpotifyForbiddenError("API error 403: Forbidden")
        service = PlaylistService(mock_spotify_api)

        with pytest.raises(PlaylistAccessError, match="Spotify refused this request for the playlist"):
            service.get_playlist('playlist123')

    def test_get_playlist_lets_a_dead_session_through(self, mock_spotify_api):
        """A token Spotify refuses, and cannot be refreshed, is not a playlist
        problem: callers must be able to tell it from one."""
        from shuffify.spotify.exceptions import SpotifyTokenExpiredError

        mock_spotify_api.get_playlist.side_effect = SpotifyTokenExpiredError("Token expired or invalid")
        service = PlaylistService(mock_spotify_api)

        with pytest.raises(SpotifyTokenExpiredError):
            service.get_playlist('playlist123')


class TestPlaylistServiceGetPlaylistMetadata:
    """Tests for get_playlist_metadata method."""

    def test_get_metadata_success(self, mock_spotify_api):
        """Should return metadata dict without fetching tracks."""
        mock_spotify_api.get_playlist.return_value = {
            "id": "pl1",
            "name": "Cool Playlist",
            "owner": {"id": "owner1"},
            "description": "A playlist",
            "tracks": {"total": 42},
        }
        service = PlaylistService(mock_spotify_api)
        result = service.get_playlist_metadata("pl1")

        assert result["id"] == "pl1"
        assert result["name"] == "Cool Playlist"
        assert result["owner_id"] == "owner1"
        assert result["total_tracks"] == 42
        # Should NOT call get_playlist_tracks
        mock_spotify_api.get_playlist_tracks.assert_not_called()

    def test_get_metadata_empty_id(self, mock_spotify_api):
        """Should raise PlaylistNotFoundError for empty ID."""
        service = PlaylistService(mock_spotify_api)
        with pytest.raises(PlaylistNotFoundError):
            service.get_playlist_metadata("")

    def test_get_metadata_not_found(self, mock_spotify_api, monkeypatch):
        """Should raise PlaylistNotFoundError on 404, once the public-page
        scraper also comes back empty. The scraper is stubbed, so the test
        makes no network request."""
        from unittest.mock import create_autospec

        from shuffify.services import playlist_service
        from shuffify.spotify.exceptions import SpotifyNotFoundError

        # The public page has nothing either.
        scraper = create_autospec(playlist_service.scrape_playlist_metadata, return_value=None)
        monkeypatch.setattr(playlist_service, "scrape_playlist_metadata", scraper)
        mock_spotify_api.get_playlist.side_effect = (
            SpotifyNotFoundError("Not found")
        )
        service = PlaylistService(mock_spotify_api)
        with pytest.raises(PlaylistNotFoundError):
            service.get_playlist_metadata("pl1")
        scraper.assert_called_once_with("pl1")

    def test_get_metadata_api_failure(self, mock_spotify_api):
        """Should raise PlaylistError on generic failure."""
        mock_spotify_api.get_playlist.side_effect = (
            Exception("API error")
        )
        service = PlaylistService(mock_spotify_api)
        with pytest.raises(PlaylistError):
            service.get_playlist_metadata("pl1")


class TestPlaylistServiceGetPlaylistSummary:
    """Tests for get_playlist_summary method."""

    def test_summary_is_the_metadata_without_tracks(self, mock_spotify_api):
        mock_spotify_api.get_playlist.return_value = {
            "id": "pl1",
            "name": "Their Finds",
            "owner": {"id": "bob"},
            "description": "Not mine",
            "items": {"total": 25},
        }
        service = PlaylistService(mock_spotify_api)

        result = service.get_playlist_summary("pl1")

        assert (result.id, result.name, result.owner_id, result.description, result.total_tracks) == (
            "pl1", "Their Finds", "bob", "Not mine", 25
        )
        assert result.tracks == []
        mock_spotify_api.get_playlist_tracks.assert_not_called()


class TestPlaylistServiceUpdatePlaylistTracks:
    """Tests for update_playlist_tracks method."""

    def test_update_playlist_tracks_success(self, mock_spotify_api, sample_track_uris):
        """Should update playlist and return True."""
        service = PlaylistService(mock_spotify_api)

        result = service.update_playlist_tracks('playlist123', sample_track_uris)

        assert result is True
        mock_spotify_api.update_playlist_tracks.assert_called_once_with(
            'playlist123', sample_track_uris
        )

    def test_update_playlist_tracks_empty_list(self, mock_spotify_api):
        """Should handle empty track list (clears playlist)."""
        service = PlaylistService(mock_spotify_api)

        result = service.update_playlist_tracks('playlist123', [])

        assert result is True
        mock_spotify_api.update_playlist_tracks.assert_called_once_with('playlist123', [])

    def test_update_playlist_tracks_empty_id(self, mock_spotify_api, sample_track_uris):
        """Should raise PlaylistUpdateError for empty ID."""
        service = PlaylistService(mock_spotify_api)

        with pytest.raises(PlaylistUpdateError) as exc_info:
            service.update_playlist_tracks('', sample_track_uris)
        assert "Playlist ID is required" in str(exc_info.value)

    def test_update_playlist_tracks_api_returns_false(self, mock_spotify_api, sample_track_uris):
        """Should raise PlaylistUpdateError when API returns False."""
        mock_spotify_api.update_playlist_tracks.return_value = False
        service = PlaylistService(mock_spotify_api)

        with pytest.raises(PlaylistUpdateError) as exc_info:
            service.update_playlist_tracks('playlist123', sample_track_uris)
        assert "Spotify API returned failure" in str(exc_info.value)

    def test_update_playlist_tracks_api_failure(self, mock_spotify_api, sample_track_uris):
        """Should raise PlaylistUpdateError on API exception."""
        mock_spotify_api.update_playlist_tracks.side_effect = Exception("API error")
        service = PlaylistService(mock_spotify_api)

        with pytest.raises(PlaylistUpdateError) as exc_info:
            service.update_playlist_tracks('playlist123', sample_track_uris)
        assert "Failed to update playlist" in str(exc_info.value)


class TestPlaylistServiceGetTrackUris:
    """Tests for get_track_uris method."""

    def test_get_track_uris_success(self, mock_spotify_api, sample_track_uris):
        """Should return list of track URIs."""
        service = PlaylistService(mock_spotify_api)

        result = service.get_track_uris('playlist123')

        assert result == sample_track_uris

    def test_get_track_uris_empty_playlist(self, mock_spotify_api):
        """Should return empty list for empty playlist."""
        mock_spotify_api.get_playlist_tracks.return_value = []
        service = PlaylistService(mock_spotify_api)

        result = service.get_track_uris('playlist123')

        assert result == []
