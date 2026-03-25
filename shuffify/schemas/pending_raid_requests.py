"""
Pydantic schemas for pending raid track operations.
"""

from typing import List

from pydantic import BaseModel, Field


class PromoteTracksRequest(BaseModel):
    """Request to promote specific pending tracks."""

    track_ids: List[int] = Field(
        ..., min_length=1, max_length=200
    )


class DismissTracksRequest(BaseModel):
    """Request to dismiss specific pending tracks."""

    track_ids: List[int] = Field(
        ..., min_length=1, max_length=200
    )


class UnpromoteTracksRequest(BaseModel):
    """Request to unpromote tracks back to pending."""

    track_uris: List[str] = Field(
        ..., min_length=1, max_length=200
    )
