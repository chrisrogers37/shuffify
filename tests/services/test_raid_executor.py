"""
Tests for raid_executor._fetch_raid_sources_with_limits.

Verifies that per-source raid_count limits are applied to the
*fresh* pool (source URIs minus exclusion set), not to the raw
source. Sampling raid_count from the raw source before exclusion
collapses yield to near-zero when the source heavily overlaps the
user's existing catalog — which is the dominant production case
after a few weeks of raiding the same editorial playlist.
"""

from unittest.mock import MagicMock, patch

import pytest

from shuffify.services.executors.raid_executor import (
    _fetch_raid_sources_with_limits,
)
from shuffify.services.source_resolver.base import (
    ResolveAllResult, ResolveResult,
)
from shuffify.models.db import UpstreamSource


def _make_resolve_all(source, uris):
    """Build a ResolveAllResult with one source returning the given URIs."""
    result = ResolveResult(
        track_uris=list(uris),
        pathway_name="public_scraper",
        success=True,
    )
    return ResolveAllResult(
        new_uris=list(uris),
        source_results=[(source, result)],
    )


@pytest.fixture
def patched_resolver_and_tracking():
    """Patch SourceResolver and _update_source_tracking for isolated logic tests."""
    with patch(
        "shuffify.services.executors.raid_executor.SourceResolver"
    ) as resolver_cls, patch(
        "shuffify.services.executors.raid_executor."
        "_update_source_tracking"
    ):
        yield resolver_cls


class TestFetchRaidSourcesWithLimits:
    """raid_count must cap the *fresh* pool, not the raw source."""

    def test_returns_exactly_fresh_pool_when_smaller_than_raid_count(
        self, db_app, patched_resolver_and_tracking,
    ):
        """When fresh pool < raid_count, every fresh URI must be returned.

        Production case: editorial source has 100 URIs, 94 already
        promoted/dismissed, fresh pool = 6, raid_count = 10. Today's run
        should drip all 6, not a random subset of 10 from the full 100.
        """
        source = UpstreamSource(
            source_playlist_id="src1",
            source_type="external",
            raid_count=10,
        )
        source_uris = [f"spotify:track:{i:022d}" for i in range(100)]
        excluded = set(source_uris[:94])
        fresh = set(source_uris[94:])

        resolver_cls = patched_resolver_and_tracking
        resolver_cls.return_value.resolve_all.return_value = (
            _make_resolve_all(source, source_uris)
        )

        with db_app.app_context():
            with patch(
                "shuffify.services.executors.raid_executor._load_sources",
                return_value=[source],
            ):
                result = _fetch_raid_sources_with_limits(
                    api=MagicMock(),
                    source_ids=["src1"],
                    exclusion_set=excluded,
                    user_id=None,
                )

        assert set(result) == fresh, (
            f"Expected all 6 fresh URIs, got {len(result)}: {result}"
        )

    def test_returned_uris_never_overlap_exclusion(
        self, db_app, patched_resolver_and_tracking,
    ):
        """Final invariant: no returned URI may be in the exclusion set."""
        source = UpstreamSource(
            source_playlist_id="src2",
            source_type="external",
            raid_count=5,
        )
        source_uris = [f"spotify:track:{i:022d}" for i in range(20)]
        excluded = set(source_uris[:15])

        resolver_cls = patched_resolver_and_tracking
        resolver_cls.return_value.resolve_all.return_value = (
            _make_resolve_all(source, source_uris)
        )

        with db_app.app_context():
            with patch(
                "shuffify.services.executors.raid_executor._load_sources",
                return_value=[source],
            ):
                result = _fetch_raid_sources_with_limits(
                    api=MagicMock(),
                    source_ids=["src2"],
                    exclusion_set=excluded,
                    user_id=None,
                )

        assert excluded.isdisjoint(result)

    def test_caps_at_raid_count_when_fresh_pool_is_larger(
        self, db_app, patched_resolver_and_tracking,
    ):
        """When fresh pool > raid_count, return exactly raid_count fresh URIs."""
        source = UpstreamSource(
            source_playlist_id="src3",
            source_type="external",
            raid_count=5,
        )
        source_uris = [f"spotify:track:{i:022d}" for i in range(20)]
        excluded = set()  # all fresh

        resolver_cls = patched_resolver_and_tracking
        resolver_cls.return_value.resolve_all.return_value = (
            _make_resolve_all(source, source_uris)
        )

        with db_app.app_context():
            with patch(
                "shuffify.services.executors.raid_executor._load_sources",
                return_value=[source],
            ):
                result = _fetch_raid_sources_with_limits(
                    api=MagicMock(),
                    source_ids=["src3"],
                    exclusion_set=excluded,
                    user_id=None,
                )

        assert len(result) == 5
        assert set(result).issubset(set(source_uris))

    def test_empty_fresh_pool_returns_empty(
        self, db_app, patched_resolver_and_tracking,
    ):
        """When every source URI is excluded, return [] (not crash)."""
        source = UpstreamSource(
            source_playlist_id="src4",
            source_type="external",
            raid_count=5,
        )
        source_uris = [f"spotify:track:{i:022d}" for i in range(10)]
        excluded = set(source_uris)

        resolver_cls = patched_resolver_and_tracking
        resolver_cls.return_value.resolve_all.return_value = (
            _make_resolve_all(source, source_uris)
        )

        with db_app.app_context():
            with patch(
                "shuffify.services.executors.raid_executor._load_sources",
                return_value=[source],
            ):
                result = _fetch_raid_sources_with_limits(
                    api=MagicMock(),
                    source_ids=["src4"],
                    exclusion_set=excluded,
                    user_id=None,
                )

        assert result == []
