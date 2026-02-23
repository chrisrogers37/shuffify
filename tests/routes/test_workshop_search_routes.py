"""
Tests for the Workshop search route and search caching.

Covers the POST /workshop/search endpoint, input validation,
and the SpotifyCache search result caching methods.
"""

import json
from unittest.mock import patch, Mock, MagicMock

import pytest
import redis

from shuffify.spotify.cache import SpotifyCache
from shuffify.schemas.requests import WorkshopSearchRequest


# =============================================================================
# Schema Validation Tests
# =============================================================================


class TestWorkshopSearchRequestSchema:
    """Tests for WorkshopSearchRequest Pydantic schema."""

    def test_valid_query(self):
        """Valid query string should pass validation."""
        req = WorkshopSearchRequest(query="the beatles")
        assert req.query == "the beatles"
        assert req.limit == 20
        assert req.offset == 0

    def test_query_is_stripped(self):
        """Leading/trailing whitespace should be stripped."""
        req = WorkshopSearchRequest(query="  hello world  ")
        assert req.query == "hello world"

    def test_empty_query_rejected(self):
        """Empty string should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="")

    def test_whitespace_only_query_rejected(self):
        """Whitespace-only string should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="   ")

    def test_query_max_length(self):
        """Query longer than 200 characters should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="a" * 201)

    def test_custom_limit_and_offset(self):
        """Custom limit and offset should be accepted."""
        req = WorkshopSearchRequest(query="test", limit=10, offset=40)
        assert req.limit == 10
        assert req.offset == 40

    def test_limit_below_minimum_rejected(self):
        """Limit of 0 should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="test", limit=0)

    def test_limit_above_maximum_rejected(self):
        """Limit above 50 should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="test", limit=51)

    def test_negative_offset_rejected(self):
        """Negative offset should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="test", offset=-1)


# =============================================================================
# Search Cache Tests
# =============================================================================


class TestSearchCache:
    """Tests for SpotifyCache search methods."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        return MagicMock(spec=redis.Redis)

    @pytest.fixture
    def cache(self, mock_redis):
        """Create a SpotifyCache with mocked Redis."""
        return SpotifyCache(mock_redis)

    def test_get_search_results_cache_miss(self, cache, mock_redis):
        """Cache miss should return None."""
        mock_redis.get.return_value = None
        result = cache.get_search_results("test query", 0)
        assert result is None

    def test_get_search_results_cache_hit(self, cache, mock_redis):
        """Cache hit should return deserialized data."""
        tracks = [{"id": "t1", "name": "Track 1", "uri": "spotify:track:t1"}]
        mock_redis.get.return_value = json.dumps(tracks).encode("utf-8")
        result = cache.get_search_results("test query", 0)
        assert result == tracks

    def test_set_search_results(self, cache, mock_redis):
        """Setting search results should call setex with 120s TTL."""
        tracks = [{"id": "t1", "name": "Track 1"}]
        cache.set_search_results("test query", 0, tracks)
        mock_redis.setex.assert_called_once()
        args = mock_redis.setex.call_args
        assert args[0][1] == 120  # Default TTL

    def test_search_cache_normalizes_query(self, cache, mock_redis):
        """Query should be normalized (lowercase, stripped) for cache key."""
        mock_redis.get.return_value = None
        cache.get_search_results("  The Beatles  ", 0)
        key_used = mock_redis.get.call_args[0][0]
        assert "the beatles" in key_used

    def test_search_cache_includes_offset_in_key(self, cache, mock_redis):
        """Different offsets should produce different cache keys."""
        mock_redis.get.return_value = None
        cache.get_search_results("test", 0)
        key_0 = mock_redis.get.call_args[0][0]

        cache.get_search_results("test", 20)
        key_20 = mock_redis.get.call_args[0][0]

        assert key_0 != key_20

    def test_search_cache_redis_error_returns_none(self, cache, mock_redis):
        """Redis errors should return None, not raise."""
        mock_redis.get.side_effect = redis.RedisError("Connection lost")
        result = cache.get_search_results("test", 0)
        assert result is None

    def test_set_search_results_redis_error_returns_false(self, cache, mock_redis):
        """Redis errors on set should return False, not raise."""
        mock_redis.setex.side_effect = redis.RedisError("Connection lost")
        result = cache.set_search_results("test", 0, [{"id": "t1"}])
        assert result is False


# =============================================================================
# Search Route Tests
# =============================================================================


class TestWorkshopSearchRoute:
    """Tests for POST /workshop/search."""

    def test_search_requires_auth(self, client):
        """Unauthenticated search should return 401."""
        response = client.post(
            "/workshop/search",
            data=json.dumps({"query": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_search_requires_json_body(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Search without JSON body should return 400."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        response = authenticated_client.post(
            "/workshop/search",
            data="not valid json",
            content_type="application/json",
        )
        assert response.status_code == 400

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_search_validates_empty_query(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Empty query should return validation error."""
        mock_auth.return_value = Mock()
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        response = authenticated_client.post(
            "/workshop/search",
            data=json.dumps({"query": ""}),
            content_type="application/json",
        )
        assert response.status_code == 400

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_search_returns_tracks(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Valid search should return transformed track list."""
        mock_client = Mock()
        mock_client.search_tracks.return_value = [
            {
                "id": "track1",
                "name": "Yesterday",
                "uri": "spotify:track:track1",
                "duration_ms": 125000,
                "artists": [
                    {
                        "name": "The Beatles",
                        "external_urls": {"spotify": "http://..."},
                    }
                ],
                "album": {
                    "name": "Help!",
                    "images": [{"url": "https://example.com/help.jpg"}],
                },
                "external_urls": {"spotify": "http://..."},
            }
        ]

        mock_auth.return_value = mock_client
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        response = authenticated_client.post(
            "/workshop/search",
            data=json.dumps({"query": "yesterday beatles"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert len(data["tracks"]) == 1
        assert data["tracks"][0]["name"] == "Yesterday"
        assert data["tracks"][0]["uri"] == "spotify:track:track1"
        assert data["tracks"][0]["artists"] == ["The Beatles"]
        assert data["tracks"][0]["album_name"] == "Help!"
        assert data["tracks"][0]["album_image_url"] == "https://example.com/help.jpg"

    @patch("shuffify.is_db_available")
    @patch("shuffify.routes.get_db_user")
    @patch("shuffify.routes.require_auth")
    def test_search_returns_empty_for_no_results(
        self,
        mock_auth,
        mock_get_db_user,
        mock_db_available,
        authenticated_client,
    ):
        """Search with no Spotify results should return empty tracks array."""
        mock_client = Mock()
        mock_client.search_tracks.return_value = []

        mock_auth.return_value = mock_client
        mock_db_available.return_value = True
        mock_user = MagicMock()
        mock_user.id = 1
        mock_get_db_user.return_value = mock_user

        response = authenticated_client.post(
            "/workshop/search",
            data=json.dumps({"query": "xyznonexistent123"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert len(data["tracks"]) == 0
