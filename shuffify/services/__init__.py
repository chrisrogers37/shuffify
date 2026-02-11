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
    # Workshop Session Service
    "WorkshopSessionService",
    "WorkshopSessionError",
    "WorkshopSessionNotFoundError",
    "WorkshopSessionLimitError",
    # Upstream Source Service
    "UpstreamSourceService",
    "UpstreamSourceError",
    "UpstreamSourceNotFoundError",
]
