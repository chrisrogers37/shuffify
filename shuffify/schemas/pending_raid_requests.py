"""
Pydantic schemas for pending raid track operations.
"""

from typing import List

from pydantic import BaseModel, Field


class DismissTracksRequest(BaseModel):
    """Request to dismiss specific pending tracks."""

    track_ids: List[int] = Field(
        ..., min_length=1, max_length=200
    )
