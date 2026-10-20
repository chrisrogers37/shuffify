"""
Playlist service for managing Spotify playlist operations.

Handles playlist retrieval, track management, and playlist updates.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import requests

from shuffify.models.playlist import Playlist
from shuffify.services.source_resolver.base import (
    find_nested_key,
)
from shuffify.services.source_resolver.public_scraper_pathway import (
    EMBED_URL as _EMBED_URL,
)
from shuffify.spotify.api import SpotifyAPI, playlist_item_count
from shuffify.spotify.exceptions import (
    SpotifyAPIError,
    SpotifyAuthError,
    SpotifyForbiddenError,
    SpotifyNotFoundError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Scraper-based metadata fallback
# ---------------------------------------------------------------
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

    entity = find_nested_key(data, "entity") or {}
    name = (
        entity.get("name")
        or find_nested_key(data, "name")
    )
    if not name:
        return None

    # Owner may be nested under entity.owner or similar
    owner = entity.get("owner") or find_nested_key(
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


class PlaylistError(Exception):
    """Base exception for playlist operations."""

    pass


class PlaylistNotFoundError(PlaylistError):
    """Raised when a playlist cannot be found."""

    pass


class PlaylistUpdateError(PlaylistError):
    """Raised when a playlist update fails."""

    pass


class PlaylistAccessError(PlaylistError):
    """Raised when the user may not edit a playlist, or Spotify will not let them read it."""

    pass


class PlaylistService:
    """Service for managing Spotify playlist operations."""

    def __init__(self, api: SpotifyAPI):
        """
        Initialize the playlist service.

        Args:
            api: An authenticated SpotifyAPI instance.
        """
        self._api = api

    def validate_user_can_edit(
        self,
        playlist_id: str,
        user_spotify_id: str,
        *,
        skip_cache: bool = False,
        lean: bool = False,
    ) -> Dict[str, Any]:
        """
        Defense-in-depth guard: raise unless the user can edit the playlist.

        A user may edit a playlist if they own it or it is collaborative.
        Spotify also rejects unauthorized writes server-side, but this fails
        fast with a clear 403 and — crucially — blocks schedule creation
        against playlists the user cannot edit, which would otherwise fail
        forever at execution time.

        Args:
            playlist_id: The Spotify playlist ID to check.
            user_spotify_id: The authenticated user's Spotify ID.
            skip_cache: Require a fresh ownership/collaboration response.
            lean: Read only the ID, name, owner and collaborative flag
                (``SpotifyAPI.get_playlist_summary``): always fresh, with
                no ``/me`` read and no cache write.

        Returns:
            The metadata used to authorize access.

        Raises:
            PlaylistAccessError: If the playlist cannot be edited or accessed.
        """
        try:
            if lean:
                raw = self._api.get_playlist_summary(playlist_id)
            elif skip_cache:
                raw = self._api.get_playlist(playlist_id, skip_cache=True)
            else:
                raw = self._api.get_playlist(playlist_id)
        except (SpotifyNotFoundError, SpotifyForbiddenError) as e:
            raise PlaylistAccessError("Playlist not found or not accessible.") from e

        owner = raw.get("owner") if isinstance(raw, dict) else None
        owner_id = owner.get("id") if isinstance(owner, dict) else None
        collaborative = (
            bool(raw.get("collaborative")) if isinstance(raw, dict) else False
        )

        if owner_id == user_spotify_id or collaborative:
            return raw

        raise PlaylistAccessError("You do not have permission to modify this playlist.")

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
            playlists = self._api.get_user_playlists(skip_cache=skip_cache)
            logger.debug(f"Retrieved {len(playlists)} user playlists")
            return playlists
        except Exception as e:
            logger.error(f"Failed to get user playlists: {e}", exc_info=True)
            raise PlaylistError(f"Failed to fetch playlists: {e}")

    def get_playlist(self, playlist_id: str) -> Playlist:
        """
        Fetch a single playlist with its tracks.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            A Playlist model instance.

        Raises:
            PlaylistNotFoundError: If the playlist doesn't exist.
            PlaylistAccessError: If Spotify refuses with 403, as it does for
                the tracks of a playlist the user neither owns nor edits.
            SpotifyAuthError: If Spotify refuses the session itself (a token
                it rejects that cannot be refreshed). Left unwrapped so a
                caller can tell an ended session from a playlist problem.
            PlaylistError: If fetching fails for other reasons.
        """
        if not playlist_id:
            raise PlaylistNotFoundError("Playlist ID is required")

        try:
            playlist = Playlist.from_spotify(self._api, playlist_id)
            logger.debug(
                f"Retrieved playlist '{playlist.name}' with {len(playlist)} tracks"
            )
            return playlist
        except (ValueError, SpotifyNotFoundError):
            logger.error(f"Invalid playlist ID: {playlist_id}")
            raise PlaylistNotFoundError(f"Playlist not found: {playlist_id}")
        except SpotifyForbiddenError as e:
            raise PlaylistAccessError("Spotify refused this request for the playlist.") from e
        except SpotifyAuthError:
            raise
        except Exception as e:
            logger.error(f"Failed to get playlist {playlist_id}: {e}", exc_info=True)
            raise PlaylistError(f"Failed to fetch playlist: {e}")

    def get_playlist_summary(self, playlist_id: str) -> Playlist:
        """The playlist without its tracks, for when get_playlist is refused.

        Raises:
            PlaylistNotFoundError, PlaylistError: as get_playlist_metadata.
        """
        meta = self.get_playlist_metadata(playlist_id)
        return Playlist(
            id=meta["id"],
            name=meta["name"],
            owner_id=meta["owner_id"],
            description=meta.get("description"),
            total_tracks=meta.get("total_tracks"),
        )

    def get_playlist_metadata(
        self, playlist_id: str
    ) -> Dict[str, Any]:
        """
        Fetch playlist metadata only (no tracks).

        Tries the authenticated Spotify API first
        (GET /playlists/{playlist_id}), then falls back to
        scraping the public embed page if the API returns 404.
        Spotify restricts API access to non-owned playlists.

        Args:
            playlist_id: The Spotify playlist ID.

        Returns:
            Dict with keys: id, name, owner_id, description,
            total_tracks. May include ``scraped: True`` if the
            data came from the scraper fallback.

        Raises:
            PlaylistNotFoundError: If the playlist doesn't exist
                and scraping also fails.
            PlaylistError: If fetching fails for other reasons.
        """
        if not playlist_id:
            raise PlaylistNotFoundError("Playlist ID is required")

        try:
            data = self._api.get_playlist(playlist_id)
            return {
                "id": data["id"],
                "name": data["name"],
                "owner_id": data["owner"]["id"],
                "description": data.get("description"),
                "total_tracks": playlist_item_count(data, default=None),
            }
        except (ValueError, SpotifyNotFoundError):
            # API returned 404 — try scraper fallback
            logger.info(
                "API returned 404 for %s, trying "
                "scraper fallback",
                playlist_id,
            )
            scraped = scrape_playlist_metadata(playlist_id)
            if scraped:
                logger.info(
                    "Scraped metadata for %s: '%s'",
                    playlist_id,
                    scraped.get("name"),
                )
                return scraped
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
            success = self._api.update_playlist_tracks(playlist_id, track_uris)
            if success:
                logger.info(
                    f"Updated playlist {playlist_id} with {len(track_uris)} tracks"
                )
                return True
            else:
                raise PlaylistUpdateError("Spotify API returned failure")
        except (PlaylistUpdateError, SpotifyAPIError, SpotifyAuthError):
            # Preserve write certainty and confirmed batch counts for callers.
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
        playlist = self.get_playlist(playlist_id)
        return playlist.get_track_uris()
