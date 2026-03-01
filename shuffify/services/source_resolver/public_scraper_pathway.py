"""Public scraper pathway — extracts tracks from Spotify's public web pages."""

import json
import logging
import re
from typing import List, Optional

import requests

from .base import ResolveResult

logger = logging.getLogger(__name__)

# Regex patterns for extracting Spotify track identifiers
URI_PATTERN = re.compile(r'"(spotify:track:[a-zA-Z0-9]{22})"')
URL_PATTERN = re.compile(r'/track/([a-zA-Z0-9]{22})')

EMBED_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
PUBLIC_URL = "https://open.spotify.com/playlist/{playlist_id}"

REQUEST_TIMEOUT = 10
USER_AGENT = "Shuffify/1.0 (playlist-management)"

CACHE_PREFIX = "shuffify:cache:scrape:"
CACHE_TTL = 3600  # 1 hour


class PublicScraperPathway:
    """Pathway 3: Extract track URIs from Spotify's public web pages.

    Last-resort fallback. Attempts two strategies:
    1. Embed endpoint (lighter HTML, more structured)
    2. Public playlist page (heavier, but more complete)

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

        # Strategy 1: Embed endpoint
        uris = self._scrape_embed(playlist_id)
        if uris:
            self._set_cached(playlist_id, uris)
            return ResolveResult(
                track_uris=uris,
                pathway_name=self.name,
                success=True,
            )

        # Strategy 2: Public page
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

    def _scrape_embed(self, playlist_id: str) -> List[str]:
        """Extract URIs from the embed endpoint."""
        url = EMBED_URL.format(playlist_id=playlist_id)
        try:
            resp = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": USER_AGENT},
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
                headers={"User-Agent": USER_AGENT},
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


def _extract_uris(html: str) -> List[str]:
    """Extract unique track URIs from HTML content."""
    seen = set()
    uris = []

    # Pattern 1: spotify:track:ID in JSON/script blocks
    for match in URI_PATTERN.finditer(html):
        uri = match.group(1)
        if uri not in seen:
            seen.add(uri)
            uris.append(uri)

    # Pattern 2: /track/ID in URLs
    for match in URL_PATTERN.finditer(html):
        track_id = match.group(1)
        uri = f"spotify:track:{track_id}"
        if uri not in seen:
            seen.add(uri)
            uris.append(uri)

    return uris
