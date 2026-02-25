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
from .raid_requests import (
    WatchPlaylistRequest,
    UnwatchPlaylistRequest,
    RaidNowRequest,
)
from .playlist_preference_requests import (
    SaveOrderRequest,
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
    # Raid schemas
    "WatchPlaylistRequest",
    "UnwatchPlaylistRequest",
    "RaidNowRequest",
    # Playlist Preference schemas
    "SaveOrderRequest",
]
