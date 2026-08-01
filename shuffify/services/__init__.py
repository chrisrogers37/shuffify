"""
Shuffify Services Package

This package provides a clean service layer with separation of concerns.
All services can be imported directly from this package.

Usage:
    from shuffify.services import AuthService, PlaylistService, ShuffleService, StateService

    # Or import specific exceptions
    from shuffify.services import AuthenticationError, PlaylistError, ShuffleError, StateError

Example:
    from shuffify.services import AuthService, PlaylistService

    # Authenticate
    token = AuthService.exchange_code_for_token(code)
    client, user = AuthService.authenticate_and_get_user(token)

    # Get playlists
    playlist_service = PlaylistService(client)
    playlists = playlist_service.get_user_playlists()
"""

# Auth Service
# Activity Log Service
from shuffify.services.activity_log_service import (
    ActivityLogError,
    ActivityLogService,
)
from shuffify.services.auth_service import (
    AuthenticationError,
    AuthService,
    TokenValidationError,
)

# Dashboard Service
from shuffify.services.dashboard_service import (
    DashboardError,
    DashboardService,
)

# Job Executor Service
from shuffify.services.executors import (
    JobExecutionError,
    JobExecutorService,
)

# Login History Service
from shuffify.services.login_history_service import (
    LoginHistoryError,
    LoginHistoryNotFoundError,
    LoginHistoryService,
)

# Pending Raid Service
from shuffify.services.pending_raid_service import (
    PendingRaidService,
)

# Playlist Pair Service
from shuffify.services.playlist_pair_service import (
    PlaylistPairError,
    PlaylistPairExistsError,
    PlaylistPairNotFoundError,
    PlaylistPairService,
)

# Playlist Preference Service
from shuffify.services.playlist_preference_service import (
    PlaylistPreferenceError,
    PlaylistPreferenceNotFoundError,
    PlaylistPreferenceService,
)

# Playlist Service
from shuffify.services.playlist_service import (
    PlaylistAccessError,
    PlaylistError,
    PlaylistNotFoundError,
    PlaylistService,
    PlaylistUpdateError,
)

# Playlist Snapshot Service
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotError,
    PlaylistSnapshotNotFoundError,
    PlaylistSnapshotService,
)

# Raid Sync Service
from shuffify.services.raid_sync_service import (
    RaidSyncError,
    RaidSyncService,
)

# Scheduler Service
from shuffify.services.scheduler_service import (
    ScheduleError,
    ScheduleNotFoundError,
    SchedulerService,
)

# Shuffle Service
from shuffify.services.shuffle_service import (
    InvalidAlgorithmError,
    ParameterValidationError,
    ShuffleError,
    ShuffleExecutionError,
    ShuffleService,
)

# State Service
from shuffify.services.state_service import (
    PLAYLIST_STATES_KEY,
    AlreadyAtOriginalError,
    NoHistoryError,
    PlaylistState,
    StateError,
    StateService,
)

# Token Service
from shuffify.services.token_service import (
    TokenEncryptionError,
    TokenService,
)

# Upstream Source Service
from shuffify.services.upstream_source_service import (
    UpstreamSourceError,
    UpstreamSourceLimitError,
    UpstreamSourceNotFoundError,
    UpstreamSourceService,
)

# User Service
from shuffify.services.user_service import (
    UpsertResult,
    UserNotFoundError,
    UserService,
    UserServiceError,
)

# User Settings Service
from shuffify.services.user_settings_service import (
    UserSettingsError,
    UserSettingsService,
)

# Workshop Session Service
from shuffify.services.workshop_session_service import (
    WorkshopSessionError,
    WorkshopSessionLimitError,
    WorkshopSessionNotFoundError,
    WorkshopSessionService,
)

__all__ = [
    # Services
    "AuthService",
    "PlaylistService",
    "ShuffleService",
    "StateService",
    # Auth Exceptions
    "AuthenticationError",
    "TokenValidationError",
    # Playlist Exceptions
    "PlaylistError",
    "PlaylistNotFoundError",
    "PlaylistUpdateError",
    "PlaylistAccessError",
    # Shuffle Exceptions
    "ShuffleError",
    "InvalidAlgorithmError",
    "ParameterValidationError",
    "ShuffleExecutionError",
    # State Exceptions
    "StateError",
    "NoHistoryError",
    "AlreadyAtOriginalError",
    # State Types
    "PlaylistState",
    "PLAYLIST_STATES_KEY",
    # User Service
    "UserService",
    "UserServiceError",
    "UserNotFoundError",
    "UpsertResult",
    # Workshop Session Service
    "WorkshopSessionService",
    "WorkshopSessionError",
    "WorkshopSessionNotFoundError",
    "WorkshopSessionLimitError",
    # Upstream Source Service
    "UpstreamSourceService",
    "UpstreamSourceError",
    "UpstreamSourceNotFoundError",
    "UpstreamSourceLimitError",
    # Token Service
    "TokenService",
    "TokenEncryptionError",
    # Scheduler Service
    "SchedulerService",
    "ScheduleError",
    "ScheduleNotFoundError",
    # Job Executor Service
    "JobExecutorService",
    "JobExecutionError",
    # Login History Service
    "LoginHistoryService",
    "LoginHistoryError",
    "LoginHistoryNotFoundError",
    # User Settings Service
    "UserSettingsService",
    "UserSettingsError",
    # Playlist Snapshot Service
    "PlaylistSnapshotService",
    "PlaylistSnapshotError",
    "PlaylistSnapshotNotFoundError",
    # Activity Log Service
    "ActivityLogService",
    "ActivityLogError",
    # Dashboard Service
    "DashboardService",
    "DashboardError",
    # Playlist Pair Service
    "PlaylistPairService",
    "PlaylistPairError",
    "PlaylistPairNotFoundError",
    "PlaylistPairExistsError",
    # Raid Sync Service
    "RaidSyncService",
    "RaidSyncError",
    # Playlist Preference Service
    "PlaylistPreferenceService",
    "PlaylistPreferenceError",
    "PlaylistPreferenceNotFoundError",
    # Pending Raid Service
    "PendingRaidService",
]
