"""
Spotify API integration module.

This module provides a clean, modular interface for Spotify OAuth authentication
and API operations.

Architecture:
    - credentials.py: SpotifyCredentials for config/DI
    - auth.py: SpotifyAuthManager for OAuth and token management
    - api.py: SpotifyAPI for data operations
    - client.py: SpotifyClient facade (combines auth + api)
    - exceptions.py: Exception hierarchy

Usage (preferred - explicit dependencies):
    from shuffify.spotify import (
        SpotifyCredentials,
        SpotifyAuthManager,
        SpotifyAPI,
        TokenInfo,
    )

    # Create credentials from Flask config
    credentials = SpotifyCredentials.from_flask_config(app.config)

    # Create auth manager
    auth_manager = SpotifyAuthManager(credentials)

    # Get auth URL for OAuth flow
    auth_url = auth_manager.get_auth_url()

    # After callback, exchange code for token
    token_info = auth_manager.exchange_code(code)

    # Create API client for data operations
    api = SpotifyAPI(token_info, auth_manager)
    playlists = api.get_user_playlists()

Usage (legacy - backward compatible):
    from shuffify.spotify import SpotifyClient

    client = SpotifyClient(token=session['spotify_token'])
    playlists = client.get_user_playlists()
"""

# Credentials (for dependency injection)
from .credentials import SpotifyCredentials

# Auth (token management)
from .auth import (
    SpotifyAuthManager,
    TokenInfo,
    DEFAULT_SCOPES,
)

# API (data operations)
from .api import SpotifyAPI

# Client (facade for backward compatibility)
from .client import SpotifyClient

# Exceptions
from .exceptions import (
    SpotifyError,
    SpotifyAuthError,
    SpotifyTokenError,
    SpotifyTokenExpiredError,
    SpotifyAPIError,
    SpotifyRateLimitError,
    SpotifyNotFoundError,
)


__all__ = [
    # Credentials
    'SpotifyCredentials',

    # Auth
    'SpotifyAuthManager',
    'TokenInfo',
    'DEFAULT_SCOPES',

    # API
    'SpotifyAPI',

    # Client (facade)
    'SpotifyClient',

    # Exceptions
    'SpotifyError',
    'SpotifyAuthError',
    'SpotifyTokenError',
    'SpotifyTokenExpiredError',
    'SpotifyAPIError',
    'SpotifyRateLimitError',
    'SpotifyNotFoundError',
]
