"""
Redis caching layer for Spotify API responses.

Provides caching functionality to reduce API calls and improve response times.
Cache keys are prefixed and organized by data type with appropriate TTLs.
"""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, TypeVar

import redis

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SpotifyCache:
    """
    Redis-based cache for Spotify API responses.

    Provides methods to cache and retrieve Spotify API data with
    configurable TTLs per data type.

    Example:
        cache = SpotifyCache(redis_client, key_prefix='shuffify:cache:')

        # Cache user playlists
        cache.set_playlists(user_id, playlists, ttl=60)

        # Retrieve cached playlists
        playlists = cache.get_playlists(user_id)
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        key_prefix: str = "shuffify:cache:",
        default_ttl: int = 300,
        playlist_ttl: int = 60,
        user_ttl: int = 600,
    ):
        """
        Initialize the cache.

        Args:
            redis_client: Redis client instance.
            key_prefix: Prefix for all cache keys.
            default_ttl: Default TTL in seconds.
            playlist_ttl: TTL for playlist data.
            user_ttl: TTL for user profile data.
        """
        self._redis = redis_client
        self._prefix = key_prefix
        self._default_ttl = default_ttl
        self._playlist_ttl = playlist_ttl
        self._user_ttl = user_ttl

    def _make_key(self, namespace: str, *parts: str) -> str:
        """
        Create a cache key from namespace and parts.

        Args:
            namespace: Key namespace (e.g., 'user', 'playlist').
            *parts: Key components.

        Returns:
            Formatted cache key.
        """
        return f"{self._prefix}{namespace}:{':'.join(parts)}"

    def _serialize(self, data: Any) -> bytes:
        """Serialize data to bytes for storage."""
        return json.dumps(data).encode("utf-8")

    def _deserialize(self, data: bytes) -> Any:
        """Deserialize bytes to Python object."""
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))

    def _playlist_account_key(
        self, namespace: str, playlist_id: str, user_id: str
    ) -> str:
        """Key private data by verified account, never by owner or bearer token.

        The versioned namespace keeps these keys apart from unscoped entries.
        Hashing each component also prevents separators or Redis glob characters
        in an identifier from merging scopes or widening invalidation scans.
        """
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("Playlist cache requires a Spotify account ID")
        return self._make_key(
            f"{namespace}_account_v2",
            hashlib.sha256(playlist_id.encode("utf-8")).hexdigest(),
            hashlib.sha256(user_id.encode("utf-8")).hexdigest(),
        )

    # =========================================================================
    # User Data
    # =========================================================================

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get cached user profile.

        Args:
            user_id: Spotify user ID.

        Returns:
            User profile dict or None if not cached.
        """
        try:
            key = self._make_key("user", user_id)
            data = self._redis.get(key)
            if data:
                logger.debug(f"Cache hit for user: {user_id}")
                return self._deserialize(data)
            logger.debug(f"Cache miss for user: {user_id}")
            return None
        except redis.RedisError as e:
            logger.warning(f"Redis error getting user cache: {e}")
            return None

    def set_user(
        self, user_id: str, user_data: Dict[str, Any], ttl: Optional[int] = None
    ) -> bool:
        """
        Cache user profile.

        Args:
            user_id: Spotify user ID.
            user_data: User profile data.
            ttl: Time-to-live in seconds (default: user_ttl).

        Returns:
            True if cached successfully.
        """
        try:
            key = self._make_key("user", user_id)
            ttl = ttl or self._user_ttl
            self._redis.setex(key, ttl, self._serialize(user_data))
            logger.debug(f"Cached user: {user_id} (TTL: {ttl}s)")
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis error setting user cache: {e}")
            return False

    # =========================================================================
    # Playlist Data
    # =========================================================================

    def get_playlists(self, user_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached user playlists.

        Args:
            user_id: Spotify user ID.

        Returns:
            List of playlist dicts or None if not cached.
        """
        try:
            key = self._make_key("playlists", user_id)
            data = self._redis.get(key)
            if data:
                logger.debug(f"Cache hit for playlists: {user_id}")
                return self._deserialize(data)
            logger.debug(f"Cache miss for playlists: {user_id}")
            return None
        except redis.RedisError as e:
            logger.warning(f"Redis error getting playlists cache: {e}")
            return None

    def set_playlists(
        self, user_id: str, playlists: List[Dict[str, Any]], ttl: Optional[int] = None
    ) -> bool:
        """
        Cache user playlists.

        Args:
            user_id: Spotify user ID.
            playlists: List of playlist data.
            ttl: Time-to-live in seconds (default: playlist_ttl).

        Returns:
            True if cached successfully.
        """
        try:
            key = self._make_key("playlists", user_id)
            ttl = ttl or self._playlist_ttl
            self._redis.setex(key, ttl, self._serialize(playlists))
            logger.debug(
                f"Cached {len(playlists)} playlists for user: {user_id} (TTL: {ttl}s)"
            )
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis error setting playlists cache: {e}")
            return False

    def get_playlist(
        self, playlist_id: str, *, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached single playlist.

        Args:
            playlist_id: Spotify playlist ID.
            user_id: Authenticated Spotify account ID, resolved from /me.

        Returns:
            Playlist dict or None if not cached.
        """
        try:
            key = self._playlist_account_key("playlist", playlist_id, user_id)
            data = self._redis.get(key)
            if data:
                logger.debug(f"Cache hit for playlist: {playlist_id}")
                return self._deserialize(data)
            logger.debug(f"Cache miss for playlist: {playlist_id}")
            return None
        except redis.RedisError as e:
            logger.warning(f"Redis error getting playlist cache: {e}")
            return None

    def set_playlist(
        self, playlist_id: str, playlist: Dict[str, Any], ttl: Optional[int] = None,
        *, user_id: str,
    ) -> bool:
        """
        Cache single playlist.

        Args:
            playlist_id: Spotify playlist ID.
            user_id: Authenticated Spotify account ID, resolved from /me.
            playlist: Playlist data.
            ttl: Time-to-live in seconds (default: playlist_ttl).

        Returns:
            True if cached successfully.
        """
        try:
            key = self._playlist_account_key("playlist", playlist_id, user_id)
            ttl = ttl or self._playlist_ttl
            self._redis.setex(key, ttl, self._serialize(playlist))
            logger.debug(f"Cached playlist: {playlist_id} (TTL: {ttl}s)")
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis error setting playlist cache: {e}")
            return False

    # =========================================================================
    # Playlist Tracks
    # =========================================================================

    def get_playlist_tracks(
        self, playlist_id: str, *, user_id: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached playlist tracks.

        Args:
            playlist_id: Spotify playlist ID.
            user_id: Authenticated Spotify account ID, resolved from /me.

        Returns:
            List of track dicts or None if not cached.
        """
        try:
            key = self._playlist_account_key("tracks", playlist_id, user_id)
            data = self._redis.get(key)
            if data:
                logger.debug(f"Cache hit for tracks: {playlist_id}")
                return self._deserialize(data)
            logger.debug(f"Cache miss for tracks: {playlist_id}")
            return None
        except redis.RedisError as e:
            logger.warning(f"Redis error getting tracks cache: {e}")
            return None

    def set_playlist_tracks(
        self, playlist_id: str, tracks: List[Dict[str, Any]], ttl: Optional[int] = None,
        *, user_id: str,
    ) -> bool:
        """
        Cache playlist tracks.

        Args:
            playlist_id: Spotify playlist ID.
            user_id: Authenticated Spotify account ID, resolved from /me.
            tracks: List of track data.
            ttl: Time-to-live in seconds (default: playlist_ttl).

        Returns:
            True if cached successfully.
        """
        try:
            key = self._playlist_account_key("tracks", playlist_id, user_id)
            ttl = ttl or self._playlist_ttl
            self._redis.setex(key, ttl, self._serialize(tracks))
            logger.debug(
                f"Cached {len(tracks)} tracks for playlist: {playlist_id} (TTL: {ttl}s)"
            )
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis error setting tracks cache: {e}")
            return False

    # =========================================================================
    # Search Results — Tracks
    # =========================================================================

    def get_search_results(
        self, query: str, offset: int = 0
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached search results.

        Args:
            query: The search query string (lowercased for cache key).
            offset: Pagination offset.

        Returns:
            List of track dicts or None if not cached.
        """
        try:
            normalized_query = query.strip().lower()
            key = self._make_key("search", normalized_query, str(offset))
            data = self._redis.get(key)
            if data:
                logger.debug(
                    f"Cache hit for search: {normalized_query} offset={offset}"
                )
                return self._deserialize(data)
            logger.debug(
                f"Cache miss for search: {normalized_query} offset={offset}"
            )
            return None
        except redis.RedisError as e:
            logger.warning(f"Redis error getting search cache: {e}")
            return None

    def set_search_results(
        self,
        query: str,
        offset: int,
        results: List[Dict[str, Any]],
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Cache search results.

        Args:
            query: The search query string.
            offset: Pagination offset.
            results: List of track data from Spotify search.
            ttl: Time-to-live in seconds (default: 120s).

        Returns:
            True if cached successfully.
        """
        try:
            normalized_query = query.strip().lower()
            key = self._make_key("search", normalized_query, str(offset))
            ttl = ttl or 120
            self._redis.setex(key, ttl, self._serialize(results))
            logger.debug(
                f"Cached {len(results)} search results for: "
                f"{normalized_query} offset={offset} (TTL: {ttl}s)"
            )
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis error setting search cache: {e}")
            return False

    # =========================================================================
    # Search Results — Playlists
    # =========================================================================

    def get_search_playlists(
        self, query: str, limit: int
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Get cached playlist search results.

        Args:
            query: The search query string.
            limit: The result limit used in the search.

        Returns:
            List of playlist summary dicts, or None if not cached.
        """
        try:
            key = self._make_key(
                "search_playlists", f"{query.lower()}:{limit}"
            )
            data = self._redis.get(key)
            if data:
                logger.debug(f"Cache hit for playlist search: {query!r}")
                return self._deserialize(data)
            logger.debug(f"Cache miss for playlist search: {query!r}")
            return None
        except redis.RedisError as e:
            logger.warning(f"Redis error getting search cache: {e}")
            return None

    def set_search_playlists(
        self,
        query: str,
        limit: int,
        results: List[Dict[str, Any]],
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Cache playlist search results.

        Args:
            query: The search query string.
            limit: The result limit used in the search.
            results: List of playlist summary dicts.
            ttl: Time-to-live in seconds (default: default_ttl / 300s).

        Returns:
            True if cached successfully.
        """
        try:
            key = self._make_key(
                "search_playlists", f"{query.lower()}:{limit}"
            )
            ttl = ttl or self._default_ttl
            self._redis.setex(key, ttl, self._serialize(results))
            logger.debug(
                f"Cached {len(results)} playlist search results for "
                f"{query!r} (TTL: {ttl}s)"
            )
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis error setting search cache: {e}")
            return False

    # =========================================================================
    # Cache Management
    # =========================================================================

    def invalidate_playlist(self, playlist_id: str) -> bool:
        """
        Invalidate metadata and tracks for every account caching a playlist.

        Use after modifying a playlist to ensure fresh data.

        Args:
            playlist_id: Spotify playlist ID.

        Returns:
            True if invalidation succeeded.
        """
        try:
            keys = [
                self._make_key("playlist", playlist_id),
                self._make_key("tracks", playlist_id),
            ]
            self._redis.delete(*keys)
            # A collaborative/public playlist may be cached by several accounts.
            # SCAN keeps Redis commands bounded; no persistent account index is
            # needed, and every scoped entry still has the ordinary playlist TTL.
            for namespace in ("playlist", "tracks"):
                prefix = self._make_key(
                    f"{namespace}_account_v2",
                    hashlib.sha256(playlist_id.encode("utf-8")).hexdigest(),
                    "",
                )
                # Escape the configurable prefix, keeping only our final glob.
                pattern = "".join(
                    f"\\{char}" if char in "\\*?[]" else char for char in prefix
                ) + "*"
                cursor = 0
                while True:
                    cursor, scoped_keys = self._redis.scan(
                        cursor, match=pattern, count=100
                    )
                    # COUNT is a Redis hint, so cap each DELETE explicitly too.
                    for start in range(0, len(scoped_keys), 100):
                        self._redis.delete(*scoped_keys[start:start + 100])
                    if cursor == 0:
                        break
            logger.debug(f"Invalidated cache for playlist: {playlist_id}")
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis error invalidating playlist cache: {e}")
            return False

    def invalidate_user_playlists(self, user_id: str) -> bool:
        """
        Invalidate cached playlists list for a user.

        Use after playlist changes to ensure fresh list.

        Args:
            user_id: Spotify user ID.

        Returns:
            True if invalidation succeeded.
        """
        try:
            key = self._make_key("playlists", user_id)
            self._redis.delete(key)
            logger.debug(f"Invalidated playlists cache for user: {user_id}")
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis error invalidating user playlists cache: {e}")
            return False
