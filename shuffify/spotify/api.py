"""
Spotify API data operations.

Handles all Spotify Web API data operations: playlists, tracks, user info, etc.
This module is responsible for data operations only, separating them from
authentication concerns.
"""

import logging
from functools import wraps
from typing import Dict, List, Any, Optional, Callable

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


def api_error_handler(func: Callable) -> Callable:
    """
    Decorator for handling Spotify API errors consistently.

    Catches spotipy exceptions and converts them to our exception types.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except spotipy.SpotifyException as e:
            logger.error(f"Spotify API error in {func.__name__}: {e}")
            if e.http_status == 404:
                raise SpotifyNotFoundError(f"Resource not found: {e.msg}")
            elif e.http_status == 429:
                retry_after = e.headers.get('Retry-After', 60) if e.headers else 60
                raise SpotifyRateLimitError(
                    f"Rate limited: {e.msg}",
                    retry_after=int(retry_after)
                )
            elif e.http_status == 401:
                raise SpotifyTokenExpiredError(f"Token expired or invalid: {e.msg}")
            else:
                raise SpotifyAPIError(f"API error: {e.msg}")
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}", exc_info=True)
            raise SpotifyAPIError(f"Unexpected error: {e}")
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
