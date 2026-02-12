"""
Tests for schedule request validation schemas.

Tests ScheduleCreateRequest and ScheduleUpdateRequest Pydantic models
for schedule creation and update payload validation.
"""

import pytest
from pydantic import ValidationError

from shuffify.schemas.schedule_requests import (
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
    VALID_JOB_TYPES,
    VALID_SCHEDULE_TYPES,
    VALID_INTERVAL_VALUES,
)


# =============================================================================
# Helpers
# =============================================================================

def _base_create_kwargs(**overrides):
    """Return minimal valid kwargs for ScheduleCreateRequest."""
    defaults = {
        "job_type": "shuffle",
        "target_playlist_id": "playlist_abc",
        "target_playlist_name": "My Playlist",
        "schedule_type": "interval",
        "schedule_value": "daily",
        "algorithm_name": "BasicShuffle",
    }
    defaults.update(overrides)
    return defaults


# =============================================================================
# ScheduleCreateRequest — Valid inputs
# =============================================================================

class TestScheduleCreateRequestValid:
    """Tests for valid ScheduleCreateRequest payloads."""

    def test_valid_shuffle_schedule(self):
        req = ScheduleCreateRequest(**_base_create_kwargs())
        assert req.job_type == "shuffle"
        assert req.algorithm_name == "BasicShuffle"

    def test_valid_raid_schedule(self):
        req = ScheduleCreateRequest(**_base_create_kwargs(
            job_type="raid",
            source_playlist_ids=["src_1"],
            algorithm_name=None,
        ))
        assert req.job_type == "raid"
        assert req.source_playlist_ids == ["src_1"]

    def test_valid_raid_and_shuffle(self):
        req = ScheduleCreateRequest(**_base_create_kwargs(
            job_type="raid_and_shuffle",
            source_playlist_ids=["src_1", "src_2"],
            algorithm_name="BasicShuffle",
        ))
        assert req.job_type == "raid_and_shuffle"
        assert len(req.source_playlist_ids) == 2

    def test_valid_cron_expression(self):
        req = ScheduleCreateRequest(**_base_create_kwargs(
            schedule_type="cron",
            schedule_value="0 6 * * 1",
        ))
        assert req.schedule_type == "cron"
        assert req.schedule_value == "0 6 * * 1"

    def test_all_interval_values(self):
        for val in VALID_INTERVAL_VALUES:
            req = ScheduleCreateRequest(**_base_create_kwargs(
                schedule_value=val,
            ))
            assert req.schedule_value == val

    def test_defaults_applied(self):
        req = ScheduleCreateRequest(**_base_create_kwargs(
            schedule_type="interval",
            schedule_value="daily",
        ))
        assert req.schedule_type == "interval"
        assert req.schedule_value == "daily"

    def test_algorithm_params_accepted(self):
        req = ScheduleCreateRequest(**_base_create_kwargs(
            algorithm_params={"keep_first": 5},
        ))
        assert req.algorithm_params == {"keep_first": 5}


# =============================================================================
# ScheduleCreateRequest — Invalid inputs
# =============================================================================

class TestScheduleCreateRequestInvalid:
    """Tests for invalid ScheduleCreateRequest payloads."""

    def test_invalid_job_type(self):
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreateRequest(**_base_create_kwargs(
                job_type="invalid",
            ))
        errors = exc_info.value.errors()
        assert any("job_type" in str(e) for e in errors)

    def test_missing_source_for_raid(self):
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreateRequest(**_base_create_kwargs(
                job_type="raid",
                source_playlist_ids=None,
                algorithm_name=None,
            ))
        errors = exc_info.value.errors()
        assert any("source_playlist_ids" in str(e) for e in errors)

    def test_missing_algorithm_for_shuffle(self):
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreateRequest(**_base_create_kwargs(
                job_type="shuffle",
                algorithm_name=None,
            ))
        errors = exc_info.value.errors()
        assert any("algorithm_name" in str(e) for e in errors)

    def test_invalid_algorithm_name(self):
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreateRequest(**_base_create_kwargs(
                algorithm_name="nonexistent_algo",
            ))
        errors = exc_info.value.errors()
        assert any("algorithm" in str(e).lower() for e in errors)

    def test_invalid_schedule_type(self):
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreateRequest(**_base_create_kwargs(
                schedule_type="monthly",
            ))
        errors = exc_info.value.errors()
        assert any("schedule_type" in str(e) for e in errors)

    def test_invalid_interval_value(self):
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreateRequest(**_base_create_kwargs(
                schedule_type="interval",
                schedule_value="hourly",
            ))
        errors = exc_info.value.errors()
        assert any("interval" in str(e).lower() for e in errors)

    def test_invalid_cron_wrong_fields(self):
        with pytest.raises(ValidationError) as exc_info:
            ScheduleCreateRequest(**_base_create_kwargs(
                schedule_type="cron",
                schedule_value="0 6 *",
            ))
        errors = exc_info.value.errors()
        assert any("5 fields" in str(e) for e in errors)

    def test_empty_target_playlist_id(self):
        with pytest.raises(ValidationError):
            ScheduleCreateRequest(**_base_create_kwargs(
                target_playlist_id="",
            ))

    def test_empty_target_playlist_name(self):
        with pytest.raises(ValidationError):
            ScheduleCreateRequest(**_base_create_kwargs(
                target_playlist_name="",
            ))

    def test_whitespace_job_type_normalized(self):
        req = ScheduleCreateRequest(**_base_create_kwargs(
            job_type="  shuffle  ",
        ))
        assert req.job_type == "shuffle"

    def test_whitespace_only_algorithm_name_becomes_none(self):
        """Whitespace-only algorithm_name normalizes to None."""
        with pytest.raises(ValidationError):
            # job_type=shuffle requires algorithm_name, so None fails
            ScheduleCreateRequest(**_base_create_kwargs(
                algorithm_name="   ",
            ))


# =============================================================================
# ScheduleUpdateRequest
# =============================================================================

class TestScheduleUpdateRequest:
    """Tests for ScheduleUpdateRequest schema."""

    def test_valid_partial_update_is_enabled(self):
        req = ScheduleUpdateRequest(is_enabled=False)
        assert req.is_enabled is False
        assert req.job_type is None

    def test_all_fields_none_is_valid(self):
        req = ScheduleUpdateRequest()
        assert req.job_type is None
        assert req.schedule_type is None
        assert req.algorithm_name is None
        assert req.is_enabled is None

    def test_invalid_job_type_on_update(self):
        with pytest.raises(ValidationError) as exc_info:
            ScheduleUpdateRequest(job_type="invalid")
        errors = exc_info.value.errors()
        assert any("job_type" in str(e) for e in errors)

    def test_invalid_algorithm_on_update(self):
        with pytest.raises(ValidationError) as exc_info:
            ScheduleUpdateRequest(algorithm_name="nonexistent")
        errors = exc_info.value.errors()
        assert any("algorithm" in str(e).lower() for e in errors)

    def test_valid_job_type_on_update(self):
        req = ScheduleUpdateRequest(job_type="raid")
        assert req.job_type == "raid"

    def test_valid_schedule_type_on_update(self):
        req = ScheduleUpdateRequest(schedule_type="cron")
        assert req.schedule_type == "cron"

    def test_invalid_schedule_type_on_update(self):
        with pytest.raises(ValidationError):
            ScheduleUpdateRequest(schedule_type="monthly")

    def test_extra_fields_ignored(self):
        req = ScheduleUpdateRequest(
            is_enabled=True,
            extra_field="should_be_ignored",
        )
        assert req.is_enabled is True
        assert not hasattr(req, "extra_field")

    def test_whitespace_job_type_normalized(self):
        req = ScheduleUpdateRequest(job_type="  shuffle  ")
        assert req.job_type == "shuffle"

    def test_whitespace_algorithm_name_becomes_none(self):
        req = ScheduleUpdateRequest(algorithm_name="   ")
        assert req.algorithm_name is None
