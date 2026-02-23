"""
Tests for the scheduler module (APScheduler integration).

Tests _parse_schedule, init_scheduler, add/remove job functions,
_execute_scheduled_job, and event listeners.
"""

import logging
from unittest.mock import Mock, patch, MagicMock

import pytest

import shuffify.scheduler as scheduler_module
from shuffify.scheduler import (
    _parse_schedule,
    _on_job_executed,
    _on_job_error,
    _on_job_missed,
    init_scheduler,
    add_job_for_schedule,
    remove_job_for_schedule,
    _execute_scheduled_job,
    shutdown_scheduler,
    get_scheduler,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def reset_scheduler():
    """Reset global scheduler state between tests."""
    scheduler_module._scheduler = None
    yield
    if (
        scheduler_module._scheduler is not None
        and hasattr(scheduler_module._scheduler, 'running')
        and scheduler_module._scheduler.running
    ):
        try:
            scheduler_module._scheduler.shutdown(wait=False)
        except Exception:
            pass
    scheduler_module._scheduler = None


# =============================================================================
# _parse_schedule — Interval
# =============================================================================

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


# =============================================================================
# init_scheduler
# =============================================================================

class TestInitScheduler:
    """Tests for init_scheduler()."""

    def test_scheduler_disabled(self, app):
        app.config["SCHEDULER_ENABLED"] = False
        result = init_scheduler(app)
        assert result is None

    def test_werkzeug_reloader_child(self, app, monkeypatch):
        app.debug = True
        monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
        result = init_scheduler(app)
        assert result is None

    def test_werkzeug_main_process(self, app, monkeypatch):
        """When WERKZEUG_RUN_MAIN=true, scheduler should init."""
        app.debug = True
        app.config["SCHEDULER_ENABLED"] = True
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")

        with patch.object(
            scheduler_module, '_register_existing_jobs'
        ):
            result = init_scheduler(app)

        assert result is not None

    def test_already_running_returns_existing(self, app, monkeypatch):
        app.config["SCHEDULER_ENABLED"] = True
        app.debug = False
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")

        with patch.object(
            scheduler_module, '_register_existing_jobs'
        ):
            first = init_scheduler(app)
            second = init_scheduler(app)

        assert first is second

    def test_successful_init_returns_scheduler(self, app, monkeypatch):
        app.config["SCHEDULER_ENABLED"] = True
        app.debug = False
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")

        with patch.object(
            scheduler_module, '_register_existing_jobs'
        ):
            result = init_scheduler(app)

        assert result is not None
        assert result.running


# =============================================================================
# add_job_for_schedule / remove_job_for_schedule
# =============================================================================

class TestAddRemoveJob:
    """Tests for add_job_for_schedule and remove_job_for_schedule."""

    def test_add_job_no_scheduler_raises(self):
        mock_schedule = Mock()
        mock_schedule.id = 1
        mock_schedule.schedule_type = "interval"
        mock_schedule.schedule_value = "daily"

        with pytest.raises(RuntimeError, match="not initialized"):
            add_job_for_schedule(mock_schedule, Mock())

    def test_add_and_remove_job(self):
        mock_sched = MagicMock()
        scheduler_module._scheduler = mock_sched

        mock_schedule = Mock()
        mock_schedule.id = 42
        mock_schedule.schedule_type = "interval"
        mock_schedule.schedule_value = "daily"

        add_job_for_schedule(mock_schedule, Mock())

        mock_sched.add_job.assert_called_once()
        call_kwargs = mock_sched.add_job.call_args
        assert call_kwargs.kwargs["id"] == "schedule_42"
        assert call_kwargs.kwargs["trigger"] == "interval"

        remove_job_for_schedule(42)
        mock_sched.remove_job.assert_called_with("schedule_42")

    def test_add_replaces_existing(self):
        mock_sched = MagicMock()
        scheduler_module._scheduler = mock_sched

        mock_schedule = Mock()
        mock_schedule.id = 10
        mock_schedule.schedule_type = "interval"
        mock_schedule.schedule_value = "daily"

        # Add twice — should not raise
        add_job_for_schedule(mock_schedule, Mock())
        add_job_for_schedule(mock_schedule, Mock())

        assert mock_sched.add_job.call_count == 2

    def test_remove_nonexistent_no_error(self, app, monkeypatch):
        app.config["SCHEDULER_ENABLED"] = True
        app.debug = False
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")

        with patch.object(
            scheduler_module, '_register_existing_jobs'
        ):
            init_scheduler(app)

        # Should not raise
        remove_job_for_schedule(999)

    def test_remove_with_no_scheduler(self):
        """Remove when scheduler is None should silently return."""
        remove_job_for_schedule(1)


# =============================================================================
# _execute_scheduled_job
# =============================================================================

class TestExecuteScheduledJob:
    """Tests for _execute_scheduled_job."""

    def test_successful_execution(self, app):
        with patch(
            "shuffify.services.executors.JobExecutorService"
        ) as mock_executor:
            _execute_scheduled_job(app, 42)
            mock_executor.execute.assert_called_once_with(42)

    @patch("shuffify.scheduler.logger")
    def test_execution_failure_logged(self, mock_logger, app):
        with patch(
            "shuffify.services.executors.JobExecutorService"
        ) as mock_executor:
            mock_executor.execute.side_effect = Exception("boom")
            _execute_scheduled_job(app, 42)
        mock_logger.error.assert_called_once()
        assert "failed" in mock_logger.error.call_args[0][0].lower()


# =============================================================================
# Event listeners
# =============================================================================

class TestEventListeners:
    """Tests for scheduler event listener functions."""

    @patch("shuffify.scheduler.logger")
    def test_on_job_executed(self, mock_logger):
        event = Mock()
        event.job_id = "schedule_1"
        _on_job_executed(event)
        mock_logger.info.assert_called_once()
        assert "schedule_1" in mock_logger.info.call_args[0][0]

    @patch("shuffify.scheduler.logger")
    def test_on_job_error(self, mock_logger):
        event = Mock()
        event.job_id = "schedule_2"
        event.exception = RuntimeError("test error")
        event.traceback = None
        _on_job_error(event)
        mock_logger.error.assert_called_once()
        assert "schedule_2" in mock_logger.error.call_args[0][0]

    @patch("shuffify.scheduler.logger")
    def test_on_job_missed(self, mock_logger):
        event = Mock()
        event.job_id = "schedule_3"
        _on_job_missed(event)
        mock_logger.warning.assert_called_once()
        assert "schedule_3" in mock_logger.warning.call_args[0][0]


# =============================================================================
# shutdown_scheduler
# =============================================================================

class TestShutdownScheduler:
    """Tests for shutdown_scheduler."""

    def test_shutdown_running(self, app, monkeypatch):
        app.config["SCHEDULER_ENABLED"] = True
        app.debug = False
        monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")

        with patch.object(
            scheduler_module, '_register_existing_jobs'
        ):
            init_scheduler(app)

        assert get_scheduler() is not None
        shutdown_scheduler()
        assert scheduler_module._scheduler is None

    def test_shutdown_when_none(self):
        """Shutdown with no scheduler should not raise."""
        shutdown_scheduler()
        assert scheduler_module._scheduler is None
