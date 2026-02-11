"""
Tests for SpotifyAPI.search_playlists().

Covers successful searches, empty results, caching, and error handling.
"""

import time
from unittest.mock import Mock, patch

import spotipy

from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.cache import SpotifyCache
from shuffify.spotify.credentials import SpotifyCredentials


# =============================================================================
# Fixtures
# =============================================================================


import pytest


@pytest.fixture
def credentials():
    """Valid SpotifyCredentials."""
    return SpotifyCredentials(
        client_id="test_client_id",
        client_secret="test_client_secret",
        redirect_uri="http://localhost:5000/callback",
    )


@pytest.fixture
def auth_manager(credentials):
    """SpotifyAuthManager instance."""
    return SpotifyAuthManager(credentials)


@pytest.fixture
def valid_token():
    """Valid TokenInfo."""
    return TokenInfo(
        access_token="test_access_token",
        token_type="Bearer",
        expires_at=time.time() + 3600,
        refresh_token="test_refresh_token",
    )


@pytest.fixture
def mock_sp():
    """Mock spotipy.Spotify instance."""
    return Mock(spec=spotipy.Spotify)


@pytest.fixture
def api(valid_token, auth_manager, mock_sp):
    """SpotifyAPI with mocked spotipy client."""
    with patch(
        "shuffify.spotify.api.spotipy.Spotify", return_value=mock_sp
    ):
        return SpotifyAPI(valid_token, auth_manager)


@pytest.fixture
def sample_search_response():
    """Sample Spotify search API response for playlists."""
    return {
        "playlists": {
            "items": [
                {
                    "id": "playlist_abc",
                    "name": "Jazz Vibes",
                    "owner": {"display_name": "Spotify"},
                    "images": [{"url": "https://example.com/jazz.jpg"}],
                    "tracks": {"total": 50},
                },
                {
                    "id": "playlist_def",
                    "name": "Smooth Jazz",
                    "owner": {"display_name": "JazzFan42"},
                    "images": [],
                    "tracks": {"total": 30},
                },
                {
                    "id": "playlist_ghi",
                    "name": "Jazz Classics",
                    "owner": {},
                    "images": None,
                    "tracks": {},
                },
            ]
        }
    }


# =============================================================================
# Tests
# =============================================================================


class TestSearchPlaylists:
    """Tests for SpotifyAPI.search_playlists()."""

    def test_search_returns_formatted_results(
        self, api, mock_sp, sample_search_response
    ):
        """Search should return formatted playlist summaries."""
        mock_sp.search.return_value = sample_search_response

        results = api.search_playlists("jazz")

        assert len(results) == 3
        assert results[0]["id"] == "playlist_abc"
        assert results[0]["name"] == "Jazz Vibes"
        assert results[0]["owner_display_name"] == "Spotify"
        assert results[0]["image_url"] == "https://example.com/jazz.jpg"
        assert results[0]["total_tracks"] == 50

    def test_search_handles_missing_images(
        self, api, mock_sp, sample_search_response
    ):
        """Playlists without images should have None for image_url."""
        mock_sp.search.return_value = sample_search_response

        results = api.search_playlists("jazz")

        assert results[1]["image_url"] is None

    def test_search_handles_missing_owner(
        self, api, mock_sp, sample_search_response
    ):
        """Playlists with missing owner should show 'Unknown'."""
        mock_sp.search.return_value = sample_search_response

        results = api.search_playlists("jazz")

        assert results[2]["owner_display_name"] == "Unknown"

    def test_search_handles_missing_track_total(
        self, api, mock_sp, sample_search_response
    ):
        """Playlists with missing track total should default to 0."""
        mock_sp.search.return_value = sample_search_response

        results = api.search_playlists("jazz")

        assert results[2]["total_tracks"] == 0

    def test_search_empty_results(self, api, mock_sp):
        """Empty search results should return empty list."""
        mock_sp.search.return_value = {"playlists": {"items": []}}

        results = api.search_playlists("xyznonexistent")

        assert results == []
        mock_sp.search.assert_called_once_with(
            q="xyznonexistent", type="playlist", limit=10
        )

    def test_search_skips_none_items(self, api, mock_sp):
        """None items in search results should be skipped."""
        mock_sp.search.return_value = {
            "playlists": {
                "items": [
                    None,
                    {
                        "id": "valid",
                        "name": "Valid Playlist",
                        "owner": {"display_name": "User"},
                        "images": [],
                        "tracks": {"total": 5},
                    },
                    None,
                ]
            }
        }

        results = api.search_playlists("test")

        assert len(results) == 1
        assert results[0]["id"] == "valid"

    def test_search_respects_limit(self, api, mock_sp):
        """Limit parameter should be passed to Spotify API."""
        mock_sp.search.return_value = {"playlists": {"items": []}}

        api.search_playlists("test", limit=5)

        mock_sp.search.assert_called_once_with(
            q="test", type="playlist", limit=5
        )

    def test_search_clamps_limit_min(self, api, mock_sp):
        """Limit below 1 should be clamped to 1."""
        mock_sp.search.return_value = {"playlists": {"items": []}}

        api.search_playlists("test", limit=0)

        mock_sp.search.assert_called_once_with(
            q="test", type="playlist", limit=1
        )

    def test_search_clamps_limit_max(self, api, mock_sp):
        """Limit above 50 should be clamped to 50."""
        mock_sp.search.return_value = {"playlists": {"items": []}}

        api.search_playlists("test", limit=100)

        mock_sp.search.assert_called_once_with(
            q="test", type="playlist", limit=50
        )

    def test_search_calls_spotify_api(self, api, mock_sp):
        """Search should call sp.search with correct parameters."""
        mock_sp.search.return_value = {"playlists": {"items": []}}

        api.search_playlists("my query", limit=10)

        mock_sp.search.assert_called_once_with(
            q="my query", type="playlist", limit=10
        )


class TestSearchPlaylistsWithCache:
    """Tests for search_playlists caching behavior."""

    def test_search_returns_cached_results(
        self, valid_token, auth_manager, mock_sp
    ):
        """Cached results should be returned without calling API."""
        import redis as redis_lib

        mock_redis = Mock(spec=redis_lib.Redis)
        cache = SpotifyCache(mock_redis)

        mock_redis.get.return_value = (
            b'[{"id": "cached", "name": "Cached Playlist"}]'
        )

        with patch(
            "shuffify.spotify.api.spotipy.Spotify",
            return_value=mock_sp,
        ):
            api = SpotifyAPI(valid_token, auth_manager, cache=cache)
            results = api.search_playlists("jazz")

        assert len(results) == 1
        assert results[0]["id"] == "cached"
        mock_sp.search.assert_not_called()

    def test_search_skip_cache(
        self, valid_token, auth_manager, mock_sp
    ):
        """skip_cache=True should bypass cache."""
        import redis as redis_lib

        mock_redis = Mock(spec=redis_lib.Redis)
        cache = SpotifyCache(mock_redis)
        mock_redis.get.return_value = b'[{"id": "cached"}]'

        mock_sp.search.return_value = {
            "playlists": {
                "items": [
                    {
                        "id": "fresh",
                        "name": "Fresh",
                        "owner": {"display_name": "User"},
                        "images": [],
                        "tracks": {"total": 1},
                    }
                ]
            }
        }

        with patch(
            "shuffify.spotify.api.spotipy.Spotify",
            return_value=mock_sp,
        ):
            api = SpotifyAPI(valid_token, auth_manager, cache=cache)
            results = api.search_playlists("jazz", skip_cache=True)

        assert results[0]["id"] == "fresh"
        mock_sp.search.assert_called_once()
