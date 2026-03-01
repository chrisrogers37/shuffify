"""Tests for PublicScraperPathway."""

import json

import pytest
from unittest.mock import Mock, patch, MagicMock

from shuffify.services.source_resolver.public_scraper_pathway import (
    PublicScraperPathway,
    _extract_uris,
    CACHE_PREFIX,
    CACHE_TTL,
)


@pytest.fixture
def pathway():
    return PublicScraperPathway(redis_client=None)


@pytest.fixture
def pathway_with_cache():
    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    return PublicScraperPathway(redis_client=mock_redis)


@pytest.fixture
def mock_source():
    source = Mock()
    source.source_playlist_id = "pl_test123"
    source.source_type = "external"
    return source


SAMPLE_EMBED_HTML = """
<html>
<script>
{"tracks":[
  {"uri":"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"},
  {"uri":"spotify:track:bbbbbbbbbbbbbbbbbbbbbb"}
]}
</script>
</html>
"""

SAMPLE_PAGE_HTML = """
<html>
<a href="/track/cccccccccccccccccccccc">Song C</a>
<a href="/track/dddddddddddddddddddddd">Song D</a>
</html>
"""

MIXED_HTML = """
<script>{"uri":"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}</script>
<a href="/track/bbbbbbbbbbbbbbbbbbbbbb">Song B</a>
<a href="/track/aaaaaaaaaaaaaaaaaaaaaa">Song A dup</a>
"""


class TestExtractUris:
    """Tests for _extract_uris() helper."""

    def test_uri_pattern(self):
        html = '"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"'
        result = _extract_uris(html)
        assert result == ["spotify:track:aaaaaaaaaaaaaaaaaaaaaa"]

    def test_url_pattern(self):
        html = '<a href="/track/bbbbbbbbbbbbbbbbbbbbbb">Song</a>'
        result = _extract_uris(html)
        assert result == ["spotify:track:bbbbbbbbbbbbbbbbbbbbbb"]

    def test_both_patterns(self):
        result = _extract_uris(MIXED_HTML)
        assert len(result) == 2
        assert "spotify:track:aaaaaaaaaaaaaaaaaaaaaa" in result
        assert "spotify:track:bbbbbbbbbbbbbbbbbbbbbb" in result

    def test_deduplicates(self):
        html = (
            '"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"'
            '"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"'
        )
        result = _extract_uris(html)
        assert len(result) == 1

    def test_deduplicates_across_patterns(self):
        """URI pattern and URL pattern for same track."""
        result = _extract_uris(MIXED_HTML)
        uris = [
            u for u in result
            if u == "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"
        ]
        assert len(uris) == 1

    def test_empty_html(self):
        assert _extract_uris("") == []
        assert _extract_uris("<html></html>") == []

    def test_ignores_short_ids(self):
        html = '"spotify:track:short"'
        assert _extract_uris(html) == []

    def test_ignores_non_track_uris(self):
        html = '"spotify:album:aaaaaaaaaaaaaaaaaaaaaa"'
        assert _extract_uris(html) == []


class TestCanHandle:
    """Tests for PublicScraperPathway.can_handle()."""

    def test_handles_external(self, pathway):
        source = Mock(source_type="external")
        assert pathway.can_handle(source) is True

    def test_handles_own(self, pathway):
        source = Mock(source_type="own")
        assert pathway.can_handle(source) is True

    def test_rejects_search_query(self, pathway):
        source = Mock(source_type="search_query")
        assert pathway.can_handle(source) is False


class TestResolve:
    """Tests for PublicScraperPathway.resolve()."""

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_embed_success(
        self, mock_get, pathway, mock_source
    ):
        resp = Mock(status_code=200, text=SAMPLE_EMBED_HTML)
        mock_get.return_value = resp

        result = pathway.resolve(mock_source)
        assert result.success is True
        assert result.pathway_name == "public_scraper"
        assert len(result.track_uris) == 2
        # Only one request needed (embed worked)
        assert mock_get.call_count == 1

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_embed_fails_falls_to_public_page(
        self, mock_get, pathway, mock_source
    ):
        embed_resp = Mock(status_code=404, text="")
        page_resp = Mock(status_code=200, text=SAMPLE_PAGE_HTML)
        mock_get.side_effect = [embed_resp, page_resp]

        result = pathway.resolve(mock_source)
        assert result.success is True
        assert mock_get.call_count == 2

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_both_strategies_fail(
        self, mock_get, pathway, mock_source
    ):
        mock_get.return_value = Mock(
            status_code=200, text="<html></html>"
        )

        result = pathway.resolve(mock_source)
        assert result.success is False
        assert "no tracks" in result.error_message.lower()

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_http_error(self, mock_get, pathway, mock_source):
        mock_get.side_effect = Exception("Connection refused")
        result = pathway.resolve(mock_source)
        assert result.success is False

    def test_no_playlist_id(self, pathway):
        source = Mock(
            source_playlist_id=None, source_type="external"
        )
        result = pathway.resolve(source)
        assert result.success is False
        assert "No playlist ID" in result.error_message

    def test_name_property(self, pathway):
        assert pathway.name == "public_scraper"


class TestCaching:
    """Tests for Redis cache integration."""

    def test_cache_hit_returns_cached(self, mock_source):
        mock_redis = MagicMock()
        cached_uris = [
            "spotify:track:aaaaaaaaaaaaaaaaaaaaaa",
            "spotify:track:bbbbbbbbbbbbbbbbbbbbbb",
        ]
        mock_redis.get.return_value = json.dumps(
            cached_uris
        ).encode()
        pathway = PublicScraperPathway(redis_client=mock_redis)

        result = pathway.resolve(mock_source)
        assert result.success is True
        assert result.track_uris == cached_uris

    def test_cache_hit_empty_returns_failure(self, mock_source):
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps([]).encode()
        pathway = PublicScraperPathway(redis_client=mock_redis)

        result = pathway.resolve(mock_source)
        assert result.success is False

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_cache_miss_fetches_and_stores(
        self, mock_get, mock_source
    ):
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        pathway = PublicScraperPathway(redis_client=mock_redis)

        mock_get.return_value = Mock(
            status_code=200, text=SAMPLE_EMBED_HTML
        )

        result = pathway.resolve(mock_source)
        assert result.success is True

        # Verify cache was set
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][0] == f"{CACHE_PREFIX}pl_test123"
        assert call_args[0][1] == CACHE_TTL

    def test_cache_error_falls_through(self, mock_source):
        """Redis errors should not prevent scraping."""
        mock_redis = MagicMock()
        mock_redis.get.side_effect = Exception("Redis down")
        pathway = PublicScraperPathway(redis_client=mock_redis)

        with patch(
            "shuffify.services.source_resolver"
            ".public_scraper_pathway.requests.get"
        ) as mock_get:
            mock_get.return_value = Mock(
                status_code=200, text=SAMPLE_EMBED_HTML
            )
            result = pathway.resolve(mock_source)
            assert result.success is True

    def test_no_redis_skips_cache(self, mock_source):
        """Without Redis, scraping always happens."""
        pathway = PublicScraperPathway(redis_client=None)

        with patch(
            "shuffify.services.source_resolver"
            ".public_scraper_pathway.requests.get"
        ) as mock_get:
            mock_get.return_value = Mock(
                status_code=200, text=SAMPLE_EMBED_HTML
            )
            result = pathway.resolve(mock_source)
            assert result.success is True
