"""
Spotify API integration module.

This module provides a clean, modular interface for Spotify OAuth authentication
and API operations, with optional Redis caching.

Architecture:
    - credentials.py: SpotifyCredentials for config/DI
    - auth.py: SpotifyAuthManager for OAuth and token management
    - api.py: SpotifyAPI for data operations
    - error_handling.py: Retry logic, backoff, error classification
    - cache.py: SpotifyCache for Redis-based response caching
    - exceptions.py: Exception hierarchy

Usage:
    from shuffify.spotify import (
        SpotifyCredentials,
        SpotifyAuthManager,
        SpotifyAPI,
        SpotifyCache,
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

    # Create API client for data operations (with optional caching)
    import redis
    redis_client = redis.from_url('redis://localhost:6379/0')
    cache = SpotifyCache(redis_client)
    api = SpotifyAPI(token_info, auth_manager, cache=cache)
    playlists = api.get_user_playlists()
"""

# Credentials (for dependency injection)
# API (data operations)
from .api import SpotifyAPI

# Auth (token management)
from .auth import (
    DEFAULT_SCOPES,
    SpotifyAuthManager,
    TokenInfo,
)

# Cache (Redis-based caching)
from .cache import SpotifyCache

# Client (facade for backward compatibility)
from .credentials import SpotifyCredentials

# Exceptions
from .exceptions import (
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyError,
    SpotifyNotFoundError,
    SpotifyRateLimitError,
    SpotifyTokenError,
    SpotifyTokenExpiredError,
)

# URL parser utility
from .url_parser import parse_spotify_playlist_url

__all__ = [
    # Credentials
    "SpotifyCredentials",
    # Auth
    "SpotifyAuthManager",
    "TokenInfo",
    "DEFAULT_SCOPES",
    # API
    "SpotifyAPI",
    # URL Parser
    "parse_spotify_playlist_url",
    # Cache
    "SpotifyCache",
    # Client (facade)
    # Exceptions
    "SpotifyError",
    "SpotifyAuthError",
    "SpotifyTokenError",
    "SpotifyTokenExpiredError",
    "SpotifyAPIError",
    "SpotifyRateLimitError",
    "SpotifyNotFoundError",
]
