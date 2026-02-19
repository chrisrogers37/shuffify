"""
Spotify client facade.

Provides a unified interface combining authentication and API operations.
This module maintains backward compatibility while using the new modular
architecture internally.

For new code, consider using SpotifyAuthManager and SpotifyAPI directly
for better separation of concerns.
"""

import logging
from typing import Dict, List, Any, Optional, TYPE_CHECKING

from .auth import SpotifyAuthManager, TokenInfo
from .api import SpotifyAPI
from .credentials import SpotifyCredentials
from .exceptions import SpotifyTokenError

if TYPE_CHECKING:
    from .cache import SpotifyCache

logger = logging.getLogger(__name__)


class SpotifyClient:
    """
    Unified Spotify client facade.

    Combines authentication and API functionality in a single interface.
    This class maintains backward compatibility with existing code while
    delegating to the new SpotifyAuthManager and SpotifyAPI internally.

    For new code, prefer using SpotifyAuthManager and SpotifyAPI directly:
        - SpotifyAuthManager: For OAuth flow and token management
        - SpotifyAPI: For data operations (playlists, tracks, etc.)

    Example (legacy pattern - still supported):
        client = SpotifyClient(token=session['spotify_token'])
        playlists = client.get_user_playlists()

    Example (new pattern - preferred):
        credentials = SpotifyCredentials.from_flask_config(app.config)
        auth_manager = SpotifyAuthManager(credentials)
        token_info = TokenInfo.from_dict(session['spotify_token'])
        api = SpotifyAPI(token_info, auth_manager)
        playlists = api.get_user_playlists()
    """

    def __init__(
        self,
        token: Optional[Dict[str, Any]] = None,
        credentials: Optional[Dict[str, str]] = None,
        cache: Optional["SpotifyCache"] = None,
    ):
        """
        Initialize the Spotify client.

        Args:
            token: OAuth token dictionary (for authenticated operations).
            credentials: OAuth credentials dict with client_id, client_secret,
                         redirect_uri. If not provided, loads from Flask config.
            cache: Optional SpotifyCache instance for caching API responses.

        Note:
            When credentials are not provided, this will attempt to load from
            Flask's current_app.config. This is deprecated - prefer passing
            credentials explicitly or using SpotifyCredentials.
        """
        self._credentials = self._resolve_credentials(credentials)
        self._auth_manager = SpotifyAuthManager(self._credentials)
        self._token_info: Optional[TokenInfo] = None
        self._api: Optional[SpotifyAPI] = None
        self._cache = cache

        if token:
            self._initialize_with_token(token)

    def _resolve_credentials(
        self, credentials: Optional[Dict[str, str]]
    ) -> SpotifyCredentials:
        """
        Resolve credentials from explicit dict or Flask config.

        Args:
            credentials: Optional credentials dictionary.

        Returns:
            SpotifyCredentials instance.
        """
        if credentials:
            return SpotifyCredentials(
                client_id=credentials["client_id"],
                client_secret=credentials["client_secret"],
                redirect_uri=credentials["redirect_uri"],
            )

        # Fall back to Flask config (for backward compatibility)
        try:
            from flask import current_app

            return SpotifyCredentials.from_flask_config(current_app.config)
        except RuntimeError:
            # Not in Flask context - try environment
            return SpotifyCredentials.from_env()

    def _initialize_with_token(self, token: Dict[str, Any]) -> None:
        """
        Initialize the API client with a token.

        Args:
            token: OAuth token dictionary.

        Raises:
            ValueError: If token is invalid or expired.
        """
        try:
            self._token_info = TokenInfo.from_dict(token)

            # Validate token (will raise if expired)
            self._token_info.validate()

            # Create API client
            self._api = SpotifyAPI(
                self._token_info,
                self._auth_manager,
                auto_refresh=True,
                cache=self._cache,
            )
            logger.info(
                "SpotifyClient initialized with valid token%s",
                " (with cache)" if self._cache else "",
            )

        except SpotifyTokenError as e:
            logger.error(f"Token validation failed: {e}")
            raise ValueError(f"Invalid or expired token: {e}")

    @property
    def token_info(self) -> Optional[TokenInfo]:
        """Get the current token info."""
        if self._api:
            return self._api.token_info
        return self._token_info

    @property
    def is_authenticated(self) -> bool:
        """Check if client has a valid token."""
        return self._api is not None

    # =========================================================================
    # Authentication Methods
    # =========================================================================

    def get_auth_url(self) -> str:
        """
        Get the Spotify authorization URL.

        Returns:
            Authorization URL for user to visit.

        Raises:
            SpotifyAuthError: If URL generation fails.
        """
        return self._auth_manager.get_auth_url()

    def get_token(self, code: str) -> Dict[str, Any]:
        """
        Exchange authorization code for token.

        Args:
            code: Authorization code from OAuth callback.

        Returns:
            Token dictionary with access_token, refresh_token, etc.

        Raises:
            Exception: If token exchange fails (for backward compatibility).
        """
        try:
            token_info = self._auth_manager.exchange_code(code)
            self._token_info = token_info
            self._api = SpotifyAPI(token_info, self._auth_manager, cache=self._cache)
            return token_info.to_dict()
        except SpotifyTokenError as e:
            # Preserve backward-compatible exception format
            raise Exception(f"Token exchange failed: {e}")

    # =========================================================================
    # User Methods
    # =========================================================================

    def get_current_user(self) -> Dict[str, Any]:
        """
        Get the current user's profile.

        Returns:
            User profile dictionary.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_authenticated()
        return self._api.get_current_user()

    # =========================================================================
    # Playlist Methods
    # =========================================================================

    def get_user_playlists(self, skip_cache: bool = False) -> List[Dict[str, Any]]:
        """
        Get all playlists the user can edit.

        Args:
            skip_cache: If True, bypass cache and fetch fresh data from Spotify.

        Returns:
            List of playlist dictionaries.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_authenticated()
        return self._api.get_user_playlists(skip_cache=skip_cache)

    def get_playlist(self, playlist_id: str) -> Dict[str, Any]:
        """
        Get a single playlist.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            Playlist dictionary.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_authenticated()
        return self._api.get_playlist(playlist_id)

    def get_playlist_tracks(self, playlist_id: str) -> List[Dict[str, Any]]:
        """
        Get all tracks from a playlist.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            List of track dictionaries.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_authenticated()
        return self._api.get_playlist_tracks(playlist_id)

    def update_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """
        Update a playlist with new track order.

        Args:
            playlist_id: The Spotify playlist ID.
            track_uris: List of track URIs in desired order.

        Returns:
            True if update succeeded.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the Spotify API call fails.
            SpotifyTokenExpiredError: If the token has expired.
        """
        self._ensure_authenticated()
        return self._api.update_playlist_tracks(playlist_id, track_uris)

    def search_playlists(
        self, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search for playlists by name.

        Args:
            query: Search query string.
            limit: Maximum number of results (1-50).

        Returns:
            List of playlist summary dictionaries.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_authenticated()
        return self._api.search_playlists(query, limit=limit)

    # =========================================================================
    # Audio Features Methods
    # =========================================================================

    def get_track_audio_features(
        self, track_ids: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get audio features for tracks.

        Args:
            track_ids: List of track IDs or URIs.

        Returns:
            Dictionary mapping track ID to audio features.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_authenticated()
        return self._api.get_audio_features(track_ids)

    # =========================================================================
    # Search Methods
    # =========================================================================

    def search_tracks(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        market: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search Spotify's catalog for tracks.

        Args:
            query: Search query string.
            limit: Maximum number of results (1-50, default 20).
            offset: Pagination offset (default 0).
            market: Optional ISO 3166-1 alpha-2 country code.

        Returns:
            List of track dictionaries from Spotify search.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the search fails.
        """
        self._ensure_authenticated()
        return self._api.search_tracks(
            query=query, limit=limit, offset=offset, market=market
        )

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _ensure_authenticated(self) -> None:
        """
        Ensure the client is authenticated.

        Raises:
            RuntimeError: If not authenticated.
        """
        if not self._api:
            raise RuntimeError(
                "Spotify client not initialized. Please authenticate first."
            )
