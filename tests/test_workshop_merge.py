"""
Tests for the Workshop source playlist merging feature (Phase 3).

Covers the /api/user-playlists endpoint and the interaction between
source playlists and the existing /playlist/<id> endpoint.
"""

import json
from unittest.mock import patch, Mock, MagicMock

from shuffify.models.playlist import Playlist


# =============================================================================
# Helpers
# =============================================================================


def _sample_playlists():
    """Return a list of sample playlist dicts as returned by PlaylistService."""
    return [
        {
            "id": "playlist_main",
            "name": "Main Playlist",
            "owner": {"id": "user123"},
            "tracks": {"total": 25},
            "images": [{"url": "https://example.com/main.jpg"}],
            "collaborative": False,
        },
        {
            "id": "playlist_source",
            "name": "Source Playlist",
            "owner": {"id": "user123"},
            "tracks": {"total": 10},
            "images": [{"url": "https://example.com/source.jpg"}],
            "collaborative": False,
        },
        {
            "id": "playlist_no_art",
            "name": "No Art Playlist",
            "owner": {"id": "user123"},
            "tracks": {"total": 5},
            "images": [],
            "collaborative": False,
        },
    ]


# =============================================================================
# GET /api/user-playlists Tests
# =============================================================================


class TestApiUserPlaylists:
    """Tests for GET /api/user-playlists."""

    def test_returns_401_when_not_authenticated(self, client):
        """Unauthenticated request should return 401."""
        response = client.get("/api/user-playlists")
        assert response.status_code == 401
        data = response.get_json()
        assert data["success"] is False

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.playlists.PlaylistService")
    def test_returns_playlist_list(
        self,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Should return JSON list with id, name, track_count, image_url."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        mock_ps_instance = Mock()
        mock_ps_instance.get_user_playlists.return_value = _sample_playlists()
        mock_playlist_svc.return_value = mock_ps_instance

        response = authenticated_client.get("/api/user-playlists")
        assert response.status_code == 200

        data = response.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 3

        first = data["playlists"][0]
        assert "id" in first
        assert "name" in first
        assert "track_count" in first
        assert "image_url" in first

        # Playlist with no art returns None for image_url
        no_art = next(
            p for p in data["playlists"] if p["id"] == "playlist_no_art"
        )
        assert no_art["image_url"] is None

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.playlists.PlaylistService")
    def test_returns_correct_track_count(
        self,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Track count should be extracted from tracks.total."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        mock_ps_instance = Mock()
        mock_ps_instance.get_user_playlists.return_value = _sample_playlists()
        mock_playlist_svc.return_value = mock_ps_instance

        response = authenticated_client.get("/api/user-playlists")
        data = response.get_json()

        source = next(
            p for p in data["playlists"] if p["id"] == "playlist_source"
        )
        assert source["track_count"] == 10

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.playlists.PlaylistService")
    def test_handles_empty_playlist_list(
        self,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Should return empty list when user has no playlists."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        mock_ps_instance = Mock()
        mock_ps_instance.get_user_playlists.return_value = []
        mock_playlist_svc.return_value = mock_ps_instance

        response = authenticated_client.get("/api/user-playlists")
        data = response.get_json()
        assert data["success"] is True
        assert data["playlists"] == []

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.playlists.PlaylistService")
    def test_handles_service_error(
        self,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Should return 500 when PlaylistService raises."""
        from shuffify.services import PlaylistError

        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        mock_ps_instance = Mock()
        mock_ps_instance.get_user_playlists.side_effect = PlaylistError(
            "API down"
        )
        mock_playlist_svc.return_value = mock_ps_instance

        response = authenticated_client.get("/api/user-playlists")
        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False


# =============================================================================
# Source Playlist Loading Tests (via existing GET /playlist/<id>)
# =============================================================================


class TestSourcePlaylistLoading:
    """
    Verify GET /playlist/<id> returns data with all fields the source
    panel JS needs.
    """

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.playlists.PlaylistService")
    def test_playlist_endpoint_returns_tracks_with_required_fields(
        self,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """GET /playlist/<id> must include uri, name, artists, album_image_url, duration_ms, id."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        mock_playlist = Playlist(
            id="source_pl",
            name="Source",
            owner_id="user123",
            tracks=[
                {
                    "id": "t1",
                    "name": "Track One",
                    "uri": "spotify:track:t1",
                    "duration_ms": 200000,
                    "is_local": False,
                    "artists": ["Artist A"],
                    "artist_urls": [
                        "https://open.spotify.com/artist/a1"
                    ],
                    "album_name": "Album A",
                    "album_image_url": "https://example.com/a1.jpg",
                    "track_url": "https://open.spotify.com/track/t1",
                },
            ],
        )

        mock_ps_instance = Mock()
        mock_ps_instance.get_playlist.return_value = mock_playlist
        mock_playlist_svc.return_value = mock_ps_instance

        response = authenticated_client.get("/playlist/source_pl")
        assert response.status_code == 200

        data = response.get_json()
        assert "tracks" in data
        assert len(data["tracks"]) == 1

        track = data["tracks"][0]
        assert "uri" in track
        assert "name" in track
        assert "artists" in track
        assert "album_image_url" in track
        assert "duration_ms" in track
        assert "id" in track
