"""
Spotify API integration module.

This module provides a clean, modular interface for Spotify OAuth authentication
and API operations, with optional Redis caching.

Architecture:
    - credentials.py: SpotifyCredentials, and credentials_for, the one place credentials come from
    - auth.py: SpotifyAuthManager for OAuth and token management
    - api.py: SpotifyAPI for data operations
    - http_client.py: SpotifyHTTPClient for token refresh, retries, rate limits and pagination
    - endpoints.py: The base of every Spotify URL the app builds
    - error_handling.py: Retry logic, backoff, error classification
    - cache.py: SpotifyCache for Redis-based response caching
    - exceptions.py: Exception hierarchy
    - url_parser.py: parse_spotify_playlist_url for Spotify URLs, URIs and bare IDs

Usage:
    from shuffify.spotify import (
        credentials_for,
        SpotifyAuthManager,
        SpotifyAPI,
        SpotifyCache,
        TokenInfo,
    )

    # Get the credentials of the app to call Spotify with
    credentials = credentials_for(user)

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

# Credentials (for dependency injection)
from .credentials import SpotifyCredentials, credentials_for

# Exceptions
from .exceptions import (
    SpotifyAPIError,
    SpotifyAppRejected,
    SpotifyAppUnavailable,
    SpotifyAuthError,
    SpotifyError,
    SpotifyForbiddenError,
    SpotifyGrantRevoked,
    SpotifyNotFoundError,
    SpotifyQuotaExceededError,
    SpotifyRateLimitError,
    SpotifyTokenError,
    SpotifyTokenExpiredError,
    SpotifyUserNotAllowed,
    spotify_failure_of,
)

# URL parser utility
from .url_parser import parse_spotify_playlist_url

__all__ = [
    # Credentials
    "SpotifyCredentials",
    "credentials_for",
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
    # Exceptions
    "SpotifyError",
    "SpotifyAuthError",
    "SpotifyTokenError",
    "SpotifyTokenExpiredError",
    "SpotifyAPIError",
    "SpotifyRateLimitError",
    "SpotifyQuotaExceededError",
    "SpotifyNotFoundError",
    "SpotifyForbiddenError",
    "SpotifyAppUnavailable",
    "SpotifyAppRejected",
    "SpotifyGrantRevoked",
    "SpotifyUserNotAllowed",
    "spotify_failure_of",
]
