"""
Tests for raid panel request validation schemas.

Tests WatchPlaylistRequest, UnwatchPlaylistRequest, and
RaidNowRequest Pydantic models.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.raid_requests import (
    WatchPlaylistRequest,
    UnwatchPlaylistRequest,
    RaidNowRequest,
)


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
