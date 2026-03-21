"""Tests for PublicScraperPathway.

Tests cover all extraction strategies:
1. __NEXT_DATA__ JSON parsing (modern Spotify pages)
2. trackList script block parsing (embed player format)
3. Regex fallback (legacy/unknown page formats)
Plus caching, error handling, and integration with resolve().
"""

import pytest
from unittest.mock import Mock, patch

from shuffify.services.source_resolver.public_scraper_pathway import (
    PublicScraperPathway,
    _extract_uris,
    _extract_from_next_data,
    _extract_from_track_list,
    _extract_with_regex,
    _get_track_uri_from_item,
    _walk_json_for_tracks,
    _find_key,
    _try_parse_json,
)


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def pathway():
    return PublicScraperPathway()


@pytest.fixture
def pathway_with_cache():
    return PublicScraperPathway()


@pytest.fixture
def mock_source():
    source = Mock()
    source.source_playlist_id = "pl_test123"
    source.source_type = "external"
    return source


# ======================================================================
# Mock HTML fixtures — realistic Spotify page structures
# ======================================================================

# Modern Spotify embed with __NEXT_DATA__ containing tracks.items[]
NEXT_DATA_TRACKS_ITEMS_HTML = """
<html>
<head><title>Spotify Embed</title></head>
<body>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "state": {
        "data": {
          "entity": {
            "name": "Test Playlist",
            "tracks": {
              "totalCount": 3,
              "items": [
                {
                  "track": {
                    "uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa",
                    "name": "Song A",
                    "id": "aaaaaaaaaaaaaaaaaaaaaa"
                  }
                },
                {
                  "track": {
                    "uri": "spotify:track:bbbbbbbbbbbbbbbbbbbbbb",
                    "name": "Song B",
                    "id": "bbbbbbbbbbbbbbbbbbbbbb"
                  }
                },
                {
                  "track": {
                    "uri": "spotify:track:cccccccccccccccccccccc",
                    "name": "Song C",
                    "id": "cccccccccccccccccccccc"
                  }
                }
              ]
            }
          }
        }
      }
    }
  }
}
</script>
</body>
</html>
"""

# __NEXT_DATA__ with trackList format (embed-style)
NEXT_DATA_TRACK_LIST_HTML = """
<html>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "state": {
        "data": {
          "entity": {
            "trackList": [
              {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa", "uid": "a1"},
              {"uri": "spotify:track:bbbbbbbbbbbbbbbbbbbbbb", "uid": "b1"}
            ]
          }
        }
      }
    }
  }
}
</script>
</html>
"""

# Embed page with trackList in a regular script tag (not __NEXT_DATA__)
SCRIPT_TRACK_LIST_HTML = """
<html>
<script>
{
  "trackList": [
    {"uri": "spotify:track:dddddddddddddddddddddd", "uid": "d1"},
    {"uri": "spotify:track:eeeeeeeeeeeeeeeeeeeeee", "uid": "e1"}
  ],
  "name": "Embedded Playlist"
}
</script>
</html>
"""

# Flat items format (no nested "track" key)
NEXT_DATA_FLAT_ITEMS_HTML = """
<html>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "data": {
        "tracks": {
          "items": [
            {
              "uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa",
              "name": "Flat Song A"
            },
            {
              "uri": "spotify:track:bbbbbbbbbbbbbbbbbbbbbb",
              "name": "Flat Song B"
            }
          ]
        }
      }
    }
  }
}
</script>
</html>
"""

# ID-only format (no URIs, just track IDs)
NEXT_DATA_ID_ONLY_HTML = """
<html>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "state": {
        "data": {
          "entity": {
            "tracks": {
              "items": [
                {"track": {"id": "aaaaaaaaaaaaaaaaaaaaaa", "name": "ID Song"}}
              ]
            }
          }
        }
      }
    }
  }
}
</script>
</html>
"""

# Legacy HTML with only regex-extractable patterns
LEGACY_URI_HTML = """
<html>
<script>
{"tracks":[
  {"uri":"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"},
  {"uri":"spotify:track:bbbbbbbbbbbbbbbbbbbbbb"}
]}
</script>
</html>
"""

LEGACY_URL_HTML = """
<html>
<a href="/track/cccccccccccccccccccccc">Song C</a>
<a href="/track/dddddddddddddddddddddd">Song D</a>
</html>
"""

MIXED_LEGACY_HTML = """
<script>{"uri":"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}</script>
<a href="/track/bbbbbbbbbbbbbbbbbbbbbb">Song B</a>
<a href="/track/aaaaaaaaaaaaaaaaaaaaaa">Song A dup</a>
"""

# Malformed __NEXT_DATA__
MALFORMED_NEXT_DATA_HTML = """
<html>
<script id="__NEXT_DATA__" type="application/json">
{this is not valid json!!!}
</script>
<script>{"uri":"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}</script>
</html>
"""

# __NEXT_DATA__ with no track data — should fall through
NEXT_DATA_NO_TRACKS_HTML = """
<html>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "state": {
        "data": {
          "entity": {
            "name": "Empty Playlist",
            "description": "No tracks here"
          }
        }
      }
    }
  }
}
</script>
<script>{"uri":"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}</script>
</html>
"""


# ======================================================================
# Tests: _extract_from_next_data
# ======================================================================


class TestExtractFromNextData:
    """Tests for __NEXT_DATA__ JSON extraction."""

    def test_tracks_items_nested_format(self):
        """tracks.items[].track.uri pattern (most common)."""
        result = _extract_from_next_data(NEXT_DATA_TRACKS_ITEMS_HTML)
        assert len(result) == 3
        assert "spotify:track:aaaaaaaaaaaaaaaaaaaaaa" in result
        assert "spotify:track:bbbbbbbbbbbbbbbbbbbbbb" in result
        assert "spotify:track:cccccccccccccccccccccc" in result

    def test_track_list_format(self):
        """trackList[].uri pattern (embed-style)."""
        result = _extract_from_next_data(NEXT_DATA_TRACK_LIST_HTML)
        assert len(result) == 2
        assert "spotify:track:aaaaaaaaaaaaaaaaaaaaaa" in result
        assert "spotify:track:bbbbbbbbbbbbbbbbbbbbbb" in result

    def test_flat_items_format(self):
        """items[].uri pattern (no nested track object)."""
        result = _extract_from_next_data(NEXT_DATA_FLAT_ITEMS_HTML)
        assert len(result) == 2
        assert "spotify:track:aaaaaaaaaaaaaaaaaaaaaa" in result
        assert "spotify:track:bbbbbbbbbbbbbbbbbbbbbb" in result

    def test_id_only_format(self):
        """items[].track.id pattern (constructs URI from ID)."""
        result = _extract_from_next_data(NEXT_DATA_ID_ONLY_HTML)
        assert len(result) == 1
        assert result[0] == "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"

    def test_no_next_data_tag(self):
        """Returns empty when __NEXT_DATA__ tag is absent."""
        assert _extract_from_next_data("<html></html>") == []

    def test_malformed_json(self):
        """Returns empty when __NEXT_DATA__ contains invalid JSON."""
        assert _extract_from_next_data(MALFORMED_NEXT_DATA_HTML) == []

    def test_no_tracks_in_data(self):
        """Returns empty when __NEXT_DATA__ exists but has no tracks."""
        result = _extract_from_next_data(NEXT_DATA_NO_TRACKS_HTML)
        assert result == []

    def test_deduplicates_uris(self):
        """Same URI appearing multiple times is only returned once."""
        html = """
        <script id="__NEXT_DATA__" type="application/json">
        {
          "props": {
            "pageProps": {
              "state": {
                "data": {
                  "entity": {
                    "tracks": {
                      "items": [
                        {"track": {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}},
                        {"track": {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}}
                      ]
                    }
                  }
                }
              }
            }
          }
        }
        </script>
        """
        result = _extract_from_next_data(html)
        assert len(result) == 1


# ======================================================================
# Tests: _extract_from_track_list
# ======================================================================


class TestExtractFromTrackList:
    """Tests for trackList extraction from script blocks."""

    def test_basic_track_list(self):
        result = _extract_from_track_list(SCRIPT_TRACK_LIST_HTML)
        assert len(result) == 2
        assert "spotify:track:dddddddddddddddddddddd" in result
        assert "spotify:track:eeeeeeeeeeeeeeeeeeeeee" in result

    def test_no_track_list_in_scripts(self):
        html = '<script>{"name":"no tracks here"}</script>'
        assert _extract_from_track_list(html) == []

    def test_invalid_json_in_script(self):
        html = '<script>trackList is not JSON</script>'
        assert _extract_from_track_list(html) == []

    def test_track_list_not_a_list(self):
        html = '<script>{"trackList": "not a list"}</script>'
        assert _extract_from_track_list(html) == []

    def test_multiple_script_blocks(self):
        """Finds trackList even if it's not in the first script."""
        html = """
        <script>{"analytics": true}</script>
        <script>{"trackList": [
            {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}
        ]}</script>
        """
        result = _extract_from_track_list(html)
        assert len(result) == 1

    def test_nested_track_list(self):
        """trackList nested inside another object."""
        html = """
        <script>{"data": {"entity": {"trackList": [
            {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}
        ]}}}</script>
        """
        result = _extract_from_track_list(html)
        assert len(result) == 1

    def test_deduplicates_across_scripts(self):
        """Same track in multiple scripts is only counted once."""
        html = """
        <script>{"trackList": [
            {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}
        ]}</script>
        <script>{"trackList": [
            {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}
        ]}</script>
        """
        result = _extract_from_track_list(html)
        assert len(result) == 1


# ======================================================================
# Tests: _extract_with_regex (fallback)
# ======================================================================


class TestExtractWithRegex:
    """Tests for regex-based fallback extraction."""

    def test_uri_pattern(self):
        html = '"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"'
        result = _extract_with_regex(html)
        assert result == ["spotify:track:aaaaaaaaaaaaaaaaaaaaaa"]

    def test_url_pattern(self):
        html = '<a href="/track/bbbbbbbbbbbbbbbbbbbbbb">Song</a>'
        result = _extract_with_regex(html)
        assert result == ["spotify:track:bbbbbbbbbbbbbbbbbbbbbb"]

    def test_both_patterns(self):
        result = _extract_with_regex(MIXED_LEGACY_HTML)
        assert len(result) == 2
        assert "spotify:track:aaaaaaaaaaaaaaaaaaaaaa" in result
        assert "spotify:track:bbbbbbbbbbbbbbbbbbbbbb" in result

    def test_deduplicates(self):
        html = (
            '"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"'
            '"spotify:track:aaaaaaaaaaaaaaaaaaaaaa"'
        )
        result = _extract_with_regex(html)
        assert len(result) == 1

    def test_deduplicates_across_patterns(self):
        result = _extract_with_regex(MIXED_LEGACY_HTML)
        uris = [
            u for u in result
            if u == "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"
        ]
        assert len(uris) == 1

    def test_empty_html(self):
        assert _extract_with_regex("") == []
        assert _extract_with_regex("<html></html>") == []

    def test_ignores_short_ids(self):
        html = '"spotify:track:short"'
        assert _extract_with_regex(html) == []

    def test_ignores_non_track_uris(self):
        html = '"spotify:album:aaaaaaaaaaaaaaaaaaaaaa"'
        assert _extract_with_regex(html) == []


# ======================================================================
# Tests: _extract_uris (orchestrator)
# ======================================================================


class TestExtractUris:
    """Tests for the main _extract_uris orchestrator."""

    def test_prefers_next_data(self):
        """__NEXT_DATA__ is used when available."""
        result = _extract_uris(NEXT_DATA_TRACKS_ITEMS_HTML)
        assert len(result) == 3

    def test_falls_to_track_list(self):
        """trackList is used when __NEXT_DATA__ absent."""
        result = _extract_uris(SCRIPT_TRACK_LIST_HTML)
        assert len(result) == 2

    def test_falls_to_regex(self):
        """Regex used when structured JSON unavailable."""
        result = _extract_uris(LEGACY_URI_HTML)
        assert len(result) == 2

    def test_malformed_next_data_falls_to_regex(self):
        """Malformed __NEXT_DATA__ falls through to regex."""
        result = _extract_uris(MALFORMED_NEXT_DATA_HTML)
        assert len(result) == 1
        assert result[0] == "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"

    def test_empty_next_data_falls_through(self):
        """__NEXT_DATA__ with no tracks falls to regex."""
        result = _extract_uris(NEXT_DATA_NO_TRACKS_HTML)
        # Should find the URI via regex in the second script tag
        assert len(result) == 1
        assert result[0] == "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"

    def test_empty_html(self):
        assert _extract_uris("") == []

    def test_url_only_extraction(self):
        result = _extract_uris(LEGACY_URL_HTML)
        assert len(result) == 2


# ======================================================================
# Tests: Helper functions
# ======================================================================


class TestGetTrackUriFromItem:
    """Tests for _get_track_uri_from_item."""

    def test_nested_track_uri(self):
        item = {"track": {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}}
        assert (
            _get_track_uri_from_item(item)
            == "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"
        )

    def test_flat_uri(self):
        item = {"uri": "spotify:track:bbbbbbbbbbbbbbbbbbbbbb"}
        assert (
            _get_track_uri_from_item(item)
            == "spotify:track:bbbbbbbbbbbbbbbbbbbbbb"
        )

    def test_id_only_nested(self):
        item = {"track": {"id": "aaaaaaaaaaaaaaaaaaaaaa"}}
        assert (
            _get_track_uri_from_item(item)
            == "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"
        )

    def test_id_only_flat(self):
        item = {"id": "bbbbbbbbbbbbbbbbbbbbbb"}
        assert (
            _get_track_uri_from_item(item)
            == "spotify:track:bbbbbbbbbbbbbbbbbbbbbb"
        )

    def test_non_track_uri_ignored(self):
        item = {"uri": "spotify:album:aaaaaaaaaaaaaaaaaaaaaa"}
        assert _get_track_uri_from_item(item) is None

    def test_non_dict_returns_none(self):
        assert _get_track_uri_from_item("not a dict") is None
        assert _get_track_uri_from_item(42) is None
        assert _get_track_uri_from_item(None) is None

    def test_empty_dict(self):
        assert _get_track_uri_from_item({}) is None

    def test_short_id_ignored(self):
        item = {"id": "short"}
        assert _get_track_uri_from_item(item) is None

    def test_prefers_uri_over_id(self):
        """When both URI and ID present, URI wins."""
        item = {
            "track": {
                "uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa",
                "id": "bbbbbbbbbbbbbbbbbbbbbb",
            }
        }
        result = _get_track_uri_from_item(item)
        assert result == "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"


class TestWalkJsonForTracks:
    """Tests for _walk_json_for_tracks."""

    def test_deeply_nested_tracks(self):
        data = {
            "a": {
                "b": {
                    "c": {
                        "tracks": {
                            "items": [
                                {"track": {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}}
                            ]
                        }
                    }
                }
            }
        }
        result = _walk_json_for_tracks(data)
        assert len(result) == 1

    def test_empty_structure(self):
        assert _walk_json_for_tracks({}) == []
        assert _walk_json_for_tracks({"data": {}}) == []

    def test_handles_lists(self):
        data = [{"tracks": {"items": [
            {"uri": "spotify:track:aaaaaaaaaaaaaaaaaaaaaa"}
        ]}}]
        result = _walk_json_for_tracks(data)
        assert len(result) == 1

    def test_depth_guard(self):
        """Does not crash on deeply nested structures."""
        data = {"a": None}
        node = data
        for _ in range(50):
            node["a"] = {"a": None}
            node = node["a"]
        # Should not raise, just return empty
        result = _walk_json_for_tracks(data)
        assert result == []


class TestFindKey:
    """Tests for _find_key."""

    def test_top_level(self):
        assert _find_key({"x": 42}, "x") == 42

    def test_nested(self):
        assert _find_key({"a": {"b": {"x": 99}}}, "x") == 99

    def test_in_list(self):
        assert _find_key([{"x": 1}], "x") == 1

    def test_missing(self):
        assert _find_key({"a": 1}, "x") is None

    def test_none_value(self):
        """Returns None for missing key, not for key with None value."""
        # _find_key returns None both for missing keys and None values
        assert _find_key({"x": None}, "x") is None


class TestTryParseJson:
    """Tests for _try_parse_json."""

    def test_valid_dict(self):
        assert _try_parse_json('{"a": 1}') == {"a": 1}

    def test_valid_non_dict(self):
        """Lists and other types return None."""
        assert _try_parse_json("[1, 2, 3]") is None

    def test_invalid_json(self):
        assert _try_parse_json("not json") is None

    def test_empty_string(self):
        assert _try_parse_json("") is None


# ======================================================================
# Tests: can_handle
# ======================================================================


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


# ======================================================================
# Tests: resolve (integration)
# ======================================================================


class TestResolve:
    """Tests for PublicScraperPathway.resolve()."""

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_embed_success_with_next_data(
        self, mock_get, pathway, mock_source
    ):
        resp = Mock(
            status_code=200, text=NEXT_DATA_TRACKS_ITEMS_HTML
        )
        mock_get.return_value = resp

        result = pathway.resolve(mock_source)
        assert result.success is True
        assert result.pathway_name == "public_scraper"
        assert len(result.track_uris) == 3
        assert mock_get.call_count == 1

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_embed_success_with_track_list(
        self, mock_get, pathway, mock_source
    ):
        resp = Mock(
            status_code=200, text=SCRIPT_TRACK_LIST_HTML
        )
        mock_get.return_value = resp

        result = pathway.resolve(mock_source)
        assert result.success is True
        assert len(result.track_uris) == 2
        assert mock_get.call_count == 1

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_embed_success_with_regex_fallback(
        self, mock_get, pathway, mock_source
    ):
        resp = Mock(
            status_code=200, text=LEGACY_URI_HTML
        )
        mock_get.return_value = resp

        result = pathway.resolve(mock_source)
        assert result.success is True
        assert len(result.track_uris) == 2

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_embed_fails_falls_to_public_page(
        self, mock_get, pathway, mock_source
    ):
        embed_resp = Mock(status_code=404, text="")
        page_resp = Mock(
            status_code=200, text=LEGACY_URL_HTML
        )
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

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_browser_headers_sent(
        self, mock_get, pathway, mock_source
    ):
        """Verify browser-like headers are used for requests."""
        mock_get.return_value = Mock(
            status_code=200, text=NEXT_DATA_TRACKS_ITEMS_HTML
        )

        pathway.resolve(mock_source)

        call_kwargs = mock_get.call_args
        headers = call_kwargs.kwargs.get(
            "headers", call_kwargs[1].get("headers", {})
        )
        assert "Mozilla" in headers.get("User-Agent", "")
        assert "Accept" in headers


# ======================================================================
# Tests: Caching
# ======================================================================


class TestCaching:
    """Tests for database cache integration."""

    def test_cache_hit_returns_cached(
        self, mock_source, db_app
    ):
        """Pre-populated cache row is returned directly."""
        from datetime import datetime, timedelta, timezone
        from shuffify.models.db import (
            ScrapedPlaylistCache, db,
        )

        with db_app.app_context():
            now = datetime.now(timezone.utc)
            row = ScrapedPlaylistCache(
                playlist_id="pl_test123",
                track_count=2,
                scraped_at=now,
                scrape_pathway="embed",
                expires_at=now + timedelta(hours=1),
            )
            row.track_uris = [
                "spotify:track:aaaaaaaaaaaaaaaaaaaaaa",
                "spotify:track:bbbbbbbbbbbbbbbbbbbbbb",
            ]
            db.session.add(row)
            db.session.commit()

            pathway = PublicScraperPathway()
            result = pathway.resolve(mock_source)
            assert result.success is True
            assert len(result.track_uris) == 2

    def test_cache_hit_empty_returns_failure(
        self, mock_source, db_app
    ):
        """Cached empty result returns failure."""
        from datetime import datetime, timedelta, timezone
        from shuffify.models.db import (
            ScrapedPlaylistCache, db,
        )

        with db_app.app_context():
            now = datetime.now(timezone.utc)
            row = ScrapedPlaylistCache(
                playlist_id="pl_test123",
                track_count=0,
                scraped_at=now,
                scrape_pathway="none",
                expires_at=now + timedelta(hours=1),
            )
            row.track_uris = []
            db.session.add(row)
            db.session.commit()

            pathway = PublicScraperPathway()
            result = pathway.resolve(mock_source)
            assert result.success is False

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_cache_miss_fetches_and_stores(
        self, mock_get, mock_source, db_app
    ):
        """Cache miss triggers scraping and stores result."""
        from shuffify.models.db import (
            ScrapedPlaylistCache,
        )

        with db_app.app_context():
            mock_get.return_value = Mock(
                status_code=200,
                text=NEXT_DATA_TRACKS_ITEMS_HTML,
            )

            pathway = PublicScraperPathway()
            result = pathway.resolve(mock_source)
            assert result.success is True

            # Verify cache row was created
            row = ScrapedPlaylistCache.query.filter_by(
                playlist_id="pl_test123"
            ).first()
            assert row is not None
            assert row.track_count > 0
            assert row.scrape_pathway == "embed"

    def test_cache_read_error_returns_none(self, db_app):
        """DB errors in _get_cached return None gracefully."""
        with db_app.app_context():
            with patch(
                "shuffify.models.db"
                ".ScrapedPlaylistCache.query"
            ) as mock_q:
                mock_q.filter.side_effect = Exception(
                    "DB down"
                )
                result = PublicScraperPathway._get_cached(
                    "pl_test123"
                )
                assert result is None

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_expired_cache_triggers_rescrape(
        self, mock_get, mock_source, db_app
    ):
        """Expired cache row is ignored; fresh scrape runs."""
        from datetime import datetime, timedelta, timezone
        from shuffify.models.db import (
            ScrapedPlaylistCache, db,
        )

        with db_app.app_context():
            now = datetime.now(timezone.utc)
            row = ScrapedPlaylistCache(
                playlist_id="pl_test123",
                track_count=1,
                scraped_at=now - timedelta(hours=2),
                scrape_pathway="embed",
                expires_at=now - timedelta(hours=1),
            )
            row.track_uris = [
                "spotify:track:oldoldoldoldoldoldoldold",
            ]
            db.session.add(row)
            db.session.commit()

            mock_get.return_value = Mock(
                status_code=200,
                text=NEXT_DATA_TRACKS_ITEMS_HTML,
            )

            pathway = PublicScraperPathway()
            result = pathway.resolve(mock_source)
            assert result.success is True
            # Should have new tracks, not the old cached one
            assert (
                "spotify:track:oldoldoldoldoldoldoldold"
                not in result.track_uris
            )

    @patch(
        "shuffify.services.source_resolver"
        ".public_scraper_pathway.requests.get"
    )
    def test_failed_scrape_caches_empty(
        self, mock_get, mock_source, db_app
    ):
        """Both strategies failing still caches empty."""
        from shuffify.models.db import (
            ScrapedPlaylistCache,
        )

        with db_app.app_context():
            mock_get.return_value = Mock(
                status_code=200, text="<html></html>"
            )

            pathway = PublicScraperPathway()
            result = pathway.resolve(mock_source)
            assert result.success is False

            # Verify empty was cached
            row = ScrapedPlaylistCache.query.filter_by(
                playlist_id="pl_test123"
            ).first()
            assert row is not None
            assert row.track_count == 0
