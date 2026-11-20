"""Pure timing compatibility and the API-safe worker refresh hint.

Memory registration/leadership/event coverage lives with WorkerScheduler tests;
exact fire admission tests replace synchronous scheduled execution coverage.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import shuffify.scheduler as scheduler_module
from shuffify.enums import IntervalValue
from shuffify.scheduler import InvalidSchedule, _parse_schedule
from shuffify.services.scheduled_admission import build_cadence

# Each interval's trigger options; a new IntervalValue member needs its row here.
INTERVAL_OPTIONS = {
    "every_6h": {"hours": 6},
    "every_12h": {"hours": 12},
    "daily": {"days": 1},
    "every_3d": {"days": 3},
    "weekly": {"weeks": 1},
}


def assert_runs_daily(caplog, schedule_type, schedule_value, reason, trigger):
    """The parser refuses the definition; a stored row with it runs daily, logging only its id and the reason."""
    with pytest.raises(InvalidSchedule) as refused:
        _parse_schedule(schedule_type, schedule_value)
    assert str(refused.value) == reason
    row = SimpleNamespace(
        id=7, schedule_type=schedule_type, schedule_value=schedule_value, timezone="UTC",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    with caplog.at_level("WARNING", logger="shuffify.services.scheduled_admission"):
        cadence = build_cadence(row)
    assert str(cadence.trigger) == trigger
    assert [record.getMessage() for record in caplog.records] == [f"Schedule runs daily: {reason} (schedule_id=7)"]


class TestParseScheduleInterval:
    """Tests for _parse_schedule with interval type."""

    @pytest.mark.parametrize("value", list(IntervalValue))
    def test_every_interval_value_has_a_trigger(self, value):
        assert _parse_schedule("interval", value) == ("interval", INTERVAL_OPTIONS[value])

    def test_unknown_interval_defaults_daily(self, caplog):
        assert_runs_daily(caplog, "interval", "unknown", "unknown interval", "interval[1 day, 0:00:00]")


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

    def test_invalid_cron_3_fields(self, caplog):
        assert_runs_daily(caplog, "cron", "0 6 *", "cron expression is not five fields", "cron[hour='0', minute='0']")

    def test_invalid_cron_single_field(self, caplog):
        assert_runs_daily(caplog, "cron", "30", "cron expression is not five fields", "cron[hour='0', minute='0']")

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

    def test_unknown_type_defaults_interval_daily(self, caplog):
        assert_runs_daily(caplog, "monthly", "1", "unknown schedule type", "interval[1 day, 0:00:00]")

    def test_empty_type_defaults(self, caplog):
        assert_runs_daily(caplog, "", "daily", "unknown schedule type", "interval[1 day, 0:00:00]")


def test_web_refresh_is_only_a_bounded_hint(app_ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler_module.WorkerControlClient, 'poke', lambda self: calls.append(True))
    scheduler_module.add_job_for_schedule(object())
    scheduler_module.remove_job_for_schedule(42)
    assert calls == [True, True]
