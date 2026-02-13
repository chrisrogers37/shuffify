# Phase 2: Track Management (Search & Delete)

**PR Title:** `feat: Add track search and delete to Playlist Workshop`
**Risk Level:** Low-Medium -- All changes are client-side staging until user explicitly commits. The only new server-side data operation is a read-only Spotify search call. No new write operations are introduced; the existing commit endpoint already handles the resulting URI array (deletions mean fewer URIs; additions mean more URIs).
**Estimated Effort:** 2-3 days for a mid-level engineer, 4-5 days for a junior engineer.
**Files Created:**
- `tests/test_workshop_search.py` -- Tests for the new search route and search caching
**Files Modified:**
- `shuffify/spotify/api.py` -- Add `search_tracks()` method
- `shuffify/spotify/client.py` -- Add `search_tracks()` facade method
- `shuffify/spotify/cache.py` -- Add search result caching (get/set)
- `shuffify/routes.py` -- Add `POST /workshop/search` route
- `shuffify/schemas/requests.py` -- Add `WorkshopSearchRequest` Pydantic schema
- `shuffify/schemas/__init__.py` -- Export `WorkshopSearchRequest`
- `shuffify/templates/workshop.html` -- Add delete buttons on tracks, search sidebar panel, add-from-search handler
- `CHANGELOG.md` -- Add entry under `[Unreleased]`
**Files Deleted:** None

---

## Context

Phase 1 established the Playlist Workshop as a staging area where users can reorder tracks and preview shuffles before committing to Spotify. However, users cannot yet modify the track composition of a playlist. They can reorder what is there, but they cannot remove unwanted tracks or discover and add new ones.

This Phase adds two essential capabilities:
1. **Delete tracks** from the working copy by clicking an X button on each track row. The track is removed from `workshopState.workingUris` and the DOM immediately. It is not deleted from Spotify until the user clicks "Save to Spotify."
2. **Search Spotify's catalog** from a panel in the workshop sidebar. The user types a query, sees results with album art and artist names, and clicks a + button to add a track to the end of the working playlist.

Both operations are purely client-side staging. The existing Phase 1 commit endpoint (`POST /workshop/<playlist_id>/commit`) already calls `update_playlist_tracks()` which sends the full `track_uris` array as a replacement. Fewer URIs means tracks are removed; extra URIs means tracks are added. No changes to the commit flow are needed.

---

## Dependencies

**Prerequisites:**
- Phase 1 (Workshop Core) must be completed and merged. This plan assumes the following exist:
  - `GET /workshop/<playlist_id>` route
  - `POST /workshop/<playlist_id>/commit` route
  - `POST /workshop/<playlist_id>/preview-shuffle` route
  - `WorkshopCommitRequest` Pydantic schema
  - `workshop.html` template with `workshopState`, `trackDataByUri`, `rerenderTrackList()`, `markDirty()`, `showNotification()`, and SortableJS integration

**What this unlocks:**
- Phase 3 (Playlist Merging) can reuse the search panel pattern and the "add track from external source" JavaScript logic.
- Phase 4 (External Playlist Raiding) builds on the `trackDataByUri` registry pattern to accept tracks from public playlists.

**Parallel work coordination with Phase 3:**
- Phase 3 adds a **left sidebar** for source playlist merging.
- Phase 2 places the search panel in the **right sidebar** (the existing `lg:col-span-1` column, below the Shuffle Controls and Playlist Info panels).
- Both phases touch `workshop.html` but in different regions. If merged in the same sprint, whoever merges second resolves any template conflicts.

---

## Detailed Implementation Plan

### Step 1: Add Search Caching to `SpotifyCache`

**File:** `/Users/chris/Projects/shuffify/shuffify/spotify/cache.py`

**Why:** Search results are expensive API calls that return the same results for the same query within a short window. We cache them with a 120-second TTL (short enough that new releases appear quickly, long enough to handle rapid re-typing and pagination).

**Where:** After the Audio Features section (after line 349) and before the Cache Management section (line 351). Add a new section.

**Current code at the insertion point (lines 349-355):**
```python
            return False

    # =========================================================================
    # Cache Management
    # =========================================================================

    def invalidate_playlist(self, playlist_id: str) -> bool:
```

**Add this new section between lines 350 and 351:**

```python
    # =========================================================================
    # Search Results
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
                logger.debug(f"Cache hit for search: {normalized_query} offset={offset}")
                return self._deserialize(data)
            logger.debug(f"Cache miss for search: {normalized_query} offset={offset}")
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
```

**Why 120s TTL and not a constructor parameter:** Search is a new, non-core feature. Unlike playlists (60s) or audio features (24h), search results are ephemeral and only relevant during the current workshop session. Hardcoding 120s keeps the constructor signature stable. If we later want configurability, we add a `search_ttl` parameter to `__init__` following the same pattern as `playlist_ttl`.

**Why normalize the query:** Users might type "the beatles", "The Beatles", or "THE BEATLES". All should share a cache entry. We lowercase and strip whitespace for the cache key.

### Step 2: Add `search_tracks()` to `SpotifyAPI`

**File:** `/Users/chris/Projects/shuffify/shuffify/spotify/api.py`

**Why:** The SpotifyAPI class is where all Spotify data operations live. This follows the established pattern of `@api_error_handler` decorated methods with cache-first lookups.

**Where:** After the Audio Features Operations section (after line 519, the end of `get_audio_features`), add a new section.

**Current code at end of file (line 519-520):**
```python
        return features
```

**Add after line 519 (at the end of the class, before the file ends):**

```python
    # =========================================================================
    # Search Operations
    # =========================================================================

    @api_error_handler
    def search_tracks(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        market: Optional[str] = None,
        skip_cache: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Search Spotify's catalog for tracks.

        Args:
            query: Search query string (e.g., artist name, track title).
            limit: Maximum number of results (1-50, default 20).
            offset: Result offset for pagination (default 0).
            market: ISO 3166-1 alpha-2 country code for market filtering.
            skip_cache: If True, bypass cache and fetch fresh data.

        Returns:
            List of track dictionaries with id, name, uri, artists, album info.

        Raises:
            SpotifyAPIError: If the search request fails.
        """
        self._ensure_valid_token()

        # Clamp limit to Spotify's maximum
        limit = max(1, min(limit, 50))
        offset = max(0, offset)

        # Check cache first
        if self._cache and not skip_cache:
            cached = self._cache.get_search_results(query, offset)
            if cached is not None:
                return cached

        results = self._sp.search(
            q=query, limit=limit, offset=offset, type="track", market=market
        )

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
```

**Design decisions:**
- Returns raw Spotify track objects (not the simplified dict format from `Playlist.from_spotify`). The JavaScript code will extract the fields it needs (name, artists, album images, URI). This avoids coupling the search API to the Playlist model.
- Clamping `limit` to 1-50 matches the Spotify API constraint.
- The `market` parameter is optional and not used in the initial implementation but is included for future use (e.g., filtering results to the user's country).

### Step 3: Add `search_tracks()` to `SpotifyClient` Facade

**File:** `/Users/chris/Projects/shuffify/shuffify/spotify/client.py`

**Why:** Routes interact with `SpotifyClient`, not `SpotifyAPI` directly. The facade needs a passthrough method. This follows the exact pattern of every other facade method (e.g., `get_user_playlists`, `get_playlist_tracks`).

**Where:** After the Audio Features Methods section (after `get_track_audio_features` which ends around line 307), add a new section before the Private Methods section.

**Current code (lines 308-312):**
```python
    # =========================================================================
    # Private Methods
    # =========================================================================
```

**Insert before line 308:**

```python
    # =========================================================================
    # Search Methods
    # =========================================================================

    def search_tracks(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        market: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search Spotify's catalog for tracks.

        Args:
            query: Search query string.
            limit: Maximum number of results (1-50, default 20).
            offset: Pagination offset (default 0).
            market: Optional ISO 3166-1 alpha-2 country code.

        Returns:
            List of track dictionaries from Spotify search.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the search fails.
        """
        self._ensure_authenticated()
        return self._api.search_tracks(
            query=query, limit=limit, offset=offset, market=market
        )

```

**No additional imports needed.** The `List`, `Dict`, `Any`, and `Optional` types are already imported at the top of `client.py` (line 13).

### Step 4: Add `WorkshopSearchRequest` Pydantic Schema

**File:** `/Users/chris/Projects/shuffify/shuffify/schemas/requests.py`

**Why:** All request data goes through Pydantic validation. The search route needs to validate the query string (non-empty, reasonable length) and optional pagination parameters.

**Where:** After the `WorkshopCommitRequest` class that Phase 1 added (at the very end of the file). If Phase 1 has not yet been merged, add it after the `parse_shuffle_request` function (after line 197).

**Code to add at the end of the file:**

```python

class WorkshopSearchRequest(BaseModel):
    """Schema for searching Spotify's catalog from the workshop."""

    query: str = Field(
        ..., min_length=1, max_length=200, description="Search query string"
    )
    limit: Annotated[int, Field(ge=1, le=50)] = 20
    offset: Annotated[int, Field(ge=0)] = 0

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        """Ensure query is not just whitespace."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("Search query cannot be empty or whitespace")
        return stripped

    class Config:
        extra = "ignore"
```

**No import changes needed for this file.** `BaseModel`, `Field`, `Annotated`, and `field_validator` are already imported (line 8). The `str` type annotation is built-in.

### Step 5: Export `WorkshopSearchRequest` from Schemas Package

**File:** `/Users/chris/Projects/shuffify/shuffify/schemas/__init__.py`

**Current imports (lines 9-18):**
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

**Note:** Phase 1 adds `WorkshopCommitRequest` to this list. Whether Phase 1 is merged or not, Phase 2 adds `WorkshopSearchRequest`. The final import block should include both.

**Change the import block to:**
```python
from .requests import (
    ShuffleRequest,
    ShuffleRequestBase,
    BasicShuffleParams,
    BalancedShuffleParams,
    StratifiedShuffleParams,
    PercentageShuffleParams,
    PlaylistQueryParams,
    WorkshopCommitRequest,
    WorkshopSearchRequest,
    parse_shuffle_request,
)
```

**Also add to the `__all__` list (line 20-33).** Add `"WorkshopSearchRequest",` after `"WorkshopCommitRequest",` (or after `"PlaylistQueryParams",` if Phase 1 is not yet merged). Add `"WorkshopCommitRequest",` too if Phase 1 added it but forgot to add to `__all__`.

### Step 6: Add `POST /workshop/search` Route

**File:** `/Users/chris/Projects/shuffify/shuffify/routes.py`

**6a. Update imports (line 31).**

The current import is:
```python
from shuffify.schemas import parse_shuffle_request, PlaylistQueryParams
```

Phase 1 changes it to:
```python
from shuffify.schemas import parse_shuffle_request, PlaylistQueryParams, WorkshopCommitRequest
```

Phase 2 changes it to:
```python
from shuffify.schemas import parse_shuffle_request, PlaylistQueryParams, WorkshopCommitRequest, WorkshopSearchRequest
```

**6b. Add the search route** after the Phase 1 workshop routes section (after `workshop_commit`). This goes at the very end of the Workshop Routes section.

```python
@main.route("/workshop/search", methods=["POST"])
def workshop_search():
    """
    Search Spotify's catalog for tracks.

    Expects JSON body: { "query": "...", "limit": 20, "offset": 0 }
    Returns JSON: { "success": true, "tracks": [...] }
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Validate with Pydantic
    search_request = WorkshopSearchRequest(**data)

    # Execute search via SpotifyClient facade
    playlist_service = PlaylistService(client)
    raw_tracks = client.search_tracks(
        query=search_request.query,
        limit=search_request.limit,
        offset=search_request.offset,
    )

    # Transform raw Spotify track objects to simplified format
    # matching the structure used by workshopState/trackDataByUri
    tracks = []
    for track in raw_tracks:
        if not track.get("id") or not track.get("uri"):
            continue
        tracks.append({
            "id": track["id"],
            "name": track["name"],
            "uri": track["uri"],
            "duration_ms": track.get("duration_ms", 0),
            "artists": [
                artist.get("name", "Unknown")
                for artist in track.get("artists", [])
            ],
            "album_name": track.get("album", {}).get("name", ""),
            "album_image_url": (
                track.get("album", {}).get("images", [{}])[0].get("url", "")
            ),
        })

    logger.info(
        f"Workshop search for '{search_request.query}' returned {len(tracks)} tracks"
    )

    return jsonify({
        "success": True,
        "tracks": tracks,
        "query": search_request.query,
        "offset": search_request.offset,
        "limit": search_request.limit,
    })
```

**Why the route is `/workshop/search` (no playlist_id):** Search is not scoped to a specific playlist. The results are generic Spotify catalog tracks. The JS client determines which workshop session receives the results.

**Why we transform the raw Spotify response:** The raw Spotify track objects contain 30+ fields. We return only the 7 fields that the frontend needs: `id`, `name`, `uri`, `duration_ms`, `artists`, `album_name`, `album_image_url`. This matches the exact structure that `Playlist.from_spotify` produces in `/Users/chris/Projects/shuffify/shuffify/models/playlist.py` (lines 40-59), so the client can add tracks to `trackDataByUri` without format conversion.

**Why we call `client.search_tracks()` directly** instead of going through `PlaylistService`: The PlaylistService handles playlist-specific operations. Search is a catalog operation, not a playlist operation. Calling the client directly is the correct architectural boundary. Note: `PlaylistService(client)` is instantiated on line for consistency, but actually `playlist_service` is unused here. We should remove it. The corrected code should be:

```python
    raw_tracks = client.search_tracks(
        query=search_request.query,
        limit=search_request.limit,
        offset=search_request.offset,
    )
```

(Without the `playlist_service = PlaylistService(client)` line.)

### Step 7: Modify Workshop Template -- Add Delete Buttons and Search Panel

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/workshop.html`

This is the largest single change. I break it into three subsections: (A) Delete button on each track row, (B) Search panel in the right sidebar, (C) JavaScript handlers.

#### 7A: Add Delete Button to Each Track Row

**Where:** In the Phase 1 template, each track row ends with a drag handle (the six-dot SVG icon). We add a delete (X) button BEFORE the drag handle.

**Current track row structure (from Phase 1 plan, within `{% for track in playlist.tracks %}` loop):**
```html
                            <!-- Duration -->
                            <span class="w-24 text-right text-white/50 text-sm hidden sm:block">
                                {{ '%d:%02d' | format(track.duration_ms // 60000, (track.duration_ms % 60000) // 1000) }}
                            </span>

                            <!-- Drag Handle -->
                            <span class="drag-handle w-8 text-center text-white/30 hover:text-white/60 cursor-grab active:cursor-grabbing ml-2">
```

**Replace the Duration + Drag Handle portion with:**
```html
                            <!-- Duration -->
                            <span class="w-24 text-right text-white/50 text-sm hidden sm:block">
                                {{ '%d:%02d' | format(track.duration_ms // 60000, (track.duration_ms % 60000) // 1000) }}
                            </span>

                            <!-- Delete Button -->
                            <button class="delete-track-btn w-8 text-center text-white/20 hover:text-red-400 transition duration-150 ml-1 flex-shrink-0"
                                    onclick="deleteTrack('{{ track.uri }}', this)"
                                    title="Remove from playlist">
                                <svg class="w-4 h-4 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                                </svg>
                            </button>

                            <!-- Drag Handle -->
                            <span class="drag-handle w-8 text-center text-white/30 hover:text-white/60 cursor-grab active:cursor-grabbing ml-1 flex-shrink-0">
```

**Also update the track list header** to add a column for the delete button. The current header has `<span class="w-8"></span>` at the end (for the drag handle column). Change it to include a delete column:

**Current header (from Phase 1 plan):**
```html
                    <div class="px-4 py-3 border-b border-white/10 flex items-center text-white/60 text-xs uppercase tracking-wide font-semibold">
                        <span class="w-10 text-center">#</span>
                        <span class="w-10"></span>
                        <span class="flex-1 ml-3">Title</span>
                        <span class="w-24 text-right hidden sm:block">Duration</span>
                        <span class="w-8"></span>
                    </div>
```

**Change to:**
```html
                    <div class="px-4 py-3 border-b border-white/10 flex items-center text-white/60 text-xs uppercase tracking-wide font-semibold">
                        <span class="w-10 text-center">#</span>
                        <span class="w-10"></span>
                        <span class="flex-1 ml-3">Title</span>
                        <span class="w-24 text-right hidden sm:block">Duration</span>
                        <span class="w-8"></span>
                        <span class="w-8"></span>
                    </div>
```

(The extra `<span class="w-8"></span>` accounts for the delete button column width.)

#### 7B: Add Search Panel to Right Sidebar

**Where:** In the Phase 1 template, the right sidebar (`lg:col-span-1`) has two panels: "Shuffle Preview" and "Playlist Info". Add a third panel BETWEEN them.

**After the Shuffle Controls Panel closing `</div>` and before the Playlist Info Panel opening `<div>`:**

```html
                <!-- Search Spotify Panel -->
                <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-5">
                    <h2 class="text-white font-bold text-lg mb-3">Search Spotify</h2>
                    <p class="text-white/60 text-sm mb-3">Find tracks to add to your playlist.</p>

                    <!-- Search Input -->
                    <div class="flex items-center space-x-2 mb-4">
                        <input id="search-input"
                               type="text"
                               placeholder="Search tracks, artists..."
                               class="flex-1 px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white placeholder-white/40 focus:ring-2 focus:ring-white/30 focus:border-transparent text-sm"
                               onkeydown="if(event.key==='Enter'){event.preventDefault(); searchSpotify();}"
                               maxlength="200"
                               autocomplete="off">
                        <button id="search-btn"
                                onclick="searchSpotify()"
                                class="px-3 py-2 rounded-lg bg-white/20 hover:bg-white/30 text-white transition duration-150 flex-shrink-0"
                                title="Search">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                            </svg>
                        </button>
                    </div>

                    <!-- Search Results Container -->
                    <div id="search-results" class="space-y-1 max-h-[40vh] overflow-y-auto workshop-scrollbar">
                        <!-- Results populated by JS -->
                        <p id="search-placeholder" class="text-white/40 text-sm text-center py-4">
                            Type a query and press Enter
                        </p>
                    </div>

                    <!-- Load More Button (hidden by default) -->
                    <button id="search-load-more"
                            onclick="searchSpotifyMore()"
                            class="hidden w-full mt-2 px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white/70 text-sm transition duration-150">
                        Load more results
                    </button>
                </div>
```

**Why this goes in the RIGHT sidebar:** Phase 3 (Playlist Merging) will add a source panel to the left side of the layout. Keeping search in the right sidebar avoids conflicts and maintains the logical grouping: "actions on this playlist" are on the right.

#### 7C: Add JavaScript Handlers

**Where:** Inside the existing `<script>` block in `workshop.html`, after the `showNotification()` function and before the closing `</script>` tag.

**Add the following JavaScript sections:**

```javascript
// =============================================================================
// Delete Track
// =============================================================================

function deleteTrack(uri, buttonElement) {
    // Find the index of this specific track occurrence
    const trackItem = buttonElement.closest('.track-item');
    if (!trackItem) return;

    // Get the index from DOM position (handles duplicates correctly)
    const allItems = Array.from(document.querySelectorAll('#track-list .track-item'));
    const domIndex = allItems.indexOf(trackItem);
    if (domIndex === -1) return;

    // Remove from workingUris at the same index
    const removedUri = workshopState.workingUris.splice(domIndex, 1)[0];

    // Remove the DOM element with a fade-out animation
    trackItem.style.transition = 'opacity 0.2s ease-out, max-height 0.3s ease-out';
    trackItem.style.opacity = '0';
    trackItem.style.maxHeight = trackItem.offsetHeight + 'px';
    trackItem.style.overflow = 'hidden';

    setTimeout(() => {
        trackItem.style.maxHeight = '0';
        trackItem.style.padding = '0';
        setTimeout(() => {
            trackItem.remove();
            renumberTracks();
            updateTrackCount();
            markDirty();
        }, 300);
    }, 200);

    showNotification('Track removed from working copy.', 'info');
}


// =============================================================================
// Track Count Update
// =============================================================================

function updateTrackCount() {
    const count = workshopState.workingUris.length;
    const countEl = document.getElementById('track-count');
    const infoCountEl = document.getElementById('info-track-count');
    if (countEl) countEl.textContent = count;
    if (infoCountEl) infoCountEl.textContent = count;
}


// =============================================================================
// Search Spotify
// =============================================================================

let searchState = {
    currentQuery: '',
    currentOffset: 0,
    isSearching: false,
    limit: 20,
};


function searchSpotify() {
    const input = document.getElementById('search-input');
    const query = input.value.trim();
    if (!query) return;

    // New search — reset offset
    searchState.currentQuery = query;
    searchState.currentOffset = 0;

    // Clear previous results
    const container = document.getElementById('search-results');
    container.innerHTML = '';
    document.getElementById('search-placeholder').remove?.();

    executeSearch(query, 0, false);
}


function searchSpotifyMore() {
    if (searchState.isSearching || !searchState.currentQuery) return;
    executeSearch(
        searchState.currentQuery,
        searchState.currentOffset,
        true  // append mode
    );
}


function executeSearch(query, offset, append) {
    if (searchState.isSearching) return;
    searchState.isSearching = true;

    const searchBtn = document.getElementById('search-btn');
    const loadMoreBtn = document.getElementById('search-load-more');
    searchBtn.disabled = true;

    const container = document.getElementById('search-results');
    if (!append) {
        container.innerHTML = '<p class="text-white/40 text-sm text-center py-4">Searching...</p>';
    } else {
        loadMoreBtn.textContent = 'Loading...';
        loadMoreBtn.disabled = true;
    }

    fetch('/workshop/search', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({
            query: query,
            limit: searchState.limit,
            offset: offset,
        }),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => { throw new Error(data.message || 'Search failed.'); });
        }
        return response.json();
    })
    .then(data => {
        if (!data.success) {
            throw new Error(data.message || 'Search returned an error.');
        }

        if (!append) {
            container.innerHTML = '';
        }

        if (data.tracks.length === 0 && offset === 0) {
            container.innerHTML = '<p class="text-white/40 text-sm text-center py-4">No results found.</p>';
            loadMoreBtn.classList.add('hidden');
            return;
        }

        data.tracks.forEach(track => {
            container.appendChild(createSearchResultElement(track));
        });

        // Update offset for next page
        searchState.currentOffset = offset + data.tracks.length;

        // Show/hide load more button
        if (data.tracks.length >= searchState.limit) {
            loadMoreBtn.classList.remove('hidden');
        } else {
            loadMoreBtn.classList.add('hidden');
        }
    })
    .catch(error => {
        console.error('Search error:', error);
        if (!append) {
            container.innerHTML = `<p class="text-red-400 text-sm text-center py-4">${error.message}</p>`;
        } else {
            showNotification(error.message || 'Failed to load more results.', 'error');
        }
        loadMoreBtn.classList.add('hidden');
    })
    .finally(() => {
        searchState.isSearching = false;
        searchBtn.disabled = false;
        loadMoreBtn.disabled = false;
        loadMoreBtn.textContent = 'Load more results';
    });
}


function createSearchResultElement(track) {
    const el = document.createElement('div');
    el.className = 'flex items-center p-2 rounded-lg hover:bg-white/5 transition duration-150 group';
    el.dataset.uri = track.uri;

    // Check if track is already in the working playlist
    const alreadyAdded = workshopState.workingUris.includes(track.uri);

    const albumImg = track.album_image_url
        ? `<img src="${track.album_image_url}" alt="" class="w-8 h-8 rounded object-cover" loading="lazy">`
        : `<div class="w-8 h-8 rounded bg-black/20 flex items-center justify-center text-white/30">
               <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55C7.79 13 6 14.79 6 17s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
           </div>`;

    const artists = Array.isArray(track.artists) ? track.artists.join(', ') : '';
    const durationMin = Math.floor((track.duration_ms || 0) / 60000);
    const durationSec = Math.floor(((track.duration_ms || 0) % 60000) / 1000);
    const durationStr = `${durationMin}:${String(durationSec).padStart(2, '0')}`;

    el.innerHTML = `
        ${albumImg}
        <div class="ml-2 flex-1 min-w-0">
            <p class="text-white text-xs font-medium truncate">${escapeHtml(track.name)}</p>
            <p class="text-white/40 text-xs truncate">${escapeHtml(artists)}</p>
        </div>
        <span class="text-white/30 text-xs mr-2 hidden sm:inline">${durationStr}</span>
        <button class="add-track-btn flex-shrink-0 w-6 h-6 rounded-full ${alreadyAdded ? 'bg-white/10 text-white/30 cursor-default' : 'bg-white/20 hover:bg-green-500/50 text-white/60 hover:text-white'} flex items-center justify-center transition duration-150"
                onclick="addTrackFromSearch(this, ${escapeHtml(JSON.stringify(track))})"
                title="${alreadyAdded ? 'Already in playlist' : 'Add to playlist'}"
                ${alreadyAdded ? 'disabled' : ''}>
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="${alreadyAdded ? 'M5 13l4 4L19 7' : 'M12 6v12M6 12h12'}"></path>
            </svg>
        </button>
    `;

    return el;
}


function escapeHtml(text) {
    const div = document.createElement('div');
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
}


// =============================================================================
// Add Track from Search Results
// =============================================================================

function addTrackFromSearch(button, track) {
    if (!track || !track.uri) return;

    // Add track data to the lookup map
    trackDataByUri[track.uri] = {
        id: track.id,
        name: track.name,
        artists: track.artists || [],
        album_image_url: track.album_image_url || '',
        duration_ms: track.duration_ms || 0,
        uri: track.uri,
    };

    // Add URI to the end of the working list
    workshopState.workingUris.push(track.uri);

    // Create and append a new track DOM element
    const trackList = document.getElementById('track-list');
    const newTrackEl = createTrackElement(track, workshopState.workingUris.length);
    trackList.appendChild(newTrackEl);

    // Update button state to show "already added"
    button.disabled = true;
    button.classList.remove('hover:bg-green-500/50', 'text-white/60', 'hover:text-white', 'bg-white/20');
    button.classList.add('bg-white/10', 'text-white/30', 'cursor-default');
    button.innerHTML = `
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path>
        </svg>
    `;
    button.title = 'Already in playlist';

    updateTrackCount();
    markDirty();
    showNotification(`Added "${track.name}" to playlist.`, 'success');

    // Scroll track list to bottom to show new track
    trackList.scrollTop = trackList.scrollHeight;
}


function createTrackElement(track, position) {
    const el = document.createElement('div');
    el.className = 'track-item flex items-center px-4 py-2 hover:bg-white/5 transition duration-150 border-b border-white/5 cursor-grab active:cursor-grabbing';
    el.dataset.uri = track.uri;
    el.dataset.trackId = track.id;

    const artists = Array.isArray(track.artists) ? track.artists.join(', ') : '';
    const durationMin = Math.floor((track.duration_ms || 0) / 60000);
    const durationSec = Math.floor(((track.duration_ms || 0) % 60000) / 1000);
    const durationStr = `${durationMin}:${String(durationSec).padStart(2, '0')}`;

    const albumImg = track.album_image_url
        ? `<img src="${track.album_image_url}" alt="" class="w-full h-full object-cover" loading="lazy">`
        : `<div class="w-full h-full flex items-center justify-center text-white/30">
               <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55C7.79 13 6 14.79 6 17s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
           </div>`;

    el.innerHTML = `
        <span class="track-number w-10 text-center text-white/50 text-sm font-mono">${position}</span>
        <div class="w-10 h-10 rounded overflow-hidden flex-shrink-0 bg-black/20">${albumImg}</div>
        <div class="ml-3 flex-1 min-w-0">
            <p class="text-white text-sm font-medium truncate">${escapeHtml(track.name)}</p>
            <p class="text-white/50 text-xs truncate">${escapeHtml(artists)}</p>
        </div>
        <span class="w-24 text-right text-white/50 text-sm hidden sm:block">${durationStr}</span>
        <button class="delete-track-btn w-8 text-center text-white/20 hover:text-red-400 transition duration-150 ml-1 flex-shrink-0"
                onclick="deleteTrack('${track.uri}', this)"
                title="Remove from playlist">
            <svg class="w-4 h-4 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
            </svg>
        </button>
        <span class="drag-handle w-8 text-center text-white/30 hover:text-white/60 cursor-grab active:cursor-grabbing ml-1 flex-shrink-0">
            <svg class="w-5 h-5 mx-auto" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 6h2v2H8V6zm6 0h2v2h-2V6zM8 11h2v2H8v-2zm6 0h2v2h-2v-2zm-6 5h2v2H8v-2zm6 0h2v2h-2v-2z"/>
            </svg>
        </span>
    `;

    return el;
}
```

**Important: Update `rerenderTrackList()` to handle tracks that exist in `workingUris` but not in the DOM.** The Phase 1 `rerenderTrackList()` moves existing DOM nodes. After adding a track from search, the element is already in the DOM, so it works. However, after an undo that reverts to `savedUris` (which might not include the added track), we need to handle the case where `workingUris` contains URIs without DOM elements.

**Update the existing `undoPreview()` function** in the Phase 1 JavaScript to also remove any DOM elements for URIs that are NOT in `savedUris`:

**Current `undoPreview()`:**
```javascript
function undoPreview() {
    workshopState.workingUris = [...workshopState.savedUris];
    rerenderTrackList();
    markDirty();
    showNotification('Reverted to last saved order.', 'success');
}
```

**Change to:**
```javascript
function undoPreview() {
    workshopState.workingUris = [...workshopState.savedUris];
    fullRerenderTrackList();
    updateTrackCount();
    markDirty();
    showNotification('Reverted to last saved order.', 'success');
}
```

**Add a new `fullRerenderTrackList()` function** that handles both reordering existing elements AND removing elements that are no longer in `workingUris` AND creating elements for URIs that are in `workingUris` but not in the DOM:

```javascript
// =============================================================================
// Full Re-render (handles additions and deletions, not just reordering)
// =============================================================================

function fullRerenderTrackList() {
    const container = document.getElementById('track-list');
    const existingElements = {};
    container.querySelectorAll('.track-item').forEach(el => {
        // Build a map of URI -> [elements] to handle duplicates
        const uri = el.dataset.uri;
        if (!existingElements[uri]) existingElements[uri] = [];
        existingElements[uri].push(el);
    });

    // Track which elements we keep
    const usedElements = new Set();

    workshopState.workingUris.forEach((uri, index) => {
        const available = existingElements[uri];
        let el = null;

        if (available && available.length > 0) {
            // Reuse existing DOM element
            el = available.shift();
            usedElements.add(el);
            container.appendChild(el);
        } else if (trackDataByUri[uri]) {
            // Create new element for track added from search
            el = createTrackElement(trackDataByUri[uri], index + 1);
            container.appendChild(el);
        }
    });

    // Remove DOM elements that are no longer in workingUris
    container.querySelectorAll('.track-item').forEach(el => {
        if (!usedElements.has(el) && !workshopState.workingUris.includes(el.dataset.uri)) {
            el.remove();
        }
    });

    renumberTracks();
}
```

**Also update the existing `rerenderTrackList()`** to use `fullRerenderTrackList` instead, or leave it as-is for the simpler shuffle-preview case and call `fullRerenderTrackList` only when URIs have been added/removed. The simplest approach: replace `rerenderTrackList()` with the full version:

```javascript
function rerenderTrackList() {
    fullRerenderTrackList();
}
```

This ensures that after a shuffle preview (which only reorders, doesn't add/remove), the full re-render still works correctly.

### Step 8: Update `commitToSpotify()` Response Handling

**Why:** When the user commits a playlist that has more or fewer tracks than the original, the `savedUris` must update to include all current `workingUris`. The Phase 1 `commitToSpotify()` already does this correctly:

```javascript
workshopState.savedUris = [...workshopState.workingUris];
```

No changes needed to the commit function. It already replaces `savedUris` with whatever `workingUris` contains (which includes additions and excludes deletions).

---

## Test Plan

**New test file: `/Users/chris/Projects/shuffify/tests/test_workshop_search.py`**

```python
"""
Tests for the Workshop search route and search caching.

Covers the POST /workshop/search endpoint, input validation,
and the SpotifyAPI.search_tracks() method.
"""

import json
from unittest.mock import patch, Mock, MagicMock

import pytest
import redis

from shuffify.spotify.cache import SpotifyCache
from shuffify.schemas.requests import WorkshopSearchRequest


# =============================================================================
# Schema Validation Tests
# =============================================================================


class TestWorkshopSearchRequestSchema:
    """Tests for WorkshopSearchRequest Pydantic schema."""

    def test_valid_query(self):
        """Valid query string should pass validation."""
        req = WorkshopSearchRequest(query="the beatles")
        assert req.query == "the beatles"
        assert req.limit == 20
        assert req.offset == 0

    def test_query_is_stripped(self):
        """Leading/trailing whitespace should be stripped."""
        req = WorkshopSearchRequest(query="  hello world  ")
        assert req.query == "hello world"

    def test_empty_query_rejected(self):
        """Empty string should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="")

    def test_whitespace_only_query_rejected(self):
        """Whitespace-only string should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="   ")

    def test_query_max_length(self):
        """Query longer than 200 characters should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="a" * 201)

    def test_custom_limit_and_offset(self):
        """Custom limit and offset should be accepted."""
        req = WorkshopSearchRequest(query="test", limit=10, offset=40)
        assert req.limit == 10
        assert req.offset == 40

    def test_limit_below_minimum_rejected(self):
        """Limit of 0 should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="test", limit=0)

    def test_limit_above_maximum_rejected(self):
        """Limit above 50 should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="test", limit=51)

    def test_negative_offset_rejected(self):
        """Negative offset should be rejected."""
        with pytest.raises(Exception):
            WorkshopSearchRequest(query="test", offset=-1)


# =============================================================================
# Search Cache Tests
# =============================================================================


class TestSearchCache:
    """Tests for SpotifyCache search methods."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        return MagicMock(spec=redis.Redis)

    @pytest.fixture
    def cache(self, mock_redis):
        """Create a SpotifyCache with mocked Redis."""
        return SpotifyCache(mock_redis)

    def test_get_search_results_cache_miss(self, cache, mock_redis):
        """Cache miss should return None."""
        mock_redis.get.return_value = None
        result = cache.get_search_results("test query", 0)
        assert result is None

    def test_get_search_results_cache_hit(self, cache, mock_redis):
        """Cache hit should return deserialized data."""
        tracks = [{"id": "t1", "name": "Track 1", "uri": "spotify:track:t1"}]
        mock_redis.get.return_value = json.dumps(tracks).encode("utf-8")
        result = cache.get_search_results("test query", 0)
        assert result == tracks

    def test_set_search_results(self, cache, mock_redis):
        """Setting search results should call setex with 120s TTL."""
        tracks = [{"id": "t1", "name": "Track 1"}]
        cache.set_search_results("test query", 0, tracks)
        mock_redis.setex.assert_called_once()
        args = mock_redis.setex.call_args
        assert args[0][1] == 120  # Default TTL

    def test_search_cache_normalizes_query(self, cache, mock_redis):
        """Query should be normalized (lowercase, stripped) for cache key."""
        mock_redis.get.return_value = None
        cache.get_search_results("  The Beatles  ", 0)
        key_used = mock_redis.get.call_args[0][0]
        assert "the beatles" in key_used

    def test_search_cache_includes_offset_in_key(self, cache, mock_redis):
        """Different offsets should produce different cache keys."""
        mock_redis.get.return_value = None
        cache.get_search_results("test", 0)
        key_0 = mock_redis.get.call_args[0][0]

        cache.get_search_results("test", 20)
        key_20 = mock_redis.get.call_args[0][0]

        assert key_0 != key_20

    def test_search_cache_redis_error_returns_none(self, cache, mock_redis):
        """Redis errors should return None, not raise."""
        mock_redis.get.side_effect = redis.RedisError("Connection lost")
        result = cache.get_search_results("test", 0)
        assert result is None

    def test_set_search_results_redis_error_returns_false(self, cache, mock_redis):
        """Redis errors on set should return False, not raise."""
        mock_redis.setex.side_effect = redis.RedisError("Connection lost")
        result = cache.set_search_results("test", 0, [{"id": "t1"}])
        assert result is False


# =============================================================================
# Search Route Tests
# =============================================================================


class TestWorkshopSearchRoute:
    """Tests for POST /workshop/search."""

    def test_search_requires_auth(self, client):
        """Unauthenticated search should return 401."""
        response = client.post(
            "/workshop/search",
            data=json.dumps({"query": "test"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @patch("shuffify.routes.AuthService")
    def test_search_requires_json_body(self, mock_auth_svc, client):
        """Search without JSON body should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test",
                "token_type": "Bearer",
                "expires_at": 9999999999,
                "refresh_token": "test",
            }

        response = client.post(
            "/workshop/search",
            data="not json",
            content_type="text/plain",
        )
        assert response.status_code == 400

    @patch("shuffify.routes.AuthService")
    def test_search_validates_empty_query(self, mock_auth_svc, client):
        """Empty query should return validation error."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test",
                "token_type": "Bearer",
                "expires_at": 9999999999,
                "refresh_token": "test",
            }

        response = client.post(
            "/workshop/search",
            data=json.dumps({"query": ""}),
            content_type="application/json",
        )
        # Should fail Pydantic validation (caught by global error handler)
        assert response.status_code in (400, 422)

    @patch("shuffify.routes.AuthService")
    def test_search_returns_tracks(self, mock_auth_svc, client):
        """Valid search should return transformed track list."""
        mock_client = Mock()
        mock_client.search_tracks.return_value = [
            {
                "id": "track1",
                "name": "Yesterday",
                "uri": "spotify:track:track1",
                "duration_ms": 125000,
                "artists": [
                    {"name": "The Beatles", "external_urls": {"spotify": "http://..."}}
                ],
                "album": {
                    "name": "Help!",
                    "images": [{"url": "https://example.com/help.jpg"}],
                },
                "external_urls": {"spotify": "http://..."},
            }
        ]

        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = mock_client

        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test",
                "token_type": "Bearer",
                "expires_at": 9999999999,
                "refresh_token": "test",
            }

        response = client.post(
            "/workshop/search",
            data=json.dumps({"query": "yesterday beatles"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert len(data["tracks"]) == 1
        assert data["tracks"][0]["name"] == "Yesterday"
        assert data["tracks"][0]["uri"] == "spotify:track:track1"
        assert data["tracks"][0]["artists"] == ["The Beatles"]
        assert data["tracks"][0]["album_name"] == "Help!"
        assert data["tracks"][0]["album_image_url"] == "https://example.com/help.jpg"

    @patch("shuffify.routes.AuthService")
    def test_search_returns_empty_for_no_results(self, mock_auth_svc, client):
        """Search with no Spotify results should return empty tracks array."""
        mock_client = Mock()
        mock_client.search_tracks.return_value = []

        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = mock_client

        with client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test",
                "token_type": "Bearer",
                "expires_at": 9999999999,
                "refresh_token": "test",
            }

        response = client.post(
            "/workshop/search",
            data=json.dumps({"query": "xyznonexistent123"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert len(data["tracks"]) == 0
```

**Existing tests to verify still pass:**
```bash
pytest tests/ -v
```

**Manual verification steps:**
1. Start dev server with `python run.py`
2. Log in via Spotify OAuth
3. Open the Workshop for any playlist
4. Verify each track row has an X (delete) button on hover
5. Click the X button on a track -- verify it disappears with animation, track count updates, "Modified" badge appears
6. Click "Undo Changes" -- verify the deleted track reappears in its original position
7. In the Search panel, type a query (e.g., "bohemian rhapsody") and press Enter
8. Verify search results appear with album art, track name, artist name, and + button
9. Click the + button on a search result -- verify it appears at the bottom of the track list
10. Verify the + button changes to a checkmark after adding
11. Click "Save to Spotify" -- verify the playlist in Spotify reflects deletions and additions
12. Scroll to the bottom of search results -- click "Load more results" -- verify additional results load
13. Try searching with an empty query -- verify nothing happens
14. Test with a very long search query -- verify it is truncated/rejected

---

## Documentation Updates

**CHANGELOG.md** -- Add under `## [Unreleased]` / `### Added`:

```markdown
- **Track Management in Workshop** - Add and remove tracks within the Playlist Workshop
  - Delete button (X) on each track row to remove from working copy
  - Search Spotify panel in workshop sidebar to find new tracks
  - Add button (+) on search results to append track to working playlist
  - Search results cached in Redis for 120 seconds to reduce API calls
  - New `POST /workshop/search` endpoint with Pydantic validation
  - New `SpotifyAPI.search_tracks()` method wrapping spotipy search
  - New `SpotifyCache` search result caching (get/set with query normalization)
  - All changes are client-side staging until "Save to Spotify" is clicked
```

---

## Edge Cases

### 1. Deleting the only track in a playlist
The `workingUris` array becomes empty. The "Save to Spotify" button remains enabled (empty is a valid state -- the commit endpoint handles empty arrays via `playlist_replace_items(playlist_id, [])`). The track list shows an empty state. The user can add tracks from search before saving.

### 2. Adding duplicate tracks
Spotify playlists allow the same track to appear multiple times. The search result + button only disables for the first occurrence check (`workshopState.workingUris.includes(track.uri)`). If the user wants duplicates, they can drag-and-drop or search again. This matches Spotify's own behavior.

### 3. Adding a track that was just deleted
If the user deletes "Track A" and then searches for it and adds it back, it will appear at the end of the playlist (not in its original position). The `trackDataByUri` entry for the URI may already exist from the initial page load, and the new search result data will overwrite it. This is correct because the search result data is more current.

### 4. Search with special characters
Pydantic strips whitespace. Spotify's search API handles special characters (quotes, ampersands, etc.) internally. The `escapeHtml()` function in JavaScript prevents XSS from track names containing HTML-like characters.

### 5. Very long search results (scrolling)
The search results container has `max-h-[40vh] overflow-y-auto` and the `workshop-scrollbar` class. Pagination via "Load more results" prevents loading thousands of results at once. Each page loads 20 results (the Spotify API default).

### 6. Network error during search
The `executeSearch()` function has a `.catch()` handler that displays the error in the results container (for the first page) or as a notification (for "load more"). The search button re-enables in the `.finally()` block.

### 7. Adding a track to a playlist that has 10,000 tracks
Spotify's playlist limit is 10,000 tracks. The commit endpoint sends all URIs. If the total exceeds 10,000, the Spotify API will return an error, which will be caught by the `@api_error_handler` decorator and shown to the user.

### 8. Search while offline / session expired
The search endpoint returns 401 if the session has expired. The JavaScript `.catch()` handler shows the error message. The user can re-authenticate by navigating to the dashboard.

### 9. Rapid repeated searches (debounce)
The current implementation does not debounce. The `isSearching` flag prevents overlapping requests but does not debounce. This is acceptable for Phase 2 because the user must explicitly press Enter or click the search button. There is no auto-search-on-type. If we add auto-search later, debouncing will be needed.

### 10. Undo after adding tracks from search
Clicking "Undo Changes" reverts `workingUris` to `savedUris`. Any tracks added from search that are not in `savedUris` will be removed from the DOM by `fullRerenderTrackList()`. The `trackDataByUri` entries remain (harmless in-memory data) so that if the user re-adds the track, the data is immediately available.

---

## Verification Checklist

```bash
# 1. Lint check (REQUIRED)
flake8 shuffify/

# 2. All tests pass (REQUIRED)
pytest tests/ -v

# 3. New search tests pass specifically
pytest tests/test_workshop_search.py -v

# 4. Code formatting
black --check shuffify/

# 5. Quick combined check
flake8 shuffify/ && pytest tests/ -v && echo "Ready to push!"
```

Manual checks:
- [ ] Workshop page loads with delete buttons on each track
- [ ] Clicking X removes a track with animation, track count updates
- [ ] "Modified" badge appears after deleting a track
- [ ] "Undo Changes" restores deleted tracks to original positions
- [ ] Search panel visible in right sidebar below Shuffle Preview
- [ ] Typing a query and pressing Enter shows search results
- [ ] Search results show album art, track name, artist, duration
- [ ] Clicking + adds track to bottom of playlist, button changes to checkmark
- [ ] "Load more results" button appears when 20 results returned
- [ ] Empty search results show "No results found" message
- [ ] "Save to Spotify" commits deletions and additions correctly
- [ ] SortableJS drag-and-drop still works after adding/removing tracks
- [ ] No console errors during search/add/delete operations
- [ ] Session expiry during search shows appropriate error

---

## What NOT To Do

1. **Do NOT modify the existing commit route.** The `POST /workshop/<playlist_id>/commit` endpoint already handles any URI array. Deletions are expressed as fewer URIs; additions are expressed as more URIs. No changes needed.

2. **Do NOT add a separate "delete track from Spotify" API call.** The workshop is a staging area. Deletions happen only when the user clicks "Save to Spotify" and the full URI array (minus deleted tracks) is sent as a replacement.

3. **Do NOT add search to the dashboard.** Search belongs exclusively in the workshop. The dashboard is for playlist selection and quick shuffles.

4. **Do NOT use `sp.search()` with `type='track,artist,album'`.** We only need track results. Adding other types would require different result parsing and UI. Keep it simple: `type='track'`.

5. **Do NOT store search results in Flask session.** Search results are transient UI data. They live in JavaScript memory and in the Redis cache. Storing them in the session would bloat Redis session storage.

6. **Do NOT use `element.id` for identifying tracks to delete.** Playlists can contain duplicate tracks (same URI multiple times). Use DOM position (the `closest('.track-item')` + index approach) to find the correct occurrence.

7. **Do NOT put the search panel in the left sidebar.** The left sidebar is reserved for Phase 3 (Playlist Merging source panel). Search goes in the RIGHT sidebar.

8. **Do NOT add auto-search-as-you-type.** This would generate excessive API calls and cache entries. Require the user to press Enter or click the search button. Debounced auto-search can be a future enhancement.

9. **Do NOT skip the `escapeHtml()` call when rendering search results.** Track names and artist names come from Spotify's API and could theoretically contain HTML-like characters. Always escape user-facing strings to prevent XSS.

10. **Do NOT remove deleted tracks from `trackDataByUri`.** The metadata map is harmless in memory and may be needed if the user undoes or re-adds the track. Only `workingUris` determines what is in the playlist.

11. **Do NOT call `showNotification()` for every search result rendered.** Only show notifications for discrete user actions (track added, track deleted, search error). Showing notifications for each of 20 search results would flood the UI.

12. **Do NOT use `innerHTML` to rebuild the entire track list after deleting a track.** Remove only the specific DOM element. Rebuilding with `innerHTML` would destroy SortableJS bindings and event listeners on all other tracks.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/spotify/api.py` - Add `search_tracks()` method following the existing pattern of `@api_error_handler` decorated methods with cache support
- `/Users/chris/Projects/shuffify/shuffify/spotify/cache.py` - Add `get_search_results()` and `set_search_results()` methods following the existing get/set pattern
- `/Users/chris/Projects/shuffify/shuffify/routes.py` - Add `POST /workshop/search` route following the existing JSON endpoint pattern
- `/Users/chris/Projects/shuffify/shuffify/templates/workshop.html` - Add delete buttons, search panel HTML, and all JavaScript handlers (largest single change)
- `/Users/chris/Projects/shuffify/shuffify/schemas/requests.py` - Add `WorkshopSearchRequest` Pydantic schema following the existing validation pattern