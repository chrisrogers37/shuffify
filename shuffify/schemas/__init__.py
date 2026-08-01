"""
Pydantic schemas for request/response validation.

This module provides type-safe validation for all API endpoints.
"""

from pydantic import ValidationError

from .pending_raid_requests import (
    DismissTracksRequest,
    PromoteTracksRequest,
)
from .playlist_pair_requests import (
    ArchiveTracksRequest,
    CreatePairRequest,
    UnarchiveTracksRequest,
    UpdatePairRequest,
)
from .playlist_preference_requests import (
    SaveOrderRequest,
)
from .raid_requests import (
    AddRaidUrlRequest,
    RaidNowRequest,
    UnwatchPlaylistRequest,
    WatchPlaylistRequest,
)
from .requests import (
    BalancedShuffleParams,
    BasicShuffleParams,
    ExternalPlaylistRequest,
    PercentageShuffleParams,
    PlaylistQueryParams,
    ShuffleRequest,
    ShuffleRequestBase,
    StratifiedShuffleParams,
    WorkshopCommitRequest,
    WorkshopSearchRequest,
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
    "UpdatePairRequest",
    "ArchiveTracksRequest",
    "UnarchiveTracksRequest",
    # Raid schemas
    "WatchPlaylistRequest",
    "AddRaidUrlRequest",
    "UnwatchPlaylistRequest",
    "RaidNowRequest",
    # Playlist Preference schemas
    "SaveOrderRequest",
    # Pending Raid schemas
    "PromoteTracksRequest",
    "DismissTracksRequest",
]
