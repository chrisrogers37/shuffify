"""
Playlist service for managing Spotify playlist operations.

Handles playlist retrieval, track management, and playlist updates.
"""

import json
import logging
import re
from typing import Dict, List, Any, Optional

import requests

from shuffify.spotify.client import SpotifyClient
from shuffify.spotify.exceptions import SpotifyNotFoundError
from shuffify.models.playlist import Playlist

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Scraper-based metadata fallback
# ---------------------------------------------------------------
_EMBED_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
_SCRAPE_TIMEOUT = 10
_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json"'
    r"[^>]*>(.*?)</script>",
    re.DOTALL,
)
_OG_TITLE_RE = re.compile(
    r'<meta\s+property="og:title"\s+content="([^"]+)"',
)


def scrape_playlist_metadata(
    playlist_id: str,
) -> Optional[Dict[str, Any]]:
    """Scrape basic playlist metadata from Spotify's public embed page.

    Fallback for when the authenticated API returns 404 (e.g. Spotify
    restricts GET /playlists/{id} for non-owned playlists).

    Returns a dict with keys ``id``, ``name``, ``owner_id`` (may be
    ``"unknown"``), ``description``, ``total_tracks``, and
    ``scraped: True``.  Returns ``None`` if scraping fails entirely.
    """
    url = _EMBED_URL.format(playlist_id=playlist_id)
    try:
        resp = requests.get(
            url,
            timeout=_SCRAPE_TIMEOUT,
            headers=_SCRAPE_HEADERS,
        )
        if resp.status_code != 200:
            logger.info(
                "Embed page returned %d for %s",
                resp.status_code,
                playlist_id,
            )
            return None
    except Exception as e:
        logger.info(
            "Embed page request failed for %s: %s",
            playlist_id,
            e,
        )
        return None

    html = resp.text

    # Strategy 1: parse __NEXT_DATA__ JSON
    meta = _parse_next_data_metadata(html, playlist_id)
    if meta:
        return meta

    # Strategy 2: og:title fallback (name only, no owner)
    og_match = _OG_TITLE_RE.search(html)
    if og_match:
        return {
            "id": playlist_id,
            "name": og_match.group(1),
            "owner_id": "unknown",
            "description": None,
            "total_tracks": None,
            "scraped": True,
        }

    return None


def _parse_next_data_metadata(
    html: str, playlist_id: str
) -> Optional[Dict[str, Any]]:
    """Extract playlist metadata from __NEXT_DATA__ JSON."""
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None

    entity = _find_key(data, "entity") or {}
    name = (
        entity.get("name")
        or _find_key(data, "name")
    )
    if not name:
        return None

    # Owner may be nested under entity.owner or similar
    owner = entity.get("owner") or _find_key(
        data, "owner"
    )
    owner_id = (
        owner.get("id", "unknown")
        if isinstance(owner, dict)
        else "unknown"
    )

    track_list = entity.get("trackList")
    total_tracks = (
        len(track_list)
        if isinstance(track_list, list)
        else None
    )

    return {
        "id": playlist_id,
        "name": name,
        "owner_id": owner_id,
        "description": entity.get("description"),
        "total_tracks": total_tracks,
        "scraped": True,
    }


def _find_key(data: Any, key: str) -> Any:
    """Find the first occurrence of a key in nested structure."""
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            result = _find_key(value, key)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_key(item, key)
            if result is not None:
                return result
    return None


class PlaylistError(Exception):
    """Base exception for playlist operations."""

    pass


class PlaylistNotFoundError(PlaylistError):
    """Raised when a playlist cannot be found."""

    pass


class PlaylistUpdateError(PlaylistError):
    """Raised when a playlist update fails."""

    pass


class PlaylistService:
    """Service for managing Spotify playlist operations."""

    def __init__(self, spotify_client: SpotifyClient):
        """
        Initialize the playlist service.

        Args:
            spotify_client: An authenticated SpotifyClient instance.
        """
        self._client = spotify_client

    def get_user_playlists(
        self, skip_cache: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetch all playlists the user can edit.

        Args:
            skip_cache: If True, bypass cache and fetch fresh data.

        Returns:
            List of playlist dictionaries (owned or collaborative).

        Raises:
            PlaylistError: If fetching playlists fails.
        """
        try:
            playlists = self._client.get_user_playlists(skip_cache=skip_cache)
            logger.debug(f"Retrieved {len(playlists)} user playlists")
            return playlists
        except Exception as e:
            logger.error(f"Failed to get user playlists: {e}", exc_info=True)
            raise PlaylistError(f"Failed to fetch playlists: {e}")

    def get_playlist(
        self, playlist_id: str, include_features: bool = False
    ) -> Playlist:
        """
        Fetch a single playlist with its tracks.

        Args:
            playlist_id: The Spotify playlist ID.
            include_features: Whether to include audio features for tracks.

        Returns:
            A Playlist model instance.

        Raises:
            PlaylistNotFoundError: If the playlist doesn't exist.
            PlaylistError: If fetching fails for other reasons.
        """
        if not playlist_id:
            raise PlaylistNotFoundError("Playlist ID is required")

        try:
            playlist = Playlist.from_spotify(
                self._client, playlist_id, include_features=include_features
            )
            logger.debug(
                f"Retrieved playlist '{playlist.name}' with {len(playlist)} tracks"
            )
            return playlist
        except (ValueError, SpotifyNotFoundError):
            logger.error(f"Invalid playlist ID: {playlist_id}")
            raise PlaylistNotFoundError(f"Playlist not found: {playlist_id}")
        except Exception as e:
            logger.error(f"Failed to get playlist {playlist_id}: {e}", exc_info=True)
            raise PlaylistError(f"Failed to fetch playlist: {e}")

    def get_playlist_metadata(
        self, playlist_id: str
    ) -> Dict[str, Any]:
        """
        Fetch playlist metadata only (no tracks).

        Uses GET /playlists/{playlist_id} which works for any public
        playlist regardless of ownership. Unlike get_playlist(), this
        does NOT call GET /playlists/{playlist_id}/items which is
        restricted to owners/collaborators since Feb 2026.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            Dict with keys: id, name, owner_id, description,
            total_tracks.

        Raises:
            PlaylistNotFoundError: If the playlist doesn't exist.
            PlaylistError: If fetching fails for other reasons.
        """
        if not playlist_id:
            raise PlaylistNotFoundError("Playlist ID is required")

        try:
            data = self._client.get_playlist(playlist_id)
            total_tracks_meta = data.get(
                "tracks", data.get("items", {})
            )
            total_tracks = (
                total_tracks_meta.get("total")
                if isinstance(total_tracks_meta, dict)
                else None
            )
            return {
                "id": data["id"],
                "name": data["name"],
                "owner_id": data["owner"]["id"],
                "description": data.get("description"),
                "total_tracks": total_tracks,
            }
        except (ValueError, SpotifyNotFoundError):
            raise PlaylistNotFoundError(
                f"Playlist not found: {playlist_id}"
            )
        except Exception as e:
            logger.error(
                f"Failed to get playlist metadata "
                f"{playlist_id}: {e}",
                exc_info=True,
            )
            raise PlaylistError(
                f"Failed to fetch playlist metadata: {e}"
            )

    def get_playlist_stats(self, playlist_id: str) -> Dict[str, Any]:
        """
        Get audio feature statistics for a playlist.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            Dictionary of feature statistics (tempo, energy, valence, danceability).

        Raises:
            PlaylistNotFoundError: If the playlist doesn't exist.
            PlaylistError: If fetching fails.
        """
        playlist = self.get_playlist(playlist_id, include_features=True)
        stats = playlist.get_feature_stats()
        logger.debug(f"Computed stats for playlist {playlist_id}")
        return stats

    def update_playlist_tracks(self, playlist_id: str, track_uris: List[str]) -> bool:
        """
        Update a playlist with a new track order.

        Args:
            playlist_id: The Spotify playlist ID.
            track_uris: List of track URIs in the desired order.

        Returns:
            True if update succeeded.

        Raises:
            PlaylistUpdateError: If the update fails.
        """
        if not playlist_id:
            raise PlaylistUpdateError("Playlist ID is required")

        if not track_uris:
            logger.warning(
                f"Attempting to update playlist {playlist_id} with empty track list"
            )

        try:
            success = self._client.update_playlist_tracks(playlist_id, track_uris)
            if success:
                logger.info(
                    f"Updated playlist {playlist_id} with {len(track_uris)} tracks"
                )
                return True
            else:
                raise PlaylistUpdateError("Spotify API returned failure")
        except PlaylistUpdateError:
            raise
        except Exception as e:
            logger.error(f"Failed to update playlist {playlist_id}: {e}", exc_info=True)
            raise PlaylistUpdateError(f"Failed to update playlist: {e}")

    def get_track_uris(self, playlist_id: str) -> List[str]:
        """
        Get just the track URIs for a playlist.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            List of track URIs in current order.

        Raises:
            PlaylistNotFoundError: If the playlist doesn't exist.
            PlaylistError: If fetching fails.
        """
        playlist = self.get_playlist(playlist_id, include_features=False)
        return playlist.get_track_uris()

    def validate_playlist_has_tracks(self, playlist: Playlist) -> None:
        """
        Validate that a playlist has tracks.

        Args:
            playlist: The Playlist to validate.

        Raises:
            PlaylistError: If playlist has no tracks.
        """
        if not playlist.tracks:
            raise PlaylistError(f"Playlist '{playlist.name}' has no tracks")
