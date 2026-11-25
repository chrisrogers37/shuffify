"""
Pydantic schemas for raid panel API endpoints.
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from shuffify.enums import IntervalValue
from shuffify.schemas.workshop_finalize_requests import PlaylistID
from shuffify.services.schedule_utils import (
    TIME_RE as _TIME_RE,
)
from shuffify.services.upstream_source_service import MAX_SOURCES_PER_TARGET
from shuffify.spotify.url_parser import parse_spotify_playlist_url


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
        # Fail fast at the schema boundary if the URL is not a
        # recognizable Spotify playlist URL/URI/ID. Pushes the
        # 400 ahead of any business logic.
        if parse_spotify_playlist_url(v) is None:
            raise ValueError(
                "Invalid Spotify playlist URL"
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


class RaidNowRequest(BaseModel):
    """Request to trigger an immediate raid."""

    model_config = {"extra": "ignore"}

    # Null means every configured source; a list names at most as many as one playlist can have.
    source_playlist_ids: Optional[List[PlaylistID]] = Field(
        default=None, min_length=1, max_length=MAX_SOURCES_PER_TARGET
    )
