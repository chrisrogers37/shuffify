"""
Spotify API data operations.

Handles all Spotify Web API data operations: playlists, tracks, user info, etc.
This module is responsible for data operations only, separating them from
authentication concerns.
"""

import logging
import time
from functools import wraps
from typing import Dict, List, Any, Optional, Callable
from requests.exceptions import RequestException, ConnectionError, Timeout

import spotipy

from .auth import TokenInfo, SpotifyAuthManager
from .credentials import SpotifyCredentials
from .exceptions import (
    SpotifyAPIError,
    SpotifyRateLimitError,
    SpotifyNotFoundError,
    SpotifyTokenExpiredError,
)

logger = logging.getLogger(__name__)

# Silence spotipy's verbose logging
logging.getLogger('spotipy').setLevel(logging.WARNING)

# Retry configuration
MAX_RETRIES = 4
BASE_DELAY = 2  # seconds
MAX_DELAY = 16  # seconds


def _calculate_backoff_delay(attempt: int, base_delay: float = BASE_DELAY) -> float:
    """
    Calculate exponential backoff delay with jitter.

    Args:
        attempt: The current retry attempt (0-indexed).
        base_delay: Base delay in seconds.

    Returns:
        Delay in seconds, capped at MAX_DELAY.
    """
    delay = min(base_delay * (2 ** attempt), MAX_DELAY)
    return delay


def api_error_handler(func: Callable) -> Callable:
    """
    Decorator for handling Spotify API errors with automatic retry.

    Catches spotipy exceptions and converts them to our exception types.
    Implements exponential backoff for rate limits and transient errors.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exception = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                return func(*args, **kwargs)

            except spotipy.SpotifyException as e:
                last_exception = e

                # Don't retry 404 or 401 errors
                if e.http_status == 404:
                    raise SpotifyNotFoundError(f"Resource not found: {e.msg}")
                elif e.http_status == 401:
                    raise SpotifyTokenExpiredError(f"Token expired or invalid: {e.msg}")
                elif e.http_status == 429:
                    # Rate limited - use Retry-After header or calculate backoff
                    retry_after = e.headers.get('Retry-After', 60) if e.headers else 60
                    if attempt < MAX_RETRIES:
                        delay = max(int(retry_after), _calculate_backoff_delay(attempt))
                        logger.warning(
                            f"Rate limited in {func.__name__}, attempt {attempt + 1}/{MAX_RETRIES + 1}. "
                            f"Retrying in {delay}s"
                        )
                        time.sleep(delay)
                        continue
                    raise SpotifyRateLimitError(
                        f"Rate limited after {MAX_RETRIES + 1} attempts: {e.msg}",
                        retry_after=int(retry_after)
                    )
                elif e.http_status in (500, 502, 503, 504):
                    # Server errors - retry with backoff
                    if attempt < MAX_RETRIES:
                        delay = _calculate_backoff_delay(attempt)
                        logger.warning(
                            f"Server error {e.http_status} in {func.__name__}, attempt {attempt + 1}/{MAX_RETRIES + 1}. "
                            f"Retrying in {delay}s"
                        )
                        time.sleep(delay)
                        continue
                    logger.error(f"Spotify API error in {func.__name__} after {MAX_RETRIES + 1} attempts: {e}")
                    raise SpotifyAPIError(f"API error after retries: {e.msg}")
                else:
                    # Other client errors - don't retry
                    logger.error(f"Spotify API error in {func.__name__}: {e}")
                    raise SpotifyAPIError(f"API error: {e.msg}")

            except (ConnectionError, Timeout, RequestException) as e:
                # Network errors - retry with backoff
                last_exception = e
                if attempt < MAX_RETRIES:
                    delay = _calculate_backoff_delay(attempt)
                    logger.warning(
                        f"Network error in {func.__name__}, attempt {attempt + 1}/{MAX_RETRIES + 1}. "
                        f"Retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
                    continue
                logger.error(f"Network error in {func.__name__} after {MAX_RETRIES + 1} attempts: {e}", exc_info=True)
                raise SpotifyAPIError(f"Network error after retries: {e}")

            except Exception as e:
                # Unexpected errors - don't retry
                logger.error(f"Unexpected error in {func.__name__}: {e}", exc_info=True)
                raise SpotifyAPIError(f"Unexpected error: {e}")

        # Should not reach here, but handle it just in case
        if last_exception:
            raise SpotifyAPIError(f"Failed after {MAX_RETRIES + 1} attempts: {last_exception}")

    return wrapper


class SpotifyAPI:
    """
    Spotify Web API client for data operations.

    Handles all data operations: fetching playlists, tracks, user info,
    and updating playlists. Token refresh is delegated to the auth manager
    when needed.

    Example:
        credentials = SpotifyCredentials.from_flask_config(app.config)
        auth_manager = SpotifyAuthManager(credentials)
        token_info = TokenInfo.from_dict(session['spotify_token'])

        api = SpotifyAPI(token_info, auth_manager)
        playlists = api.get_user_playlists()
    """

    # Batch size for API operations
    BATCH_SIZE = 100
    AUDIO_FEATURES_BATCH_SIZE = 50

    def __init__(
        self,
        token_info: TokenInfo,
        auth_manager: Optional[SpotifyAuthManager] = None,
        auto_refresh: bool = True
    ):
        """
        Initialize the API client.

        Args:
            token_info: Valid TokenInfo with access token.
            auth_manager: Optional auth manager for token refresh.
            auto_refresh: Whether to automatically refresh expired tokens.

        Raises:
            SpotifyTokenExpiredError: If token is expired and cannot be refreshed.
        """
        self._auth_manager = auth_manager
        self._auto_refresh = auto_refresh and auth_manager is not None

        # Ensure token is valid
        if token_info.is_expired:
            if self._auto_refresh:
                token_info = auth_manager.ensure_valid_token(token_info)
            else:
                raise SpotifyTokenExpiredError("Token is expired")

        self._token_info = token_info
        self._sp = spotipy.Spotify(auth=token_info.access_token)
        logger.debug("SpotifyAPI initialized with valid token")

    @property
    def token_info(self) -> TokenInfo:
        """Get the current token info (may have been refreshed)."""
        return self._token_info

    def _ensure_valid_token(self) -> None:
        """
        Ensure the token is valid, refreshing if necessary.

        Raises:
            SpotifyTokenExpiredError: If token cannot be made valid.
        """
        if not self._token_info.is_expired:
            return

        if not self._auto_refresh:
            raise SpotifyTokenExpiredError("Token expired and auto-refresh disabled")

        logger.info("Token expired, refreshing...")
        self._token_info = self._auth_manager.ensure_valid_token(self._token_info)
        self._sp = spotipy.Spotify(auth=self._token_info.access_token)

    # =========================================================================
    # User Operations
    # =========================================================================

    @api_error_handler
    def get_current_user(self) -> Dict[str, Any]:
        """
        Get the current user's profile.

        Returns:
            User profile dictionary.

        Raises:
            SpotifyAPIError: If the request fails.
        """
        self._ensure_valid_token()
        user = self._sp.current_user()
        logger.debug(f"Retrieved user: {user.get('display_name', 'Unknown')}")
        return user

    # =========================================================================
    # Playlist Operations
    # =========================================================================

    @api_error_handler
    def get_user_playlists(self) -> List[Dict[str, Any]]:
        """
        Get all playlists the user can edit.

        Returns:
            List of playlist dictionaries (owned or collaborative).

        Raises:
            SpotifyAPIError: If the request fails.
        """
        self._ensure_valid_token()
        playlists = []
        user_id = self._sp.current_user()['id']

        results = self._sp.current_user_playlists()
        while results:
            for playlist in results['items']:
                # Include playlists the user owns or can collaborate on
                if playlist['owner']['id'] == user_id or playlist.get('collaborative'):
                    playlists.append(playlist)
            results = self._sp.next(results) if results['next'] else None

        logger.debug(f"Retrieved {len(playlists)} editable playlists")
        return playlists

    @api_error_handler
    def get_playlist(self, playlist_id: str) -> Dict[str, Any]:
        """
        Get a single playlist by ID.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            Playlist dictionary.

        Raises:
            SpotifyNotFoundError: If playlist doesn't exist.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_valid_token()
        playlist = self._sp.playlist(playlist_id)
        logger.debug(f"Retrieved playlist: {playlist.get('name', 'Unknown')}")
        return playlist

    @api_error_handler
    def get_playlist_tracks(self, playlist_id: str) -> List[Dict[str, Any]]:
        """
        Get all tracks from a playlist.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            List of track dictionaries.

        Raises:
            SpotifyNotFoundError: If playlist doesn't exist.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_valid_token()
        tracks = []

        results = self._sp.playlist_items(playlist_id)
        while results:
            for item in results['items']:
                # Only include valid tracks (not None, not local-only)
                track = item.get('track')
                if track and track.get('uri'):
                    tracks.append(track)
            results = self._sp.next(results) if results['next'] else None

        logger.debug(f"Retrieved {len(tracks)} tracks from playlist {playlist_id}")
        return tracks

    @api_error_handler
    def update_playlist_tracks(
        self,
        playlist_id: str,
        track_uris: List[str]
    ) -> bool:
        """
        Replace all tracks in a playlist with a new list.

        Args:
            playlist_id: The Spotify playlist ID.
            track_uris: List of track URIs in the desired order.

        Returns:
            True if update succeeded.

        Raises:
            SpotifyNotFoundError: If playlist doesn't exist.
            SpotifyAPIError: If the update fails.
        """
        self._ensure_valid_token()

        # Handle empty playlist
        if not track_uris:
            self._sp.playlist_replace_items(playlist_id, [])
            logger.info(f"Cleared playlist {playlist_id}")
            return True

        # Replace first batch (up to 100 tracks)
        self._sp.playlist_replace_items(playlist_id, track_uris[:self.BATCH_SIZE])

        # Add remaining tracks in batches
        for i in range(self.BATCH_SIZE, len(track_uris), self.BATCH_SIZE):
            batch = track_uris[i:i + self.BATCH_SIZE]
            self._sp.playlist_add_items(playlist_id, batch)

        logger.info(f"Updated playlist {playlist_id} with {len(track_uris)} tracks")
        return True

    # =========================================================================
    # Audio Features Operations
    # =========================================================================

    @api_error_handler
    def get_audio_features(
        self,
        track_ids: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get audio features for multiple tracks.

        Args:
            track_ids: List of track IDs or URIs.

        Returns:
            Dictionary mapping track ID to audio features.

        Raises:
            SpotifyAPIError: If the request fails.
        """
        self._ensure_valid_token()
        features = {}

        # Extract IDs from URIs if needed
        valid_ids = []
        for tid in track_ids:
            if tid:
                # Handle both URI format and plain ID
                clean_id = tid.split(":")[-1] if ":" in tid else tid
                valid_ids.append(clean_id)

        # Fetch in batches
        for i in range(0, len(valid_ids), self.AUDIO_FEATURES_BATCH_SIZE):
            batch = valid_ids[i:i + self.AUDIO_FEATURES_BATCH_SIZE]
            results = self._sp.audio_features(batch)

            if results:
                for track_id, feature in zip(batch, results):
                    if feature:
                        features[track_id] = feature

        logger.debug(f"Retrieved audio features for {len(features)} tracks")
        return features
