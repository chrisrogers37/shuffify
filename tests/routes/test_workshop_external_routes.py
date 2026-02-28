"""
Tests for the Workshop external playlist routes.

Covers URL loading, playlist search, session history, and error handling.
"""

import json
from unittest.mock import patch, Mock, MagicMock

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

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch("shuffify.routes.workshop.parse_spotify_playlist_url")
    def test_load_by_full_url(
        self,
        mock_parse_url,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Loading by full Spotify URL should return tracks."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.spotify_id = "user123"
        mock_get_db_user.return_value = mock_user

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

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.workshop.parse_spotify_playlist_url")
    def test_load_by_invalid_url_returns_400(
        self,
        mock_parse_url,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Invalid URL that cannot be parsed should return 400."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

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

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch("shuffify.routes.workshop.parse_spotify_playlist_url")
    def test_load_private_playlist_returns_404(
        self,
        mock_parse_url,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Loading a private/deleted playlist should return 404."""
        from shuffify.services import PlaylistError

        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

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

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_load_external_requires_json(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Non-JSON request should return 400."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data="not valid json",
            content_type="application/json",
        )
        assert response.status_code == 400

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_load_external_requires_url_or_query(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Request without url or query should return 400."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

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

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_search_returns_playlist_list(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Search query should return a list of playlists."""
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
        mock_auth.return_value = mock_client
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

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

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_search_returns_results(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Search should return playlist results."""
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
        mock_auth.return_value = mock_client
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        response = authenticated_client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": "test"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 1

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_search_empty_query_returns_400(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Empty search query should return 400."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        response = authenticated_client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": ""}),
            content_type="application/json",
        )

        assert response.status_code == 400

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_search_too_long_query_returns_400(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Query exceeding 200 characters should return 400."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

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


# =============================================================================
# Restricted Playlist Detection Tests (Spotify Feb 2026 API)
# =============================================================================


def _make_restricted_playlist():
    """A Playlist with total_tracks > 0 but no actual tracks."""
    return Playlist(
        id="restricted_pl_123",
        name="Top Hits 2026",
        owner_id="spotify_editorial",
        description="The hottest tracks",
        total_tracks=50,
        tracks=[],
    )


def _make_genuinely_empty_playlist():
    """A Playlist with total_tracks=0 and no actual tracks."""
    return Playlist(
        id="empty_pl_456",
        name="Empty Playlist",
        owner_id="other_user",
        description="Nothing here",
        total_tracks=0,
        tracks=[],
    )


def _make_owned_playlist_with_tracks():
    """A Playlist owned by the current user with tracks."""
    return Playlist(
        id="owned_pl_789",
        name="My Playlist",
        owner_id="user123",
        description="My music",
        total_tracks=5,
        tracks=[
            {
                "id": f"track{i}",
                "name": f"Track {i}",
                "uri": f"spotify:track:track{i}",
                "duration_ms": 200000,
                "is_local": False,
                "artists": [f"Artist {i}"],
                "artist_urls": [
                    f"https://open.spotify.com/artist/a{i}"
                ],
                "album_name": f"Album {i}",
                "album_image_url": (
                    f"https://example.com/img{i}.jpg"
                ),
                "track_url": (
                    f"https://open.spotify.com/track/track{i}"
                ),
            }
            for i in range(1, 6)
        ],
    )


def _make_owned_empty_playlist():
    """A Playlist owned by the current user with no tracks."""
    return Playlist(
        id="owned_empty_pl",
        name="My Empty Playlist",
        owner_id="user123",
        description="Nothing yet",
        total_tracks=0,
        tracks=[],
    )


class TestRestrictedPlaylistDetection:
    """Tests for restricted playlist detection (Feb 2026 API)."""

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch(
        "shuffify.routes.workshop.parse_spotify_playlist_url"
    )
    def test_restricted_playlist_returns_restricted_mode(
        self,
        mock_parse_url,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Non-owned playlist with declared tracks but
        0 returned should return mode: restricted."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.spotify_id = "user123"
        mock_get_db_user.return_value = mock_user

        mock_parse_url.return_value = "restricted_pl_123"

        mock_ps = Mock()
        mock_ps.get_playlist.return_value = (
            _make_restricted_playlist()
        )
        mock_playlist_svc.return_value = mock_ps

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({
                "url": "restricted_pl_123"
            }),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["mode"] == "restricted"
        assert data["playlist"]["id"] == (
            "restricted_pl_123"
        )
        assert data["playlist"]["track_count"] == 50
        assert len(data["tracks"]) == 0
        assert "message" in data
        assert "suggested_search" in data
        assert data["suggested_search"] == "Top Hits 2026"

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch(
        "shuffify.routes.workshop.parse_spotify_playlist_url"
    )
    def test_genuinely_empty_playlist_returns_tracks_mode(
        self,
        mock_parse_url,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Non-owned playlist with 0 declared tracks and
        0 returned should return mode: tracks (not restricted)."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.spotify_id = "user123"
        mock_get_db_user.return_value = mock_user

        mock_parse_url.return_value = "empty_pl_456"

        mock_ps = Mock()
        mock_ps.get_playlist.return_value = (
            _make_genuinely_empty_playlist()
        )
        mock_playlist_svc.return_value = mock_ps

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({
                "url": "empty_pl_456"
            }),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["mode"] == "tracks"

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch(
        "shuffify.routes.workshop.parse_spotify_playlist_url"
    )
    def test_owned_playlist_with_tracks_returns_tracks_mode(
        self,
        mock_parse_url,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Owned playlist with tracks should return
        mode: tracks."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.spotify_id = "user123"
        mock_get_db_user.return_value = mock_user

        mock_parse_url.return_value = "owned_pl_789"

        mock_ps = Mock()
        mock_ps.get_playlist.return_value = (
            _make_owned_playlist_with_tracks()
        )
        mock_playlist_svc.return_value = mock_ps

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({
                "url": "owned_pl_789"
            }),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["mode"] == "tracks"
        assert len(data["tracks"]) == 5

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    @patch("shuffify.routes.workshop.PlaylistService")
    @patch(
        "shuffify.routes.workshop.parse_spotify_playlist_url"
    )
    def test_owned_empty_playlist_returns_tracks_mode(
        self,
        mock_parse_url,
        mock_playlist_svc,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Owned playlist with 0 tracks should return
        mode: tracks (not restricted)."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.spotify_id = "user123"
        mock_get_db_user.return_value = mock_user

        mock_parse_url.return_value = "owned_empty_pl"

        mock_ps = Mock()
        mock_ps.get_playlist.return_value = (
            _make_owned_empty_playlist()
        )
        mock_playlist_svc.return_value = mock_ps

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({
                "url": "owned_empty_pl"
            }),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["mode"] == "tracks"


class TestSearchPlaylistsOwnerIdField:
    """Tests for owner_id field in search results."""

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_search_playlists_includes_owner_id(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Search results should include owner_id field."""
        mock_client = Mock()
        mock_client.search_playlists.return_value = [
            {
                "id": "pl1",
                "name": "Jazz Mix",
                "owner_display_name": "Spotify",
                "owner_id": "spotify",
                "image_url": (
                    "https://example.com/img.jpg"
                ),
                "total_tracks": 50,
            }
        ]
        mock_auth.return_value = mock_client
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        response = authenticated_client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": "jazz"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 1
        assert data["playlists"][0]["owner_id"] == (
            "spotify"
        )
