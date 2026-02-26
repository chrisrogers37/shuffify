"""
Tests for ActivityLogService.

Tests cover log creation, query methods, error handling,
and the non-blocking guarantee.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from shuffify.models.db import db, ActivityLog, User
from shuffify.services.activity_log_service import (
    ActivityLogService,
)
from shuffify.enums import ActivityType


@pytest.fixture
def app_ctx(db_app):
    """Provide app context with a test user."""
    with db_app.app_context():
        user = User(
            spotify_id="activity_test_user",
            display_name="Activity Test User",
        )
        db.session.add(user)
        db.session.commit()
        yield user


class TestActivityLogServiceLog:
    """Tests for the log() method."""

    def test_log_basic_activity(self, app_ctx):
        """Should create an activity log entry."""
        user = app_ctx
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Shuffled 'My Playlist'",
        )

        assert result is not None
        assert result.activity_type == "shuffle"
        assert (
            result.description == "Shuffled 'My Playlist'"
        )
        assert result.user_id == user.id

    def test_log_with_playlist_context(self, app_ctx):
        """Should store playlist ID and name."""
        user = app_ctx
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=(
                ActivityType.WORKSHOP_COMMIT
            ),
            description="Committed changes",
            playlist_id="pl_123",
            playlist_name="My Playlist",
        )

        assert result.playlist_id == "pl_123"
        assert result.playlist_name == "My Playlist"

    def test_log_with_metadata(self, app_ctx):
        """Should store JSON metadata."""
        user = app_ctx
        meta = {
            "algorithm": "basic",
            "track_count": 42,
        }
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Shuffled",
            metadata=meta,
        )

        assert result.metadata_json == meta

    def test_log_truncates_long_description(
        self, app_ctx
    ):
        """Should truncate descriptions over 500 chars."""
        user = app_ctx
        long_desc = "x" * 600
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description=long_desc,
        )

        assert len(result.description) == 500

    def test_log_returns_none_on_db_error(self, app_ctx):
        """Should return None (not raise) on DB error."""
        with patch.object(
            db.session,
            "commit",
            side_effect=Exception("DB error"),
        ):
            result = ActivityLogService.log(
                user_id=app_ctx.id,
                activity_type=ActivityType.SHUFFLE,
                description="Should not crash",
            )
            assert result is None

    def test_log_sets_created_at(self, app_ctx):
        """Should auto-set created_at to UTC now."""
        user = app_ctx
        before = datetime.now(timezone.utc).replace(
            tzinfo=None
        )
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.LOGIN,
            description="Logged in",
        )
        after = datetime.now(timezone.utc).replace(
            tzinfo=None
        )

        assert result is not None
        assert result.created_at >= before
        assert result.created_at <= after

    def test_log_all_activity_types(self, app_ctx):
        """Should accept every ActivityType value."""
        user = app_ctx
        for at in ActivityType:
            result = ActivityLogService.log(
                user_id=user.id,
                activity_type=at,
                description=f"Testing {at.value}",
            )
            assert result is not None

    def test_log_nullable_fields(self, app_ctx):
        """Should allow None for optional fields."""
        user = app_ctx
        result = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.LOGOUT,
            description="Logged out",
            playlist_id=None,
            playlist_name=None,
            metadata=None,
        )

        assert result is not None
        assert result.playlist_id is None
        assert result.playlist_name is None
        assert result.metadata_json is None


class TestActivityLogServiceGetRecent:
    """Tests for the get_recent() method."""

    def test_get_recent_empty(self, app_ctx):
        """Should return empty list when no activities."""
        result = ActivityLogService.get_recent(
            app_ctx.id
        )
        assert result == []

    def test_get_recent_returns_ordered(self, app_ctx):
        """Should return most recent first."""
        user = app_ctx
        for i in range(5):
            ActivityLogService.log(
                user_id=user.id,
                activity_type=ActivityType.SHUFFLE,
                description=f"Shuffle {i}",
            )

        results = ActivityLogService.get_recent(user.id)
        assert len(results) == 5
        assert results[0].description == "Shuffle 4"

    def test_get_recent_respects_limit(self, app_ctx):
        """Should return at most 'limit' entries."""
        user = app_ctx
        for i in range(10):
            ActivityLogService.log(
                user_id=user.id,
                activity_type=ActivityType.SHUFFLE,
                description=f"Shuffle {i}",
            )

        results = ActivityLogService.get_recent(
            user.id, limit=3
        )
        assert len(results) == 3

    def test_get_recent_filters_by_type(self, app_ctx):
        """Should filter by activity_type."""
        user = app_ctx
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="A shuffle",
        )
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.LOGIN,
            description="A login",
        )
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Another shuffle",
        )

        results = ActivityLogService.get_recent(
            user.id,
            activity_type=ActivityType.SHUFFLE,
        )
        assert len(results) == 2
        assert all(
            r.activity_type == "shuffle"
            for r in results
        )

    def test_get_recent_isolates_users(self, app_ctx):
        """Should only return activities for user."""
        user1 = app_ctx
        user2 = User(
            spotify_id="other_activity_user",
            display_name="Other",
        )
        db.session.add(user2)
        db.session.commit()

        ActivityLogService.log(
            user_id=user1.id,
            activity_type=ActivityType.SHUFFLE,
            description="User 1 shuffle",
        )
        ActivityLogService.log(
            user_id=user2.id,
            activity_type=ActivityType.SHUFFLE,
            description="User 2 shuffle",
        )

        results = ActivityLogService.get_recent(
            user1.id
        )
        assert len(results) == 1
        assert (
            results[0].description == "User 1 shuffle"
        )

    def test_get_recent_nonexistent_user(self, app_ctx):
        """Should return empty for unknown user."""
        results = ActivityLogService.get_recent(99999)
        assert results == []


class TestActivityLogServiceGetActivitySince:
    """Tests for the get_activity_since() method."""

    def test_get_activity_since_filters_by_date(
        self, app_ctx
    ):
        """Should return activities after given date."""
        user = app_ctx
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Recent shuffle",
        )

        since = datetime.now(
            timezone.utc
        ) - timedelta(hours=1)
        results = ActivityLogService.get_activity_since(
            user.id, since
        )
        assert len(results) >= 1

    def test_get_activity_since_future_returns_empty(
        self, app_ctx
    ):
        """Should return empty when since is future."""
        user = app_ctx
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Past shuffle",
        )

        future = datetime.now(
            timezone.utc
        ) + timedelta(hours=1)
        results = ActivityLogService.get_activity_since(
            user.id, future
        )
        assert len(results) == 0


class TestActivityLogServiceGetActivitySummary:
    """Tests for the get_activity_summary() method."""

    def test_get_summary_counts_by_type(self, app_ctx):
        """Should return counts grouped by type."""
        user = app_ctx
        for _ in range(3):
            ActivityLogService.log(
                user_id=user.id,
                activity_type=ActivityType.SHUFFLE,
                description="Shuffle",
            )
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.LOGIN,
            description="Login",
        )

        summary = (
            ActivityLogService.get_activity_summary(
                user.id
            )
        )
        assert summary.get("shuffle") == 3
        assert summary.get("login") == 1

    def test_get_summary_empty_user(self, app_ctx):
        """Should return empty dict for no activity."""
        summary = (
            ActivityLogService.get_activity_summary(
                99999
            )
        )
        assert summary == {}

    def test_get_summary_respects_days_param(
        self, app_ctx
    ):
        """Should count activities within day range."""
        user = app_ctx
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Recent",
        )

        summary = (
            ActivityLogService.get_activity_summary(
                user.id, days=30
            )
        )
        assert summary.get("shuffle", 0) >= 1

    def test_get_summary_returns_empty_on_error(
        self, app_ctx
    ):
        """Should return empty dict on database error."""
        with patch.object(
            db.session,
            "query",
            side_effect=Exception("DB fail"),
        ):
            summary = (
                ActivityLogService.get_activity_summary(
                    app_ctx.id
                )
            )
            assert summary == {}


class TestActivityLogModel:
    """Tests for the ActivityLog model itself."""

    def test_to_dict(self, app_ctx):
        """Should serialize to dict correctly."""
        user = app_ctx
        activity = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Test shuffle",
            playlist_id="pl_1",
            playlist_name="Test Playlist",
            metadata={"key": "value"},
        )

        d = activity.to_dict()
        assert d["activity_type"] == "shuffle"
        assert d["description"] == "Test shuffle"
        assert d["playlist_id"] == "pl_1"
        assert d["playlist_name"] == "Test Playlist"
        assert d["metadata"] == {"key": "value"}
        assert "created_at" in d
        assert "id" in d

    def test_repr(self, app_ctx):
        """Should have a useful repr."""
        user = app_ctx
        activity = ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Test",
        )

        r = repr(activity)
        assert "ActivityLog" in r
        assert "shuffle" in r

    def test_user_relationship(self, app_ctx):
        """Should be accessible via user.activities."""
        user = app_ctx
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Test",
        )

        refreshed = db.session.get(User, user.id)
        assert refreshed.activities.count() == 1

    def test_cascade_delete(self, app_ctx):
        """Activities deleted when user is deleted."""
        user = app_ctx
        ActivityLogService.log(
            user_id=user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Test",
        )
        assert (
            ActivityLog.query.filter_by(
                user_id=user.id
            ).count()
            == 1
        )

        db.session.delete(user)
        db.session.commit()

        assert (
            ActivityLog.query.filter_by(
                user_id=user.id
            ).count()
            == 0
        )
