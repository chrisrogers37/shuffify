"""
Tests for schedule_utils module.

Covers build_cron for all frequencies, edge-case times, and error paths,
and the words a routine's operation and timing are shown in.
"""

from datetime import datetime, timezone

import pytest
from apscheduler.triggers.cron import CronTrigger

from shuffify.services.schedule_utils import (
    TIME_CAPABLE_FREQUENCIES,
    TIME_RE,
    build_cron,
    normalize_timing,
    operation_label,
    schedule_revision,
    serialize_timing,
    timing_text,
)


class TestBuildCron:
    """Tests for build_cron helper."""

    def test_daily(self):
        assert build_cron("daily", "14:30") == "30 14 * * *"

    def test_every_3d(self):
        assert build_cron("every_3d", "09:00") == "00 09 */3 * *"

    def test_weekly(self):
        assert build_cron("weekly", "22:15") == "15 22 * * 0"

    def test_midnight(self):
        assert build_cron("daily", "00:00") == "00 00 * * *"

    def test_end_of_day(self):
        assert build_cron("daily", "23:59") == "59 23 * * *"

    def test_single_digit_minutes(self):
        assert build_cron("weekly", "08:05") == "05 08 * * 0"

    def test_invalid_frequency_raises(self):
        with pytest.raises(ValueError, match="Cannot build cron"):
            build_cron("every_6h", "12:00")

    def test_hourly_raises(self):
        with pytest.raises(ValueError, match="Cannot build cron"):
            build_cron("hourly", "12:00")


class TestTimeCapableFrequencies:
    """Tests for the TIME_CAPABLE_FREQUENCIES constant."""

    def test_contains_expected_values(self):
        assert TIME_CAPABLE_FREQUENCIES == {"daily", "every_3d", "weekly"}


class TestTimeRegex:
    """Tests for the TIME_RE pattern."""

    @pytest.mark.parametrize("val", ["00:00", "12:30", "23:59", "09:05"])
    def test_valid_times_match(self, val):
        assert TIME_RE.match(val)

    @pytest.mark.parametrize("val", ["0:00", "12:3", "noon", "", "12:00:00"])
    def test_invalid_times_no_match(self, val):
        assert not TIME_RE.match(val)


@pytest.mark.parametrize("operation, expected", [
    ("raid", "Collect"), ("raid_and_shuffle", "Collect and shuffle"), ("raid_and_drip", "Collect and add"),
    ("drip", "Add collected tracks"), ("shuffle", "Shuffle"), ("rotate", "Rotate"),
])
def test_each_job_type_has_an_operation_label(operation, expected):
    assert operation_label(operation) == expected


@pytest.mark.parametrize("kind, value, expected", [
    ("interval", "every_6h", "Every 6 hours"),
    ("interval", "every_12h", "Every 12 hours"),
    ("interval", "daily", "Every day"),
    ("interval", "every_3d", "Every 3 days"),
    ("interval", "weekly", "Every week"),
    ("interval", "hourly", "Custom timing (hourly)"),
    ("cron", "0 9 * * *", "Every day at 09:00 UTC"),
    ("cron", "05 18 */3 * *", "Calendar day */3 at 18:05 UTC"),
    # shuffify.scheduler passes the weekday field to APScheduler 3, which counts from Monday (0).
    ("cron", "0 9 * * 0", "Every Monday at 09:00 UTC"),
    ("cron", "15 6 * * 1", "Every Tuesday at 06:15 UTC"),
    ("cron", "0 21 * * 6", "Every Sunday at 21:00 UTC"),
    ("cron", "0 9 * * 7", "Custom timing (cron 0 9 * * 7, UTC)"),
    ("cron", "0 9 * * mon", "Every Monday at 09:00 UTC"),
    ("cron", "0 9 1 * *", "Custom timing (cron 0 9 1 * *, UTC)"),
    ("cron", "0 9 */3 * 1", "Custom timing (cron 0 9 */3 * 1, UTC)"),
    ("cron", "0 9 * 2 *", "Custom timing (cron 0 9 * 2 *, UTC)"),
    ("cron", "60 9 * * *", "Custom timing (cron 60 9 * * *, UTC)"),
    ("cron", "0 24 * * *", "Custom timing (cron 0 24 * * *, UTC)"),
    ("cron", "*/5 * * * *", "Custom timing (cron */5 * * * *, UTC)"),
    ("cron", "0 9 * *", "Custom timing (cron 0 9 * *, UTC)"),
])
def test_timing_text_states_only_what_the_scheduler_runs(kind, value, expected):
    assert timing_text(kind, value) == expected


def test_the_weekday_names_match_what_apscheduler_runs():
    start = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    for field in range(7):
        trigger = CronTrigger(minute="0", hour="9", day="*", month="*", day_of_week=str(field), timezone=timezone.utc)
        fire = trigger.get_next_fire_time(None, start)
        assert timing_text("cron", f"0 9 * * {field}") == f"Every {fire.strftime('%A')} at 09:00 UTC"


def test_typed_weekly_uses_named_day_and_explicit_zone():
    from types import SimpleNamespace

    fields = normalize_timing({"kind": "weekly", "time": "09:00", "weekday": "mon", "timezone": "America/New_York"})
    assert fields == {"schedule_type": "cron", "schedule_value": "0 9 * * mon", "timezone": "America/New_York"}
    schedule = SimpleNamespace(**fields)
    timing = serialize_timing(schedule)
    assert timing["text"] == "Every Monday at 09:00 America/New_York"
    assert timing["legacy"] is False


def test_invalid_zone_and_cron_are_rejected():
    with pytest.raises(ValueError, match="timezone"):
        normalize_timing({"kind": "daily", "time": "09:00", "timezone": "No/Such_Zone"})
    with pytest.raises(ValueError, match="cron"):
        normalize_timing({"kind": "advanced", "expression": "60 9 * * *"})


def test_legacy_numeric_weekdays_and_calendar_three_day_stay_raw():
    from types import SimpleNamespace

    monday = serialize_timing(SimpleNamespace(schedule_type="cron", schedule_value="0 9 * * 0", timezone=None),
                              effective_timezone="America/Chicago")
    tuesday = serialize_timing(SimpleNamespace(schedule_type="cron", schedule_value="0 9 * * 1", timezone=None),
                               effective_timezone="America/Chicago")
    calendar = serialize_timing(SimpleNamespace(schedule_type="cron", schedule_value="0 9 */3 * *", timezone=None),
                                effective_timezone="America/Chicago")
    assert monday["text"] == "Every Monday at 09:00 America/Chicago"
    assert tuesday["text"] == "Every Tuesday at 09:00 America/Chicago"
    assert monday["kind"] == tuesday["kind"] == "advanced"
    assert calendar["kind"] == "advanced"
    assert "72-hour" in calendar["explanation"]


def test_revision_ignores_run_tracking_but_includes_timezone():
    from types import SimpleNamespace

    schedule = SimpleNamespace(
        id=1, user_id=2, job_type="shuffle", target_playlist_id="target",
        target_playlist_name="Target", source_playlist_ids=[], algorithm_name="BasicShuffle",
        algorithm_params={}, schedule_type="interval", schedule_value="daily", timezone=None,
        is_enabled=True, last_status=None, last_run_at=None, updated_at=None,
    )
    before = schedule_revision(schedule)
    schedule.last_status = "failed"
    schedule.updated_at = datetime.now(timezone.utc)
    assert schedule_revision(schedule) == before
    schedule.timezone = "UTC"
    assert schedule_revision(schedule) != before
