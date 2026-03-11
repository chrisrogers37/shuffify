"""
Tests for rotate-specific schedule schema validation.

Tests ScheduleCreateRequest and ScheduleUpdateRequest
with rotate job type parameters (swap mode only).
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.schedule_requests import (
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
)


# =============================================================================
# ScheduleCreateRequest — Valid Rotate
# =============================================================================


class TestRotateScheduleCreateValid:
    """Tests for valid rotate schedule creation."""

    def test_swap_mode(self):
        req = ScheduleCreateRequest(
            job_type="rotate",
            target_playlist_id="p1",
            target_playlist_name="My Playlist",
            algorithm_params={
                "rotation_mode": "swap",
                "target_size": 50,
            },
        )
        assert req.job_type == "rotate"

    def test_rotation_count_optional(self):
        req = ScheduleCreateRequest(
            job_type="rotate",
            target_playlist_id="p1",
            target_playlist_name="My Playlist",
            algorithm_params={
                "rotation_mode": "swap",
                "target_size": 50,
            },
        )
        assert (
            req.algorithm_params.get("rotation_count")
            is None
        )

    def test_rotation_count_provided(self):
        req = ScheduleCreateRequest(
            job_type="rotate",
            target_playlist_id="p1",
            target_playlist_name="My Playlist",
            algorithm_params={
                "rotation_mode": "swap",
                "target_size": 50,
                "rotation_count": 10,
            },
        )
        assert (
            req.algorithm_params["rotation_count"] == 10
        )

    def test_algorithm_name_not_required(self):
        req = ScheduleCreateRequest(
            job_type="rotate",
            target_playlist_id="p1",
            target_playlist_name="My Playlist",
            algorithm_params={
                "rotation_mode": "swap",
                "target_size": 50,
            },
        )
        assert req.algorithm_name is None

    def test_source_ids_not_required(self):
        req = ScheduleCreateRequest(
            job_type="rotate",
            target_playlist_id="p1",
            target_playlist_name="My Playlist",
            algorithm_params={
                "rotation_mode": "swap",
                "target_size": 50,
            },
        )
        assert req.source_playlist_ids is None

    def test_rotation_count_string_integer(self):
        req = ScheduleCreateRequest(
            job_type="rotate",
            target_playlist_id="p1",
            target_playlist_name="My Playlist",
            algorithm_params={
                "rotation_mode": "swap",
                "target_size": 50,
                "rotation_count": "7",
            },
        )
        assert req.algorithm_params is not None

    def test_extra_params_preserved(self):
        req = ScheduleCreateRequest(
            job_type="rotate",
            target_playlist_id="p1",
            target_playlist_name="My Playlist",
            algorithm_params={
                "rotation_mode": "swap",
                "target_size": 50,
                "custom_key": "value",
            },
        )
        assert (
            req.algorithm_params["custom_key"] == "value"
        )

    def test_mode_defaults_to_swap(self):
        """rotation_mode defaults to swap if omitted."""
        req = ScheduleCreateRequest(
            job_type="rotate",
            target_playlist_id="p1",
            target_playlist_name="My Playlist",
            algorithm_params={
                "target_size": 50,
            },
        )
        assert req.job_type == "rotate"


# =============================================================================
# ScheduleCreateRequest — Invalid Rotate
# =============================================================================


class TestRotateScheduleCreateInvalid:
    """Tests for invalid rotate schedule creation."""

    def test_missing_target_size(self):
        """target_size is always required for rotate."""
        with pytest.raises(
            ValidationError,
            match="target_size",
        ):
            ScheduleCreateRequest(
                job_type="rotate",
                target_playlist_id="p1",
                target_playlist_name="My Playlist",
                algorithm_params={
                    "rotation_mode": "swap",
                },
            )

    def test_null_algorithm_params(self):
        with pytest.raises(
            ValidationError,
            match="target_size",
        ):
            ScheduleCreateRequest(
                job_type="rotate",
                target_playlist_id="p1",
                target_playlist_name="My Playlist",
                algorithm_params=None,
            )

    def test_no_algorithm_params(self):
        with pytest.raises(
            ValidationError,
            match="target_size",
        ):
            ScheduleCreateRequest(
                job_type="rotate",
                target_playlist_id="p1",
                target_playlist_name="My Playlist",
            )

    def test_invalid_rotation_mode(self):
        with pytest.raises(
            ValidationError,
            match="Invalid rotation_mode",
        ):
            ScheduleCreateRequest(
                job_type="rotate",
                target_playlist_id="p1",
                target_playlist_name="My Playlist",
                algorithm_params={
                    "rotation_mode": "archive_oldest",
                    "target_size": 50,
                },
            )

    def test_refresh_mode_rejected(self):
        with pytest.raises(
            ValidationError,
            match="Invalid rotation_mode",
        ):
            ScheduleCreateRequest(
                job_type="rotate",
                target_playlist_id="p1",
                target_playlist_name="My Playlist",
                algorithm_params={
                    "rotation_mode": "refresh",
                    "target_size": 50,
                },
            )

    def test_rotation_count_zero(self):
        with pytest.raises(
            ValidationError,
            match="positive integer",
        ):
            ScheduleCreateRequest(
                job_type="rotate",
                target_playlist_id="p1",
                target_playlist_name="My Playlist",
                algorithm_params={
                    "rotation_mode": "swap",
                    "target_size": 50,
                    "rotation_count": 0,
                },
            )

    def test_rotation_count_negative(self):
        with pytest.raises(
            ValidationError,
            match="positive integer",
        ):
            ScheduleCreateRequest(
                job_type="rotate",
                target_playlist_id="p1",
                target_playlist_name="My Playlist",
                algorithm_params={
                    "rotation_mode": "swap",
                    "target_size": 50,
                    "rotation_count": -1,
                },
            )

    def test_rotation_count_non_numeric(self):
        with pytest.raises(
            ValidationError,
            match="positive integer",
        ):
            ScheduleCreateRequest(
                job_type="rotate",
                target_playlist_id="p1",
                target_playlist_name="My Playlist",
                algorithm_params={
                    "rotation_mode": "swap",
                    "target_size": 50,
                    "rotation_count": "abc",
                },
            )

    def test_target_size_zero(self):
        with pytest.raises(
            ValidationError,
            match="positive integer",
        ):
            ScheduleCreateRequest(
                job_type="rotate",
                target_playlist_id="p1",
                target_playlist_name="My Playlist",
                algorithm_params={
                    "rotation_mode": "swap",
                    "target_size": 0,
                },
            )

    def test_target_size_non_numeric(self):
        with pytest.raises(
            ValidationError,
            match="positive integer",
        ):
            ScheduleCreateRequest(
                job_type="rotate",
                target_playlist_id="p1",
                target_playlist_name="My Playlist",
                algorithm_params={
                    "rotation_mode": "swap",
                    "target_size": "abc",
                },
            )

    def test_rotate_in_valid_job_types(self):
        """Rotate is recognized as a valid job type."""
        req = ScheduleCreateRequest(
            job_type="rotate",
            target_playlist_id="p1",
            target_playlist_name="My Playlist",
            algorithm_params={
                "rotation_mode": "swap",
                "target_size": 50,
            },
        )
        assert req.job_type == "rotate"


# =============================================================================
# ScheduleUpdateRequest — Rotate
# =============================================================================


class TestRotateScheduleUpdate:
    """Tests for updating rotate schedules."""

    def test_update_job_type_to_rotate(self):
        req = ScheduleUpdateRequest(
            job_type="rotate"
        )
        assert req.job_type == "rotate"

    def test_update_algorithm_params(self):
        req = ScheduleUpdateRequest(
            algorithm_params={
                "rotation_mode": "swap",
                "rotation_count": 10,
                "target_size": 50,
            }
        )
        assert (
            req.algorithm_params["rotation_mode"]
            == "swap"
        )

    def test_rotate_passes_job_type_validator(self):
        req = ScheduleUpdateRequest(
            job_type="rotate"
        )
        assert req.job_type == "rotate"
