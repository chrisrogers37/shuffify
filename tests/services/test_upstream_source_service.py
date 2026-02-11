"""
Tests for UpstreamSourceService.

Tests cover add, list, get, delete, and duplicate detection.
"""

import pytest

from shuffify.models.db import db
from shuffify.services.user_service import UserService
from shuffify.services.upstream_source_service import (
    UpstreamSourceService,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
)


@pytest.fixture
def db_app():
    """Create a Flask app with in-memory SQLite for testing."""
    import os

    os.environ["SPOTIFY_CLIENT_ID"] = "test_id"
    os.environ["SPOTIFY_CLIENT_SECRET"] = "test_secret"

    from shuffify import create_app

    app = create_app("development")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


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
