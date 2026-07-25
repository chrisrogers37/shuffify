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
            result = _fetch_raid_sources_with_limits(
                api=MagicMock(),
                sources=[source],
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
            result = _fetch_raid_sources_with_limits(
                api=MagicMock(),
                sources=[source],
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
            result = _fetch_raid_sources_with_limits(
                api=MagicMock(),
                sources=[source],
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
            result = _fetch_raid_sources_with_limits(
                api=MagicMock(),
                sources=[source],
                exclusion_set=excluded,
                user_id=None,
            )

        assert result == []


class TestLoadSourcesTargetScoping:
    """_load_sources must scope to the target playlist and include all
    source types (SR-001, SR-002)."""

    def test_excludes_other_targets_search_sources(self, db_app):
        """A search source on target A must not be loaded when raiding
        target B -- prevents cross-playlist leakage (SR-002)."""
        from shuffify.services.executors.raid_executor import (
            _load_sources,
        )
        from shuffify.services.user_service import UserService
        from shuffify.models.db import UpstreamSource, db

        with db_app.app_context():
            result = UserService.upsert_from_spotify(
                {"id": "leak_user", "display_name": "L", "images": []}
            )
            uid = result.user.id
            db.session.add_all([
                UpstreamSource(
                    user_id=uid,
                    target_playlist_id="targetA",
                    source_type="search_query",
                    search_query="jazz",
                    raid_count=5,
                ),
                UpstreamSource(
                    user_id=uid,
                    target_playlist_id="targetB",
                    source_type="search_query",
                    search_query="rock",
                    raid_count=5,
                ),
            ])
            db.session.commit()

            loaded = _load_sources([], uid, "targetA")

            queries = [s.search_query for s in loaded]
            assert "jazz" in queries
            assert "rock" not in queries

    def test_includes_search_sources_for_target_with_empty_source_ids(
        self, db_app
    ):
        """A search source (no source_playlist_id) must be loaded for its
        target even when source_ids is empty -- the schedule's playlist-only
        list omits it (SR-001)."""
        from shuffify.services.executors.raid_executor import (
            _load_sources,
        )
        from shuffify.services.user_service import UserService
        from shuffify.models.db import UpstreamSource, db

        with db_app.app_context():
            result = UserService.upsert_from_spotify(
                {"id": "search_user", "display_name": "S", "images": []}
            )
            uid = result.user.id
            db.session.add(
                UpstreamSource(
                    user_id=uid,
                    target_playlist_id="tgt",
                    source_type="search_query",
                    search_query="ambient",
                    raid_count=5,
                )
            )
            db.session.commit()

            loaded = _load_sources([], uid, "tgt")

            assert any(
                s.source_type == "search_query"
                and s.search_query == "ambient"
                for s in loaded
            )
