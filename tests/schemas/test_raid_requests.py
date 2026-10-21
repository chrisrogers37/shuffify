"""
Tests for raid panel request validation schemas.

Tests UnwatchPlaylistRequest, AddRaidUrlRequest,
and RaidNowRequest Pydantic models.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.raid_requests import (
    AddRaidUrlRequest,
    RaidNowRequest,
    UnwatchPlaylistRequest,
)
from shuffify.services.schedule_utils import build_cron

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


# Real 22-char Spotify playlist ID used in the valid-URL fixtures.
_VALID_PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"
_VALID_PLAYLIST_URL = (
    f"https://open.spotify.com/playlist/{_VALID_PLAYLIST_ID}"
)


class TestAddRaidUrlRequestValid:
    """Tests for valid AddRaidUrlRequest payloads."""

    def test_minimal_request(self):
        req = AddRaidUrlRequest(
            url=_VALID_PLAYLIST_URL,
        )
        assert _VALID_PLAYLIST_ID in req.url
        assert req.auto_schedule is True
        assert req.schedule_value == "daily"

    def test_full_request(self):
        req = AddRaidUrlRequest(
            url=_VALID_PLAYLIST_URL,
            auto_schedule=False,
            schedule_value="weekly",
        )
        assert req.auto_schedule is False
        assert req.schedule_value == "weekly"

    def test_url_whitespace_stripped(self):
        req = AddRaidUrlRequest(
            url=f"  {_VALID_PLAYLIST_URL}  "
        )
        assert req.url == _VALID_PLAYLIST_URL

    def test_extra_fields_ignored(self):
        req = AddRaidUrlRequest(
            url=_VALID_PLAYLIST_URL,
            unknown_field="ignored",
        )
        assert req.url is not None

    def test_valid_schedule_values(self):
        for val in [
            "daily", "weekly", "every_6h",
            "every_12h", "every_3d",
        ]:
            req = AddRaidUrlRequest(
                url=_VALID_PLAYLIST_URL,
                schedule_value=val,
            )
            assert req.schedule_value == val

    def test_schedule_value_case_insensitive(self):
        req = AddRaidUrlRequest(
            url=_VALID_PLAYLIST_URL,
            schedule_value="DAILY",
        )
        assert req.schedule_value == "daily"

    def test_accepts_spotify_uri(self):
        """URI form is accepted by the validator."""
        req = AddRaidUrlRequest(
            url=f"spotify:playlist:{_VALID_PLAYLIST_ID}",
        )
        assert req.url == f"spotify:playlist:{_VALID_PLAYLIST_ID}"

    def test_accepts_bare_playlist_id(self):
        """Bare 22-char playlist ID is accepted."""
        req = AddRaidUrlRequest(url=_VALID_PLAYLIST_ID)
        assert req.url == _VALID_PLAYLIST_ID


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
                url=_VALID_PLAYLIST_URL,
                schedule_value="every_minute",
            )

    def test_non_spotify_url_raises(self):
        """Non-Spotify URLs are rejected at the schema boundary."""
        with pytest.raises(ValidationError) as exc:
            AddRaidUrlRequest(url="https://example.com/p")
        assert "Invalid Spotify playlist URL" in str(exc.value)

    def test_malformed_spotify_url_raises(self):
        """Spotify domain but wrong path → rejected."""
        with pytest.raises(ValidationError):
            AddRaidUrlRequest(
                url="https://open.spotify.com/album/abc123",
            )

    def test_short_id_raises(self):
        """22-char check is enforced."""
        with pytest.raises(ValidationError):
            AddRaidUrlRequest(
                url="https://open.spotify.com/playlist/abc123",
            )


# =============================================================================
# Schedule Time Support
# =============================================================================


class TestScheduleTime:
    """Tests for schedule_time on raid URL requests."""

    def test_add_url_with_schedule_time(self):
        req = AddRaidUrlRequest(
            url=_VALID_PLAYLIST_URL,
            schedule_time="09:00",
        )
        assert req.schedule_time == "09:00"

    def test_add_url_time_none_by_default(self):
        req = AddRaidUrlRequest(url=_VALID_PLAYLIST_URL)
        assert req.schedule_time is None

    def test_invalid_time_format_raises(self):
        with pytest.raises(ValidationError):
            AddRaidUrlRequest(
                url=_VALID_PLAYLIST_URL,
                schedule_time="2pm",
            )


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
