"""
Tests for schedule_utils module.

Covers build_cron for all frequencies, edge-case times, and error paths.
"""

import pytest

from shuffify.services.schedule_utils import (
    build_cron,
    TIME_CAPABLE_FREQUENCIES,
    TIME_RE,
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
