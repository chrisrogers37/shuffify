"""Registration reports actual APScheduler state and preserves matching intervals."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import shuffify.scheduler as scheduler_module
from shuffify.models.db import Schedule, User, db


def _schedule(enabled=True):
    return SimpleNamespace(
        id=42, schedule_type="interval", schedule_value="daily", timezone="UTC", is_enabled=enabled
    )


def test_matching_interval_is_not_readded(monkeypatch):
    next_run = datetime(2026, 10, 14, tzinfo=timezone.utc)
    job = SimpleNamespace(args=[42], trigger=IntervalTrigger(days=1, timezone="UTC"), next_run_time=next_run)
    scheduler = MagicMock(timezone=timezone.utc)
    scheduler.get_job.return_value = job
    monkeypatch.setattr(scheduler_module, "_scheduler", scheduler)

    scheduler_module.add_job_for_schedule(_schedule())
    assert scheduler.add_job.call_count == 0
    assert scheduler.remove_job.call_count == 0
    assert scheduler_module.get_schedule_registration(_schedule()) == {
        "status": "registered", "next_run_at": next_run.isoformat(), "effective_timezone": "UTC"
    }


def test_real_apscheduler_job_repeated_sync_retains_next_run(db_app, monkeypatch):
    """APScheduler normalizes add_job(args=[id]) to a tuple on its real Job."""
    with db_app.app_context():
        user = User(spotify_id="registration-owner")
        db.session.add(user)
        db.session.flush()
        schedule = Schedule(
            user_id=user.id, job_type="shuffle", target_playlist_id="target",
            schedule_type="interval", schedule_value="daily", timezone="UTC",
        )
        db.session.add(schedule)
        db.session.commit()

        actual = BackgroundScheduler(timezone="UTC")  # It is never started.
        job = actual.add_job(
            scheduler_module._execute_scheduled_job, "interval", days=1,
            id=f"schedule_{schedule.id}", args=[schedule.id], timezone="UTC",
        )
        assert job.args == (schedule.id,)
        next_run = datetime(2026, 10, 14, tzinfo=timezone.utc)
        job._modify(next_run_time=next_run)
        scheduler = SimpleNamespace(
            running=True, timezone=timezone.utc, get_job=actual.get_job,
            add_job=MagicMock(side_effect=AssertionError("matching interval was replaced")),
        )
        monkeypatch.setattr(scheduler_module, "_scheduler", scheduler)

        first = scheduler_module.sync_schedule_registration(schedule)
        second = scheduler_module.sync_schedule_registration(schedule)
        assert first == second == {
            "status": "registered", "next_run_at": next_run.isoformat(), "effective_timezone": "UTC"
        }
        assert actual.get_job(f"schedule_{schedule.id}") is job
        scheduler.add_job.assert_not_called()


def test_matching_but_paused_real_job_is_repaired_on_sync(db_app, monkeypatch):
    with db_app.app_context():
        user = User(spotify_id="paused-registration-owner")
        db.session.add(user)
        db.session.flush()
        schedule = Schedule(
            user_id=user.id, job_type="shuffle", target_playlist_id="target",
            schedule_type="interval", schedule_value="daily", timezone="UTC",
        )
        db.session.add(schedule)
        db.session.commit()

        actual = BackgroundScheduler(timezone="UTC")  # No thread or app is started.
        job = actual.add_job(
            scheduler_module._execute_scheduled_job, "interval", days=1,
            id=f"schedule_{schedule.id}", args=[schedule.id], timezone="UTC",
        )
        assert job.args == (schedule.id,)
        job._modify(next_run_time=None)
        resumed_at = datetime(2026, 10, 14, tzinfo=timezone.utc)

        def replace_paused_job(**kwargs):
            assert kwargs["replace_existing"] is True
            assert kwargs["id"] == f"schedule_{schedule.id}"
            assert kwargs["args"] == [schedule.id]
            job._modify(next_run_time=resumed_at)

        add_job = MagicMock(side_effect=replace_paused_job)
        scheduler = SimpleNamespace(
            running=True, timezone=timezone.utc, get_job=actual.get_job, add_job=add_job,
        )
        monkeypatch.setattr(scheduler_module, "_scheduler", scheduler)
        assert scheduler_module.get_schedule_registration(schedule)["status"] == "needs_attention"

        assert scheduler_module.sync_schedule_registration(schedule) == {
            "status": "registered", "next_run_at": resumed_at.isoformat(), "effective_timezone": "UTC"
        }
        add_job.assert_called_once()
        scheduler_module.sync_schedule_registration(schedule)
        add_job.assert_called_once()  # The healthy replacement is now reused.


def test_missing_enabled_and_stray_paused_job_need_attention(monkeypatch):
    scheduler = MagicMock(timezone=timezone.utc)
    scheduler.get_job.return_value = None
    monkeypatch.setattr(scheduler_module, "_scheduler", scheduler)
    assert scheduler_module.get_schedule_registration(_schedule())["status"] == "needs_attention"
    assert scheduler_module.get_schedule_registration(_schedule(False))["status"] == "paused"
    scheduler.get_job.return_value = object()
    assert scheduler_module.get_schedule_registration(_schedule(False))["status"] == "needs_attention"


def test_uninspectable_scheduler_is_unavailable(monkeypatch):
    monkeypatch.setattr(scheduler_module, "_scheduler", None)
    assert scheduler_module.get_schedule_registration(_schedule())["status"] == "unavailable"
    assert scheduler_module.get_schedule_registration(_schedule(False))["status"] == "unavailable"
