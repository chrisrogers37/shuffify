"""Tests for SourceResolver."""

import pytest
from unittest.mock import Mock

from shuffify.services.source_resolver.base import (
    ResolveResult,
)
from shuffify.services.source_resolver.resolver import SourceResolver


@pytest.fixture
def mock_api():
    return Mock()


def _make_source(source_type="external", playlist_id="pl_1"):
    source = Mock()
    source.source_type = source_type
    source.source_playlist_id = playlist_id
    return source


def _make_pathway(
    name="test", can_handle=True, result=None
):
    pathway = Mock()
    pathway.name = name
    pathway.can_handle.return_value = can_handle
    if result is None:
        result = ResolveResult(
            track_uris=["spotify:track:a"],
            pathway_name=name,
            success=True,
        )
    pathway.resolve.return_value = result
    return pathway


class TestResolve:
    """Tests for SourceResolver.resolve()."""

    def test_uses_first_successful_pathway(self, mock_api):
        p1 = _make_pathway(name="p1", result=ResolveResult(
            track_uris=["spotify:track:a"],
            pathway_name="p1",
            success=True,
        ))
        p2 = _make_pathway(name="p2")
        resolver = SourceResolver(pathways=[p1, p2])
        source = _make_source()

        result = resolver.resolve(source, api=mock_api)
        assert result.pathway_name == "p1"
        p2.resolve.assert_not_called()

    def test_falls_through_on_failure(self, mock_api):
        p1 = _make_pathway(name="p1", result=ResolveResult(
            track_uris=[],
            pathway_name="p1",
            success=False,
        ))
        p2 = _make_pathway(name="p2", result=ResolveResult(
            track_uris=["spotify:track:b"],
            pathway_name="p2",
            success=True,
        ))
        resolver = SourceResolver(pathways=[p1, p2])
        source = _make_source()

        result = resolver.resolve(source, api=mock_api)
        assert result.pathway_name == "p2"
        assert result.success is True

    def test_accepts_partial_results(self, mock_api):
        p1 = _make_pathway(name="p1", result=ResolveResult(
            track_uris=["spotify:track:x"],
            pathway_name="p1",
            success=False,
            partial=True,
        ))
        resolver = SourceResolver(pathways=[p1])
        source = _make_source()

        result = resolver.resolve(source, api=mock_api)
        assert result.partial is True
        assert len(result.track_uris) == 1

    def test_skips_pathways_that_cant_handle(self, mock_api):
        p1 = _make_pathway(name="p1", can_handle=False)
        p2 = _make_pathway(name="p2")
        resolver = SourceResolver(pathways=[p1, p2])
        source = _make_source()

        result = resolver.resolve(source, api=mock_api)
        assert result.pathway_name == "p2"
        p1.resolve.assert_not_called()

    def test_all_pathways_exhausted(self, mock_api):
        p1 = _make_pathway(name="p1", result=ResolveResult(
            track_uris=[],
            pathway_name="p1",
            success=False,
        ))
        resolver = SourceResolver(pathways=[p1])
        source = _make_source()

        result = resolver.resolve(source, api=mock_api)
        assert result.success is False
        assert result.pathway_name == "none"
        assert "exhausted" in result.error_message

    def test_no_pathways(self, mock_api):
        resolver = SourceResolver(pathways=[])
        source = _make_source()
        result = resolver.resolve(source, api=mock_api)
        assert result.success is False


class TestResolveAll:
    """Tests for SourceResolver.resolve_all()."""

    def test_resolves_multiple_sources(self, mock_api):
        pathway = _make_pathway(name="p1")
        pathway.resolve.side_effect = [
            ResolveResult(
                track_uris=["spotify:track:a", "spotify:track:b"],
                pathway_name="p1", success=True,
            ),
            ResolveResult(
                track_uris=["spotify:track:c"],
                pathway_name="p1", success=True,
            ),
        ]
        resolver = SourceResolver(pathways=[pathway])
        sources = [_make_source(playlist_id="p1"),
                    _make_source(playlist_id="p2")]

        result = resolver.resolve_all(sources, api=mock_api)
        assert result.new_uris == [
            "spotify:track:a",
            "spotify:track:b",
            "spotify:track:c",
        ]
        assert len(result.source_results) == 2

    def test_deduplicates_against_exclude(self, mock_api):
        pathway = _make_pathway(name="p1", result=ResolveResult(
            track_uris=["spotify:track:a", "spotify:track:b"],
            pathway_name="p1", success=True,
        ))
        resolver = SourceResolver(pathways=[pathway])
        source = _make_source()

        result = resolver.resolve_all(
            [source], api=mock_api,
            exclude_uris={"spotify:track:a"},
        )
        assert result.new_uris == ["spotify:track:b"]

    def test_deduplicates_across_sources(self, mock_api):
        pathway = _make_pathway(name="p1")
        pathway.resolve.side_effect = [
            ResolveResult(
                track_uris=["spotify:track:a", "spotify:track:b"],
                pathway_name="p1", success=True,
            ),
            ResolveResult(
                track_uris=["spotify:track:b", "spotify:track:c"],
                pathway_name="p1", success=True,
            ),
        ]
        resolver = SourceResolver(pathways=[pathway])
        sources = [_make_source(playlist_id="p1"),
                    _make_source(playlist_id="p2")]

        result = resolver.resolve_all(sources, api=mock_api)
        assert result.new_uris == [
            "spotify:track:a",
            "spotify:track:b",
            "spotify:track:c",
        ]

    def test_empty_sources(self, mock_api):
        resolver = SourceResolver(pathways=[])
        result = resolver.resolve_all([], api=mock_api)
        assert result.new_uris == []
        assert result.source_results == []

    def test_returns_per_source_results(self, mock_api):
        pathway = _make_pathway(name="p1")
        pathway.resolve.side_effect = [
            ResolveResult(
                track_uris=["spotify:track:a"],
                pathway_name="p1", success=True,
            ),
            ResolveResult(
                track_uris=[],
                pathway_name="p1", success=False,
            ),
        ]
        resolver = SourceResolver(pathways=[pathway])
        s1 = _make_source(playlist_id="p1")
        s2 = _make_source(playlist_id="p2")

        result = resolver.resolve_all([s1, s2], api=mock_api)
        assert len(result.source_results) == 2
        assert result.source_results[0][1].success is True
        assert result.source_results[1][1].success is False


class TestDefaultPathways:
    """Tests for default pathway configuration."""

    def test_includes_direct_api(self):
        resolver = SourceResolver()
        names = [p.name for p in resolver._pathways]
        assert "direct_api" in names
