"""
Pydantic schemas for request/response validation.

This module provides type-safe validation for all API endpoints.
"""

from pydantic import ValidationError

from .requests import (
    ShuffleRequest,
    ShuffleRequestBase,
    BasicShuffleParams,
    BalancedShuffleParams,
    StratifiedShuffleParams,
    PercentageShuffleParams,
    PlaylistQueryParams,
    WorkshopCommitRequest,
    WorkshopSearchRequest,
    parse_shuffle_request,
)

__all__ = [
    # Exceptions
    "ValidationError",
    # Request schemas
    "ShuffleRequest",
    "ShuffleRequestBase",
    "BasicShuffleParams",
    "BalancedShuffleParams",
    "StratifiedShuffleParams",
    "PercentageShuffleParams",
    "PlaylistQueryParams",
    "WorkshopCommitRequest",
    "WorkshopSearchRequest",
    # Utility functions
    "parse_shuffle_request",
]
