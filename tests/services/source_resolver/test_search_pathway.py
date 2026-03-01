"""Tests for SearchPathway."""

import pytest
from unittest.mock import Mock

from shuffify.services.source_resolver.search_pathway import (
    SearchPathway,
    MAX_PAGES,
    PAGE_SIZE,
)


@pytest.fixture
def pathway():
    return SearchPathway()


@pytest.fixture
def mock_source():
    source = Mock()
    source.source_type = "search_query"
    source.search_query = "indie folk 2024"
    return source


@pytest.fixture
def mock_api():
    api = Mock()
    api.search_tracks.return_value = [
        {"uri": f"spotify:track:s{i}", "name": f"Track {i}"}
        for i in range(PAGE_SIZE)
    ]
    return api


class TestCanHandle:
    """Tests for SearchPathway.can_handle()."""

    def test_handles_search_query(self, pathway):
        source = Mock(source_type="search_query")
        assert pathway.can_handle(source) is True

    def test_rejects_own(self, pathway):
        source = Mock(source_type="own")
        assert pathway.can_handle(source) is False

    def test_rejects_external(self, pathway):
        source = Mock(source_type="external")
        assert pathway.can_handle(source) is False


class TestResolve:
    """Tests for SearchPathway.resolve()."""

    def test_happy_path(self, pathway, mock_source, mock_api):
        result = pathway.resolve(mock_source, api=mock_api)
        assert result.success is True
        assert result.partial is True
        assert result.pathway_name == "search"
        assert len(result.track_uris) == PAGE_SIZE

    def test_paginates_two_pages(self, pathway, mock_source, mock_api):
        page1 = [
            {"uri": f"spotify:track:a{i}", "name": f"T{i}"}
            for i in range(PAGE_SIZE)
        ]
        page2 = [
            {"uri": f"spotify:track:b{i}", "name": f"T{i}"}
            for i in range(PAGE_SIZE)
        ]
        mock_api.search_tracks.side_effect = [page1, page2]

        result = pathway.resolve(mock_source, api=mock_api)
        assert len(result.track_uris) == PAGE_SIZE * 2
        assert mock_api.search_tracks.call_count == MAX_PAGES

    def test_stops_on_empty_page(self, pathway, mock_source, mock_api):
        page1 = [
            {"uri": f"spotify:track:a{i}", "name": f"T{i}"}
            for i in range(3)
        ]
        mock_api.search_tracks.side_effect = [page1, []]

        result = pathway.resolve(mock_source, api=mock_api)
        assert len(result.track_uris) == 3
        assert result.success is True

    def test_no_query_returns_failure(self, pathway, mock_api):
        source = Mock(source_type="search_query", search_query=None)
        result = pathway.resolve(source, api=mock_api)
        assert result.success is False
        assert "No search query" in result.error_message

    def test_empty_query_returns_failure(self, pathway, mock_api):
        source = Mock(source_type="search_query", search_query="")
        result = pathway.resolve(source, api=mock_api)
        assert result.success is False

    def test_no_api_returns_failure(self, pathway, mock_source):
        result = pathway.resolve(mock_source, api=None)
        assert result.success is False
        assert "No API client" in result.error_message

    def test_empty_results(self, pathway, mock_source, mock_api):
        mock_api.search_tracks.return_value = []
        result = pathway.resolve(mock_source, api=mock_api)
        assert result.success is False
        assert result.track_uris == []

    def test_deduplicates_within_pages(
        self, pathway, mock_source, mock_api
    ):
        tracks = [
            {"uri": "spotify:track:dup", "name": "Same"},
            {"uri": "spotify:track:dup", "name": "Same"},
            {"uri": "spotify:track:other", "name": "Other"},
        ]
        mock_api.search_tracks.side_effect = [tracks, []]
        result = pathway.resolve(mock_source, api=mock_api)
        assert result.track_uris == [
            "spotify:track:dup",
            "spotify:track:other",
        ]

    def test_api_error_with_partial(
        self, pathway, mock_source, mock_api
    ):
        """If first page succeeds but second fails, return partial."""
        page1 = [
            {"uri": f"spotify:track:a{i}", "name": f"T{i}"}
            for i in range(PAGE_SIZE)
        ]
        mock_api.search_tracks.side_effect = [
            page1,
            Exception("Rate limited"),
        ]
        result = pathway.resolve(mock_source, api=mock_api)
        assert result.partial is True
        assert len(result.track_uris) == PAGE_SIZE

    def test_api_error_no_results(
        self, pathway, mock_source, mock_api
    ):
        mock_api.search_tracks.side_effect = Exception("Timeout")
        result = pathway.resolve(mock_source, api=mock_api)
        assert result.success is False
        assert result.partial is False
        assert "Timeout" in result.error_message

    def test_name_property(self, pathway):
        assert pathway.name == "search"
