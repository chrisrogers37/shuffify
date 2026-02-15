"""
Pydantic validation schemas for schedule API requests.

Validates schedule creation and update payloads.
"""

from typing import Optional, List, Dict, Any
from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

from shuffify.enums import (
    JobType, ScheduleType, IntervalValue, RotationMode,
)
from shuffify.shuffle_algorithms.registry import ShuffleRegistry

VALID_JOB_TYPES = set(JobType)
VALID_SCHEDULE_TYPES = set(ScheduleType)
VALID_INTERVAL_VALUES = set(IntervalValue)
VALID_ROTATION_MODES = set(RotationMode)


class ScheduleCreateRequest(BaseModel):
    """Schema for creating a new schedule."""

    job_type: str = Field(...)
    target_playlist_id: str = Field(..., min_length=1)
    target_playlist_name: str = Field(
        ..., min_length=1, max_length=255
    )
    schedule_type: str = Field(default=ScheduleType.INTERVAL)
    schedule_value: str = Field(default=IntervalValue.DAILY)
    source_playlist_ids: Optional[List[str]] = Field(
        default=None
    )
    algorithm_name: Optional[str] = Field(default=None)
    algorithm_params: Optional[Dict[str, Any]] = Field(
        default=None
    )

    @field_validator("job_type")
    @classmethod
    def validate_job_type(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_JOB_TYPES:
            raise ValueError(
                f"Invalid job_type '{v}'. Must be one of: "
                f"{', '.join(sorted(VALID_JOB_TYPES))}"
            )
        return v

    @field_validator("schedule_type")
    @classmethod
    def validate_schedule_type(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in VALID_SCHEDULE_TYPES:
            raise ValueError(
                f"Invalid schedule_type '{v}'. Must be one "
                f"of: {', '.join(sorted(VALID_SCHEDULE_TYPES))}"
            )
        return v

    @field_validator("schedule_value")
    @classmethod
    def validate_schedule_value(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("schedule_value cannot be empty")
        return v

    @field_validator("algorithm_name")
    @classmethod
    def validate_algorithm_name(
        cls, v: Optional[str]
    ) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        valid = set(
            ShuffleRegistry.get_available_algorithms().keys()
        )
        if v not in valid:
            raise ValueError(
                f"Invalid algorithm '{v}'. Valid options: "
                f"{', '.join(sorted(valid))}"
            )
        return v

    @model_validator(mode="after")
    def validate_job_requirements(self):
        """Cross-field validation for job type requirements."""
        if self.job_type in (JobType.RAID, JobType.RAID_AND_SHUFFLE):
            if not self.source_playlist_ids:
                raise ValueError(
                    f"source_playlist_ids required for "
                    f"job_type '{self.job_type}'"
                )
        if self.job_type in (
            JobType.SHUFFLE, JobType.RAID_AND_SHUFFLE
        ):
            if not self.algorithm_name:
                raise ValueError(
                    f"algorithm_name required for "
                    f"job_type '{self.job_type}'"
                )
        if self.job_type == JobType.ROTATE:
            params = self.algorithm_params or {}
            rotation_mode = params.get("rotation_mode")
            if not rotation_mode:
                raise ValueError(
                    "algorithm_params.rotation_mode "
                    "required for job_type 'rotate'"
                )
            if rotation_mode not in VALID_ROTATION_MODES:
                raise ValueError(
                    "Invalid rotation_mode '{}'. "
                    "Must be one of: {}".format(
                        rotation_mode,
                        ", ".join(
                            sorted(VALID_ROTATION_MODES)
                        ),
                    )
                )
            rotation_count = params.get(
                "rotation_count"
            )
            if rotation_count is not None:
                try:
                    count = int(rotation_count)
                    if count < 1:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise ValueError(
                        "rotation_count must be a "
                        "positive integer"
                    )
        if self.schedule_type == ScheduleType.INTERVAL:
            if self.schedule_value not in VALID_INTERVAL_VALUES:
                raise ValueError(
                    f"Invalid interval "
                    f"'{self.schedule_value}'. Must be one "
                    f"of: "
                    f"{', '.join(sorted(VALID_INTERVAL_VALUES))}"
                )
        if self.schedule_type == ScheduleType.CRON:
            parts = self.schedule_value.split()
            if len(parts) != 5:
                raise ValueError(
                    "Cron expression must have 5 fields: "
                    "minute hour day month day_of_week"
                )
        return self


class ScheduleUpdateRequest(BaseModel):
    """Schema for updating an existing schedule."""

    job_type: Optional[str] = None
    target_playlist_id: Optional[str] = None
    target_playlist_name: Optional[str] = None
    schedule_type: Optional[str] = None
    schedule_value: Optional[str] = None
    source_playlist_ids: Optional[List[str]] = None
    algorithm_name: Optional[str] = None
    algorithm_params: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None

    @field_validator("job_type")
    @classmethod
    def validate_job_type(
        cls, v: Optional[str]
    ) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in VALID_JOB_TYPES:
            raise ValueError(
                f"Invalid job_type '{v}'. Must be one of: "
                f"{', '.join(sorted(VALID_JOB_TYPES))}"
            )
        return v

    @field_validator("schedule_type")
    @classmethod
    def validate_schedule_type(
        cls, v: Optional[str]
    ) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        if v not in VALID_SCHEDULE_TYPES:
            raise ValueError(f"Invalid schedule_type '{v}'")
        return v

    @field_validator("algorithm_name")
    @classmethod
    def validate_algorithm_name(
        cls, v: Optional[str]
    ) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        valid = set(
            ShuffleRegistry.get_available_algorithms().keys()
        )
        if v not in valid:
            raise ValueError(f"Invalid algorithm '{v}'")
        return v

    class Config:
        extra = "ignore"
