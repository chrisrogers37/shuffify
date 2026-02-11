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

from shuffify.shuffle_algorithms.registry import ShuffleRegistry


VALID_JOB_TYPES = {"raid", "shuffle", "raid_and_shuffle"}
VALID_SCHEDULE_TYPES = {"interval", "cron"}
VALID_INTERVAL_VALUES = {
    "every_6h",
    "every_12h",
    "daily",
    "every_3d",
    "weekly",
}


class ScheduleCreateRequest(BaseModel):
    """Schema for creating a new schedule."""

    job_type: str = Field(...)
    target_playlist_id: str = Field(..., min_length=1)
    target_playlist_name: str = Field(
        ..., min_length=1, max_length=255
    )
    schedule_type: str = Field(default="interval")
    schedule_value: str = Field(default="daily")
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
        if self.job_type in ("raid", "raid_and_shuffle"):
            if not self.source_playlist_ids:
                raise ValueError(
                    f"source_playlist_ids required for "
                    f"job_type '{self.job_type}'"
                )
        if self.job_type in ("shuffle", "raid_and_shuffle"):
            if not self.algorithm_name:
                raise ValueError(
                    f"algorithm_name required for "
                    f"job_type '{self.job_type}'"
                )
        if self.schedule_type == "interval":
            if self.schedule_value not in VALID_INTERVAL_VALUES:
                raise ValueError(
                    f"Invalid interval "
                    f"'{self.schedule_value}'. Must be one "
                    f"of: "
                    f"{', '.join(sorted(VALID_INTERVAL_VALUES))}"
                )
        if self.schedule_type == "cron":
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
