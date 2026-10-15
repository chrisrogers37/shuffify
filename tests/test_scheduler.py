"""Pure timing compatibility and API-safe scheduler lifecycle.

Memory registration/leadership/event coverage lives with WorkerScheduler tests;
exact fire admission tests replace synchronous scheduled execution coverage.
"""

from datetime import datetime, timezone

import pytest

import shuffify.scheduler as scheduler_module
from shuffify.scheduler import _cleanup_stale_executions, _parse_schedule


class TestParseScheduleInterval:
    """Tests for _parse_schedule with interval type."""

    def test_every_6h(self):
        trigger, kwargs = _parse_schedule("interval", "every_6h")
        assert trigger == "interval"
        assert kwargs == {"hours": 6}

    def test_every_12h(self):
        trigger, kwargs = _parse_schedule("interval", "every_12h")
        assert trigger == "interval"
        assert kwargs == {"hours": 12}

    def test_daily(self):
        trigger, kwargs = _parse_schedule("interval", "daily")
        assert trigger == "interval"
        assert kwargs == {"days": 1}

    def test_every_3d(self):
        trigger, kwargs = _parse_schedule("interval", "every_3d")
        assert trigger == "interval"
        assert kwargs == {"days": 3}

    def test_weekly(self):
        trigger, kwargs = _parse_schedule("interval", "weekly")
        assert trigger == "interval"
        assert kwargs == {"weeks": 1}

    def test_unknown_interval_defaults_daily(self):
        trigger, kwargs = _parse_schedule("interval", "unknown")
        assert trigger == "interval"
        assert kwargs == {"days": 1}


# =============================================================================
# _parse_schedule — Cron
# =============================================================================

class TestParseScheduleCron:
    """Tests for _parse_schedule with cron type."""

    def test_valid_5_field_cron(self):
        trigger, kwargs = _parse_schedule("cron", "30 6 * * 1")
        assert trigger == "cron"
        assert kwargs == {
            "minute": "30",
            "hour": "6",
            "day": "*",
            "month": "*",
            "day_of_week": "1",
        }

    def test_invalid_cron_3_fields(self):
        trigger, kwargs = _parse_schedule("cron", "0 6 *")
        assert trigger == "cron"
        assert kwargs == {"hour": 0, "minute": 0}

    def test_invalid_cron_single_field(self):
        trigger, kwargs = _parse_schedule("cron", "30")
        assert trigger == "cron"
        assert kwargs == {"hour": 0, "minute": 0}

    def test_midnight_daily_cron(self):
        trigger, kwargs = _parse_schedule("cron", "0 0 * * *")
        assert trigger == "cron"
        assert kwargs["minute"] == "0"
        assert kwargs["hour"] == "0"


# =============================================================================
# _parse_schedule — Unknown type
# =============================================================================

class TestParseScheduleUnknown:
    """Tests for _parse_schedule with unknown schedule type."""

    def test_unknown_type_defaults_interval_daily(self):
        trigger, kwargs = _parse_schedule("monthly", "1")
        assert trigger == "interval"
        assert kwargs == {"days": 1}

    def test_empty_type_defaults(self):
        trigger, kwargs = _parse_schedule("", "daily")
        assert trigger == "interval"
        assert kwargs == {"days": 1}


@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('reloader', [None, 'true', 'false'])
def test_web_scheduler_entry_points_never_construct_jobs(app, monkeypatch, enabled, reloader):
    app.config['SCHEDULER_ENABLED'] = enabled
    if reloader is not None:
        monkeypatch.setenv('WERKZEUG_RUN_MAIN', reloader)
    assert scheduler_module.init_scheduler(app) is None
    assert scheduler_module.get_scheduler() is None
    scheduler_module.shutdown_scheduler()


def test_web_refresh_is_only_a_bounded_hint(app_ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler_module.WorkerControlClient, 'poke', lambda self: calls.append(True))
    scheduler_module.add_job_for_schedule(object())
    scheduler_module.remove_job_for_schedule(42)
    assert calls == [True, True]


class TestStaleCleanup:
    """Tests for _cleanup_stale_executions."""

    def test_cleans_old_running_records(self, db_app):
        """Stale running records should be marked failed."""
        from datetime import timedelta

        from shuffify.models.db import (
            JobExecution,
            Schedule,
            User,
            db,
        )

        user = User(
            spotify_id="test_user",
            display_name="Test",
        )
        db.session.add(user)
        db.session.flush()

        schedule = Schedule(
            user_id=user.id,
            job_type="shuffle",
            target_playlist_id="pl1",
            target_playlist_name="Test Playlist",
            schedule_type="interval",
            schedule_value="daily",
        )
        db.session.add(schedule)
        db.session.flush()

        # Create a stale execution (started 60 min ago)
        stale_exec = JobExecution(
            schedule_id=schedule.id,
            started_at=(
                datetime.now(timezone.utc)
                - timedelta(minutes=60)
            ),
            status="running",
        )
        db.session.add(stale_exec)
        db.session.commit()

        _cleanup_stale_executions(max_age_minutes=30)

        db.session.refresh(stale_exec)
        assert stale_exec.status == "failed"
        assert "stale" in stale_exec.error_message.lower()

    def test_leaves_recent_running_records(self, db_app):
        """Recent running records should not be cleaned."""
        from datetime import timedelta

        from shuffify.models.db import (
            JobExecution,
            Schedule,
            User,
            db,
        )

        user = User(
            spotify_id="test_user2",
            display_name="Test2",
        )
        db.session.add(user)
        db.session.flush()

        schedule = Schedule(
            user_id=user.id,
            job_type="shuffle",
            target_playlist_id="pl2",
            target_playlist_name="Test Playlist 2",
            schedule_type="interval",
            schedule_value="daily",
        )
        db.session.add(schedule)
        db.session.flush()

        # Create a recent execution (started 5 min ago)
        recent_exec = JobExecution(
            schedule_id=schedule.id,
            started_at=(
                datetime.now(timezone.utc)
                - timedelta(minutes=5)
            ),
            status="running",
        )
        db.session.add(recent_exec)
        db.session.commit()

        _cleanup_stale_executions(max_age_minutes=30)

        db.session.refresh(recent_exec)
        assert recent_exec.status == "running"
