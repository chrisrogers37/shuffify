"""Tests for DirectAPIPathway."""

import pytest
from unittest.mock import Mock

from shuffify.services.source_resolver.direct_api_pathway import (
    DirectAPIPathway,
)
from shuffify.spotify.exceptions import SpotifyNotFoundError


@pytest.fixture
def pathway():
    return DirectAPIPathway()


@pytest.fixture
def mock_source():
    source = Mock()
    source.source_playlist_id = "pl_abc123"
    source.source_type = "external"
    return source


@pytest.fixture
def mock_api():
    api = Mock()
    api.get_playlist_tracks.return_value = [
        {"uri": f"spotify:track:track{i}", "name": f"Track {i}"}
        for i in range(5)
    ]
    return api


class TestCanHandle:
    """Tests for DirectAPIPathway.can_handle()."""

    def test_handles_own(self, pathway):
        source = Mock(source_type="own")
        assert pathway.can_handle(source) is True

    def test_handles_external(self, pathway):
        source = Mock(source_type="external")
        assert pathway.can_handle(source) is True

    def test_rejects_search_query(self, pathway):
        source = Mock(source_type="search_query")
        assert pathway.can_handle(source) is False

    def test_rejects_unknown(self, pathway):
        source = Mock(source_type="other")
        assert pathway.can_handle(source) is False


class TestResolve:
    """Tests for DirectAPIPathway.resolve()."""

    def test_happy_path(self, pathway, mock_source, mock_api):
        result = pathway.resolve(mock_source, api=mock_api)
        assert result.success is True
        assert result.pathway_name == "direct_api"
        assert len(result.track_uris) == 5
        assert result.track_uris[0] == "spotify:track:track0"

    def test_empty_response_returns_failure(
        self, pathway, mock_source, mock_api
    ):
        """Empty tracks means Feb 2026 restriction — not an error."""
        mock_api.get_playlist_tracks.return_value = []
        result = pathway.resolve(mock_source, api=mock_api)
        assert result.success is False
        assert result.track_uris == []
        assert result.error_message is None

    def test_not_found_raises_through(
        self, pathway, mock_source, mock_api
    ):
        mock_api.get_playlist_tracks.side_effect = (
            SpotifyNotFoundError("Not found")
        )
        with pytest.raises(SpotifyNotFoundError):
            pathway.resolve(mock_source, api=mock_api)

    def test_api_error_returns_failure(
        self, pathway, mock_source, mock_api
    ):
        mock_api.get_playlist_tracks.side_effect = Exception(
            "Connection error"
        )
        result = pathway.resolve(mock_source, api=mock_api)
        assert result.success is False
        assert "Connection error" in result.error_message

    def test_no_api_returns_failure(self, pathway, mock_source):
        result = pathway.resolve(mock_source, api=None)
        assert result.success is False
        assert "No API client" in result.error_message

    def test_no_playlist_id_returns_failure(self, pathway, mock_api):
        source = Mock(source_playlist_id=None, source_type="external")
        result = pathway.resolve(source, api=mock_api)
        assert result.success is False
        assert "No playlist ID" in result.error_message

    def test_skips_tracks_without_uri(
        self, pathway, mock_source, mock_api
    ):
        mock_api.get_playlist_tracks.return_value = [
            {"uri": "spotify:track:a", "name": "A"},
            {"name": "Local track"},  # No URI
            {"uri": "spotify:track:b", "name": "B"},
        ]
        result = pathway.resolve(mock_source, api=mock_api)
        assert result.success is True
        assert result.track_uris == [
            "spotify:track:a",
            "spotify:track:b",
        ]

    def test_name_property(self, pathway):
        assert pathway.name == "direct_api"
