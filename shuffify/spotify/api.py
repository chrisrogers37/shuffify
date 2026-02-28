"""
Spotify API data operations.

Handles all Spotify Web API data operations: playlists, tracks, user info, etc.
This module is responsible for data operations only, separating them from
authentication concerns.

Supports optional Redis caching for improved performance.
"""

import logging
from typing import Dict, List, Any, Optional, TYPE_CHECKING

from .auth import TokenInfo, SpotifyAuthManager
from .error_handling import api_error_handler
from .exceptions import (
    SpotifyTokenExpiredError,
)
from .http_client import SpotifyHTTPClient

if TYPE_CHECKING:
    from .cache import SpotifyCache

logger = logging.getLogger(__name__)


class SpotifyAPI:
    """
    Spotify Web API client for data operations.

    Handles all data operations: fetching playlists, tracks, user info,
    and updating playlists. Token refresh is delegated to the auth manager
    when needed.

    Supports optional Redis caching for improved performance.

    Example:
        credentials = SpotifyCredentials.from_flask_config(app.config)
        auth_manager = SpotifyAuthManager(credentials)
        token_info = TokenInfo.from_dict(session['spotify_token'])

        api = SpotifyAPI(token_info, auth_manager)
        playlists = api.get_user_playlists()

    Example with caching:
        from shuffify.spotify.cache import SpotifyCache
        import redis

        redis_client = redis.from_url('redis://localhost:6379/0')
        cache = SpotifyCache(redis_client)
        api = SpotifyAPI(token_info, auth_manager, cache=cache)
        playlists = api.get_user_playlists()  # Uses cache if available
    """

    # Batch size for API operations
    BATCH_SIZE = 100
    AUDIO_FEATURES_BATCH_SIZE = 50

    def __init__(
        self,
        token_info: TokenInfo,
        auth_manager: Optional[SpotifyAuthManager] = None,
        auto_refresh: bool = True,
        cache: Optional["SpotifyCache"] = None,
    ):
        """
        Initialize the API client.

        Args:
            token_info: Valid TokenInfo with access token.
            auth_manager: Optional auth manager for token refresh.
            auto_refresh: Whether to automatically refresh expired tokens.
            cache: Optional SpotifyCache instance for caching API responses.

        Raises:
            SpotifyTokenExpiredError: If token is expired and cannot be refreshed.
        """
        self._auth_manager = auth_manager
        self._auto_refresh = auto_refresh and auth_manager is not None
        self._cache = cache

        # Ensure token is valid
        if token_info.is_expired:
            if self._auto_refresh:
                token_info = auth_manager.ensure_valid_token(token_info)
            else:
                raise SpotifyTokenExpiredError("Token is expired")

        self._token_info = token_info
        self._http = SpotifyHTTPClient(
            token_info.access_token,
            on_token_refresh=(
                self._handle_token_refresh
                if self._auto_refresh
                else None
            ),
        )
        self._user_id: Optional[str] = None
        logger.debug(
            "SpotifyAPI initialized with valid token%s",
            " (with cache)" if cache else "",
        )

    def _handle_token_refresh(self) -> str:
        """Callback for SpotifyHTTPClient on 401 responses."""
        self._token_info = self._auth_manager.ensure_valid_token(
            self._token_info
        )
        return self._token_info.access_token

    @property
    def token_info(self) -> TokenInfo:
        """Get the current token info (may have been refreshed)."""
        return self._token_info

    @property
    def cache(self) -> Optional["SpotifyCache"]:
        """Get the cache instance (if configured)."""
        return self._cache

    def _ensure_valid_token(self) -> None:
        """
        Ensure the token is valid, refreshing if necessary.

        Raises:
            SpotifyTokenExpiredError: If token cannot be made valid.
        """
        if not self._token_info.is_expired:
            return

        if not self._auto_refresh:
            raise SpotifyTokenExpiredError(
                "Token expired and auto-refresh disabled"
            )

        logger.info("Token expired, refreshing...")
        self._token_info = self._auth_manager.ensure_valid_token(
            self._token_info
        )
        self._http.update_token(self._token_info.access_token)

    def _get_user_id(self) -> str:
        """Get the current user's ID, caching the result."""
        if self._user_id is None:
            user = self.get_current_user()
            self._user_id = user["id"]
        return self._user_id

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

        # Check cache first
        if self._cache and self._user_id:
            cached = self._cache.get_user(self._user_id)
            if cached:
                return cached

        user = self._http.get("/me")
        logger.debug(
            f"Retrieved user: {user.get('display_name', 'Unknown')}"
        )

        # Cache the result
        if self._cache and user:
            self._user_id = user["id"]
            self._cache.set_user(user["id"], user)

        return user

    # =========================================================================
    # Playlist Operations
    # =========================================================================

    @api_error_handler
    def get_user_playlists(
        self, skip_cache: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get all playlists the user can edit.

        Args:
            skip_cache: If True, bypass cache and fetch fresh data.

        Returns:
            List of playlist dictionaries (owned or collaborative).

        Raises:
            SpotifyAPIError: If the request fails.
        """
        self._ensure_valid_token()
        user_id = self._get_user_id()

        # Check cache first
        if self._cache and not skip_cache:
            cached = self._cache.get_playlists(user_id)
            if cached is not None:
                return cached

        playlists = []

        all_items = self._http.get_all_pages("/me/playlists")
        for playlist in all_items:
            # Include playlists the user owns or can collaborate on
            if (
                playlist["owner"]["id"] == user_id
                or playlist.get("collaborative")
            ):
                playlists.append(playlist)

        logger.debug(
            f"Retrieved {len(playlists)} editable playlists"
        )

        # Cache the result
        if self._cache:
            self._cache.set_playlists(user_id, playlists)

        return playlists

    @api_error_handler
    def get_playlist(
        self, playlist_id: str, skip_cache: bool = False
    ) -> Dict[str, Any]:
        """
        Get a single playlist by ID.

        Args:
            playlist_id: The Spotify playlist ID.
            skip_cache: If True, bypass cache and fetch fresh data.

        Returns:
            Playlist dictionary.

        Raises:
            SpotifyNotFoundError: If playlist doesn't exist.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_valid_token()

        # Check cache first
        if self._cache and not skip_cache:
            cached = self._cache.get_playlist(playlist_id)
            if cached is not None:
                return cached

        playlist = self._http.get(f"/playlists/{playlist_id}")
        logger.debug(
            f"Retrieved playlist: {playlist.get('name', 'Unknown')}"
        )

        # Cache the result
        if self._cache:
            self._cache.set_playlist(playlist_id, playlist)

        return playlist

    @api_error_handler
    def get_playlist_tracks(
        self, playlist_id: str, skip_cache: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get all tracks from a playlist.

        Args:
            playlist_id: The Spotify playlist ID.
            skip_cache: If True, bypass cache and fetch fresh data.

        Returns:
            List of track dictionaries.

        Raises:
            SpotifyNotFoundError: If playlist doesn't exist.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_valid_token()

        # Check cache first
        if self._cache and not skip_cache:
            cached = self._cache.get_playlist_tracks(playlist_id)
            if cached is not None:
                return cached

        tracks = []

        all_items = self._http.get_all_pages(
            f"/playlists/{playlist_id}/items"
        )
        for item in all_items:
            # Spotify Feb 2026: nested key renamed from "track" to "item"
            track = item.get("item")
            # Only include valid tracks (not None, not local-only)
            if track and track.get("uri"):
                tracks.append(track)

        logger.debug(
            f"Retrieved {len(tracks)} tracks from playlist "
            f"{playlist_id}"
        )

        # Cache the result
        if self._cache:
            self._cache.set_playlist_tracks(playlist_id, tracks)

        return tracks

    @api_error_handler
    def update_playlist_tracks(
        self, playlist_id: str, track_uris: List[str]
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
            self._http.put(
                f"/playlists/{playlist_id}/items",
                json={"uris": []},
            )
            logger.info(f"Cleared playlist {playlist_id}")
            # Invalidate cache after modification
            if self._cache:
                self._cache.invalidate_playlist(playlist_id)
            return True

        # Replace first batch (up to 100 tracks)
        self._http.put(
            f"/playlists/{playlist_id}/items",
            json={"uris": track_uris[: self.BATCH_SIZE]},
        )

        # Add remaining tracks in batches
        for i in range(
            self.BATCH_SIZE, len(track_uris), self.BATCH_SIZE
        ):
            batch = track_uris[i: i + self.BATCH_SIZE]
            self._http.post(
                f"/playlists/{playlist_id}/items",
                json={"uris": batch},
            )

        logger.info(
            f"Updated playlist {playlist_id} with "
            f"{len(track_uris)} tracks"
        )

        # Invalidate cache after modification
        if self._cache:
            self._cache.invalidate_playlist(playlist_id)
            if self._user_id:
                self._cache.invalidate_user_playlists(self._user_id)

        return True

    @api_error_handler
    def playlist_add_items(
        self, playlist_id: str, track_uris: List[str]
    ) -> None:
        """
        Add tracks to a playlist in batches.

        Args:
            playlist_id: The Spotify playlist ID.
            track_uris: List of track URIs to add.
        """
        self._ensure_valid_token()

        for i in range(0, len(track_uris), self.BATCH_SIZE):
            batch = track_uris[i: i + self.BATCH_SIZE]
            self._http.post(
                f"/playlists/{playlist_id}/items",
                json={"uris": batch},
            )

        if self._cache:
            self._cache.invalidate_playlist(playlist_id)

    @api_error_handler
    def playlist_remove_items(
        self, playlist_id: str, track_uris: List[str]
    ) -> bool:
        """
        Remove specific tracks from a playlist.

        Args:
            playlist_id: The Spotify playlist ID.
            track_uris: List of track URIs to remove.

        Returns:
            True if removal succeeded.

        Raises:
            SpotifyNotFoundError: If playlist doesn't exist.
            SpotifyAPIError: If the removal fails.
        """
        self._ensure_valid_token()

        if not track_uris:
            return True

        for i in range(0, len(track_uris), self.BATCH_SIZE):
            batch = track_uris[i: i + self.BATCH_SIZE]
            items = [{"uri": uri} for uri in batch]
            self._http.delete(
                f"/playlists/{playlist_id}/items",
                json={"tracks": items},
            )

        logger.info(
            "Removed %d tracks from playlist %s",
            len(track_uris), playlist_id,
        )

        if self._cache:
            self._cache.invalidate_playlist(playlist_id)
            if self._user_id:
                self._cache.invalidate_user_playlists(
                    self._user_id
                )

        return True

    @api_error_handler
    def create_user_playlist(
        self,
        user_id: str,
        name: str,
        public: bool = False,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Create a new playlist for a user.

        Args:
            user_id: The Spotify user ID.
            name: Playlist name.
            public: Whether the playlist is public.
            description: Playlist description.

        Returns:
            The created playlist dictionary.
        """
        self._ensure_valid_token()

        result = self._http.post(
            f"/users/{user_id}/playlists",
            json={
                "name": name,
                "public": public,
                "description": description,
            },
        )

        if self._cache and self._user_id:
            self._cache.invalidate_user_playlists(self._user_id)

        return result

    @api_error_handler
    def get_playlist_items_raw(
        self,
        playlist_id: str,
        fields: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Get playlist items with optional field filtering.

        Uses Spotify's field filtering to reduce response size.
        Note: Field filters use 'item(...)' not 'track(...)' per
        the Feb 2026 API changes.

        Args:
            playlist_id: The Spotify playlist ID.
            fields: Spotify field filter string.
            limit: Max items per page.

        Returns:
            Raw API response dictionary.
        """
        self._ensure_valid_token()

        params = {"limit": limit}
        if fields:
            params["fields"] = fields

        return self._http.get(
            f"/playlists/{playlist_id}/items",
            params=params,
        )

    # =========================================================================
    # Audio Features Operations
    # =========================================================================

    @api_error_handler
    def get_audio_features(
        self, track_ids: List[str], skip_cache: bool = False
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get audio features for multiple tracks.

        Args:
            track_ids: List of track IDs or URIs.
            skip_cache: If True, bypass cache and fetch fresh data.

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
                clean_id = (
                    tid.split(":")[-1] if ":" in tid else tid
                )
                valid_ids.append(clean_id)

        if not valid_ids:
            return features

        # Check cache first
        ids_to_fetch = valid_ids
        if self._cache and not skip_cache:
            cached_features = self._cache.get_audio_features(
                valid_ids
            )
            features.update(cached_features)
            ids_to_fetch = [
                tid
                for tid in valid_ids
                if tid not in cached_features
            ]
            if not ids_to_fetch:
                logger.debug(
                    f"All {len(valid_ids)} audio features "
                    f"from cache"
                )
                return features

        # Fetch uncached in batches
        new_features = {}
        for i in range(
            0, len(ids_to_fetch), self.AUDIO_FEATURES_BATCH_SIZE
        ):
            batch = ids_to_fetch[
                i: i + self.AUDIO_FEATURES_BATCH_SIZE
            ]
            result = self._http.get(
                "/audio-features",
                params={"ids": ",".join(batch)},
            )

            results = (
                result.get("audio_features", [])
                if result
                else []
            )
            if results:
                for track_id, feature in zip(batch, results):
                    if feature:
                        features[track_id] = feature
                        new_features[track_id] = feature

        logger.debug(
            f"Retrieved audio features for "
            f"{len(new_features)} tracks (from API)"
        )

        # Cache the new results
        if self._cache and new_features:
            self._cache.set_audio_features(new_features)

        return features

    # =========================================================================
    # Search Operations
    # =========================================================================

    @api_error_handler
    def search_playlists(
        self,
        query: str,
        limit: int = 10,
        skip_cache: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Search for playlists by name.

        Args:
            query: Search query string.
            limit: Maximum number of results (1-10, default 10).
            skip_cache: If True, bypass cache and fetch fresh data.

        Returns:
            List of playlist summary dictionaries with keys:
            id, name, owner_display_name, image_url, total_tracks.

        Raises:
            SpotifyAPIError: If the request fails.
        """
        self._ensure_valid_token()

        # Clamp limit to Spotify's allowed range
        limit = max(1, min(limit, 10))

        # Check cache first
        if self._cache and not skip_cache:
            cached = self._cache.get_search_playlists(query, limit)
            if cached is not None:
                return cached

        results = self._http.get(
            "/search",
            params={"q": query, "type": "playlist", "limit": limit},
        )

        playlists = []
        if (
            results
            and "playlists" in results
            and "items" in results["playlists"]
        ):
            for item in results["playlists"]["items"]:
                if item is None:
                    continue
                # Defensive fallback: search response may use
                # "items" or "tracks" for the total count depending
                # on API version. Handle both safely.
                total_key = item.get(
                    "items", item.get("tracks", {})
                )
                playlists.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "owner_display_name": item.get(
                            "owner", {}
                        ).get("display_name", "Unknown"),
                        "image_url": (
                            item["images"][0]["url"]
                            if item.get("images")
                            else None
                        ),
                        "total_tracks": total_key.get(
                            "total", 0
                        ),
                    }
                )

        logger.debug(
            f"Playlist search for '{query}' returned "
            f"{len(playlists)} results"
        )

        # Cache the results
        if self._cache and playlists:
            self._cache.set_search_playlists(
                query, limit, playlists
            )

        return playlists

    @api_error_handler
    def search_tracks(
        self,
        query: str,
        limit: int = 10,
        offset: int = 0,
        market: Optional[str] = None,
        skip_cache: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Search Spotify's catalog for tracks.

        Args:
            query: Search query string.
            limit: Maximum number of results (1-10, default 10).
            offset: Result offset for pagination (default 0).
            market: ISO 3166-1 alpha-2 country code.
            skip_cache: If True, bypass cache and fetch fresh data.

        Returns:
            List of track dictionaries from Spotify search.

        Raises:
            SpotifyAPIError: If the search request fails.
        """
        self._ensure_valid_token()

        # Clamp limit to Spotify's maximum
        limit = max(1, min(limit, 10))
        offset = max(0, offset)

        # Check cache first
        if self._cache and not skip_cache:
            cached = self._cache.get_search_results(query, offset)
            if cached is not None:
                return cached

        params = {
            "q": query,
            "type": "track",
            "limit": limit,
            "offset": offset,
        }
        if market:
            params["market"] = market

        results = self._http.get("/search", params=params)

        tracks = []
        if (
            results
            and "tracks" in results
            and "items" in results["tracks"]
        ):
            for item in results["tracks"]["items"]:
                if item and item.get("uri"):
                    tracks.append(item)

        logger.debug(
            f"Search for '{query}' returned {len(tracks)} tracks "
            f"(offset={offset}, limit={limit})"
        )

        # Cache the results
        if self._cache and tracks:
            self._cache.set_search_results(query, offset, tracks)

        return tracks
