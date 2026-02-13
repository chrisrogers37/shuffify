"""
Pydantic validation schemas for user settings requests.

Validates settings update payloads from the settings form.
"""

from typing import Optional
from pydantic import BaseModel, Field, field_validator

from shuffify.shuffle_algorithms.registry import (
    ShuffleRegistry,
)


class UserSettingsUpdateRequest(BaseModel):
    """Schema for updating user settings via the settings form."""

    default_algorithm: Optional[str] = Field(default=None)
    theme: Optional[str] = Field(default=None)
    notifications_enabled: Optional[bool] = Field(
        default=None
    )
    auto_snapshot_enabled: Optional[bool] = Field(
        default=None
    )
    max_snapshots_per_playlist: Optional[int] = Field(
        default=None, ge=1, le=50
    )
    dashboard_show_recent_activity: Optional[bool] = Field(
        default=None
    )

    @field_validator("default_algorithm")
    @classmethod
    def validate_algorithm(
        cls, v: Optional[str]
    ) -> Optional[str]:
        """Validate algorithm name if provided."""
        if v is None or v == "":
            return None
        v = v.strip()
        if not v:
            return None
        valid = set(
            ShuffleRegistry.get_available_algorithms().keys()
        )
        if v not in valid:
            raise ValueError(
                f"Invalid algorithm '{v}'. Valid: "
                f"{', '.join(sorted(valid))}"
            )
        return v

    @field_validator("theme")
    @classmethod
    def validate_theme(
        cls, v: Optional[str]
    ) -> Optional[str]:
        """Validate theme choice."""
        if v is None:
            return None
        v = v.strip().lower()
        valid_themes = {"light", "dark", "system"}
        if v not in valid_themes:
            raise ValueError(
                f"Invalid theme '{v}'. Valid: "
                f"{', '.join(sorted(valid_themes))}"
            )
        return v

    class Config:
        extra = "ignore"
