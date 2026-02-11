"""
Tests for the Workshop external playlist routes.

Covers URL loading, playlist search, session history, and error handling.
"""

import json
from unittest.mock import patch, Mock

from shuffify.models.playlist import Playlist


# =============================================================================
# Helpers
# =============================================================================


def _make_external_playlist():
    """A Playlist model instance for external playlist tests."""
    return Playlist(
        id="ext_playlist_abc",
        name="Jazz Vibes",
        owner_id="spotify_editorial",
        description="The best jazz tracks",
        tracks=[
            {
                "id": f"ext_track{i}",
                "name": f"Jazz Track {i}",
                "uri": f"spotify:track:ext_track{i}",
                "duration_ms": 240000 + (i * 1000),
                "is_local": False,
                "artists": [f"Jazz Artist {i}"],
                "artist_urls": [
                    f"https://open.spotify.com/artist/jazzartist{i}"
                ],
                "album_name": f"Jazz Album {i}",
                "album_image_url": f"https://example.com/jazz{i}.jpg",
                "track_url": (
                    f"https://open.spotify.com/track/ext_track{i}"
                ),
            }
            for i in range(1, 6)
        ],
    )


# =============================================================================
# Load External Playlist Tests (URL mode)
# =============================================================================


class TestLoadExternalPlaylistByUrl:
    """Tests for POST /workshop/load-external-playlist with URL."""

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    @patch("shuffify.routes.parse_spotify_playlist_url")
    def test_load_by_full_url(
        self,
        mock_parse_url,
        mock_playlist_svc,
        mock_auth_svc,
        authenticated_client,
    ):
        """Loading by full Spotify URL should return tracks."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_parse_url.return_value = "ext_playlist_abc"

        mock_ps = Mock()
        mock_ps.get_playlist.return_value = _make_external_playlist()
        mock_playlist_svc.return_value = mock_ps

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({
                "url": (
                    "https://open.spotify.com/playlist/ext_playlist_abc"
                )
            }),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["mode"] == "tracks"
        assert data["playlist"]["id"] == "ext_playlist_abc"
        assert data["playlist"]["name"] == "Jazz Vibes"
        assert len(data["tracks"]) == 5

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.parse_spotify_playlist_url")
    def test_load_by_invalid_url_returns_400(
        self, mock_parse_url, mock_auth_svc, authenticated_client
    ):
        """Invalid URL that cannot be parsed should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_parse_url.return_value = None

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({"url": "not-a-spotify-url"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["success"] is False
        assert "Could not parse" in data["message"]

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    @patch("shuffify.routes.parse_spotify_playlist_url")
    def test_load_private_playlist_returns_404(
        self,
        mock_parse_url,
        mock_playlist_svc,
        mock_auth_svc,
        authenticated_client,
    ):
        """Loading a private/deleted playlist should return 404."""
        from shuffify.services import PlaylistError

        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_parse_url.return_value = "private_playlist_id"

        mock_ps = Mock()
        mock_ps.get_playlist.side_effect = PlaylistError("Not found")
        mock_playlist_svc.return_value = mock_ps

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({"url": "private_playlist_id"}),
            content_type="application/json",
        )

        assert response.status_code == 404

    def test_load_external_requires_auth(self, client):
        """Unauthenticated request should return 401."""
        response = client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({"url": "some_id"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @patch("shuffify.routes.AuthService")
    def test_load_external_requires_json(
        self, mock_auth_svc, authenticated_client
    ):
        """Non-JSON request should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data="not valid json",
            content_type="application/json",
        )
        assert response.status_code == 400

    @patch("shuffify.routes.AuthService")
    def test_load_external_requires_url_or_query(
        self, mock_auth_svc, authenticated_client
    ):
        """Request without url or query should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400


# =============================================================================
# Load External Playlist Tests (Search mode)
# =============================================================================


class TestLoadExternalPlaylistBySearch:
    """Tests for POST /workshop/load-external-playlist with query."""

    @patch("shuffify.routes.AuthService")
    def test_search_returns_playlist_list(
        self, mock_auth_svc, authenticated_client
    ):
        """Search query should return a list of playlists."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_client = Mock()
        mock_client.search_playlists.return_value = [
            {
                "id": "pl1",
                "name": "Jazz Mix",
                "owner_display_name": "Spotify",
                "image_url": "https://example.com/img.jpg",
                "total_tracks": 50,
            }
        ]
        mock_auth_svc.get_authenticated_client.return_value = mock_client

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({"query": "jazz"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["mode"] == "search"
        assert len(data["playlists"]) == 1
        assert data["playlists"][0]["name"] == "Jazz Mix"


# =============================================================================
# Search Playlists Route Tests
# =============================================================================


class TestSearchPlaylistsRoute:
    """Tests for POST /workshop/search-playlists."""

    @patch("shuffify.routes.AuthService")
    def test_search_returns_results(
        self, mock_auth_svc, authenticated_client
    ):
        """Search should return playlist results."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_client = Mock()
        mock_client.search_playlists.return_value = [
            {
                "id": "result1",
                "name": "Test Playlist",
                "owner_display_name": "Owner",
                "image_url": None,
                "total_tracks": 10,
            }
        ]
        mock_auth_svc.get_authenticated_client.return_value = mock_client

        response = authenticated_client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": "test"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 1

    @patch("shuffify.routes.AuthService")
    def test_search_empty_query_returns_400(
        self, mock_auth_svc, authenticated_client
    ):
        """Empty search query should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": ""}),
            content_type="application/json",
        )

        assert response.status_code == 400

    @patch("shuffify.routes.AuthService")
    def test_search_too_long_query_returns_400(
        self, mock_auth_svc, authenticated_client
    ):
        """Query exceeding 200 characters should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": "x" * 201}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_search_requires_auth(self, client):
        """Unauthenticated search should return 401."""
        response = client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 401
