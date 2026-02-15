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
    ExternalPlaylistRequest,
    parse_shuffle_request,
)
from .schedule_requests import (
    ScheduleCreateRequest,
    ScheduleUpdateRequest,
)
from .settings_requests import (
    UserSettingsUpdateRequest,
)
from .snapshot_requests import (
    ManualSnapshotRequest,
)
from .playlist_pair_requests import (
    CreatePairRequest,
    ArchiveTracksRequest,
    UnarchiveTracksRequest,
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
    "ExternalPlaylistRequest",
    # Utility functions
    "parse_shuffle_request",
    # Schedule schemas
    "ScheduleCreateRequest",
    "ScheduleUpdateRequest",
    # Settings schemas
    "UserSettingsUpdateRequest",
    # Snapshot schemas
    "ManualSnapshotRequest",
    # Playlist Pair schemas
    "CreatePairRequest",
    "ArchiveTracksRequest",
    "UnarchiveTracksRequest",
]
