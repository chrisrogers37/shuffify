"""Pure timing compatibility and the API-safe worker refresh hint.

Memory registration/leadership/event coverage lives with WorkerScheduler tests;
exact fire admission tests replace synchronous scheduled execution coverage.
"""

import shuffify.scheduler as scheduler_module
from shuffify.scheduler import _parse_schedule


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


def test_web_refresh_is_only_a_bounded_hint(app_ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler_module.WorkerControlClient, 'poke', lambda self: calls.append(True))
    scheduler_module.add_job_for_schedule(object())
    scheduler_module.remove_job_for_schedule(42)
    assert calls == [True, True]
