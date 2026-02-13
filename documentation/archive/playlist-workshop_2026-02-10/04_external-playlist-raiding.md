# Phase 4: External Playlist Raiding

**PR Title:** `feat: Add external playlist loading via URL and search in Workshop`
**Risk Level:** Medium -- introduces a new utility module, extends SpotifyAPI and cache with search capabilities, and adds routes that accept untrusted user input (URLs). No new OAuth scopes required.
**Estimated Effort:** 3-4 days for a mid-level engineer, 5-6 days for a junior engineer.
**Files Created:**
- `shuffify/spotify/url_parser.py` -- Spotify URL/URI parser utility
- `tests/spotify/test_url_parser.py` -- Tests for URL parser
- `tests/spotify/test_api_search.py` -- Tests for search_playlists API method
- `tests/test_workshop_external.py` -- Tests for external playlist routes

**Files Modified:**
- `shuffify/spotify/api.py` -- Add `search_playlists()` method
- `shuffify/spotify/cache.py` -- Add search result caching methods
- `shuffify/spotify/client.py` -- Add `search_playlists()` facade method
- `shuffify/spotify/__init__.py` -- Export `parse_spotify_url`
- `shuffify/routes.py` -- Add 2 new routes (load-external-playlist, search-playlists)
- `shuffify/templates/workshop.html` -- Add "Load External Playlist" UI in source panel area
- `shuffify/schemas/requests.py` -- Add `ExternalPlaylistRequest` schema
- `shuffify/schemas/__init__.py` -- Export new schema
- `CHANGELOG.md` -- Add entry under `[Unreleased]`

**Files Deleted:** None

---

## Context

After Phase 3 establishes the source playlist panel (allowing users to browse their own playlists and cherry-pick tracks into the working playlist), Phase 4 extends that same source panel to support ANY public Spotify playlist -- even ones the user does not own.

Users currently have two ways to discover tracks for their working playlist:
1. Phase 2 provides track search (search for individual songs by name)
2. Phase 3 provides source playlist browsing (load one of YOUR playlists as a source)

Phase 4 fills the gap: browsing entire playlists that belong to other people or to Spotify's editorial team. Use cases include:
- Raiding Spotify editorial playlists (e.g., "Today's Top Hits") for tracks to add to a personal playlist
- Browsing a friend's public playlist and cherry-picking favorites
- Pasting a playlist URL from a social media post to explore its contents

The UX reuses Phase 3's source track panel entirely -- once an external playlist is loaded, its tracks appear in the same cherry-pick/drag interface.

---

## Dependencies

**Hard prerequisites (must be merged first):**
- **Phase 1 (Workshop Core)** -- provides the `/workshop/<playlist_id>` page, `workshopState`, `trackDataByUri`, `rerenderTrackList()`, `showNotification()`, SortableJS setup, and the `WorkshopCommitRequest` schema.
- **Phase 3 (Playlist Merging)** -- provides the source panel UI area in the workshop template, the `GET /workshop/user-playlists` route (or equivalent), the `loadSourcePlaylist()` JavaScript function, SortableJS cross-list drag configuration, and the deduplication logic. Phase 4 extends these; it does not duplicate them.

**What this unlocks:**
- Phase 5 (User Database) can persist "recently raided" external playlists as `UpstreamSource` records.

---

## Detailed Implementation Plan

### Step 1: Create `shuffify/spotify/url_parser.py`

**File:** `shuffify/spotify/url_parser.py` (NEW FILE)

This is a standalone utility module with no external dependencies beyond the standard library. It extracts a Spotify playlist ID from various URL and URI formats.

**Full file content:**

```python
"""
Spotify URL and URI parser utility.

Extracts resource IDs from various Spotify URL and URI formats.
Supports web URLs, app URIs, and bare IDs.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Spotify playlist ID format: 22 alphanumeric characters
SPOTIFY_ID_PATTERN = re.compile(r"^[a-zA-Z0-9]{22}$")

# Patterns for extracting playlist ID from various URL formats
_URL_PATTERNS = [
    # https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123
    # open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
    re.compile(
        r"(?:https?://)?open\.spotify\.com/playlist/([a-zA-Z0-9]{22})(?:\?.*)?$"
    ),
    # spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
    re.compile(r"^spotify:playlist:([a-zA-Z0-9]{22})$"),
]


def parse_spotify_playlist_url(input_string: str) -> Optional[str]:
    """
    Extract a Spotify playlist ID from a URL, URI, or bare ID.

    Supports these formats:
        - https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
        - https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123
        - open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
        - spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
        - 37i9dQZF1DXcBWIGoYBM5M  (bare ID)

    Args:
        input_string: The URL, URI, or ID to parse.

    Returns:
        The 22-character playlist ID, or None if the input
        does not match any known format.
    """
    if not input_string or not isinstance(input_string, str):
        return None

    cleaned = input_string.strip()
    if not cleaned:
        return None

    # Check if it is already a bare playlist ID
    if SPOTIFY_ID_PATTERN.match(cleaned):
        logger.debug(f"Parsed bare playlist ID: {cleaned}")
        return cleaned

    # Try each URL/URI pattern
    for pattern in _URL_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            playlist_id = match.group(1)
            logger.debug(f"Parsed playlist ID from URL/URI: {playlist_id}")
            return playlist_id

    logger.debug(f"Could not parse playlist ID from: {cleaned!r}")
    return None
```

**Key design decisions:**
- Returns `None` instead of raising an exception for invalid input. The caller (the route) decides how to surface the error to the user.
- Strips whitespace so that copy-paste from a browser address bar always works.
- Accepts bare 22-character alphanumeric IDs directly. This covers the case where a user pastes only the ID.
- Does NOT attempt to validate that the ID actually exists on Spotify. That is the responsibility of the route that calls `get_playlist()`.

### Step 2: Add `search_playlists()` to `SpotifyAPI`

**File:** `shuffify/spotify/api.py`

**Where:** After the `get_playlist_tracks()` method (after line 405), add a new method in the "Playlist Operations" section. Insert it before the `update_playlist_tracks()` method.

**Code to add after line 405 (after the closing of `get_playlist_tracks`):**

```python
    @api_error_handler
    def search_playlists(
        self, query: str, limit: int = 10, skip_cache: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Search for playlists by name.

        Args:
            query: Search query string.
            limit: Maximum number of results (1-50, default 10).
            skip_cache: If True, bypass cache and fetch fresh data.

        Returns:
            List of playlist summary dictionaries with keys:
            id, name, owner_display_name, image_url, total_tracks.

        Raises:
            SpotifyAPIError: If the request fails.
        """
        self._ensure_valid_token()

        # Clamp limit to Spotify's allowed range
        limit = max(1, min(limit, 50))

        # Check cache first
        if self._cache and not skip_cache:
            cached = self._cache.get_search_playlists(query, limit)
            if cached is not None:
                return cached

        results = self._sp.search(q=query, type="playlist", limit=limit)

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
                        "image_url": (
                            item["images"][0]["url"]
                            if item.get("images")
                            else None
                        ),
                        "total_tracks": item.get("tracks", {}).get("total", 0),
                    }
                )

        logger.debug(
            f"Playlist search for '{query}' returned {len(playlists)} results"
        )

        # Cache the results
        if self._cache and playlists:
            self._cache.set_search_playlists(query, limit, playlists)

        return playlists
```

**No import changes needed** -- all required types (`List`, `Dict`, `Any`) are already imported on line 14.

### Step 3: Add Search Caching to `SpotifyCache`

**File:** `shuffify/spotify/cache.py`

**Where:** After the "Audio Features" section (after line 349) and before the "Cache Management" section (before line 351), add a new section for search results.

**Code to add between the Audio Features and Cache Management sections:**

```python
    # =========================================================================
    # Search Results
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
            key = self._make_key("search_playlists", f"{query.lower()}:{limit}")
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
            key = self._make_key("search_playlists", f"{query.lower()}:{limit}")
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
```

**Cache TTL rationale:** Search results use `default_ttl` (300 seconds / 5 minutes). Playlist search results change less frequently than a specific user's playlist list, but should still refresh reasonably often. This matches the existing `default_ttl` pattern without requiring a new config parameter.

**Cache key design:** The key includes both the lowercased query and the limit. This means `search_playlists("jazz", 10)` and `search_playlists("jazz", 20)` are cached separately, which is correct because they return different result sets.

### Step 4: Add `search_playlists()` to `SpotifyClient` Facade

**File:** `shuffify/spotify/client.py`

**Where:** After the `get_playlist_tracks()` method (after line 263) and before the `update_playlist_tracks()` method (before line 265). This keeps the method in the Playlist Methods section.

**Code to add:**

```python
    def search_playlists(
        self, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search for playlists by name.

        Args:
            query: Search query string.
            limit: Maximum number of results (1-50).

        Returns:
            List of playlist summary dictionaries.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_authenticated()
        return self._api.search_playlists(query, limit=limit)
```

### Step 5: Export `parse_spotify_playlist_url` from Spotify Module

**File:** `shuffify/spotify/__init__.py`

**Where:** After line 62 (`from .api import SpotifyAPI`), add a new import line:

**Current (line 62):**
```python
from .api import SpotifyAPI
```

**Change to (lines 62-65):**
```python
from .api import SpotifyAPI

# URL parser utility
from .url_parser import parse_spotify_playlist_url
```

**Also update `__all__` list.** Add `"parse_spotify_playlist_url",` after `"SpotifyAPI",` (after line 88 in the current `__all__`). The current `__all__` has:

```python
    # API
    "SpotifyAPI",
```

Change to:

```python
    # API
    "SpotifyAPI",
    # URL Parser
    "parse_spotify_playlist_url",
```

### Step 6: Add `ExternalPlaylistRequest` Pydantic Schema

**File:** `shuffify/schemas/requests.py`

**Where:** After the `PlaylistQueryParams` class (after line 153), add the new schema.

**Import change at line 7.** Current:
```python
from typing import Literal, Annotated, Any, Dict
```

Change to:
```python
from typing import Literal, Annotated, Any, Dict, List, Optional
```

**Code to add after line 153 (after the `parse_bool` method in `PlaylistQueryParams`):**

```python


class ExternalPlaylistRequest(BaseModel):
    """Schema for loading an external playlist by URL or search query."""

    url: Optional[str] = Field(
        default=None,
        description="Spotify playlist URL, URI, or ID",
    )
    query: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Search query for finding playlists by name",
    )

    @field_validator("url")
    @classmethod
    def validate_url_not_empty(cls, v: Optional[str]) -> Optional[str]:
        """Strip whitespace from URL if provided."""
        if v is not None:
            v = v.strip()
            if not v:
                return None
        return v

    @field_validator("query")
    @classmethod
    def validate_query_not_empty(cls, v: Optional[str]) -> Optional[str]:
        """Strip whitespace from query if provided."""
        if v is not None:
            v = v.strip()
            if not v:
                return None
        return v

    def model_post_init(self, __context) -> None:
        """Ensure at least one of url or query is provided."""
        if not self.url and not self.query:
            raise ValueError(
                "Either 'url' or 'query' must be provided"
            )
```

### Step 7: Export New Schema

**File:** `shuffify/schemas/__init__.py`

**Where:** In the import block (lines 9-18), add `ExternalPlaylistRequest`. Current:

```python
from .requests import (
    ShuffleRequest,
    ShuffleRequestBase,
    BasicShuffleParams,
    BalancedShuffleParams,
    StratifiedShuffleParams,
    PercentageShuffleParams,
    PlaylistQueryParams,
    parse_shuffle_request,
)
```

Change to:

```python
from .requests import (
    ShuffleRequest,
    ShuffleRequestBase,
    BasicShuffleParams,
    BalancedShuffleParams,
    StratifiedShuffleParams,
    PercentageShuffleParams,
    PlaylistQueryParams,
    ExternalPlaylistRequest,
    parse_shuffle_request,
)
```

**Also add to `__all__` list (line 20-33).** Add `"ExternalPlaylistRequest",` after `"PlaylistQueryParams",`.

### Step 8: Add Workshop Routes for External Playlists

**File:** `shuffify/routes.py`

**8a. Update imports (line 31).** Current:

```python
from shuffify.schemas import parse_shuffle_request, PlaylistQueryParams
```

This line will have been changed by Phase 1 to include `WorkshopCommitRequest`. After Phase 4, it becomes:

```python
from shuffify.schemas import (
    parse_shuffle_request,
    PlaylistQueryParams,
    WorkshopCommitRequest,
    ExternalPlaylistRequest,
)
```

**8b. Add new import for the URL parser.** After the schemas import, add:

```python
from shuffify.spotify.url_parser import parse_spotify_playlist_url
```

**8c. Add two new routes at the end of the Workshop Routes section** (after the `workshop_commit` route added by Phase 1). Add a new subsection comment and two routes:

```python
# =============================================================================
# Workshop: External Playlist Routes
# =============================================================================


@main.route("/workshop/search-playlists", methods=["POST"])
def workshop_search_playlists():
    """
    Search for public playlists by name.

    Expects JSON body: { "query": "jazz vibes" }
    Returns JSON: { "success": true, "playlists": [...] }
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    query = data.get("query", "").strip()
    if not query:
        return json_error("Search query is required.", 400)

    if len(query) > 200:
        return json_error("Search query too long (max 200 characters).", 400)

    try:
        playlist_service = PlaylistService(client)
        results = client.search_playlists(query, limit=10)

        logger.info(
            f"Playlist search for '{query}' returned {len(results)} results"
        )

        return jsonify({
            "success": True,
            "playlists": results,
        })

    except Exception as e:
        logger.error(f"Playlist search failed: {e}", exc_info=True)
        return json_error("Search failed. Please try again.", 500)


@main.route("/workshop/load-external-playlist", methods=["POST"])
def workshop_load_external_playlist():
    """
    Load tracks from an external playlist by URL/URI/ID or search query.

    Expects JSON body:
        { "url": "https://open.spotify.com/playlist/..." }
        or
        { "query": "jazz vibes" }

    When a URL is provided, extracts the playlist ID and returns tracks.
    When a query is provided, searches for playlists and returns the list
    (client then calls this endpoint again with the chosen playlist's URL/ID).

    Returns JSON:
        For URL: { "success": true, "mode": "tracks", "playlist": {...}, "tracks": [...] }
        For query: { "success": true, "mode": "search", "playlists": [...] }
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Validate with Pydantic
    try:
        ext_request = ExternalPlaylistRequest(**data)
    except Exception as e:
        return json_error(str(e), 400)

    # --- URL mode: load a specific playlist ---
    if ext_request.url:
        playlist_id = parse_spotify_playlist_url(ext_request.url)
        if not playlist_id:
            return json_error(
                "Could not parse a playlist ID from the provided URL. "
                "Please use a Spotify playlist URL, URI, or ID.",
                400,
            )

        try:
            playlist_service = PlaylistService(client)
            playlist = playlist_service.get_playlist(
                playlist_id, include_features=False
            )

            # Store in session history for "recently loaded" feature
            if "external_playlist_history" not in session:
                session["external_playlist_history"] = []

            history = session["external_playlist_history"]
            # Add to front, remove duplicates, keep max 10
            entry = {
                "id": playlist.id,
                "name": playlist.name,
                "owner_id": playlist.owner_id,
                "track_count": len(playlist),
            }
            history = [h for h in history if h["id"] != playlist.id]
            history.insert(0, entry)
            session["external_playlist_history"] = history[:10]
            session.modified = True

            logger.info(
                f"Loaded external playlist '{playlist.name}' "
                f"({len(playlist)} tracks)"
            )

            return jsonify({
                "success": True,
                "mode": "tracks",
                "playlist": {
                    "id": playlist.id,
                    "name": playlist.name,
                    "owner_id": playlist.owner_id,
                    "description": playlist.description,
                    "track_count": len(playlist),
                },
                "tracks": playlist.tracks,
            })

        except PlaylistError as e:
            logger.error(f"Failed to load external playlist: {e}")
            return json_error(
                "Could not load playlist. It may be private or deleted.",
                404,
            )

    # --- Query mode: search for playlists ---
    if ext_request.query:
        try:
            results = client.search_playlists(ext_request.query, limit=10)

            logger.info(
                f"External playlist search for '{ext_request.query}' "
                f"returned {len(results)} results"
            )

            return jsonify({
                "success": True,
                "mode": "search",
                "playlists": results,
            })

        except Exception as e:
            logger.error(f"Playlist search failed: {e}", exc_info=True)
            return json_error("Search failed. Please try again.", 500)

    return json_error("Either 'url' or 'query' must be provided.", 400)
```

**Design notes on the route:**
- A single endpoint (`/workshop/load-external-playlist`) handles both URL loading and search. The `mode` field in the response tells the client what kind of result it received.
- When `url` is provided, we parse it, load the full playlist with tracks, and return them immediately. The client renders them in the source panel.
- When `query` is provided, we return a list of matching playlists (summary only, no tracks). The client displays them as selectable options. When the user clicks one, the client calls the same endpoint again with `url` set to that playlist's ID.
- Session history is stored in `session["external_playlist_history"]` as a list of up to 10 recent entries. This is lightweight (each entry ~100 bytes) and does not bloat Redis.
- The `PlaylistService.get_playlist()` call works for ANY public playlist, not just the user's own. This is because `SpotifyAPI.get_playlist()` calls `self._sp.playlist(playlist_id)`, which fetches any playlist by ID.

### Step 9: Update Workshop Template

**File:** `shuffify/templates/workshop.html`

This step extends the source panel area that Phase 3 established. Phase 3 adds a source panel below the main track list (or in the sidebar). Phase 4 adds an "External Playlist" input above or within that source panel.

**9a. Add "Load External Playlist" UI.** Insert this HTML block into the sidebar area (the `lg:col-span-1 space-y-6` div), after the "Shuffle Controls Panel" and before the "Playlist Info Panel". This places it between the shuffle controls and the info panel.

**Code to add:**

```html
                <!-- External Playlist Panel -->
                <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-5">
                    <h2 class="text-white font-bold text-lg mb-3">Load External Playlist</h2>
                    <p class="text-white/60 text-sm mb-3">Paste a Spotify playlist URL or search by name.</p>

                    <!-- URL/Search Input -->
                    <div class="flex space-x-2 mb-3">
                        <input id="external-playlist-input"
                               type="text"
                               placeholder="Paste URL or search..."
                               class="flex-1 px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white text-sm placeholder-white/40 focus:ring-2 focus:ring-white/30 focus:border-transparent"
                               onkeydown="if(event.key==='Enter') loadExternalPlaylist()">
                        <button onclick="loadExternalPlaylist()"
                                class="px-3 py-2 rounded-lg bg-white/20 hover:bg-white/30 text-white text-sm font-semibold transition duration-150 flex-shrink-0">
                            Load
                        </button>
                    </div>

                    <!-- Search Results (hidden by default) -->
                    <div id="external-search-results" class="hidden max-h-48 overflow-y-auto workshop-scrollbar space-y-1">
                        <!-- Populated dynamically -->
                    </div>

                    <!-- Recently Loaded (from session) -->
                    {% if session.get('external_playlist_history') %}
                    <div class="mt-3">
                        <p class="text-white/50 text-xs uppercase tracking-wide font-semibold mb-2">Recently Loaded</p>
                        <div id="external-recent-list" class="space-y-1 max-h-32 overflow-y-auto workshop-scrollbar">
                            {% for entry in session.get('external_playlist_history', [])[:5] %}
                            <button onclick="loadExternalById('{{ entry.id }}')"
                                    class="w-full text-left px-2 py-1.5 rounded-lg hover:bg-white/10 transition duration-150 text-sm text-white/80 truncate">
                                {{ entry.name }} <span class="text-white/40">({{ entry.track_count }} tracks)</span>
                            </button>
                            {% endfor %}
                        </div>
                    </div>
                    {% endif %}

                    <!-- Loading indicator -->
                    <div id="external-loading" class="hidden py-3 text-center">
                        <svg class="w-6 h-6 mx-auto animate-spin text-white/60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                        </svg>
                        <p class="text-white/50 text-xs mt-1">Loading...</p>
                    </div>
                </div>
```

**9b. Add JavaScript for external playlist loading.** Add this JavaScript block inside the existing `<script>` tag, after the "Notifications" section and before the closing `</script>` tag.

```javascript
// =============================================================================
// External Playlist Loading (Phase 4)
// =============================================================================

function loadExternalPlaylist() {
    const input = document.getElementById('external-playlist-input');
    const value = input.value.trim();
    if (!value) return;

    const loading = document.getElementById('external-loading');
    const searchResults = document.getElementById('external-search-results');
    loading.classList.remove('hidden');
    searchResults.classList.add('hidden');

    // Determine if this looks like a URL/URI or a search query
    const looksLikeUrl = value.includes('spotify.com/playlist/')
        || value.startsWith('spotify:playlist:')
        || /^[a-zA-Z0-9]{22}$/.test(value);

    const body = looksLikeUrl
        ? { url: value }
        : { query: value };

    fetch('/workshop/load-external-playlist', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify(body),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.message || 'Failed to load playlist.');
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success && data.mode === 'tracks') {
            // Loaded a specific playlist — display tracks in source panel
            displayExternalTracks(data.playlist, data.tracks);
            showNotification(
                `Loaded "${data.playlist.name}" (${data.tracks.length} tracks)`,
                'success'
            );
            input.value = '';
        } else if (data.success && data.mode === 'search') {
            // Got search results — display them for selection
            displaySearchResults(data.playlists);
        } else {
            showNotification(data.message || 'Unexpected response.', 'error');
        }
    })
    .catch(error => {
        console.error('External playlist load error:', error);
        showNotification(error.message || 'Failed to load playlist.', 'error');
    })
    .finally(() => {
        loading.classList.add('hidden');
    });
}

function loadExternalById(playlistId) {
    const input = document.getElementById('external-playlist-input');
    input.value = playlistId;
    loadExternalPlaylist();
}

function displaySearchResults(playlists) {
    const container = document.getElementById('external-search-results');
    container.innerHTML = '';

    if (!playlists || playlists.length === 0) {
        container.innerHTML = '<p class="text-white/50 text-sm py-2">No playlists found.</p>';
        container.classList.remove('hidden');
        return;
    }

    playlists.forEach(pl => {
        const btn = document.createElement('button');
        btn.className = 'w-full flex items-center px-2 py-2 rounded-lg hover:bg-white/10 transition duration-150 text-left';
        btn.onclick = () => {
            loadExternalById(pl.id);
            container.classList.add('hidden');
        };

        const imgHtml = pl.image_url
            ? `<img src="${pl.image_url}" alt="" class="w-8 h-8 rounded flex-shrink-0 object-cover">`
            : `<div class="w-8 h-8 rounded flex-shrink-0 bg-white/10 flex items-center justify-center text-white/30">
                   <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55C7.79 13 6 14.79 6 17s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
               </div>`;

        btn.innerHTML = `
            ${imgHtml}
            <div class="ml-2 min-w-0 flex-1">
                <p class="text-white text-sm font-medium truncate">${escapeHtml(pl.name)}</p>
                <p class="text-white/50 text-xs truncate">${escapeHtml(pl.owner_display_name)} &middot; ${pl.total_tracks} tracks</p>
            </div>
        `;
        container.appendChild(btn);
    });

    container.classList.remove('hidden');
}

function displayExternalTracks(playlistInfo, tracks) {
    // This function delegates to Phase 3's source panel rendering.
    // It calls the same loadSourceTracks() function that Phase 3 uses
    // for user-owned playlists, passing the external playlist data.
    //
    // Phase 3 must expose a function like:
    //   loadSourceTracks(playlistInfo, tracks)
    // that populates the source track list in the source panel.
    if (typeof loadSourceTracks === 'function') {
        loadSourceTracks(playlistInfo, tracks);
    } else {
        // Fallback: log a warning if Phase 3's function is not available
        console.warn('loadSourceTracks() not available. Phase 3 source panel may not be loaded.');
        showNotification(
            `Loaded "${playlistInfo.name}" but source panel is unavailable.`,
            'info'
        );
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
```

**Integration with Phase 3:** The key integration point is the `displayExternalTracks()` function. It calls `loadSourceTracks()` which Phase 3 defines to populate the source panel. This means Phase 4's JavaScript does NOT need to know the internal structure of the source panel -- it just passes data to Phase 3's existing function. If Phase 3's function name or signature differs, this single call site is the only thing that needs to change.

### Step 10: Create Test File for URL Parser

**File:** `tests/spotify/test_url_parser.py` (NEW FILE)

```python
"""
Tests for the Spotify URL parser utility.

Covers all supported URL formats, edge cases, and invalid inputs.
"""

import pytest

from shuffify.spotify.url_parser import parse_spotify_playlist_url


class TestParseSpotifyPlaylistUrl:
    """Tests for parse_spotify_playlist_url()."""

    # =========================================================================
    # Valid URL formats
    # =========================================================================

    def test_full_https_url(self):
        """Standard HTTPS URL should extract playlist ID."""
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_https_url_with_query_params(self):
        """URL with query parameters should extract playlist ID."""
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc123"
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_url_without_protocol(self):
        """URL without https:// should still work."""
        url = "open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_http_url(self):
        """HTTP (non-HTTPS) URL should also work."""
        url = "http://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_spotify_uri(self):
        """Spotify URI format should extract playlist ID."""
        uri = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(uri) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_bare_playlist_id(self):
        """A bare 22-character ID should be returned as-is."""
        bare_id = "37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(bare_id) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_url_with_leading_trailing_whitespace(self):
        """Whitespace around a URL should be stripped."""
        url = "  https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M  "
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_uri_with_whitespace(self):
        """Whitespace around a URI should be stripped."""
        uri = "  spotify:playlist:37i9dQZF1DXcBWIGoYBM5M  "
        assert parse_spotify_playlist_url(uri) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_url_with_multiple_query_params(self):
        """URL with multiple query params should extract ID."""
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc&dl=true"
        assert parse_spotify_playlist_url(url) == "37i9dQZF1DXcBWIGoYBM5M"

    # =========================================================================
    # Invalid inputs
    # =========================================================================

    def test_none_returns_none(self):
        """None input should return None."""
        assert parse_spotify_playlist_url(None) is None

    def test_empty_string_returns_none(self):
        """Empty string should return None."""
        assert parse_spotify_playlist_url("") is None

    def test_whitespace_only_returns_none(self):
        """Whitespace-only string should return None."""
        assert parse_spotify_playlist_url("   ") is None

    def test_random_string_returns_none(self):
        """Random text should return None."""
        assert parse_spotify_playlist_url("not a spotify url") is None

    def test_track_url_returns_none(self):
        """A Spotify track URL should return None (not a playlist)."""
        url = "https://open.spotify.com/track/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) is None

    def test_album_url_returns_none(self):
        """A Spotify album URL should return None (not a playlist)."""
        url = "https://open.spotify.com/album/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) is None

    def test_artist_url_returns_none(self):
        """A Spotify artist URL should return None (not a playlist)."""
        url = "https://open.spotify.com/artist/37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(url) is None

    def test_track_uri_returns_none(self):
        """A Spotify track URI should return None."""
        uri = "spotify:track:37i9dQZF1DXcBWIGoYBM5M"
        assert parse_spotify_playlist_url(uri) is None

    def test_short_id_returns_none(self):
        """An ID shorter than 22 characters should return None."""
        assert parse_spotify_playlist_url("abc123") is None

    def test_long_id_returns_none(self):
        """An ID longer than 22 characters should return None."""
        assert parse_spotify_playlist_url("a" * 23) is None

    def test_id_with_special_chars_returns_none(self):
        """An ID with special characters should return None."""
        assert parse_spotify_playlist_url("37i9dQZF1DXcBWIGoYBM_!") is None

    def test_integer_returns_none(self):
        """Non-string input should return None."""
        assert parse_spotify_playlist_url(12345) is None

    def test_boolean_returns_none(self):
        """Boolean input should return None."""
        assert parse_spotify_playlist_url(True) is None

    # =========================================================================
    # Different valid playlist IDs
    # =========================================================================

    def test_another_valid_id_from_url(self):
        """Verify with a different playlist ID format."""
        url = "https://open.spotify.com/playlist/5ABHKGoOzxkaa28ttQV9sE"
        assert parse_spotify_playlist_url(url) == "5ABHKGoOzxkaa28ttQV9sE"

    def test_numeric_heavy_id(self):
        """Verify IDs with mostly digits work."""
        bare_id = "1234567890abcDEFGH1234"
        assert parse_spotify_playlist_url(bare_id) == "1234567890abcDEFGH1234"
```

### Step 11: Create Test File for `search_playlists` API Method

**File:** `tests/spotify/test_api_search.py` (NEW FILE)

```python
"""
Tests for SpotifyAPI.search_playlists().

Covers successful searches, empty results, caching, and error handling.
"""

import pytest
import time
from unittest.mock import Mock, patch

import spotipy

from shuffify.spotify.api import SpotifyAPI
from shuffify.spotify.auth import SpotifyAuthManager, TokenInfo
from shuffify.spotify.cache import SpotifyCache
from shuffify.spotify.credentials import SpotifyCredentials
from shuffify.spotify.exceptions import SpotifyAPIError


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def credentials():
    """Valid SpotifyCredentials."""
    return SpotifyCredentials(
        client_id="test_client_id",
        client_secret="test_client_secret",
        redirect_uri="http://localhost:5000/callback",
    )


@pytest.fixture
def auth_manager(credentials):
    """SpotifyAuthManager instance."""
    return SpotifyAuthManager(credentials)


@pytest.fixture
def valid_token():
    """Valid TokenInfo."""
    return TokenInfo(
        access_token="test_access_token",
        token_type="Bearer",
        expires_at=time.time() + 3600,
        refresh_token="test_refresh_token",
    )


@pytest.fixture
def mock_sp():
    """Mock spotipy.Spotify instance."""
    return Mock(spec=spotipy.Spotify)


@pytest.fixture
def api(valid_token, auth_manager, mock_sp):
    """SpotifyAPI with mocked spotipy client."""
    with patch("shuffify.spotify.api.spotipy.Spotify", return_value=mock_sp):
        return SpotifyAPI(valid_token, auth_manager)


@pytest.fixture
def sample_search_response():
    """Sample Spotify search API response for playlists."""
    return {
        "playlists": {
            "items": [
                {
                    "id": "playlist_abc",
                    "name": "Jazz Vibes",
                    "owner": {"display_name": "Spotify"},
                    "images": [{"url": "https://example.com/jazz.jpg"}],
                    "tracks": {"total": 50},
                },
                {
                    "id": "playlist_def",
                    "name": "Smooth Jazz",
                    "owner": {"display_name": "JazzFan42"},
                    "images": [],
                    "tracks": {"total": 30},
                },
                {
                    "id": "playlist_ghi",
                    "name": "Jazz Classics",
                    "owner": {},
                    "images": None,
                    "tracks": {},
                },
            ]
        }
    }


# =============================================================================
# Tests
# =============================================================================


class TestSearchPlaylists:
    """Tests for SpotifyAPI.search_playlists()."""

    def test_search_returns_formatted_results(
        self, api, mock_sp, sample_search_response
    ):
        """Search should return formatted playlist summaries."""
        mock_sp.search.return_value = sample_search_response

        results = api.search_playlists("jazz")

        assert len(results) == 3
        assert results[0]["id"] == "playlist_abc"
        assert results[0]["name"] == "Jazz Vibes"
        assert results[0]["owner_display_name"] == "Spotify"
        assert results[0]["image_url"] == "https://example.com/jazz.jpg"
        assert results[0]["total_tracks"] == 50

    def test_search_handles_missing_images(
        self, api, mock_sp, sample_search_response
    ):
        """Playlists without images should have None for image_url."""
        mock_sp.search.return_value = sample_search_response

        results = api.search_playlists("jazz")

        assert results[1]["image_url"] is None

    def test_search_handles_missing_owner(
        self, api, mock_sp, sample_search_response
    ):
        """Playlists with missing owner should show 'Unknown'."""
        mock_sp.search.return_value = sample_search_response

        results = api.search_playlists("jazz")

        assert results[2]["owner_display_name"] == "Unknown"

    def test_search_handles_missing_track_total(
        self, api, mock_sp, sample_search_response
    ):
        """Playlists with missing track total should default to 0."""
        mock_sp.search.return_value = sample_search_response

        results = api.search_playlists("jazz")

        assert results[2]["total_tracks"] == 0

    def test_search_empty_results(self, api, mock_sp):
        """Empty search results should return empty list."""
        mock_sp.search.return_value = {
            "playlists": {"items": []}
        }

        results = api.search_playlists("xyznonexistent")

        assert results == []
        mock_sp.search.assert_called_once_with(
            q="xyznonexistent", type="playlist", limit=10
        )

    def test_search_skips_none_items(self, api, mock_sp):
        """None items in search results should be skipped."""
        mock_sp.search.return_value = {
            "playlists": {
                "items": [
                    None,
                    {
                        "id": "valid",
                        "name": "Valid Playlist",
                        "owner": {"display_name": "User"},
                        "images": [],
                        "tracks": {"total": 5},
                    },
                    None,
                ]
            }
        }

        results = api.search_playlists("test")

        assert len(results) == 1
        assert results[0]["id"] == "valid"

    def test_search_respects_limit(self, api, mock_sp):
        """Limit parameter should be passed to Spotify API."""
        mock_sp.search.return_value = {"playlists": {"items": []}}

        api.search_playlists("test", limit=5)

        mock_sp.search.assert_called_once_with(
            q="test", type="playlist", limit=5
        )

    def test_search_clamps_limit_min(self, api, mock_sp):
        """Limit below 1 should be clamped to 1."""
        mock_sp.search.return_value = {"playlists": {"items": []}}

        api.search_playlists("test", limit=0)

        mock_sp.search.assert_called_once_with(
            q="test", type="playlist", limit=1
        )

    def test_search_clamps_limit_max(self, api, mock_sp):
        """Limit above 50 should be clamped to 50."""
        mock_sp.search.return_value = {"playlists": {"items": []}}

        api.search_playlists("test", limit=100)

        mock_sp.search.assert_called_once_with(
            q="test", type="playlist", limit=50
        )

    def test_search_calls_spotify_api(self, api, mock_sp):
        """Search should call sp.search with correct parameters."""
        mock_sp.search.return_value = {"playlists": {"items": []}}

        api.search_playlists("my query", limit=10)

        mock_sp.search.assert_called_once_with(
            q="my query", type="playlist", limit=10
        )


class TestSearchPlaylistsWithCache:
    """Tests for search_playlists caching behavior."""

    def test_search_returns_cached_results(self, valid_token, auth_manager, mock_sp):
        """Cached results should be returned without calling Spotify API."""
        import redis as redis_lib

        mock_redis = Mock(spec=redis_lib.Redis)
        cache = SpotifyCache(mock_redis)

        cached_data = [{"id": "cached", "name": "Cached Playlist"}]
        mock_redis.get.return_value = (
            b'[{"id": "cached", "name": "Cached Playlist"}]'
        )

        with patch("shuffify.spotify.api.spotipy.Spotify", return_value=mock_sp):
            api = SpotifyAPI(valid_token, auth_manager, cache=cache)
            results = api.search_playlists("jazz")

        assert len(results) == 1
        assert results[0]["id"] == "cached"
        mock_sp.search.assert_not_called()

    def test_search_skip_cache(self, valid_token, auth_manager, mock_sp):
        """skip_cache=True should bypass cache."""
        import redis as redis_lib

        mock_redis = Mock(spec=redis_lib.Redis)
        cache = SpotifyCache(mock_redis)
        mock_redis.get.return_value = b'[{"id": "cached"}]'

        mock_sp.search.return_value = {
            "playlists": {
                "items": [
                    {
                        "id": "fresh",
                        "name": "Fresh",
                        "owner": {"display_name": "User"},
                        "images": [],
                        "tracks": {"total": 1},
                    }
                ]
            }
        }

        with patch("shuffify.spotify.api.spotipy.Spotify", return_value=mock_sp):
            api = SpotifyAPI(valid_token, auth_manager, cache=cache)
            results = api.search_playlists("jazz", skip_cache=True)

        assert results[0]["id"] == "fresh"
        mock_sp.search.assert_called_once()
```

### Step 12: Create Test File for External Playlist Routes

**File:** `tests/test_workshop_external.py` (NEW FILE)

```python
"""
Tests for the Workshop external playlist routes.

Covers URL loading, playlist search, session history, and error handling.
"""

import json
from unittest.mock import patch, Mock

from shuffify.models.playlist import Playlist


# =============================================================================
# Helpers
# =============================================================================


def _make_external_playlist():
    """A Playlist model instance for external playlist tests."""
    return Playlist(
        id="ext_playlist_abc",
        name="Jazz Vibes",
        owner_id="spotify_editorial",
        description="The best jazz tracks",
        tracks=[
            {
                "id": f"ext_track{i}",
                "name": f"Jazz Track {i}",
                "uri": f"spotify:track:ext_track{i}",
                "duration_ms": 240000 + (i * 1000),
                "is_local": False,
                "artists": [f"Jazz Artist {i}"],
                "artist_urls": [
                    f"https://open.spotify.com/artist/jazzartist{i}"
                ],
                "album_name": f"Jazz Album {i}",
                "album_image_url": f"https://example.com/jazz{i}.jpg",
                "track_url": f"https://open.spotify.com/track/ext_track{i}",
            }
            for i in range(1, 6)
        ],
    )


# =============================================================================
# Load External Playlist Tests (URL mode)
# =============================================================================


class TestLoadExternalPlaylistByUrl:
    """Tests for POST /workshop/load-external-playlist with URL."""

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    @patch("shuffify.routes.parse_spotify_playlist_url")
    def test_load_by_full_url(
        self,
        mock_parse_url,
        mock_playlist_svc,
        mock_auth_svc,
        authenticated_client,
    ):
        """Loading by full Spotify URL should return tracks."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_parse_url.return_value = "ext_playlist_abc"

        mock_ps = Mock()
        mock_ps.get_playlist.return_value = _make_external_playlist()
        mock_playlist_svc.return_value = mock_ps

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({
                "url": "https://open.spotify.com/playlist/ext_playlist_abc"
            }),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["mode"] == "tracks"
        assert data["playlist"]["id"] == "ext_playlist_abc"
        assert data["playlist"]["name"] == "Jazz Vibes"
        assert len(data["tracks"]) == 5

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.parse_spotify_playlist_url")
    def test_load_by_invalid_url_returns_400(
        self, mock_parse_url, mock_auth_svc, authenticated_client
    ):
        """Invalid URL that cannot be parsed should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_parse_url.return_value = None

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({"url": "not-a-spotify-url"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["success"] is False
        assert "Could not parse" in data["message"]

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    @patch("shuffify.routes.parse_spotify_playlist_url")
    def test_load_private_playlist_returns_404(
        self,
        mock_parse_url,
        mock_playlist_svc,
        mock_auth_svc,
        authenticated_client,
    ):
        """Loading a private/deleted playlist should return 404."""
        from shuffify.services import PlaylistError

        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_parse_url.return_value = "private_playlist_id"

        mock_ps = Mock()
        mock_ps.get_playlist.side_effect = PlaylistError("Not found")
        mock_playlist_svc.return_value = mock_ps

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({"url": "private_playlist_id"}),
            content_type="application/json",
        )

        assert response.status_code == 404

    def test_load_external_requires_auth(self, client):
        """Unauthenticated request should return 401."""
        response = client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({"url": "some_id"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @patch("shuffify.routes.AuthService")
    def test_load_external_requires_json(
        self, mock_auth_svc, authenticated_client
    ):
        """Non-JSON request should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data="not json",
            content_type="text/plain",
        )
        assert response.status_code == 400

    @patch("shuffify.routes.AuthService")
    def test_load_external_requires_url_or_query(
        self, mock_auth_svc, authenticated_client
    ):
        """Request without url or query should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400


# =============================================================================
# Load External Playlist Tests (Search mode)
# =============================================================================


class TestLoadExternalPlaylistBySearch:
    """Tests for POST /workshop/load-external-playlist with query."""

    @patch("shuffify.routes.AuthService")
    def test_search_returns_playlist_list(
        self, mock_auth_svc, authenticated_client
    ):
        """Search query should return a list of playlists."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_client = Mock()
        mock_client.search_playlists.return_value = [
            {
                "id": "pl1",
                "name": "Jazz Mix",
                "owner_display_name": "Spotify",
                "image_url": "https://example.com/img.jpg",
                "total_tracks": 50,
            }
        ]
        mock_auth_svc.get_authenticated_client.return_value = mock_client

        response = authenticated_client.post(
            "/workshop/load-external-playlist",
            data=json.dumps({"query": "jazz"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["mode"] == "search"
        assert len(data["playlists"]) == 1
        assert data["playlists"][0]["name"] == "Jazz Mix"


# =============================================================================
# Search Playlists Route Tests
# =============================================================================


class TestSearchPlaylistsRoute:
    """Tests for POST /workshop/search-playlists."""

    @patch("shuffify.routes.AuthService")
    def test_search_returns_results(
        self, mock_auth_svc, authenticated_client
    ):
        """Search should return playlist results."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_client = Mock()
        mock_client.search_playlists.return_value = [
            {
                "id": "result1",
                "name": "Test Playlist",
                "owner_display_name": "Owner",
                "image_url": None,
                "total_tracks": 10,
            }
        ]
        mock_auth_svc.get_authenticated_client.return_value = mock_client

        response = authenticated_client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": "test"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 1

    @patch("shuffify.routes.AuthService")
    def test_search_empty_query_returns_400(
        self, mock_auth_svc, authenticated_client
    ):
        """Empty search query should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": ""}),
            content_type="application/json",
        )

        assert response.status_code == 400

    @patch("shuffify.routes.AuthService")
    def test_search_too_long_query_returns_400(
        self, mock_auth_svc, authenticated_client
    ):
        """Query exceeding 200 characters should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": "x" * 201}),
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_search_requires_auth(self, client):
        """Unauthenticated search should return 401."""
        response = client.post(
            "/workshop/search-playlists",
            data=json.dumps({"query": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 401
```

**Note on `authenticated_client` fixture:** Phase 1's test plan references an `authenticated_client` fixture that sets up a Flask test client with a valid session token. This fixture must be added to `tests/conftest.py` as part of Phase 1. If Phase 1's implementation names it differently, the test imports here must be updated accordingly. The fixture should look like:

```python
@pytest.fixture
def authenticated_client(app, sample_token):
    """Flask test client with a pre-authenticated session."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['spotify_token'] = sample_token
    return client
```

This fixture already exists conceptually in Phase 1's test file. Phase 4's tests assume it is available in `conftest.py`.

---

## Test Plan

**New test files and counts:**

| Test File | Test Count | What It Covers |
|-----------|-----------|----------------|
| `tests/spotify/test_url_parser.py` | 22 | All URL/URI formats, edge cases, invalid inputs |
| `tests/spotify/test_api_search.py` | 12 | `search_playlists()` formatting, limits, caching, errors |
| `tests/test_workshop_external.py` | 10 | External playlist routes: URL load, search, auth, validation |

**Total new tests: 44**

**Existing tests that must still pass:** Run `pytest tests/ -v` after all changes. The new code is additive -- no existing methods are modified, only new methods are added.

**Manual verification steps:**

1. Start dev server with `python run.py`
2. Log in via Spotify OAuth
3. Open a playlist in the Workshop
4. In the "Load External Playlist" panel, paste a full Spotify URL (e.g., `https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M`)
5. Click "Load" -- verify the source panel populates with tracks from that playlist
6. Clear the input and type a search term (e.g., "jazz")
7. Click "Load" -- verify search results appear with playlist names, owners, and artwork
8. Click on a search result -- verify its tracks load into the source panel
9. Cherry-pick a track from the external playlist into the working playlist (drag or click)
10. Verify the "Recently Loaded" section shows the external playlist
11. Refresh the page -- verify "Recently Loaded" persists (session-based)
12. Try pasting a private/nonexistent playlist URL -- verify error message appears
13. Try pasting a Spotify track URL -- verify error message ("could not parse")
14. Try pasting a `spotify:playlist:` URI -- verify it works
15. Try pasting a bare 22-character ID -- verify it works

---

## Documentation Updates

**CHANGELOG.md** -- Add under `## [Unreleased]` / `### Added`:

```markdown
- **External Playlist Raiding** - Load any public Spotify playlist in the Workshop source panel
  - Paste a Spotify playlist URL, URI, or bare ID to load tracks instantly
  - Search for playlists by name using Spotify's search API
  - Reuses Phase 3's source panel for cherry-pick/drag-to-add UX
  - Session-based "Recently Loaded" history (up to 10 playlists)
  - New utility: `shuffify/spotify/url_parser.py` for parsing Spotify URL formats
  - New API method: `SpotifyAPI.search_playlists()` with Redis caching
  - New routes: `POST /workshop/load-external-playlist`, `POST /workshop/search-playlists`
  - Pydantic validation for external playlist requests (`ExternalPlaylistRequest` schema)
```

---

## Edge Cases

### 1. Private or deleted playlists
- `PlaylistService.get_playlist()` calls `SpotifyAPI.get_playlist()`, which calls `self._sp.playlist(playlist_id)`. If the playlist is private or deleted, Spotify returns a 404. The `@api_error_handler` decorator converts this to a `SpotifyNotFoundError`, which `PlaylistService` catches and re-raises as `PlaylistError`. The route catches `PlaylistError` and returns a 404 JSON response: "Could not load playlist. It may be private or deleted."

### 2. Very large external playlists (1000+ tracks)
- `SpotifyAPI.get_playlist_tracks()` already paginates through all tracks using `self._sp.next(results)`, so all tracks will be fetched regardless of count.
- The JSON response could be large (1000 tracks ~ 500KB JSON). This is acceptable for an XHR response.
- The source panel (Phase 3) should have `max-height` with overflow scroll to avoid DOM rendering issues.
- Future optimization: Add a `max_tracks` parameter to limit the number of tracks fetched. This is explicitly out of scope for Phase 4.

### 3. Malformed URLs
- The `parse_spotify_playlist_url()` function returns `None` for any input that does not match a known pattern.
- The route returns a clear 400 error: "Could not parse a playlist ID from the provided URL."
- The function does NOT attempt to fetch the URL or follow redirects. It only parses the string.

### 4. Search returning zero results
- `search_playlists()` returns an empty list `[]`.
- The JavaScript `displaySearchResults()` function shows "No playlists found." message.
- No error state needed.

### 5. Search with special characters
- Spotify's search API handles special characters in queries. The `spotipy` library URL-encodes the query parameter automatically.
- The Pydantic schema enforces `max_length=200` to prevent excessively long queries.

### 6. Session history growing without bound
- The route caps the history list at 10 entries: `session["external_playlist_history"] = history[:10]`.
- Each entry is approximately 100-150 bytes (id, name, owner_id, track_count).
- Total session overhead is approximately 1-1.5KB, which is negligible.

### 7. Duplicate entries in session history
- Before inserting a new entry, the route removes any existing entry with the same playlist ID: `history = [h for h in history if h["id"] != playlist.id]`.
- This ensures the same playlist never appears twice. Re-loading a playlist moves it to the top.

### 8. Cache key collisions for search
- The cache key includes both the lowercased query and the limit: `search_playlists:jazz:10`.
- Searches for "Jazz" and "jazz" produce the same cache key (intentional -- Spotify search is case-insensitive).
- Searches with different limits produce different cache keys (correct -- different result sets).

### 9. Network errors during search
- The `@api_error_handler` decorator on `search_playlists()` provides automatic retry with exponential backoff for transient errors (rate limits, server errors, network errors).
- If all retries fail, a `SpotifyAPIError` is raised. The route catches `Exception` and returns a 500 JSON response.
- The JavaScript `.catch()` handler shows the error notification to the user.

### 10. Phase 3 not merged yet
- If someone attempts to use Phase 4's external playlist loading before Phase 3 is merged, the `displayExternalTracks()` function will call `loadSourceTracks()`. If that function does not exist (Phase 3 not present), the fallback `console.warn` fires and the user sees an info notification.
- This is a graceful degradation -- the search and URL parsing still work, but tracks cannot be displayed in the source panel without Phase 3.

---

## Verification Checklist

```bash
# 1. Lint check (REQUIRED)
flake8 shuffify/

# 2. All tests pass (REQUIRED)
pytest tests/ -v

# 3. New URL parser tests pass
pytest tests/spotify/test_url_parser.py -v

# 4. New API search tests pass
pytest tests/spotify/test_api_search.py -v

# 5. New route tests pass
pytest tests/test_workshop_external.py -v

# 6. Code formatting
black --check shuffify/

# 7. Quick combined check
flake8 shuffify/ && pytest tests/ -v && echo "Ready to push!"
```

Manual checks:
- [ ] URL parser handles all 4 specified URL formats correctly
- [ ] `search_playlists()` returns properly formatted results
- [ ] External playlist panel appears in workshop sidebar
- [ ] Pasting a Spotify URL loads the playlist tracks
- [ ] Typing a search term shows matching playlists
- [ ] Clicking a search result loads that playlist's tracks
- [ ] Tracks appear in Phase 3's source panel (cross-list drag works)
- [ ] "Recently Loaded" section shows history
- [ ] Session history persists across page refreshes
- [ ] Invalid URL shows clear error message
- [ ] Private playlist shows "may be private or deleted" error
- [ ] Empty search shows "No playlists found"
- [ ] Unauthenticated requests return 401
- [ ] Existing workshop functionality (shuffle preview, commit, drag-and-drop) still works

---

## What NOT To Do

1. **Do NOT duplicate the source panel UI from Phase 3.** Phase 4 adds an input field and search results display. The actual track list rendering happens in Phase 3's source panel via the `loadSourceTracks()` function. If you create a separate track list for external playlists, you are duplicating Phase 3's work.

2. **Do NOT validate the playlist ID against Spotify before returning it from `parse_spotify_playlist_url()`.** The parser is a pure string-parsing utility. Network validation happens in the route when `get_playlist()` is called. Mixing network calls into the parser would violate the single-responsibility principle and make it untestable without mocking.

3. **Do NOT add new OAuth scopes.** Reading public playlists and searching are covered by the existing scopes. Adding unnecessary scopes would re-trigger the Spotify app review process.

4. **Do NOT store external playlist tracks in the Flask session.** Only store the lightweight history entries (id, name, owner_id, track_count). The full track data lives in the client-side JavaScript state and in the Redis cache. Storing tracks in the session would bloat Redis.

5. **Do NOT remove the `@api_error_handler` decorator from `search_playlists()`.** The decorator provides automatic retry for rate limits and server errors. Without it, transient failures would surface directly to the user.

6. **Do NOT make the search route a GET request.** The query should be sent in the JSON body, not as a URL parameter. This avoids URL encoding issues with special characters and keeps the pattern consistent with other workshop routes (all POST).

7. **Do NOT hardcode the maximum session history length.** The `[:10]` slice in the route is the single source of truth. If you also hardcode `10` in the template's `[:5]` loop or in JavaScript, you create multiple places to update if the limit changes.

8. **Do NOT use `innerHTML` with user-provided playlist names in JavaScript.** The `displaySearchResults()` function uses `escapeHtml()` to sanitize playlist names before inserting them into the DOM. Spotify playlist names can contain HTML characters like `<` and `>`, and failing to escape them creates an XSS vulnerability.

9. **Do NOT cache search results indefinitely.** The `default_ttl` of 300 seconds (5 minutes) is appropriate. Longer TTLs would show stale results when playlists are renamed or deleted. Shorter TTLs would increase API load without benefit.

10. **Do NOT modify `PlaylistService.get_playlist()`.** It already works for any playlist ID, not just user-owned ones. The `Playlist.from_spotify()` factory method calls `SpotifyAPI.get_playlist()` and `SpotifyAPI.get_playlist_tracks()`, both of which accept any valid playlist ID. No changes needed.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/spotify/api.py` - Add `search_playlists()` method following existing method patterns
- `/Users/chris/Projects/shuffify/shuffify/spotify/cache.py` - Add `get_search_playlists()` and `set_search_playlists()` caching methods
- `/Users/chris/Projects/shuffify/shuffify/routes.py` - Add 2 new routes for external playlist loading and search
- `/Users/chris/Projects/shuffify/shuffify/spotify/url_parser.py` - New file: URL/URI parser utility (core of Phase 4)
- `/Users/chris/Projects/shuffify/shuffify/schemas/requests.py` - Add `ExternalPlaylistRequest` Pydantic schema