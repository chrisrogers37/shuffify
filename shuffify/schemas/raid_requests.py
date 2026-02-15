"""
Pydantic schemas for raid panel API endpoints.
"""

from typing import List, Optional

from pydantic import BaseModel, field_validator

from shuffify.enums import IntervalValue


class WatchPlaylistRequest(BaseModel):
    """Request to watch a playlist as a raid source."""

    model_config = {"extra": "ignore"}

    source_playlist_id: str
    source_playlist_name: Optional[str] = None
    source_url: Optional[str] = None
    auto_schedule: bool = True
    schedule_value: str = "daily"

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
