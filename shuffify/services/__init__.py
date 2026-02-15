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
from shuffify.services.auth_service import (
    AuthService,
    AuthenticationError,
    TokenValidationError,
)

# Playlist Service
from shuffify.services.playlist_service import (
    PlaylistService,
    PlaylistError,
    PlaylistNotFoundError,
    PlaylistUpdateError,
)

# Shuffle Service
from shuffify.services.shuffle_service import (
    ShuffleService,
    ShuffleError,
    InvalidAlgorithmError,
    ParameterValidationError,
    ShuffleExecutionError,
)

# State Service
from shuffify.services.state_service import (
    StateService,
    StateError,
    NoHistoryError,
    AlreadyAtOriginalError,
    PlaylistState,
    PLAYLIST_STATES_KEY,
)

# User Service
from shuffify.services.user_service import (
    UserService,
    UserServiceError,
    UserNotFoundError,
    UpsertResult,
)

# Workshop Session Service
from shuffify.services.workshop_session_service import (
    WorkshopSessionService,
    WorkshopSessionError,
    WorkshopSessionNotFoundError,
    WorkshopSessionLimitError,
)

# Upstream Source Service
from shuffify.services.upstream_source_service import (
    UpstreamSourceService,
    UpstreamSourceError,
    UpstreamSourceNotFoundError,
)

# Token Service
from shuffify.services.token_service import (
    TokenService,
    TokenEncryptionError,
)

# Scheduler Service
from shuffify.services.scheduler_service import (
    SchedulerService,
    ScheduleError,
    ScheduleNotFoundError,
    ScheduleLimitError,
)

# Job Executor Service
from shuffify.services.job_executor_service import (
    JobExecutorService,
    JobExecutionError,
)

# Login History Service
from shuffify.services.login_history_service import (
    LoginHistoryService,
    LoginHistoryError,
    LoginHistoryNotFoundError,
)

# User Settings Service
from shuffify.services.user_settings_service import (
    UserSettingsService,
    UserSettingsError,
)

# Playlist Snapshot Service
from shuffify.services.playlist_snapshot_service import (
    PlaylistSnapshotService,
    PlaylistSnapshotError,
    PlaylistSnapshotNotFoundError,
)

# Activity Log Service
from shuffify.services.activity_log_service import (
    ActivityLogService,
    ActivityLogError,
)

# Dashboard Service
from shuffify.services.dashboard_service import (
    DashboardService,
    DashboardError,
)

# Playlist Pair Service
from shuffify.services.playlist_pair_service import (
    PlaylistPairService,
    PlaylistPairError,
    PlaylistPairNotFoundError,
    PlaylistPairExistsError,
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
    # Token Service
    "TokenService",
    "TokenEncryptionError",
    # Scheduler Service
    "SchedulerService",
    "ScheduleError",
    "ScheduleNotFoundError",
    "ScheduleLimitError",
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
]
