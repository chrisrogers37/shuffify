"""
Pydantic schemas for raid panel API endpoints.
"""

from typing import List, Optional

from pydantic import BaseModel, field_validator, model_validator

from shuffify.enums import IntervalValue
from shuffify.services.schedule_utils import (
    TIME_RE as _TIME_RE,
)


class WatchPlaylistRequest(BaseModel):
    """Request to watch a playlist as a raid source."""

    model_config = {"extra": "ignore"}

    source_playlist_id: str
    source_playlist_name: Optional[str] = None
    source_url: Optional[str] = None
    auto_schedule: bool = True
    schedule_value: str = "daily"
    schedule_time: Optional[str] = None

    @field_validator("source_playlist_id")
    @classmethod
    def validate_source_id(cls, v):
        v = v.strip()
        if not v:
            raise ValueError(
                "source_playlist_id must not be empty"
            )
        return v

    @field_validator("source_playlist_name")
    @classmethod
    def validate_source_name(cls, v):
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if len(v) > 255:
                raise ValueError(
                    "source_playlist_name must be 255 chars "
                    "or fewer"
                )
        return v

    @field_validator("schedule_value")
    @classmethod
    def validate_schedule_value(cls, v):
        v = v.strip().lower()
        valid = [e.value for e in IntervalValue]
        if v not in valid:
            raise ValueError(
                f"schedule_value must be one of: "
                f"{', '.join(valid)}"
            )
        return v

    @field_validator("schedule_time")
    @classmethod
    def validate_schedule_time(cls, v):
        if v is not None:
            v = v.strip()
            if not _TIME_RE.match(v):
                raise ValueError(
                    "schedule_time must be HH:MM format"
                )
        return v


class WatchSearchQueryRequest(BaseModel):
    """Request to watch a search query as a raid source."""

    model_config = {"extra": "ignore"}

    search_query: str
    source_name: Optional[str] = None
    auto_schedule: bool = True
    schedule_value: str = "daily"
    schedule_time: Optional[str] = None

    @field_validator("search_query")
    @classmethod
    def validate_search_query(cls, v):
        v = v.strip()
        if not v:
            raise ValueError(
                "search_query must not be empty"
            )
        if len(v) > 500:
            raise ValueError(
                "search_query must be 500 chars or fewer"
            )
        return v

    @field_validator("schedule_value")
    @classmethod
    def validate_schedule_value(cls, v):
        v = v.strip().lower()
        valid = [e.value for e in IntervalValue]
        if v not in valid:
            raise ValueError(
                f"schedule_value must be one of: "
                f"{', '.join(valid)}"
            )
        return v

    @field_validator("schedule_time")
    @classmethod
    def validate_schedule_time(cls, v):
        if v is not None:
            v = v.strip()
            if not _TIME_RE.match(v):
                raise ValueError(
                    "schedule_time must be HH:MM format"
                )
        return v


class UnwatchPlaylistRequest(BaseModel):
    """Request to remove a source from the watch list."""

    model_config = {"extra": "ignore"}

    source_id: int

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, v):
        if v <= 0:
            raise ValueError("source_id must be positive")
        return v


class AddRaidUrlRequest(BaseModel):
    """Request to add an external playlist by URL."""

    model_config = {"extra": "ignore"}

    url: str
    auto_schedule: bool = True
    schedule_value: str = "daily"
    schedule_time: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("url must not be empty")
        if len(v) > 1024:
            raise ValueError(
                "url must be 1024 chars or fewer"
            )
        return v

    @field_validator("schedule_value")
    @classmethod
    def validate_schedule_value(cls, v):
        v = v.strip().lower()
        valid = [e.value for e in IntervalValue]
        if v not in valid:
            raise ValueError(
                f"schedule_value must be one of: "
                f"{', '.join(valid)}"
            )
        return v

    @field_validator("schedule_time")
    @classmethod
    def validate_schedule_time(cls, v):
        if v is not None:
            v = v.strip()
            if not _TIME_RE.match(v):
                raise ValueError(
                    "schedule_time must be HH:MM format"
                )
        return v


class UpdateRaidScheduleRequest(BaseModel):
    """Request to update a raid schedule."""

    model_config = {"extra": "ignore"}

    schedule_value: Optional[str] = None
    schedule_time: Optional[str] = None
    is_enabled: Optional[bool] = None

    @field_validator("schedule_value")
    @classmethod
    def validate_schedule_value(cls, v):
        if v is not None:
            v = v.strip().lower()
            valid = [e.value for e in IntervalValue]
            if v not in valid:
                raise ValueError(
                    f"schedule_value must be one of: "
                    f"{', '.join(valid)}"
                )
        return v

    @field_validator("schedule_time")
    @classmethod
    def validate_schedule_time(cls, v):
        if v is not None:
            v = v.strip()
            if v and not _TIME_RE.match(v):
                raise ValueError(
                    "schedule_time must be HH:MM format"
                )
            if not v:
                return None
        return v

    @model_validator(mode="after")
    def at_least_one_field(self):
        if (
            self.schedule_value is None
            and self.schedule_time is None
            and self.is_enabled is None
        ):
            raise ValueError(
                "At least one field must be provided"
            )
        return self


class CreateRaidScheduleRequest(BaseModel):
    """Request to create a raid/drip schedule from the panel."""

    model_config = {"extra": "ignore"}

    job_type: str
    schedule_type: str = "interval"
    schedule_value: str = "daily"
    source_playlist_ids: Optional[List[str]] = None

    @field_validator("job_type")
    @classmethod
    def validate_job_type(cls, v):
        valid = {"raid", "drip", "raid_and_drip"}
        if v not in valid:
            raise ValueError(
                "job_type must be one of: "
                "{}".format(", ".join(sorted(valid)))
            )
        return v

    @field_validator("schedule_type")
    @classmethod
    def validate_schedule_type(cls, v):
        valid = {"interval", "cron"}
        if v not in valid:
            raise ValueError(
                "schedule_type must be 'interval' or 'cron'"
            )
        return v


class RaidNowRequest(BaseModel):
    """Request to trigger an immediate raid."""

    model_config = {"extra": "ignore"}

    source_playlist_ids: Optional[List[str]] = None

    @field_validator("source_playlist_ids")
    @classmethod
    def validate_source_ids(cls, v):
        if v is not None:
            if not v:
                raise ValueError(
                    "source_playlist_ids must not be empty "
                    "(use null for all sources)"
                )
            for sid in v:
                if not sid or not sid.strip():
                    raise ValueError(
                        "Each source_playlist_id must be "
                        "non-empty"
                    )
        return v
