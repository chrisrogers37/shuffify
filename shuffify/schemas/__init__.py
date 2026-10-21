"""
Pydantic schemas for request/response validation.

This module provides type-safe validation for all API endpoints.
"""

from pydantic import ValidationError

from .pending_raid_requests import DismissTracksRequest
from .playlist_pair_requests import (
    CreatePairRequest,
    UnarchiveTracksRequest,
    UpdatePairRequest,
)
from .playlist_preference_requests import (
    SaveOrderRequest,
)
from .playlist_setup_requests import (
    CreateSetupPlaylistRequest,
    SetupImpactRequest,
    SetupSaveRequest,
)
from .raid_requests import (
    AddRaidUrlRequest,
    RaidNowRequest,
    UnwatchPlaylistRequest,
)
from .requests import (
    ExternalPlaylistRequest,
    ShuffleRequest,
    WorkshopCommitRequest,
    WorkshopSearchRequest,
    parse_shuffle_request,
)
from .routine_requests import RoutineActivateRequest, RoutineDefinition, RoutineReviewRequest, RoutineStateRequest
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
    "WorkshopCommitRequest",
    "WorkshopSearchRequest",
    "ExternalPlaylistRequest",
    # Utility functions
    "parse_shuffle_request",
    # Schedule schemas
    "RoutineDefinition",
    "RoutineReviewRequest",
    "RoutineActivateRequest",
    "RoutineStateRequest",
    # Settings schemas
    "UserSettingsUpdateRequest",
    # Snapshot schemas
    "ManualSnapshotRequest",
    # Playlist Pair schemas
    "CreatePairRequest",
    "UpdatePairRequest",
    "UnarchiveTracksRequest",
    # Raid schemas
    "AddRaidUrlRequest",
    "UnwatchPlaylistRequest",
    "RaidNowRequest",
    # Playlist Preference schemas
    "SaveOrderRequest",
    # Playlist setup schemas
    "SetupImpactRequest",
    "SetupSaveRequest",
    "CreateSetupPlaylistRequest",
    # Pending Raid schemas
    "DismissTracksRequest",
]
