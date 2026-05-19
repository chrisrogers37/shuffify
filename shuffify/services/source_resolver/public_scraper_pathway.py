"""Public scraper pathway — extracts tracks from Spotify's public web pages.

Uses structured JSON extraction from embed/public pages rather than
naive regex matching. Parses __NEXT_DATA__ script tags and trackList
arrays that Spotify embeds in server-rendered HTML.
"""

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from .base import ResolveResult, find_nested_key

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
# Retry / backoff configuration
# ---------------------------------------------------------------------------
# Transient codes get retried with exponential backoff + jitter. The set is
# intentionally narrow — only codes Spotify might recover from within
# seconds. Permanent codes short-circuit immediately so the resolver can
# move on instead of burning attempts on a known-bad source.
TRANSIENT_STATUS_CODES = frozenset({429, 502, 503, 504})
PERMANENT_STATUS_CODES = frozenset({403, 404, 410})
MAX_ATTEMPTS = 3
BACKOFF_BASE = 1.0  # seconds — doubled each attempt
MAX_BACKOFF = 30  # seconds — ceiling for any single sleep

# ---------------------------------------------------------------------------
# Cache configuration
# ---------------------------------------------------------------------------
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


@dataclass
class ScrapeOutcome:
    """Result of a single scrape attempt against one Spotify URL.

    Distinguishes "we successfully fetched and parsed a page" (``confirmed``)
    from "the request failed before we could see the page" (``not confirmed``).
    The caller uses ``confirmed`` to gate cache writes: only confirmed outcomes
    are cacheable, since caching a transient failure would block subsequent
    retries for the full cache TTL.

    Attributes:
        uris: Track URIs extracted from the page. May be empty even when
            ``confirmed`` is True (genuinely empty playlist).
        confirmed: True iff the HTTP response was 200 and the body was
            parsed (regardless of whether tracks were found).
        error: Short, log-friendly description of the failure mode when
            ``confirmed`` is False. Populated for transient/permanent
            failures; None on confirmed scrapes.
    """

    uris: List[str]
    confirmed: bool
    error: Optional[str] = None


class PublicScraperPathway:
    """Pathway 3: Extract track URIs from Spotify's public web pages.

    Last-resort fallback when the authenticated API cannot access a
    playlist (e.g., Feb 2026 restrictions on foreign playlists).

    Extraction strategies (tried in order):
    1. Embed page → parse __NEXT_DATA__ JSON for tracks.items[]
    2. Embed page → parse trackList[] arrays from script blocks
    3. Public page → parse __NEXT_DATA__ JSON
    4. Any page → fallback regex extraction of track URIs/URLs

    Results are cached in the database to avoid repeated scraping.
    """

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
        embed = self._scrape_embed(playlist_id)
        if embed.uris:
            self._set_cached(playlist_id, embed.uris, "embed")
            return ResolveResult(
                track_uris=embed.uris,
                pathway_name=self.name,
                success=True,
            )

        # Strategy 2: Public page (heavier, but may have more data)
        public = self._scrape_public_page(playlist_id)
        if public.uris:
            self._set_cached(
                playlist_id, public.uris, "public_page"
            )
            return ResolveResult(
                track_uris=public.uris,
                pathway_name=self.name,
                success=True,
            )

        # Neither strategy returned tracks. Two cases:
        #
        # (a) At least one strategy confirmed the page (200 OK with parsed
        #     body but no extractable tracks) — the playlist genuinely has
        #     no tracks. Cache the empty result so we don't repeatedly
        #     hit Spotify for a known-empty playlist.
        #
        # (b) Both strategies were unconfirmed (403/429/timeout/network
        #     error) — the playlist's state is unknown. Do NOT cache; let
        #     the next call retry. This avoids the cache-poisoning bug
        #     where one transient failure blocks an hour of raids.
        if embed.confirmed or public.confirmed:
            self._set_cached(playlist_id, [], "none")
            return ResolveResult(
                track_uris=[],
                pathway_name=self.name,
                success=False,
                error_message="Scraping returned no tracks",
            )

        # Both unconfirmed — surface the failure without caching.
        failure_reason = embed.error or public.error or "scrape failed"
        return ResolveResult(
            track_uris=[],
            pathway_name=self.name,
            success=False,
            error_message=f"Scrape unconfirmed: {failure_reason}",
        )

    # ------------------------------------------------------------------
    # Scrape strategies
    # ------------------------------------------------------------------

    def _scrape_embed(self, playlist_id: str) -> ScrapeOutcome:
        """Extract URIs from the embed endpoint.

        Returns a ScrapeOutcome whose ``confirmed`` field signals whether
        the resolver can treat the result as authoritative for caching.
        """
        return self._do_scrape(
            EMBED_URL.format(playlist_id=playlist_id),
            playlist_id,
            label="Embed",
        )

    def _scrape_public_page(self, playlist_id: str) -> ScrapeOutcome:
        """Extract URIs from the public playlist page."""
        return self._do_scrape(
            PUBLIC_URL.format(playlist_id=playlist_id),
            playlist_id,
            label="Public page",
        )

    def _do_scrape(
        self, url: str, playlist_id: str, label: str
    ) -> ScrapeOutcome:
        """Fetch ``url`` and extract track URIs, classifying the outcome.

        Retries transient failures (429/5xx, network errors, timeouts) up
        to ``MAX_ATTEMPTS`` with exponential backoff + jitter, honoring
        ``Retry-After`` when present. Permanent failures (403/404/410)
        short-circuit on the first response so the resolver doesn't burn
        attempts on a known-bad source.

        ``confirmed`` is True only when an HTTP 200 was received and the
        body was parsed. All other cases return ``confirmed=False`` so the
        caller can skip the cache write.
        """
        last_error: Optional[str] = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = requests.get(
                    url,
                    timeout=REQUEST_TIMEOUT,
                    headers=REQUEST_HEADERS,
                )
            except (
                requests.Timeout,
                requests.ConnectionError,
            ) as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    "%s scrape network error for %s "
                    "(attempt %d/%d): %s",
                    label,
                    playlist_id,
                    attempt + 1,
                    MAX_ATTEMPTS,
                    e,
                )
                if attempt < MAX_ATTEMPTS - 1:
                    _sleep_with_backoff(attempt)
                    continue
                return ScrapeOutcome(
                    uris=[],
                    confirmed=False,
                    error=last_error,
                )
            except Exception as e:
                # Non-retryable client-side error (e.g. bad URL,
                # bad headers). Surface immediately.
                logger.warning(
                    "%s scrape failed for %s: %s",
                    label,
                    playlist_id,
                    e,
                )
                return ScrapeOutcome(
                    uris=[],
                    confirmed=False,
                    error=f"{type(e).__name__}: {e}",
                )

            status = resp.status_code

            if status == 200:
                return ScrapeOutcome(
                    uris=_extract_uris(resp.text),
                    confirmed=True,
                )

            if status in PERMANENT_STATUS_CODES:
                logger.warning(
                    "%s returned %d for %s (permanent, no retry)",
                    label,
                    status,
                    playlist_id,
                )
                return ScrapeOutcome(
                    uris=[],
                    confirmed=False,
                    error=f"HTTP {status}",
                )

            if status in TRANSIENT_STATUS_CODES:
                last_error = f"HTTP {status}"
                logger.warning(
                    "%s returned %d for %s "
                    "(transient, attempt %d/%d)",
                    label,
                    status,
                    playlist_id,
                    attempt + 1,
                    MAX_ATTEMPTS,
                )
                if attempt < MAX_ATTEMPTS - 1:
                    _sleep_with_backoff(
                        attempt,
                        retry_after=resp.headers.get("Retry-After"),
                    )
                    continue
                return ScrapeOutcome(
                    uris=[],
                    confirmed=False,
                    error=last_error,
                )

            # Any other non-200 (unexpected — e.g. 418, 500 without
            # a known transient mapping): log at DEBUG, do not retry.
            logger.debug(
                "%s returned %d for %s (unclassified, no retry)",
                label,
                status,
                playlist_id,
            )
            return ScrapeOutcome(
                uris=[],
                confirmed=False,
                error=f"HTTP {status}",
            )

        # Defensive: loop should always return via one of the branches
        # above. If we somehow exit normally, surface the last error.
        return ScrapeOutcome(
            uris=[],
            confirmed=False,
            error=last_error or "scrape exhausted attempts",
        )

    # ------------------------------------------------------------------
    # Cache helpers (database-backed)
    # ------------------------------------------------------------------

    @staticmethod
    def _get_cached(
        playlist_id: str,
    ) -> Optional[List[str]]:
        """Get cached scrape results from database."""
        try:
            from shuffify.models.db import (
                ScrapedPlaylistCache,
            )

            now = datetime.now(timezone.utc)
            row = (
                ScrapedPlaylistCache.query.filter(
                    ScrapedPlaylistCache.playlist_id
                    == playlist_id,
                    ScrapedPlaylistCache.expires_at > now,
                )
                .order_by(
                    ScrapedPlaylistCache.scraped_at.desc()
                )
                .first()
            )
            if row is None:
                return None
            return row.track_uris
        except Exception as e:
            logger.warning(
                "Scraper cache read error: %s", e
            )
            return None

    @staticmethod
    def _set_cached(
        playlist_id: str,
        uris: List[str],
        pathway: str = "unknown",
    ) -> None:
        """Cache scrape results in database."""
        try:
            from shuffify.models.db import (
                ScrapedPlaylistCache,
                db,
            )

            now = datetime.now(timezone.utc)
            expires = now + timedelta(seconds=CACHE_TTL)

            # Delete expired rows for this playlist
            ScrapedPlaylistCache.query.filter(
                ScrapedPlaylistCache.playlist_id
                == playlist_id,
                ScrapedPlaylistCache.expires_at <= now,
            ).delete()

            # Upsert: update existing or create new
            existing = ScrapedPlaylistCache.query.filter(
                ScrapedPlaylistCache.playlist_id
                == playlist_id,
            ).first()

            if existing:
                existing.track_uris = uris
                existing.scraped_at = now
                existing.scrape_pathway = pathway
                existing.expires_at = expires
            else:
                # NOTE: ScrapedPlaylistCache.track_count is no longer
                # written here — it was dead weight (no reader anywhere
                # in the codebase). The column is intentionally left
                # in the schema for now; a future cleanup PR can drop
                # it via Alembic migration once a few releases have
                # shipped without writers.
                row = ScrapedPlaylistCache(
                    playlist_id=playlist_id,
                    scraped_at=now,
                    scrape_pathway=pathway,
                    expires_at=expires,
                )
                row.track_uris = uris
                db.session.add(row)

            db.session.commit()
        except Exception as e:
            logger.warning(
                "Scraper cache write error: %s", e
            )
            # L2: roll back so the session stays usable for
            # subsequent operations within the same request /
            # job. Without this, SQLAlchemy may leave the
            # transaction in a failed state and every following
            # query in the same scope will raise
            # ``PendingRollbackError``.
            try:
                db.session.rollback()
            except Exception as rollback_err:
                logger.warning(
                    "Scraper cache rollback failed: %s",
                    rollback_err,
                )


# ======================================================================
# Retry helpers
# ======================================================================


def _sleep_with_backoff(
    attempt: int, retry_after: Optional[str] = None
) -> None:
    """Sleep before the next retry attempt.

    Honors a server-provided ``Retry-After`` value (seconds) when present
    and parseable; otherwise uses exponential backoff (``BACKOFF_BASE``
    doubled per attempt). Adds 0–0.5s of jitter so a burst of concurrent
    raids doesn't thunder against Spotify in lockstep, and caps any
    single sleep at ``MAX_BACKOFF`` to keep worst-case latency bounded.
    """
    base = BACKOFF_BASE * (2**attempt)
    if retry_after:
        try:
            base = float(retry_after)
        except (TypeError, ValueError):
            pass
    delay = min(base, MAX_BACKOFF) + random.uniform(0, 0.5)
    time.sleep(delay)


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

        track_list = find_nested_key(parsed, "trackList")
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


def _try_parse_json(text: str) -> Optional[Dict]:
    """Attempt to parse text as JSON, returning None on failure."""
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        return None
    except (json.JSONDecodeError, ValueError):
        return None
