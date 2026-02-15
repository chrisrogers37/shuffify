"""
Pydantic schemas for playlist pair (archive) API endpoints.
"""

import re
from typing import List, Optional

from pydantic import BaseModel, field_validator, model_validator


TRACK_URI_PATTERN = re.compile(r"^spotify:track:[a-zA-Z0-9]{22}$")


class CreatePairRequest(BaseModel):
    """Request to create a playlist pair.

    Supports two modes:
    - create_new=True: create a new Spotify playlist as archive
    - archive_playlist_id + archive_playlist_name: use existing
    """

    production_playlist_name: Optional[str] = None
    create_new: bool = False
    archive_playlist_id: Optional[str] = None
    archive_playlist_name: Optional[str] = None

    @field_validator("production_playlist_name")
    @classmethod
    def validate_production_name(cls, v):
        if v is not None:
            v = v.strip()
            if len(v) < 1 or len(v) > 255:
                raise ValueError(
                    "production_playlist_name must be 1-255 chars"
                )
        return v

    @field_validator("archive_playlist_name")
    @classmethod
    def validate_archive_name(cls, v):
        if v is not None:
            v = v.strip()
            if len(v) < 1 or len(v) > 255:
                raise ValueError(
                    "archive_playlist_name must be 1-255 chars"
                )
        return v

    @model_validator(mode="after")
    def validate_mode(self):
        if self.create_new and self.archive_playlist_id:
            raise ValueError(
                "Cannot specify both create_new and "
                "archive_playlist_id"
            )
        if (
            not self.create_new
            and not self.archive_playlist_id
        ):
            raise ValueError(
                "Must specify either create_new=true or "
                "archive_playlist_id"
            )
        if (
            self.archive_playlist_id
            and not self.archive_playlist_name
        ):
            raise ValueError(
                "archive_playlist_name is required when "
                "using existing playlist"
            )
        return self


class ArchiveTracksRequest(BaseModel):
    """Request to archive tracks to the paired archive playlist."""

    track_uris: List[str]

    @field_validator("track_uris")
    @classmethod
    def validate_track_uris(cls, v):
        if not v:
            raise ValueError("track_uris must not be empty")
        for uri in v:
            if not TRACK_URI_PATTERN.match(uri):
                raise ValueError(
                    f"Invalid track URI format: {uri}"
                )
        return v


class UnarchiveTracksRequest(BaseModel):
    """Request to unarchive tracks back to production playlist."""

    track_uris: List[str]

    @field_validator("track_uris")
    @classmethod
    def validate_track_uris(cls, v):
        if not v:
            raise ValueError("track_uris must not be empty")
        for uri in v:
            if not TRACK_URI_PATTERN.match(uri):
                raise ValueError(
                    f"Invalid track URI format: {uri}"
                )
        return v
