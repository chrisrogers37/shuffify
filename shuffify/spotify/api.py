"""
Spotify API data operations.

Handles all Spotify Web API data operations: playlists, tracks, user info, etc.
This module is responsible for data operations only, separating them from
authentication concerns.

Supports optional Redis caching for improved performance.
"""

import logging
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from .auth import SpotifyAuthManager, TokenInfo
from .error_handling import api_error_handler
from .exceptions import (
    SpotifyAPIError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
    SpotifyPartialBatchError,
    SpotifyRateLimitError,
    SpotifyTokenExpiredError,
    SpotifyWriteRejectedError,
)
from .http_client import SpotifyHTTPClient

if TYPE_CHECKING:
    from .cache import SpotifyCache

logger = logging.getLogger(__name__)

# A track URI with a well-formed ID: anything else stays out of the URL path.
_TRACK_URI = re.compile(r"spotify:track:([A-Za-z0-9]{22})")
# What get_playlist_summary asks Spotify for: enough to name a playlist and decide who may edit it.
PLAYLIST_SUMMARY_FIELDS = "id,name,owner(id),collaborative"


def _write_may_have_applied(error: Exception) -> bool:
    """Only explicit request rejections prove a failed batch did not write.

    A generic API error does not establish where in the request it failed.
    In particular, a timeout or server error can follow an applied mutation.
    """
    return not isinstance(error, (
        SpotifyForbiddenError,
        SpotifyNotFoundError,
        SpotifyRateLimitError,
        SpotifyTokenExpiredError,
        SpotifyWriteRejectedError,
    ))


def playlist_item_count(playlist: Dict[str, Any], default: Optional[int] = 0) -> Optional[int]:
    """How many items a Spotify playlist object says it holds, in either shape.

    Spotify returns a playlist's item reference as ``items`` or ``tracks``,
    varying by response, so a reader must take either.
    A missing or malformed count reads as ``default``.
    """
    if not isinstance(playlist, Mapping):
        return default
    for key in ("items", "tracks"):
        ref = playlist.get(key)
        if isinstance(ref, dict) and isinstance(ref.get("total"), int):
            return ref["total"]
    return default


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
        on_token_refresh: Optional[Callable[[TokenInfo], None]] = None,
    ):
        """
        Initialize the API client.

        Args:
            token_info: TokenInfo with an access token. May be expired -- it
                will be refreshed on construction when auto_refresh is enabled.
            auth_manager: Optional auth manager for token refresh.
            auto_refresh: Whether to automatically refresh expired tokens.
            cache: Optional SpotifyCache instance for caching API responses.
            on_token_refresh: Optional callback invoked with the new TokenInfo
                whenever the access token is refreshed: at construction, on
                expiry before a call, or on a 401 retry. Web requests use it
                to write the token back to the session; background jobs to
                store a rotated refresh token.

        Raises:
            SpotifyTokenExpiredError: If token is expired and cannot be refreshed.
        """
        self._auth_manager = auth_manager
        self._auto_refresh = auto_refresh and auth_manager is not None
        self._cache = cache
        self._on_token_refresh = on_token_refresh

        # Ensure token is valid, refreshing if needed.
        refreshed_at_init = False
        if token_info.is_expired:
            if self._auto_refresh:
                token_info = auth_manager.ensure_valid_token(token_info)
                refreshed_at_init = True
            else:
                raise SpotifyTokenExpiredError("Token is expired")

        self._token_info = token_info
        self._http = SpotifyHTTPClient(
            token_info.access_token,
            on_token_refresh=(
                self._handle_token_refresh if self._auto_refresh else None
            ),
        )
        self._user_id: Optional[str] = None
        logger.debug(
            "SpotifyAPI initialized with valid token%s",
            " (with cache)" if cache else "",
        )

        # If we refreshed while constructing, let the caller persist the new
        # token (e.g. write it back to the session) so the next request reuses
        # it instead of refreshing again.
        if refreshed_at_init:
            self._notify_token_refresh()

    def _notify_token_refresh(self) -> None:
        """Invoke the on_token_refresh callback with the current token.

        Best-effort: a failing callback is logged and swallowed so token
        persistence can never break an API call.
        """
        if self._on_token_refresh is None:
            return
        try:
            self._on_token_refresh(self._token_info)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("on_token_refresh callback failed: %s", e)

    def _handle_token_refresh(self) -> str:
        """Callback for SpotifyHTTPClient on 401 responses.

        Spotify has just rejected the token, so it is refreshed outright,
        whatever the local clock says. ensure_valid_token() would hand back
        the same token while expires_at is still ahead -- clock skew, or a
        token Spotify invalidated early -- and the retry would 401 again.
        """
        self._token_info = self._auth_manager.refresh_token(self._token_info)
        self._notify_token_refresh()
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
            raise SpotifyTokenExpiredError("Token expired and auto-refresh disabled")

        logger.info("Token expired, refreshing...")
        self._token_info = self._auth_manager.ensure_valid_token(self._token_info)
        self._http.update_token(self._token_info.access_token)
        self._notify_token_refresh()

    def _get_user_id(self) -> str:
        """Resolve the authenticated account; never infer it from a playlist."""
        if self._user_id is None:
            user = self.get_current_user()
            self._user_id = user.get("id")
        if not isinstance(self._user_id, str) or not self._user_id:
            raise SpotifyAPIError("Spotify did not return an authenticated account ID")
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
        logger.debug(f"Retrieved user: {user.get('display_name', 'Unknown')}")

        # Cache the result
        if self._cache and user:
            self._user_id = user["id"]
            self._cache.set_user(user["id"], user)

        return user

    # =========================================================================
    # Playlist Operations
    # =========================================================================

    @api_error_handler
    def get_user_playlists(self, skip_cache: bool = False) -> List[Dict[str, Any]]:
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
            if playlist["owner"]["id"] == user_id or playlist.get("collaborative"):
                playlists.append(playlist)

        logger.debug(f"Retrieved {len(playlists)} editable playlists")

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

        # Only /me can establish the account allowed to reuse private data.
        # Identity failures propagate: there is no anonymous/global fallback.
        user_id = self._get_user_id() if self._cache else None

        # Check cache first
        if self._cache and not skip_cache:
            cached = self._cache.get_playlist(playlist_id, user_id=user_id)
            if cached is not None:
                return cached

        playlist = self._http.get(f"/playlists/{playlist_id}")
        logger.debug(f"Retrieved playlist: {playlist.get('name', 'Unknown')}")

        # Cache the result
        if self._cache:
            self._cache.set_playlist(playlist_id, playlist, user_id=user_id)

        return playlist

    @api_error_handler
    def get_playlist_summary(self, playlist_id: str) -> Dict[str, Any]:
        """
        A playlist's ``id``, ``name``, ``owner.id`` and ``collaborative``, read fresh.

        One uncached request for only those fields: no ``/me`` read and no
        cache write, for deciding whether the user may edit the playlist.

        Raises:
            SpotifyNotFoundError: If Spotify does not have the playlist.
            SpotifyAPIError: If the request fails (SpotifyForbiddenError
                for a 403).
        """
        self._ensure_valid_token()
        return self._http.get(f"/playlists/{playlist_id}", params={"fields": PLAYLIST_SUMMARY_FIELDS})

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
        user_id = self._get_user_id() if self._cache else None

        # Check cache first
        if self._cache and not skip_cache:
            cached = self._cache.get_playlist_tracks(playlist_id, user_id=user_id)
            if cached is not None:
                return cached

        tracks = []

        all_items = self._http.get_all_pages(f"/playlists/{playlist_id}/items")
        for item in all_items:
            # The playlist-items resource names the nested object "track" on
            # the legacy shape and "item" on the current one. A single
            # playlist can return either, so both are read.
            track = item.get("track") or item.get("item")
            # Only include valid tracks (not None, not local-only)
            if track and track.get("uri"):
                # Preserve added_at from the playlist item wrapper
                if "added_at" in item:
                    track["added_at"] = item["added_at"]
                tracks.append(track)

        logger.debug(f"Retrieved {len(tracks)} tracks from playlist {playlist_id}")

        # Cache the result
        if self._cache:
            self._cache.set_playlist_tracks(playlist_id, tracks, user_id=user_id)

        return tracks

    @api_error_handler
    def update_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """
        Replace all tracks in a playlist with a new list.

        Args:
            playlist_id: The Spotify playlist ID.
            track_uris: List of track URIs in the desired order.

        Returns:
            True if every batch was confirmed by Spotify.

        Raises:
            SpotifyNotFoundError: If playlist doesn't exist.
            SpotifyPartialBatchError: If a batch fails mid-flight.
                The exception carries the URIs that were written
                vs. the ones that were not.
            SpotifyAPIError: If the update fails for non-batch
                reasons.
        """
        self._ensure_valid_token()

        # Clearing is a real PUT and needs the same failure diagnostics as
        # replacing a nonempty playlist.
        total_batches = max(1, (len(track_uris) + self.BATCH_SIZE - 1) // self.BATCH_SIZE)
        completed: List[str] = []

        for batch_idx in range(total_batches):
            i = batch_idx * self.BATCH_SIZE
            batch = track_uris[i : i + self.BATCH_SIZE]
            write = self._http.put if batch_idx == 0 else self._http.post
            try:
                write(
                    f"/playlists/{playlist_id}/items",
                    json={"uris": batch},
                )
            except (SpotifyAPIError, SpotifyTokenExpiredError) as e:
                if batch_idx > 0 or _write_may_have_applied(e):
                    self._invalidate_playlist_write_cache(playlist_id)
                # Preserve the ordinary auth flow when no writes succeeded.
                if batch_idx == 0 and isinstance(e, SpotifyTokenExpiredError):
                    raise
                raise SpotifyPartialBatchError(
                    playlist_id=playlist_id,
                    method="update",
                    completed_batches=batch_idx,
                    total_batches=total_batches,
                    completed_uris=list(completed),
                    remaining_uris=list(track_uris[i:]),
                    cause=e,
                ) from e
            completed.extend(batch)

        logger.info(f"Updated playlist {playlist_id} with {len(track_uris)} tracks")

        self._invalidate_playlist_write_cache(playlist_id)

        return True

    def _invalidate_playlist_write_cache(self, playlist_id: str) -> None:
        """Discard cached order and counts after a known or possible write."""
        if self._cache:
            self._cache.invalidate_playlist(playlist_id)
            if self._user_id:
                self._cache.invalidate_user_playlists(self._user_id)

    @api_error_handler
    def get_tracks(self, track_uris: List[str]) -> List[Dict[str, Any]]:
        """
        Fetch full track metadata for a list of track URIs.

        One request per track (``GET /tracks/{id}``): the batch lookup
        (``GET /tracks?ids=``) is unavailable to Development Mode apps.

        Args:
            track_uris: List of Spotify track URIs.

        Returns:
            Track dictionaries in the order given. URIs that are not
            well-formed track URIs, or that Spotify cannot find, are
            left out. If a request fails any other way (rate limited,
            Spotify unavailable), the tracks already fetched are
            returned rather than lost; the error is raised only when
            there are none.
        """
        self._ensure_valid_token()

        tracks = []
        for uri in track_uris:
            match = _TRACK_URI.fullmatch(uri)
            if not match:
                continue
            try:
                track = self._http.get(f"/tracks/{match.group(1)}")
            except SpotifyNotFoundError:
                continue
            except SpotifyAPIError as e:
                if not tracks:
                    raise
                logger.warning(
                    "Got %d of %d tracks before Spotify stopped answering: %s",
                    len(tracks),
                    len(track_uris),
                    e,
                )
                break
            if track and track.get("uri"):
                tracks.append(track)

        return tracks

    @api_error_handler
    def playlist_add_items(
        self,
        playlist_id: str,
        track_uris: List[str],
        position: Optional[int] = None,
    ) -> bool:
        """
        Add tracks to a playlist in batches.

        Args:
            playlist_id: The Spotify playlist ID.
            track_uris: List of track URIs to add.
            position: Optional insertion position (0-based).
                Each batch offsets from this position to
                maintain correct ordering.

        Returns:
            True if every batch was confirmed by Spotify.

        Raises:
            SpotifyPartialBatchError: If a batch fails mid-flight.
                The exception carries the URIs that were written
                vs. the ones that were not.
            SpotifyAPIError: For non-batch HTTP failures.
        """
        self._ensure_valid_token()

        if not track_uris:
            return True

        total_batches = (len(track_uris) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        completed: List[str] = []

        for batch_idx, i in enumerate(range(0, len(track_uris), self.BATCH_SIZE)):
            batch = track_uris[i : i + self.BATCH_SIZE]
            payload: Dict[str, Any] = {"uris": batch}
            if position is not None:
                payload["position"] = position + i
            try:
                self._http.post(
                    f"/playlists/{playlist_id}/items",
                    json=payload,
                )
            except (SpotifyAPIError, SpotifyTokenExpiredError) as e:
                if batch_idx > 0 or _write_may_have_applied(e):
                    self._invalidate_playlist_write_cache(playlist_id)
                if batch_idx == 0 and isinstance(e, SpotifyTokenExpiredError):
                    raise
                raise SpotifyPartialBatchError(
                    playlist_id=playlist_id,
                    method="add",
                    completed_batches=batch_idx,
                    total_batches=total_batches,
                    completed_uris=list(completed),
                    remaining_uris=list(track_uris[i:]),
                    cause=e,
                ) from e
            completed.extend(batch)

        if self._cache:
            self._cache.invalidate_playlist(playlist_id)

        return True

    @api_error_handler
    def playlist_remove_items(self, playlist_id: str, track_uris: List[str]) -> bool:
        """
        Remove specific tracks from a playlist.

        Note: Spotify silently no-ops on URIs that aren't on
        the playlist. This method only raises on HTTP-level
        failures; "URI was on the playlist but didn't get
        removed" is caught by `verify_playlist_state`, not
        here.

        Args:
            playlist_id: The Spotify playlist ID.
            track_uris: List of track URIs to remove.

        Returns:
            True if every batch was confirmed by Spotify.

        Raises:
            SpotifyNotFoundError: If playlist doesn't exist.
            SpotifyPartialBatchError: If a batch fails mid-flight.
            SpotifyAPIError: For non-batch HTTP failures.
        """
        self._ensure_valid_token()

        if not track_uris:
            return True

        total_batches = (len(track_uris) + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        completed: List[str] = []

        for batch_idx, i in enumerate(range(0, len(track_uris), self.BATCH_SIZE)):
            batch = track_uris[i : i + self.BATCH_SIZE]
            try:
                self._http.delete(
                    f"/playlists/{playlist_id}/items",
                    json={"items": [{"uri": u} for u in batch]},
                )
            except (SpotifyAPIError, SpotifyTokenExpiredError) as e:
                if batch_idx > 0 or _write_may_have_applied(e):
                    self._invalidate_playlist_write_cache(playlist_id)
                if batch_idx == 0 and isinstance(e, SpotifyTokenExpiredError):
                    raise
                raise SpotifyPartialBatchError(
                    playlist_id=playlist_id,
                    method="remove",
                    completed_batches=batch_idx,
                    total_batches=total_batches,
                    completed_uris=list(completed),
                    remaining_uris=list(track_uris[i:]),
                    cause=e,
                ) from e
            completed.extend(batch)

        logger.info(
            "Removed %d tracks from playlist %s",
            len(track_uris),
            playlist_id,
        )

        if self._cache:
            self._cache.invalidate_playlist(playlist_id)
            if self._user_id:
                self._cache.invalidate_user_playlists(self._user_id)

        return True

    @api_error_handler
    def create_user_playlist(
        self,
        name: str,
        public: bool = False,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Create a new playlist for the current user.

        Spotify creates it for the token's user (``POST /me/playlists``);
        the per-user ``POST /users/{id}/playlists`` is unavailable to
        Development Mode apps.

        Args:
            name: Playlist name.
            public: Whether the playlist is public.
            description: Playlist description.

        Returns:
            The created playlist dictionary.
        """
        self._ensure_valid_token()

        result = self._http.post(
            "/me/playlists",
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
    def update_playlist_details(
        self,
        playlist_id: str,
        **kwargs,
    ) -> None:
        """
        Update a playlist's details (name, public, description).

        Only provided kwargs are sent to the API.
        """
        self._ensure_valid_token()

        allowed = {"name", "public", "description"}
        body = {k: v for k, v in kwargs.items() if k in allowed}
        if not body:
            return

        self._http.put(
            f"/playlists/{playlist_id}",
            json=body,
        )
        if self._cache:
            self._cache.invalidate_playlist(playlist_id)

    @api_error_handler
    def get_playlist_items_raw(
        self,
        playlist_id: str,
        fields: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get playlist items with optional field filtering.

        Uses Spotify's field filtering to reduce response size.

        Note: the nested object is named ``track(...)`` on the legacy response
        shape and ``item(...)`` on the current one. A filter naming only one
        strips the other out of the response, so callers that read both keys
        must request both.

        Args:
            playlist_id: The Spotify playlist ID.
            fields: Spotify field filter string.
            limit: Max items per page.
            offset: Index of the first item to return.

        Returns:
            Raw API response dictionary.
        """
        self._ensure_valid_token()

        params = {"limit": limit, "offset": offset}
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
                clean_id = tid.split(":")[-1] if ":" in tid else tid
                valid_ids.append(clean_id)

        if not valid_ids:
            return features

        # Check cache first
        ids_to_fetch = valid_ids
        if self._cache and not skip_cache:
            cached_features = self._cache.get_audio_features(valid_ids)
            features.update(cached_features)
            ids_to_fetch = [tid for tid in valid_ids if tid not in cached_features]
            if not ids_to_fetch:
                logger.debug(f"All {len(valid_ids)} audio features from cache")
                return features

        # Fetch uncached in batches
        new_features = {}
        for i in range(0, len(ids_to_fetch), self.AUDIO_FEATURES_BATCH_SIZE):
            batch = ids_to_fetch[i : i + self.AUDIO_FEATURES_BATCH_SIZE]
            result = self._http.get(
                "/audio-features",
                params={"ids": ",".join(batch)},
            )

            results = result.get("audio_features", []) if result else []
            if results:
                for track_id, feature in zip(batch, results):
                    if feature:
                        features[track_id] = feature
                        new_features[track_id] = feature

        logger.debug(
            f"Retrieved audio features for {len(new_features)} tracks (from API)"
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
        if results and "playlists" in results and "items" in results["playlists"]:
            for item in results["playlists"]["items"]:
                if item is None:
                    continue
                playlists.append(
                    {
                        "id": item["id"],
                        "name": item["name"],
                        "owner_display_name": item.get("owner", {}).get(
                            "display_name", "Unknown"
                        ),
                        "owner_id": item.get("owner", {}).get("id", ""),
                        "image_url": (
                            item["images"][0]["url"] if item.get("images") else None
                        ),
                        "total_tracks": playlist_item_count(item),
                    }
                )

        logger.debug(f"Playlist search for '{query}' returned {len(playlists)} results")

        # Cache the results
        if self._cache and playlists:
            self._cache.set_search_playlists(query, limit, playlists)

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
        if results and "tracks" in results and "items" in results["tracks"]:
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
