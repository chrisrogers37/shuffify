"""
Pydantic schemas for playlist preference API endpoints.
"""

import re
from typing import List

from pydantic import BaseModel, field_validator


SPOTIFY_ID_PATTERN = re.compile(r"^[a-zA-Z0-9]{1,255}$")


class SaveOrderRequest(BaseModel):
    """Request to save playlist display order."""

    playlist_ids: List[str]

    @field_validator("playlist_ids")
    @classmethod
    def validate_playlist_ids(cls, v):
        if not v:
            raise ValueError(
                "playlist_ids must not be empty"
            )
        if len(v) > 500:
            raise ValueError(
                "playlist_ids cannot exceed 500 items"
            )
        for pid in v:
            if not SPOTIFY_ID_PATTERN.match(pid):
                raise ValueError(
                    f"Invalid playlist ID format: {pid}"
                )
        return v
