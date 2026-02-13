"""
Pydantic schemas for playlist snapshot request validation.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class ManualSnapshotRequest(BaseModel):
    """Schema for creating a manual snapshot."""

    playlist_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable playlist name",
    )
    track_uris: List[str] = Field(
        ...,
        min_length=0,
        description=(
            "Ordered list of track URIs to snapshot"
        ),
    )
    trigger_description: Optional[str] = Field(
        default=None,
        max_length=500,
        description=(
            "Optional description of why this snapshot "
            "was created"
        ),
    )

    @field_validator("track_uris")
    @classmethod
    def validate_track_uris(
        cls, v: List[str]
    ) -> List[str]:
        """Ensure all URIs look like Spotify track URIs."""
        for uri in v:
            if not uri.startswith("spotify:track:"):
                raise ValueError(
                    f"Invalid track URI format: {uri}"
                )
        return v

    class Config:
        extra = "ignore"
