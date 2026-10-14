"""
Tests for UpstreamSourceService.

Tests cover add, list, get, delete, duplicate detection,
and source count limits.
"""

from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from shuffify.models.db import UpstreamSource, User, db
from shuffify.services.upstream_source_service import (
    UpstreamSourceError,
    UpstreamSourceLimitError,
    UpstreamSourceNotFoundError,
    UpstreamSourceService,
)
from shuffify.services.user_service import UserService


@pytest.fixture
def app_ctx(db_app):
    """Provide app context with a test user."""
    with db_app.app_context():
        UserService.upsert_from_spotify({
            "id": "user123",
            "display_name": "Test User",
            "images": [],
        })
        yield


class TestUpstreamSourceServiceAdd:
    """Tests for add_source."""

    def test_add_source(self, app_ctx):
        """Should create a new upstream source."""
        source = UpstreamSourceService.add_source(
            spotify_id="user123",
            target_playlist_id="target_1",
            source_playlist_id="source_1",
            source_type="external",
            source_name="Cool Playlist",
        )

        assert source.id is not None
        assert source.source_type == "external"
        assert source.source_name == "Cool Playlist"

    def test_add_duplicate_returns_existing(self, app_ctx):
        """Should return existing source instead of duplicate."""
        source1 = UpstreamSourceService.add_source(
            "user123", "target_1", "source_1"
        )
        source2 = UpstreamSourceService.add_source(
            "user123", "target_1", "source_1"
        )

        assert source1.id == source2.id

    def test_add_source_invalid_type_raises(self, app_ctx):
        """Should reject invalid source_type."""
        with pytest.raises(
            UpstreamSourceError, match="Invalid source_type"
        ):
            UpstreamSourceService.add_source(
                "user123",
                "target",
                "source",
                source_type="invalid",
            )

    def test_add_source_unknown_user_raises(self, app_ctx):
        """Should raise for unknown user."""
        with pytest.raises(
            UpstreamSourceError, match="User not found"
        ):
            UpstreamSourceService.add_source(
                "ghost", "target", "source"
            )

    def test_add_source_with_url(self, app_ctx):
        """Should store source URL."""
        source = UpstreamSourceService.add_source(
            "user123",
            "target",
            "source",
            source_url=(
                "https://open.spotify.com/playlist/source"
            ),
        )
        assert source.source_url == (
            "https://open.spotify.com/playlist/source"
        )


class TestUpstreamSourceServiceList:
    """Tests for list_sources."""

    def test_list_sources(self, app_ctx):
        """Should return sources for user and target."""
        UpstreamSourceService.add_source(
            "user123", "target_1", "src_a"
        )
        UpstreamSourceService.add_source(
            "user123", "target_1", "src_b"
        )

        sources = UpstreamSourceService.list_sources(
            "user123", "target_1"
        )
        assert len(sources) == 2

    def test_list_sources_filters_by_target(self, app_ctx):
        """Should only return sources for the target."""
        UpstreamSourceService.add_source(
            "user123", "target_1", "src_a"
        )
        UpstreamSourceService.add_source(
            "user123", "target_2", "src_b"
        )

        sources = UpstreamSourceService.list_sources(
            "user123", "target_1"
        )
        assert len(sources) == 1

    def test_list_sources_unknown_user_returns_empty(
        self, app_ctx
    ):
        """Should return empty for unknown user."""
        sources = UpstreamSourceService.list_sources(
            "ghost", "target"
        )
        assert sources == []

    def test_list_all_sources_for_user(self, app_ctx):
        """Should return all sources across all targets."""
        UpstreamSourceService.add_source(
            "user123", "t1", "s1"
        )
        UpstreamSourceService.add_source(
            "user123", "t2", "s2"
        )

        all_sources = (
            UpstreamSourceService.list_all_sources_for_user(
                "user123"
            )
        )
        assert len(all_sources) == 2


class TestUpstreamSourceServiceDelete:
    """Tests for delete_source."""

    def test_delete_source(self, app_ctx):
        """Should delete the source."""
        source = UpstreamSourceService.add_source(
            "user123", "target", "source"
        )
        source_id = source.id

        result = UpstreamSourceService.delete_source(
            source_id, "user123"
        )
        assert result is True

        with pytest.raises(UpstreamSourceNotFoundError):
            UpstreamSourceService.get_source(
                source_id, "user123"
            )

    def test_delete_source_wrong_user_raises(self, app_ctx):
        """Should raise when source belongs to another user."""
        source = UpstreamSourceService.add_source(
            "user123", "target", "source"
        )

        UserService.upsert_from_spotify({
            "id": "other",
            "display_name": "Other",
            "images": [],
        })

        with pytest.raises(UpstreamSourceNotFoundError):
            UpstreamSourceService.delete_source(
                source.id, "other"
            )

    def test_delete_nonexistent_raises(self, app_ctx):
        """Should raise for non-existent source."""
        with pytest.raises(UpstreamSourceNotFoundError):
            UpstreamSourceService.delete_source(
                99999, "user123"
            )


class TestUpstreamSourceServiceLimits:
    """Tests for source count limits."""

    def test_count_sources(self, app_ctx):
        """Should count sources per target."""
        UpstreamSourceService.add_source(
            "user123", "target_1", "src_a"
        )
        UpstreamSourceService.add_source(
            "user123", "target_1", "src_b"
        )
        count = UpstreamSourceService.count_sources(
            "user123", "target_1"
        )
        assert count == 2

    def test_count_sources_empty(self, app_ctx):
        """Should return 0 for no sources."""
        count = UpstreamSourceService.count_sources(
            "user123", "target_1"
        )
        assert count == 0

    def test_add_source_at_limit_raises(self, app_ctx):
        """Should raise UpstreamSourceLimitError at limit."""
        limit = (
            UpstreamSourceService.MAX_SOURCES_PER_TARGET
        )
        for i in range(limit):
            UpstreamSourceService.add_source(
                "user123",
                "target_1",
                f"src_{i}",
            )

        with pytest.raises(UpstreamSourceLimitError):
            UpstreamSourceService.add_source(
                "user123",
                "target_1",
                f"src_{limit}",
            )

    def test_limit_per_target_not_global(self, app_ctx):
        """Limit applies per target, not globally."""
        limit = (
            UpstreamSourceService.MAX_SOURCES_PER_TARGET
        )
        for i in range(limit):
            UpstreamSourceService.add_source(
                "user123",
                "target_1",
                f"src_{i}",
            )

        # Different target should work
        source = UpstreamSourceService.add_source(
            "user123",
            "target_2",
            "src_0",
        )
        assert source.id is not None

    def test_duplicate_at_limit_returns_existing(
        self, app_ctx
    ):
        """Duplicate at limit returns existing, no error."""
        limit = (
            UpstreamSourceService.MAX_SOURCES_PER_TARGET
        )
        for i in range(limit):
            UpstreamSourceService.add_source(
                "user123",
                "target_1",
                f"src_{i}",
            )

        # Re-adding existing source should return it
        existing = UpstreamSourceService.add_source(
            "user123",
            "target_1",
            "src_0",
        )
        assert existing is not None


class TestUpstreamSourceServiceLocking:
    """The suite runs on SQLite, which drops FOR UPDATE, so these compile
    what the service sends for PostgreSQL, where production runs."""

    @pytest.fixture
    def postgres_sql(self, app_ctx):
        """Every ORM statement the session executes, as PostgreSQL SQL."""
        statements = []

        def capture(state):
            statements.append(str(state.statement.compile(dialect=postgresql.dialect())))

        event.listen(db.session, "do_orm_execute", capture)
        yield statements
        event.remove(db.session, "do_orm_execute", capture)

    @pytest.mark.parametrize("add", [
        lambda: UpstreamSourceService.add_source("user123", "target_1", "src_a"),
        lambda: UpstreamSourceService.add_search_source("user123", "target_1", "new releases"),
    ], ids=["playlist", "search"])
    def test_adding_a_source_locks_the_user_not_an_aggregate(self, postgres_sql, add):
        """PostgreSQL refuses FOR UPDATE with an aggregate, so the lock is
        taken on the users row, never on a count()."""
        add()
        locked = [sql for sql in postgres_sql if "FOR NO KEY UPDATE" in sql or "FOR UPDATE" in sql]
        assert locked and all("count(" not in sql.lower() for sql in locked), locked
        lock = next((i for i, sql in enumerate(postgres_sql) if "FROM users" in sql and sql in locked), None)
        assert lock is not None, f"the users row is not locked: {postgres_sql}"
        # NO KEY UPDATE, so inserts that only reference the user (activity
        # log rows) do not wait for it.
        assert "FOR NO KEY UPDATE" in postgres_sql[lock], postgres_sql[lock]
        # Before the duplicate check and the count, or they could race.
        first_read = next((i for i, sql in enumerate(postgres_sql) if "FROM upstream_sources" in sql), None)
        assert first_read is not None and lock < first_read, postgres_sql


class TestStagedWrites:
    """commit=False flushes, lets errors propagate and leaves the transaction to the caller."""

    def test_staged_additions_flush_without_committing(self, app_ctx):
        with patch("shuffify.services.base.safe_commit") as commit:
            source = UpstreamSourceService.add_source("user123", "t1", "s1", source_type="own", commit=False)
            search = UpstreamSourceService.add_search_source("user123", "t1", "lofi", commit=False)
        commit.assert_not_called()
        assert source.id is not None and search.id is not None
        assert UpstreamSourceService.count_sources("user123", "t1") == 2
        db.session.rollback()
        assert UpstreamSourceService.count_sources("user123", "t1") == 0

    def test_staged_count_and_delete_flush_without_committing(self, app_ctx):
        user = User.query.filter_by(spotify_id="user123").one()
        source = UpstreamSourceService.add_source("user123", "t1", "s1")
        with patch("shuffify.services.base.safe_commit") as commit:
            staged = UpstreamSourceService.update_raid_count(user.id, "t1", source.id, 9, commit=False)
            assert staged.raid_count == 9
            assert UpstreamSourceService.delete_source(source.id, "user123", "t1", commit=False) is True
        commit.assert_not_called()
        assert UpstreamSourceService.count_sources("user123", "t1") == 0
        db.session.rollback()
        assert db.session.get(UpstreamSource, source.id).raid_count == 5

    def test_a_staged_limit_error_propagates_without_rolling_back(self, app_ctx):
        for index in range(10):
            UpstreamSourceService.add_source("user123", "t1", f"s{index}", commit=False)
        with pytest.raises(UpstreamSourceLimitError):
            UpstreamSourceService.add_search_source("user123", "t1", "one too many", commit=False)
        assert UpstreamSourceService.count_sources("user123", "t1") == 10
        db.session.rollback()
        assert UpstreamSourceService.count_sources("user123", "t1") == 0
