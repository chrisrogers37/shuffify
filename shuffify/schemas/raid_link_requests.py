"""
Pydantic schemas for raid playlist link API endpoints.
"""

from typing import Optional

from pydantic import BaseModel, field_validator, model_validator


class CreateRaidLinkRequest(BaseModel):
    """Request to create a raid playlist link."""

    model_config = {"extra": "ignore"}

    raid_playlist_id: Optional[str] = None
    create_new: bool = True
    drip_count: int = 3
    drip_enabled: bool = False

    @field_validator("raid_playlist_id")
    @classmethod
    def validate_raid_playlist_id(cls, v):
        if v is not None:
            v = v.strip()
            if not v:
                return None
        return v

    @field_validator("drip_count")
    @classmethod
    def validate_drip_count(cls, v):
        if v < 1:
            raise ValueError(
                "drip_count must be at least 1"
            )
        if v > 50:
            raise ValueError(
                "drip_count must be 50 or fewer"
            )
        return v

    @model_validator(mode="after")
    def require_id_or_create(self):
        if not self.create_new and not self.raid_playlist_id:
            raise ValueError(
                "raid_playlist_id is required when "
                "create_new is false"
            )
        return self


class UpdateRaidLinkRequest(BaseModel):
    """Request to update a raid playlist link."""

    model_config = {"extra": "ignore"}

    drip_count: Optional[int] = None
    drip_enabled: Optional[bool] = None

    @field_validator("drip_count")
    @classmethod
    def validate_drip_count(cls, v):
        if v is not None:
            if v < 1:
                raise ValueError(
                    "drip_count must be at least 1"
                )
            if v > 50:
                raise ValueError(
                    "drip_count must be 50 or fewer"
                )
        return v

    @model_validator(mode="after")
    def at_least_one_field(self):
        if (
            self.drip_count is None
            and self.drip_enabled is None
        ):
            raise ValueError(
                "At least one field must be provided"
            )
        return self


class UpdateSourceRaidCountRequest(BaseModel):
    """Request to update a source's raid_count."""

    model_config = {"extra": "ignore"}

    source_id: int
    raid_count: int

    @field_validator("raid_count")
    @classmethod
    def validate_raid_count(cls, v):
        if v < 1:
            raise ValueError(
                "raid_count must be at least 1"
            )
        if v > 100:
            raise ValueError(
                "raid_count must be 100 or fewer"
            )
        return v
