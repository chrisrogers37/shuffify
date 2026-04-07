"""
Request validation schemas for track lock operations.
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class TrackLockToggleRequest(BaseModel):
    """Schema for toggling a track lock."""

    track_uri: str = Field(
        ..., description="Spotify track URI to lock/unlock"
    )
    position: int = Field(
        ..., ge=0, description="0-indexed track position"
    )

    @field_validator("track_uri")
    @classmethod
    def validate_track_uri(cls, v: str) -> str:
        """Ensure URI looks like a Spotify track URI."""
        if not v.startswith("spotify:track:"):
            raise ValueError(
                f"Invalid track URI format: {v}"
            )
        return v


class TrackLockBulkUnlockRequest(BaseModel):
    """Schema for bulk-unlocking tracks."""

    track_uris: Optional[List[str]] = Field(
        default=None,
        description=(
            "Specific track URIs to unlock, "
            "or null to unlock all"
        ),
    )

    @field_validator("track_uris")
    @classmethod
    def validate_track_uris(
        cls, v: Optional[List[str]]
    ) -> Optional[List[str]]:
        """Validate URIs if provided."""
        if v is not None:
            for uri in v:
                if not uri.startswith("spotify:track:"):
                    raise ValueError(
                        f"Invalid track URI format: {uri}"
                    )
        return v
