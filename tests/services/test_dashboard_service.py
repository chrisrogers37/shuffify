"""
Tests for DashboardService data aggregation.

These tests require a Flask app context with SQLAlchemy configured.
"""

import pytest
from datetime import datetime, timezone, timedelta

from shuffify.services.dashboard_service import (
    DashboardService,
    DashboardError,  # noqa: F401
)


@pytest.fixture
def db_user(app_context):
    """Create a test user in the database."""
    from shuffify.models.db import (
        db,
        User,
        Schedule,
        JobExecution,
        ActivityLog,
    )

    # Clean up
    ActivityLog.query.delete()
    JobExecution.query.delete()
    Schedule.query.delete()
    User.query.delete()
    db.session.commit()

    user = User(
        spotify_id="dashboard_test_user",
        display_name="Dashboard Tester",
    )
    db.session.add(user)
    db.session.commit()
    yield user
    # Cleanup
    ActivityLog.query.delete()
    JobExecution.query.delete()
    Schedule.query.delete()
    User.query.delete()
    db.session.commit()


@pytest.fixture
def sample_schedule(db_user, app_context):
    """Create a sample schedule for the test user."""
    from shuffify.services.scheduler_service import (
        SchedulerService,
    )

    return SchedulerService.create_schedule(
        user_id=db_user.id,
        job_type="shuffle",
        target_playlist_id="pl_dash_test",
        target_playlist_name="Dashboard Test Playlist",
        schedule_type="interval",
        schedule_value="daily",
        algorithm_name="BasicShuffle",
    )


@pytest.fixture
def sample_activities(db_user, app_context):
    """Create sample activity log entries."""
    from shuffify.services.activity_log_service import (
        ActivityLogService,
    )
    from shuffify.enums import ActivityType

    activities = []
    activities.append(
        ActivityLogService.log(
            user_id=db_user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Shuffled 'My Playlist'",
            playlist_id="pl_1",
            playlist_name="My Playlist",
        )
    )
    activities.append(
        ActivityLogService.log(
            user_id=db_user.id,
            activity_type=ActivityType.SHUFFLE,
            description="Shuffled 'Another Playlist'",
            playlist_id="pl_2",
            playlist_name="Another Playlist",
        )
    )
    activities.append(
        ActivityLogService.log(
            user_id=db_user.id,
            activity_type=ActivityType.SCHEDULE_CREATE,
            description="Created schedule for 'Test'",
        )
    )
    return activities


class TestGetDashboardData:
    """Tests for the main get_dashboard_data method."""

    def test_returns_expected_keys(
        self, db_user, app_context
    ):
        """Should return dict with all expected keys."""
        data = DashboardService.get_dashboard_data(
            db_user.id
        )
        assert "is_returning_user" in data
        assert "recent_activity" in data
        assert "activity_since_last_login" in data
        assert "activity_since_last_login_count" in data
        assert "quick_stats" in data
        assert "active_schedules" in data
        assert "recent_job_executions" in data

    def test_new_user_not_returning(
        self, db_user, app_context
    ):
        """Should mark as not returning when no last_login_at."""
        data = DashboardService.get_dashboard_data(
            db_user.id, last_login_at=None
        )
        assert data["is_returning_user"] is False
        assert data["activity_since_last_login"] == []
        assert data["activity_since_last_login_count"] == 0

    def test_returning_user_flagged(
        self, db_user, app_context
    ):
        """Should mark as returning when last_login_at given."""
        last_login = datetime.now(
            timezone.utc
        ) - timedelta(hours=1)
        data = DashboardService.get_dashboard_data(
            db_user.id, last_login_at=last_login
        )
        assert data["is_returning_user"] is True

    def test_activity_since_last_login_populated(
        self,
        db_user,
        sample_activities,
        app_context,
    ):
        """Should include activities since last login."""
        last_login = datetime.now(
            timezone.utc
        ) - timedelta(hours=1)
        data = DashboardService.get_dashboard_data(
            db_user.id, last_login_at=last_login
        )
        assert data["activity_since_last_login_count"] > 0
        assert len(data["activity_since_last_login"]) > 0


class TestQuickStats:
    """Tests for quick stats calculation."""

    def test_empty_stats_for_new_user(
        self, db_user, app_context
    ):
        """Should return zeroed stats with no activity."""
        stats = DashboardService._get_quick_stats(
            db_user.id
        )
        assert stats["total_shuffles"] == 0
        assert stats["total_scheduled_runs"] == 0
        assert stats["total_snapshots"] == 0
        assert stats["active_schedule_count"] == 0

    def test_active_schedule_count(
        self, db_user, sample_schedule, app_context
    ):
        """Should count active schedules."""
        stats = DashboardService._get_quick_stats(
            db_user.id
        )
        assert stats["active_schedule_count"] == 1

    def test_shuffle_count(
        self,
        db_user,
        sample_activities,
        app_context,
    ):
        """Should count shuffle activities."""
        stats = DashboardService._get_quick_stats(
            db_user.id
        )
        assert stats["total_shuffles"] == 2


class TestActiveSchedules:
    """Tests for active schedule retrieval."""

    def test_empty_when_no_schedules(
        self, db_user, app_context
    ):
        """Should return empty list with no schedules."""
        result = DashboardService._get_active_schedules(
            db_user.id
        )
        assert result == []

    def test_returns_active_schedules(
        self, db_user, sample_schedule, app_context
    ):
        """Should return active schedules as dicts."""
        result = DashboardService._get_active_schedules(
            db_user.id
        )
        assert len(result) == 1
        assert result[0]["target_playlist_name"] == (
            "Dashboard Test Playlist"
        )

    def test_excludes_disabled_schedules(
        self, db_user, sample_schedule, app_context
    ):
        """Should not include disabled schedules."""
        from shuffify.services.scheduler_service import (
            SchedulerService,
        )

        SchedulerService.toggle_schedule(
            sample_schedule.id, db_user.id
        )
        result = DashboardService._get_active_schedules(
            db_user.id
        )
        assert len(result) == 0


class TestRecentActivity:
    """Tests for recent activity retrieval."""

    def test_empty_when_no_activity(
        self, db_user, app_context
    ):
        """Should return empty list with no activity."""
        result = DashboardService._get_recent_activity(
            db_user.id
        )
        assert isinstance(result, list)
        assert len(result) == 0

    def test_returns_activity_dicts(
        self,
        db_user,
        sample_activities,
        app_context,
    ):
        """Should return activity as dicts with expected keys."""
        result = DashboardService._get_recent_activity(
            db_user.id
        )
        assert len(result) == 3
        assert "activity_type" in result[0]
        assert "description" in result[0]
        assert "created_at" in result[0]

    def test_respects_limit(
        self,
        db_user,
        sample_activities,
        app_context,
    ):
        """Should respect the limit parameter."""
        result = DashboardService._get_recent_activity(
            db_user.id, limit=1
        )
        assert len(result) == 1

    def test_ordered_by_created_at_desc(
        self,
        db_user,
        sample_activities,
        app_context,
    ):
        """Should return most recent activity first."""
        result = DashboardService._get_recent_activity(
            db_user.id
        )
        assert len(result) >= 2
        # Most recent should be first
        first_ts = result[0]["created_at"]
        second_ts = result[1]["created_at"]
        assert first_ts >= second_ts


class TestActivitySince:
    """Tests for activity-since-timestamp retrieval."""

    def test_filters_by_timestamp(
        self,
        db_user,
        sample_activities,
        app_context,
    ):
        """Should return activities after the timestamp."""
        # All activities were just created, so a cutoff
        # 1 hour ago should include them all
        since = datetime.now(
            timezone.utc
        ) - timedelta(hours=1)
        result = DashboardService._get_activity_since(
            db_user.id, since
        )
        assert len(result) == 3

    def test_future_cutoff_returns_empty(
        self,
        db_user,
        sample_activities,
        app_context,
    ):
        """Should return empty for a future cutoff."""
        since = datetime.now(
            timezone.utc
        ) + timedelta(hours=1)
        result = DashboardService._get_activity_since(
            db_user.id, since
        )
        assert len(result) == 0


class TestRecentExecutions:
    """Tests for recent job execution retrieval."""

    def test_empty_when_no_executions(
        self, db_user, app_context
    ):
        """Should return empty list with no executions."""
        result = (
            DashboardService._get_recent_executions(
                db_user.id
            )
        )
        assert result == []

    def test_returns_executions_with_schedule_name(
        self, db_user, sample_schedule, app_context
    ):
        """Should include schedule_name in execution dicts."""
        from shuffify.models.db import db, JobExecution

        execution = JobExecution(
            schedule_id=sample_schedule.id,
            status="success",
            tracks_added=5,
            tracks_total=50,
        )
        db.session.add(execution)
        db.session.commit()

        result = (
            DashboardService._get_recent_executions(
                db_user.id
            )
        )
        assert len(result) == 1
        assert result[0]["schedule_name"] == (
            "Dashboard Test Playlist"
        )
        assert result[0]["job_type"] == "shuffle"

    def test_respects_limit(
        self, db_user, sample_schedule, app_context
    ):
        """Should respect the limit parameter."""
        from shuffify.models.db import db, JobExecution

        for i in range(5):
            execution = JobExecution(
                schedule_id=sample_schedule.id,
                status="success",
                tracks_added=i,
                tracks_total=50,
            )
            db.session.add(execution)
        db.session.commit()

        result = (
            DashboardService._get_recent_executions(
                db_user.id, limit=2
            )
        )
        assert len(result) == 2


class TestEmptyStats:
    """Tests for the _empty_stats helper."""

    def test_all_values_zero(self):
        """Should return dict with all zero values."""
        stats = DashboardService._empty_stats()
        assert all(v == 0 for v in stats.values())
        assert "total_shuffles" in stats
        assert "total_scheduled_runs" in stats
        assert "total_snapshots" in stats
        assert "active_schedule_count" in stats


class TestGracefulDegradation:
    """Tests that dashboard degrades gracefully on errors."""

    def test_nonexistent_user_returns_defaults(
        self, app_context
    ):
        """Should return valid data for nonexistent user."""
        data = DashboardService.get_dashboard_data(
            user_id=999999
        )
        assert isinstance(data, dict)
        assert data["is_returning_user"] is False
        assert data["recent_activity"] == []
        assert data["quick_stats"]["total_shuffles"] == 0
