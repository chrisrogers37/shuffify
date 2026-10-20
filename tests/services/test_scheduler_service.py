"""
Tests for SchedulerService CRUD operations.

These tests require a Flask app context with SQLAlchemy configured.
"""

from unittest.mock import patch

import pytest
from flask import Flask
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from shuffify.services.scheduler_service import (
    ScheduleError,
    ScheduleNotFoundError,
    SchedulerService,
)


@pytest.fixture
def db_user(app_context):
    """Create a test user in the database."""
    from shuffify.models.db import JobExecution, Schedule, User, db

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

    def test_create_many_schedules_succeeds(
        self, db_user, app_context
    ):
        """Should allow creating many schedules with no limit."""
        for i in range(10):
            schedule = SchedulerService.create_schedule(
                user_id=db_user.id,
                job_type="shuffle",
                target_playlist_id=f"pl_{i}",
                target_playlist_name=f"Playlist {i}",
                schedule_type="interval",
                schedule_value="daily",
                algorithm_name="BasicShuffle",
            )
            assert schedule.id is not None

        schedules = SchedulerService.get_user_schedules(
            db_user.id
        )
        assert len(schedules) == 10


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


@pytest.fixture
def foreign_key_database():
    """Use a fresh local database with FK enforcement on every connection."""
    from shuffify.models.db import db

    app = Flask("schedule-deletion-integrity")
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        engine = db.engine

        def enable_foreign_keys(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        event.listen(engine, "connect", enable_foreign_keys)
        db.create_all()
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        db.session.commit()
        try:
            yield
        finally:
            db.session.remove()
            db.drop_all()
            engine.dispose()
            event.remove(engine, "connect", enable_foreign_keys)


@pytest.fixture
def schedule_deletion_records(foreign_key_database):
    """Two schedules with multiple executions, linked and manual snapshots."""
    from shuffify.models.db import (
        JobExecution,
        PlaylistSnapshot,
        Schedule,
        User,
        db,
    )

    users = [User(spotify_id=f"delete-user-{i}") for i in range(2)]
    db.session.add_all(users)
    db.session.flush()
    schedules = [
        Schedule(
            user_id=user.id,
            job_type="shuffle",
            target_playlist_id=f"delete-playlist-{i}",
            schedule_type="interval",
            schedule_value="daily",
        )
        for i, user in enumerate(users)
    ]
    db.session.add_all(schedules)
    db.session.flush()
    executions = [
        JobExecution(schedule_id=schedules[0].id, status="success"),
        JobExecution(schedule_id=schedules[0].id, status="success"),
        JobExecution(schedule_id=schedules[1].id, status="success"),
    ]
    db.session.add_all(executions)
    db.session.flush()
    snapshots = [
        PlaylistSnapshot(
            user_id=users[0 if i < 4 else 1].id,
            playlist_id=f"snapshot-playlist-{i}",
            playlist_name=f"Saved playlist {i}",
            track_uris_json=f'["spotify:track:original-{i}"]',
            track_count=1,
            snapshot_type="manual" if execution is None else "scheduled_pre_execution",
            trigger_description=f"Retain snapshot {i}",
            job_execution_id=execution.id if execution is not None else None,
        )
        for i, execution in enumerate(
            [executions[0], executions[0], executions[1], None, executions[2]]
        )
    ]
    db.session.add_all(snapshots)
    db.session.commit()
    return {
        "schedule_id": schedules[0].id,
        "user_id": users[0].id,
        "other_user_id": users[1].id,
        "other_schedule_id": schedules[1].id,
        "execution_ids": [execution.id for execution in executions[:2]],
        "other_execution_id": executions[2].id,
        "snapshots": [snapshot.to_dict() for snapshot in snapshots],
        "snapshot_links": [snapshot.job_execution_id for snapshot in snapshots],
    }


class TestSchedulerServiceDeleteIntegrity:
    """Exercise real ORM mutations with foreign keys and database failures."""

    def test_retains_snapshots_and_removes_job_only_after_commit(
        self, schedule_deletion_records
    ):
        from shuffify.models.db import JobExecution, PlaylistSnapshot, Schedule, db

        records = schedule_deletion_records
        calls = []
        session = db.session()

        def committed(_):
            calls.append("commit")

        def removed(schedule_id):
            assert schedule_id == records["schedule_id"]
            assert db.session.get(Schedule, schedule_id) is None
            assert JobExecution.query.filter(
                JobExecution.id.in_(records["execution_ids"])
            ).count() == 0
            calls.append("scheduler removal")

        event.listen(session, "after_commit", committed)
        try:
            with patch.object(
                SchedulerService, "_remove_apscheduler_job", side_effect=removed
            ) as remove_job:
                SchedulerService.delete_schedule(
                    records["schedule_id"], records["user_id"]
                )
        finally:
            event.remove(session, "after_commit", committed)

        remove_job.assert_called_once_with(records["schedule_id"])
        assert calls == ["commit", "scheduler removal"]
        assert db.session.get(Schedule, records["other_schedule_id"]) is not None
        assert JobExecution.query.count() == 1
        assert db.session.get(JobExecution, records["other_execution_id"]) is not None
        snapshots = PlaylistSnapshot.query.order_by(PlaylistSnapshot.id).all()
        assert [snapshot.to_dict() for snapshot in snapshots] == records["snapshots"]
        assert [snapshot.job_execution_id for snapshot in snapshots] == [
            None, None, None, None, records["other_execution_id"]
        ]

    @pytest.mark.parametrize(
        "failing_table, operation",
        [
            ("playlist_snapshots", "UPDATE OF job_execution_id"),
            ("job_executions", "DELETE"),
            ("schedules", "DELETE"),
        ],
    )
    def test_database_failure_rolls_back_and_preserves_running_job(
        self, schedule_deletion_records, failing_table, operation
    ):
        from shuffify.models.db import JobExecution, PlaylistSnapshot, Schedule, db

        records = schedule_deletion_records
        # Fail actual SQL, including a late schedule DELETE during commit.
        # All table/operation values above are fixed test constants.
        db.session.execute(text(
            f"CREATE TRIGGER reject_schedule_deletion BEFORE {operation} "
            f"ON {failing_table} BEGIN "
            "SELECT RAISE(ABORT, 'schedule deletion failure'); END"
        ))
        db.session.commit()

        with patch.object(SchedulerService, "_remove_apscheduler_job") as remove_job:
            with pytest.raises(ScheduleError, match="Failed to delete schedule") as error:
                SchedulerService.delete_schedule(
                    records["schedule_id"], records["user_id"]
                )

        remove_job.assert_not_called()
        assert isinstance(error.value.__cause__, IntegrityError)
        assert Schedule.query.count() == 2
        assert JobExecution.query.count() == 3
        snapshots = PlaylistSnapshot.query.order_by(PlaylistSnapshot.id).all()
        assert [snapshot.to_dict() for snapshot in snapshots] == records["snapshots"]
        assert [snapshot.job_execution_id for snapshot in snapshots] == records["snapshot_links"]

    def test_wrong_owner_does_not_mutate_snapshots_or_remove_job(
        self, schedule_deletion_records
    ):
        from shuffify.models.db import JobExecution, PlaylistSnapshot, Schedule

        records = schedule_deletion_records
        with patch.object(SchedulerService, "_remove_apscheduler_job") as remove_job:
            with pytest.raises(ScheduleNotFoundError):
                SchedulerService.delete_schedule(
                    records["schedule_id"], records["other_user_id"]
                )

        remove_job.assert_not_called()
        assert Schedule.query.count() == 2
        assert JobExecution.query.count() == 3
        snapshots = PlaylistSnapshot.query.order_by(PlaylistSnapshot.id).all()
        assert [snapshot.job_execution_id for snapshot in snapshots] == records["snapshot_links"]


class TestSchedulerServiceRegistration:
    """CRUD keeps the APScheduler job in sync so an enabled schedule always
    has a running job and a disabled/deleted one never does."""

    def test_create_registers_job(self, db_user, app_context):
        with patch(
            "shuffify.scheduler.add_job_for_schedule"
        ) as mock_add:
            schedule = SchedulerService.create_schedule(
                user_id=db_user.id,
                job_type="shuffle",
                target_playlist_id="pl_reg",
                target_playlist_name="P",
                schedule_type="interval",
                schedule_value="daily",
                algorithm_name="BasicShuffle",
            )
        mock_add.assert_called_once_with(schedule)

    def test_delete_removes_job_after_db_delete(
        self, sample_schedule, app_context
    ):
        from shuffify.models.db import Schedule

        sid = sample_schedule.id
        with patch(
            "shuffify.scheduler.remove_job_for_schedule"
        ) as mock_remove:
            SchedulerService.delete_schedule(
                sid, sample_schedule.user_id
            )
        mock_remove.assert_called_once_with(sid)
        assert Schedule.query.get(sid) is None

    def test_disabling_removes_job(
        self, sample_schedule, app_context
    ):
        with patch(
            "shuffify.scheduler.remove_job_for_schedule"
        ) as mock_remove, patch(
            "shuffify.scheduler.add_job_for_schedule"
        ) as mock_add:
            result = SchedulerService.update_schedule(
                sample_schedule.id,
                sample_schedule.user_id,
                is_enabled=False,
            )
        assert result.is_enabled is False
        mock_remove.assert_called_once_with(sample_schedule.id)
        mock_add.assert_not_called()

    def test_enabling_adds_job(
        self, sample_schedule, app_context
    ):
        # Disable first (real call; no worker is configured, so the hint is a no-op)
        SchedulerService.update_schedule(
            sample_schedule.id,
            sample_schedule.user_id,
            is_enabled=False,
        )
        with patch(
            "shuffify.scheduler.remove_job_for_schedule"
        ) as mock_remove, patch(
            "shuffify.scheduler.add_job_for_schedule"
        ) as mock_add:
            result = SchedulerService.update_schedule(
                sample_schedule.id,
                sample_schedule.user_id,
                is_enabled=True,
            )
        assert result.is_enabled is True
        mock_add.assert_called_once_with(result)
        mock_remove.assert_not_called()

    def test_create_survives_registration_failure(
        self, db_user, app_context
    ):
        """A scheduler-registration failure is swallowed and logged, so the
        create still succeeds -- the DB is the source of truth."""
        with patch(
            "shuffify.scheduler.add_job_for_schedule",
            side_effect=KeyError("ConflictingId"),
        ):
            schedule = SchedulerService.create_schedule(
                user_id=db_user.id,
                job_type="shuffle",
                target_playlist_id="pl_fail",
                target_playlist_name="P",
                schedule_type="interval",
                schedule_value="daily",
                algorithm_name="BasicShuffle",
            )
        assert schedule.id is not None
