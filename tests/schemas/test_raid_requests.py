"""
Tests for raid panel request validation schemas.

Tests WatchPlaylistRequest, UnwatchPlaylistRequest,
AddRaidUrlRequest, and RaidNowRequest Pydantic models.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.raid_requests import (
    WatchPlaylistRequest,
    UnwatchPlaylistRequest,
    AddRaidUrlRequest,
    RaidNowRequest,
    UpdateRaidScheduleRequest,
)
from shuffify.services.schedule_utils import build_cron


# =============================================================================
# WatchPlaylistRequest
# =============================================================================


class TestWatchPlaylistRequestValid:
    """Tests for valid WatchPlaylistRequest payloads."""

    def test_minimal_request(self):
        req = WatchPlaylistRequest(
            source_playlist_id="abc123",
        )
        assert req.source_playlist_id == "abc123"
        assert req.auto_schedule is True
        assert req.schedule_value == "daily"

    def test_full_request(self):
        req = WatchPlaylistRequest(
            source_playlist_id="abc123",
            source_playlist_name="My Source",
            source_url="https://open.spotify.com/playlist/abc123",
            auto_schedule=False,
            schedule_value="weekly",
        )
        assert req.source_playlist_name == "My Source"
        assert req.auto_schedule is False

    def test_valid_schedule_values(self):
        for val in [
            "daily", "weekly", "every_6h",
            "every_12h", "every_3d",
        ]:
            req = WatchPlaylistRequest(
                source_playlist_id="abc",
                schedule_value=val,
            )
            assert req.schedule_value == val

    def test_schedule_value_case_insensitive(self):
        req = WatchPlaylistRequest(
            source_playlist_id="abc",
            schedule_value="DAILY",
        )
        assert req.schedule_value == "daily"

    def test_extra_fields_ignored(self):
        req = WatchPlaylistRequest(
            source_playlist_id="abc",
            unknown_field="ignored",
        )
        assert req.source_playlist_id == "abc"

    def test_empty_source_name_becomes_none(self):
        req = WatchPlaylistRequest(
            source_playlist_id="abc",
            source_playlist_name="   ",
        )
        assert req.source_playlist_name is None


class TestWatchPlaylistRequestInvalid:
    """Tests for invalid WatchPlaylistRequest payloads."""

    def test_missing_source_id_raises(self):
        with pytest.raises(ValidationError):
            WatchPlaylistRequest()

    def test_empty_source_id_raises(self):
        with pytest.raises(ValidationError):
            WatchPlaylistRequest(source_playlist_id="  ")

    def test_invalid_schedule_value_raises(self):
        with pytest.raises(ValidationError):
            WatchPlaylistRequest(
                source_playlist_id="abc",
                schedule_value="every_minute",
            )


# =============================================================================
# UnwatchPlaylistRequest
# =============================================================================


class TestUnwatchPlaylistRequestValid:
    """Tests for valid UnwatchPlaylistRequest payloads."""

    def test_valid_source_id(self):
        req = UnwatchPlaylistRequest(source_id=42)
        assert req.source_id == 42


class TestUnwatchPlaylistRequestInvalid:
    """Tests for invalid UnwatchPlaylistRequest payloads."""

    def test_missing_source_id_raises(self):
        with pytest.raises(ValidationError):
            UnwatchPlaylistRequest()

    def test_zero_source_id_raises(self):
        with pytest.raises(ValidationError):
            UnwatchPlaylistRequest(source_id=0)

    def test_negative_source_id_raises(self):
        with pytest.raises(ValidationError):
            UnwatchPlaylistRequest(source_id=-1)


# =============================================================================
# RaidNowRequest
# =============================================================================


class TestRaidNowRequestValid:
    """Tests for valid RaidNowRequest payloads."""

    def test_no_source_ids(self):
        req = RaidNowRequest()
        assert req.source_playlist_ids is None

    def test_explicit_source_ids(self):
        req = RaidNowRequest(
            source_playlist_ids=["src1", "src2"]
        )
        assert len(req.source_playlist_ids) == 2


class TestRaidNowRequestInvalid:
    """Tests for invalid RaidNowRequest payloads."""

    def test_empty_list_raises(self):
        with pytest.raises(ValidationError):
            RaidNowRequest(source_playlist_ids=[])

    def test_empty_string_element_raises(self):
        with pytest.raises(ValidationError):
            RaidNowRequest(source_playlist_ids=[""])


# =============================================================================
# AddRaidUrlRequest
# =============================================================================


class TestAddRaidUrlRequestValid:
    """Tests for valid AddRaidUrlRequest payloads."""

    def test_minimal_request(self):
        req = AddRaidUrlRequest(
            url="https://open.spotify.com/playlist/abc123",
        )
        assert "abc123" in req.url
        assert req.auto_schedule is True
        assert req.schedule_value == "daily"

    def test_full_request(self):
        req = AddRaidUrlRequest(
            url="https://open.spotify.com/playlist/abc123",
            auto_schedule=False,
            schedule_value="weekly",
        )
        assert req.auto_schedule is False
        assert req.schedule_value == "weekly"

    def test_url_whitespace_stripped(self):
        req = AddRaidUrlRequest(
            url="  https://open.spotify.com/playlist/abc  "
        )
        assert req.url == (
            "https://open.spotify.com/playlist/abc"
        )

    def test_extra_fields_ignored(self):
        req = AddRaidUrlRequest(
            url="https://open.spotify.com/playlist/abc",
            unknown_field="ignored",
        )
        assert req.url is not None

    def test_valid_schedule_values(self):
        for val in [
            "daily", "weekly", "every_6h",
            "every_12h", "every_3d",
        ]:
            req = AddRaidUrlRequest(
                url="https://example.com/p",
                schedule_value=val,
            )
            assert req.schedule_value == val


class TestAddRaidUrlRequestInvalid:
    """Tests for invalid AddRaidUrlRequest payloads."""

    def test_missing_url_raises(self):
        with pytest.raises(ValidationError):
            AddRaidUrlRequest()

    def test_empty_url_raises(self):
        with pytest.raises(ValidationError):
            AddRaidUrlRequest(url="   ")

    def test_url_too_long_raises(self):
        with pytest.raises(ValidationError):
            AddRaidUrlRequest(url="x" * 1025)

    def test_invalid_schedule_value_raises(self):
        with pytest.raises(ValidationError):
            AddRaidUrlRequest(
                url="https://example.com/p",
                schedule_value="every_minute",
            )


# =============================================================================
# Schedule Time Support
# =============================================================================


class TestScheduleTime:
    """Tests for schedule_time on watch requests."""

    def test_watch_with_schedule_time(self):
        req = WatchPlaylistRequest(
            source_playlist_id="abc",
            schedule_value="daily",
            schedule_time="14:30",
        )
        assert req.schedule_time == "14:30"

    def test_watch_time_none_by_default(self):
        req = WatchPlaylistRequest(
            source_playlist_id="abc",
        )
        assert req.schedule_time is None

    def test_invalid_time_format_raises(self):
        with pytest.raises(ValidationError):
            WatchPlaylistRequest(
                source_playlist_id="abc",
                schedule_time="2pm",
            )

    def test_add_url_with_schedule_time(self):
        req = AddRaidUrlRequest(
            url="https://example.com/p",
            schedule_time="09:00",
        )
        assert req.schedule_time == "09:00"


class TestBuildCron:
    """Tests for build_cron helper."""

    def test_daily_cron(self):
        assert build_cron("daily", "14:30") == "30 14 * * *"

    def test_every_3d_cron(self):
        assert build_cron("every_3d", "09:00") == "00 09 */3 * *"

    def test_weekly_cron(self):
        assert build_cron("weekly", "22:15") == "15 22 * * 0"

    def test_unsupported_frequency_raises(self):
        with pytest.raises(ValueError):
            build_cron("every_6h", "12:00")


# =============================================================================
# UpdateRaidScheduleRequest
# =============================================================================


class TestUpdateRaidScheduleRequestValid:
    """Tests for valid UpdateRaidScheduleRequest."""

    def test_update_frequency(self):
        req = UpdateRaidScheduleRequest(
            schedule_value="weekly",
        )
        assert req.schedule_value == "weekly"

    def test_update_time(self):
        req = UpdateRaidScheduleRequest(
            schedule_time="10:00",
        )
        assert req.schedule_time == "10:00"

    def test_update_enabled(self):
        req = UpdateRaidScheduleRequest(
            is_enabled=False,
        )
        assert req.is_enabled is False

    def test_update_multiple_fields(self):
        req = UpdateRaidScheduleRequest(
            schedule_value="daily",
            schedule_time="08:00",
            is_enabled=True,
        )
        assert req.schedule_value == "daily"
        assert req.schedule_time == "08:00"
        assert req.is_enabled is True


class TestUpdateRaidScheduleRequestInvalid:
    """Tests for invalid UpdateRaidScheduleRequest."""

    def test_empty_request_raises(self):
        with pytest.raises(ValidationError):
            UpdateRaidScheduleRequest()

    def test_invalid_schedule_value_raises(self):
        with pytest.raises(ValidationError):
            UpdateRaidScheduleRequest(
                schedule_value="every_minute",
            )

    def test_invalid_time_format_raises(self):
        with pytest.raises(ValidationError):
            UpdateRaidScheduleRequest(
                schedule_time="3pm",
            )
