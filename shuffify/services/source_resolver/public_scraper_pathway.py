"""Public scraper pathway — extracts tracks from Spotify's public web pages.

Uses structured JSON extraction from embed/public pages rather than
naive regex matching. Parses __NEXT_DATA__ script tags and trackList
arrays that Spotify embeds in server-rendered HTML.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import requests

from .base import ResolveResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL templates
# ---------------------------------------------------------------------------
EMBED_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
PUBLIC_URL = "https://open.spotify.com/playlist/{playlist_id}"

# ---------------------------------------------------------------------------
# Request configuration
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT = 10

# Browser-like headers to avoid 403s from Spotify's bot detection.
REQUEST_HEADERS = {
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

# ---------------------------------------------------------------------------
# Cache configuration
# ---------------------------------------------------------------------------
CACHE_PREFIX = "shuffify:cache:scrape:"
CACHE_TTL = 3600  # 1 hour

# ---------------------------------------------------------------------------
# Extraction patterns (ordered by reliability)
# ---------------------------------------------------------------------------

# Pattern to locate the __NEXT_DATA__ script tag on Spotify pages.
# This contains a JSON blob with full playlist/track metadata.
NEXT_DATA_PATTERN = re.compile(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json"'
    r"[^>]*>(.*?)</script>",
    re.DOTALL,
)

# Pattern to locate any <script> tag containing a "trackList" key.
# The embed player stores a simplified track list in these blocks.
TRACK_LIST_SCRIPT_PATTERN = re.compile(
    r"<script[^>]*>(.*?)</script>",
    re.DOTALL,
)

# Fallback regex patterns for raw URI/URL extraction (last resort).
URI_PATTERN = re.compile(r'"(spotify:track:[a-zA-Z0-9]{22})"')
URL_PATTERN = re.compile(r"/track/([a-zA-Z0-9]{22})")


class PublicScraperPathway:
    """Pathway 3: Extract track URIs from Spotify's public web pages.

    Last-resort fallback when the authenticated API cannot access a
    playlist (e.g., Feb 2026 restrictions on foreign playlists).

    Extraction strategies (tried in order):
    1. Embed page → parse __NEXT_DATA__ JSON for tracks.items[]
    2. Embed page → parse trackList[] arrays from script blocks
    3. Public page → parse __NEXT_DATA__ JSON
    4. Any page → fallback regex extraction of track URIs/URLs

    Results are cached in Redis to avoid repeated scraping.
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client

    @property
    def name(self) -> str:
        return "public_scraper"

    def can_handle(self, source) -> bool:
        return source.source_type in ("own", "external")

    def resolve(self, source, api=None) -> ResolveResult:
        playlist_id = source.source_playlist_id
        if not playlist_id:
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
                error_message="No playlist ID on source",
            )

        # Check cache first
        cached = self._get_cached(playlist_id)
        if cached is not None:
            logger.debug(
                "Scraper cache hit for %s (%d tracks)",
                playlist_id,
                len(cached),
            )
            if cached:
                return ResolveResult(
                    track_uris=cached,
                    pathway_name=self.name,
                    success=True,
                )
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
            )

        # Strategy 1: Embed endpoint (lighter, more structured)
        uris = self._scrape_embed(playlist_id)
        if uris:
            self._set_cached(playlist_id, uris)
            return ResolveResult(
                track_uris=uris,
                pathway_name=self.name,
                success=True,
            )

        # Strategy 2: Public page (heavier, but may have more data)
        uris = self._scrape_public_page(playlist_id)
        if uris:
            self._set_cached(playlist_id, uris)
            return ResolveResult(
                track_uris=uris,
                pathway_name=self.name,
                success=True,
            )

        # Both strategies failed — cache the empty result too
        self._set_cached(playlist_id, [])
        return ResolveResult(
            track_uris=[],
            pathway_name=self.name,
            success=False,
            error_message="Scraping returned no tracks",
        )

    # ------------------------------------------------------------------
    # Scrape strategies
    # ------------------------------------------------------------------

    def _scrape_embed(self, playlist_id: str) -> List[str]:
        """Extract URIs from the embed endpoint."""
        url = EMBED_URL.format(playlist_id=playlist_id)
        try:
            resp = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers=REQUEST_HEADERS,
            )
            if resp.status_code != 200:
                logger.debug(
                    "Embed returned %d for %s",
                    resp.status_code,
                    playlist_id,
                )
                return []
            return _extract_uris(resp.text)
        except Exception as e:
            logger.warning(
                "Embed scrape failed for %s: %s",
                playlist_id,
                e,
            )
            return []

    def _scrape_public_page(self, playlist_id: str) -> List[str]:
        """Extract URIs from the public playlist page."""
        url = PUBLIC_URL.format(playlist_id=playlist_id)
        try:
            resp = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers=REQUEST_HEADERS,
            )
            if resp.status_code != 200:
                logger.debug(
                    "Public page returned %d for %s",
                    resp.status_code,
                    playlist_id,
                )
                return []
            return _extract_uris(resp.text)
        except Exception as e:
            logger.warning(
                "Public page scrape failed for %s: %s",
                playlist_id,
                e,
            )
            return []

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _get_cached(
        self, playlist_id: str
    ) -> Optional[List[str]]:
        """Get cached scrape results from Redis."""
        if self._redis is None:
            return None
        try:
            key = f"{CACHE_PREFIX}{playlist_id}"
            data = self._redis.get(key)
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.warning("Scraper cache read error: %s", e)
            return None

    def _set_cached(
        self, playlist_id: str, uris: List[str]
    ) -> None:
        """Cache scrape results in Redis."""
        if self._redis is None:
            return
        try:
            key = f"{CACHE_PREFIX}{playlist_id}"
            self._redis.setex(
                key, CACHE_TTL, json.dumps(uris)
            )
        except Exception as e:
            logger.warning("Scraper cache write error: %s", e)


# ======================================================================
# Extraction engine — multi-strategy URI extraction
# ======================================================================


def _extract_uris(html: str) -> List[str]:
    """Extract unique track URIs from HTML using multiple strategies.

    Tries structured JSON extraction first (most reliable), then
    falls back to regex pattern matching.

    Strategies (in order):
    1. __NEXT_DATA__ JSON → tracks.items[].track.uri
    2. Script blocks → trackList[].uri
    3. Regex fallback → spotify:track: patterns + /track/ URLs
    """
    # Strategy 1: __NEXT_DATA__ (full metadata)
    uris = _extract_from_next_data(html)
    if uris:
        return uris

    # Strategy 2: trackList in script blocks (embed format)
    uris = _extract_from_track_list(html)
    if uris:
        return uris

    # Strategy 3: Regex fallback (least reliable)
    return _extract_with_regex(html)


def _extract_from_next_data(html: str) -> List[str]:
    """Extract track URIs from __NEXT_DATA__ script tag.

    Modern Spotify pages embed a JSON blob in a script tag like:
        <script id="__NEXT_DATA__" type="application/json">
            {"props":{"pageProps":{"state":{"data":{"entity":
                {"trackList":[...], "tracks":{"items":[...]}}
            }}}}}
        </script>

    The exact nesting varies, so we recursively search for track
    data in known key patterns.
    """
    match = NEXT_DATA_PATTERN.search(html)
    if not match:
        return []

    try:
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        logger.debug("Failed to parse __NEXT_DATA__ JSON")
        return []

    return _walk_json_for_tracks(data)


def _extract_from_track_list(html: str) -> List[str]:
    """Extract track URIs from trackList arrays in script blocks.

    Spotify's embed player includes simplified track data in
    script tags as JSON objects containing a "trackList" key:
        {"trackList":[{"uri":"spotify:track:...","uid":"..."},...]}
    """
    seen = set()
    uris = []

    for script_match in TRACK_LIST_SCRIPT_PATTERN.finditer(html):
        content = script_match.group(1).strip()
        if "trackList" not in content:
            continue

        # Try to parse the entire script content as JSON
        parsed = _try_parse_json(content)
        if parsed is None:
            continue

        track_list = _find_key(parsed, "trackList")
        if not isinstance(track_list, list):
            continue

        for item in track_list:
            uri = _get_track_uri_from_item(item)
            if uri and uri not in seen:
                seen.add(uri)
                uris.append(uri)

    return uris


def _extract_with_regex(html: str) -> List[str]:
    """Fallback: extract track URIs using regex patterns.

    Looks for:
    1. "spotify:track:<22-char-id>" in JSON/script blocks
    2. /track/<22-char-id> in href attributes
    """
    seen = set()
    uris = []

    for match in URI_PATTERN.finditer(html):
        uri = match.group(1)
        if uri not in seen:
            seen.add(uri)
            uris.append(uri)

    for match in URL_PATTERN.finditer(html):
        track_id = match.group(1)
        uri = f"spotify:track:{track_id}"
        if uri not in seen:
            seen.add(uri)
            uris.append(uri)

    return uris


# ======================================================================
# JSON traversal helpers
# ======================================================================


def _walk_json_for_tracks(data: Any) -> List[str]:
    """Recursively walk a JSON structure to find track URIs.

    Searches for known Spotify data patterns:
    - tracks.items[].track.uri (playlist API format)
    - tracks.items[].uri (simplified format)
    - trackList[].uri (embed format)
    - entity.trackList[].uri (NEXT_DATA wrapper)
    """
    seen = set()
    uris: List[str] = []

    def _collect(uri: str) -> None:
        if uri and uri.startswith("spotify:track:") and uri not in seen:
            seen.add(uri)
            uris.append(uri)

    def _walk(node: Any, depth: int = 0) -> None:
        if depth > 20:  # Guard against pathological nesting
            return

        if isinstance(node, dict):
            # Check for tracks.items pattern (API-style)
            if "tracks" in node and isinstance(node["tracks"], dict):
                items = node["tracks"].get("items", [])
                if isinstance(items, list):
                    for item in items:
                        uri = _get_track_uri_from_item(item)
                        _collect(uri)
                    if uris:
                        return  # Found tracks, stop walking

            # Check for trackList pattern (embed-style)
            if "trackList" in node and isinstance(
                node["trackList"], list
            ):
                for item in node["trackList"]:
                    uri = _get_track_uri_from_item(item)
                    _collect(uri)
                if uris:
                    return

            # Check for items at current level
            if "items" in node and isinstance(node["items"], list):
                for item in node["items"]:
                    uri = _get_track_uri_from_item(item)
                    _collect(uri)
                if uris:
                    return

            # Recurse into dict values
            for value in node.values():
                _walk(value, depth + 1)
                if uris:
                    return

        elif isinstance(node, list):
            for item in node:
                _walk(item, depth + 1)
                if uris:
                    return

    _walk(data)
    return uris


def _get_track_uri_from_item(item: Any) -> Optional[str]:
    """Extract a spotify:track: URI from a track item dict.

    Handles multiple formats:
    - {"track": {"uri": "spotify:track:..."}}  (API format)
    - {"uri": "spotify:track:..."}              (flat format)
    - {"id": "abc123"}                          (ID-only format)
    """
    if not isinstance(item, dict):
        return None

    # Format 1: nested track object
    track = item.get("track")
    if isinstance(track, dict):
        uri = track.get("uri", "")
        if isinstance(uri, str) and uri.startswith("spotify:track:"):
            return uri

    # Format 2: flat URI
    uri = item.get("uri", "")
    if isinstance(uri, str) and uri.startswith("spotify:track:"):
        return uri

    # Format 3: ID only — construct URI
    track_id = item.get("id") or (
        track.get("id") if isinstance(track, dict) else None
    )
    if isinstance(track_id, str) and len(track_id) == 22:
        return f"spotify:track:{track_id}"

    return None


def _find_key(data: Any, key: str) -> Any:
    """Find the first occurrence of a key in a nested structure."""
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


def _try_parse_json(text: str) -> Optional[Dict]:
    """Attempt to parse text as JSON, returning None on failure."""
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        return None
    except (json.JSONDecodeError, ValueError):
        return None
