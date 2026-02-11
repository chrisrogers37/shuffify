"""
Tests for SchedulerService CRUD operations.

These tests require a Flask app context with SQLAlchemy configured.
"""

import pytest

from shuffify.services.scheduler_service import (
    SchedulerService,
    ScheduleNotFoundError,
    ScheduleLimitError,
)


@pytest.fixture
def db_user(app_context):
    """Create a test user in the database."""
    from shuffify.models.db import db, User, Schedule
    from shuffify.models.db import JobExecution

    # Clean up any leftover data from previous tests
    JobExecution.query.delete()
    Schedule.query.delete()
    User.query.delete()
    db.session.commit()

    user = User(
        spotify_id="test_user_123",
        display_name="Test User",
    )
    db.session.add(user)
    db.session.commit()
    yield user
    # Cleanup
    JobExecution.query.delete()
    Schedule.query.delete()
    User.query.delete()
    db.session.commit()


@pytest.fixture
def sample_schedule(db_user, app_context):
    """Create a sample schedule in the database."""
    schedule = SchedulerService.create_schedule(
        user_id=db_user.id,
        job_type="shuffle",
        target_playlist_id="playlist_abc",
        target_playlist_name="My Playlist",
        schedule_type="interval",
        schedule_value="daily",
        algorithm_name="BasicShuffle",
        algorithm_params={"keep_first": 0},
    )
    return schedule


class TestSchedulerServiceCreate:
    """Tests for creating schedules."""

    def test_create_schedule_success(
        self, db_user, app_context
    ):
        """Should create a schedule and return it."""
        schedule = SchedulerService.create_schedule(
            user_id=db_user.id,
            job_type="raid",
            target_playlist_id="pl_123",
            target_playlist_name="Test Playlist",
            schedule_type="interval",
            schedule_value="weekly",
            source_playlist_ids=[
                "source_1",
                "source_2",
            ],
        )
        assert schedule.id is not None
        assert schedule.job_type == "raid"
        assert schedule.is_enabled is True
        assert len(schedule.source_playlist_ids) == 2

    def test_create_schedule_limit_enforced(
        self, db_user, app_context
    ):
        """Should raise ScheduleLimitError at limit."""
        for i in range(
            SchedulerService.MAX_SCHEDULES_PER_USER
        ):
            SchedulerService.create_schedule(
                user_id=db_user.id,
                job_type="shuffle",
                target_playlist_id=f"pl_{i}",
                target_playlist_name=f"Playlist {i}",
                schedule_type="interval",
                schedule_value="daily",
                algorithm_name="BasicShuffle",
            )

        with pytest.raises(ScheduleLimitError):
            SchedulerService.create_schedule(
                user_id=db_user.id,
                job_type="shuffle",
                target_playlist_id="pl_extra",
                target_playlist_name="Extra Playlist",
                schedule_type="interval",
                schedule_value="daily",
                algorithm_name="BasicShuffle",
            )


class TestSchedulerServiceRead:
    """Tests for reading schedules."""

    def test_get_user_schedules_empty(
        self, db_user, app_context
    ):
        """Should return empty list for no schedules."""
        schedules = SchedulerService.get_user_schedules(
            db_user.id
        )
        assert schedules == []

    def test_get_user_schedules_returns_all(
        self, db_user, sample_schedule, app_context
    ):
        """Should return all schedules for the user."""
        schedules = SchedulerService.get_user_schedules(
            db_user.id
        )
        assert len(schedules) == 1
        assert schedules[0].id == sample_schedule.id

    def test_get_schedule_by_id(
        self, db_user, sample_schedule, app_context
    ):
        """Should return specific schedule by ID."""
        schedule = SchedulerService.get_schedule(
            sample_schedule.id, db_user.id
        )
        assert schedule.id == sample_schedule.id

    def test_get_schedule_wrong_user_raises(
        self, db_user, sample_schedule, app_context
    ):
        """Should raise for wrong user."""
        with pytest.raises(ScheduleNotFoundError):
            SchedulerService.get_schedule(
                sample_schedule.id, user_id=99999
            )

    def test_get_schedule_nonexistent_raises(
        self, db_user, app_context
    ):
        """Should raise for nonexistent ID."""
        with pytest.raises(ScheduleNotFoundError):
            SchedulerService.get_schedule(
                99999, db_user.id
            )


class TestSchedulerServiceUpdate:
    """Tests for updating schedules."""

    def test_update_schedule_fields(
        self, db_user, sample_schedule, app_context
    ):
        """Should update specified fields."""
        updated = SchedulerService.update_schedule(
            schedule_id=sample_schedule.id,
            user_id=db_user.id,
            schedule_value="weekly",
            is_enabled=False,
        )
        assert updated.schedule_value == "weekly"
        assert updated.is_enabled is False

    def test_update_ignores_non_allowed_fields(
        self, db_user, sample_schedule, app_context
    ):
        """Should ignore fields not in allowed set."""
        original_id = sample_schedule.id
        updated = SchedulerService.update_schedule(
            schedule_id=sample_schedule.id,
            user_id=db_user.id,
            id=99999,  # Should be ignored
        )
        assert updated.id == original_id


class TestSchedulerServiceDelete:
    """Tests for deleting schedules."""

    def test_delete_schedule_removes_from_db(
        self, db_user, sample_schedule, app_context
    ):
        """Should delete the schedule."""
        schedule_id = sample_schedule.id
        SchedulerService.delete_schedule(
            schedule_id, db_user.id
        )

        with pytest.raises(ScheduleNotFoundError):
            SchedulerService.get_schedule(
                schedule_id, db_user.id
            )

    def test_delete_nonexistent_raises(
        self, db_user, app_context
    ):
        """Should raise ScheduleNotFoundError."""
        with pytest.raises(ScheduleNotFoundError):
            SchedulerService.delete_schedule(
                99999, db_user.id
            )


class TestSchedulerServiceToggle:
    """Tests for toggling schedule state."""

    def test_toggle_disables_enabled(
        self, db_user, sample_schedule, app_context
    ):
        """Should disable an enabled schedule."""
        assert sample_schedule.is_enabled is True
        toggled = SchedulerService.toggle_schedule(
            sample_schedule.id, db_user.id
        )
        assert toggled.is_enabled is False

    def test_toggle_enables_disabled(
        self, db_user, sample_schedule, app_context
    ):
        """Should enable a disabled schedule."""
        SchedulerService.toggle_schedule(
            sample_schedule.id, db_user.id
        )
        toggled = SchedulerService.toggle_schedule(
            sample_schedule.id, db_user.id
        )
        assert toggled.is_enabled is True
