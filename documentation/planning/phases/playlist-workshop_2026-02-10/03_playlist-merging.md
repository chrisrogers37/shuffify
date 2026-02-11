# Phase 3: Playlist Merging

**PR Title:** `feat: Add source playlist panel for cross-playlist merging in Workshop`
**Risk Level:** Low -- all additions are client-side until explicit "Save to Spotify". No existing routes modified. New route is read-only (GET).
**Estimated Effort:** 3-4 days for a mid-level engineer, 5-6 days for a junior engineer.
**Files Created:**
- `tests/test_workshop_merge.py` -- Test file for source playlist / merging routes and logic

**Files Modified:**
- `shuffify/routes.py` -- Add 1 new JSON API route (`GET /api/user-playlists`)
- `shuffify/templates/workshop.html` -- Add source panel section below track list, update JS for cross-list drag, add-to-working, deduplication
- `CHANGELOG.md` -- Add entry under `[Unreleased]`

**Files Deleted:** None

---

## Context

After Phase 1, users can view, reorder, shuffle-preview, and commit tracks within a single playlist. However, there is no way to pull tracks from another playlist into the one being edited. Power users managing multiple playlists -- genre playlists, mood playlists, party playlists -- frequently need to cherry-pick tracks across playlists without leaving Shuffify.

Phase 3 adds a "Source Playlists" panel to the workshop that lets users:
1. Select another of their playlists from a dropdown (excluding the one currently being edited).
2. Browse the source playlist's tracks with full metadata (album art, artist, duration).
3. Click a "+" button on any source track to append it to the working playlist.
4. Drag tracks directly from the source panel into the main track list at a specific position (SortableJS cross-list group).
5. See a duplicate warning when attempting to add a track whose URI already exists in the working playlist.

All additions are client-side only. Nothing touches Spotify until the user clicks "Save to Spotify" (the existing Phase 1 commit flow).

---

## Dependencies

**Prerequisites:**
- **Phase 1 (Workshop Core) must be merged.** Phase 3 modifies `workshop.html` and adds to its JavaScript. The template structure, `workshopState`, `trackDataByUri`, `rerenderTrackList()`, `renumberTracks()`, `markDirty()`, `showNotification()`, and `commitToSpotify()` must all exist.

**Does NOT depend on:**
- Phase 2 (Track Management). Phases 2 and 3 can run in parallel. See "Layout Coordination" below.

**What this unlocks:**
- Phase 4 (External Playlist Raiding) extends the source panel pattern to load any public playlist by URL or search.

**Layout Coordination with Phase 2:**
- Phase 2 adds a **search panel** to the **right sidebar** (below shuffle controls).
- Phase 3 adds a **source playlists panel** as a **collapsible section below the main track list** in the left column.
- These are in different layout regions and do not conflict. The only shared file is `workshop.html`. If both PRs are open simultaneously, one will need a simple merge-conflict resolution in the template (adding their respective HTML blocks in non-overlapping locations).

---

## Detailed Implementation Plan

### Step 1: Add a JSON API Route for User's Playlists

**File:** `/Users/chris/Projects/shuffify/shuffify/routes.py`

**Why:** The existing index route (`GET /`) renders the full `dashboard.html` template -- it does not return JSON. The existing `POST /refresh-playlists` returns JSON but is a POST designed for cache-busting. The workshop needs a lightweight GET endpoint that returns the user's editable playlists as JSON for populating the source dropdown via AJAX.

**Where:** After the current Playlist API Routes section (after line 294, before the Shuffle Routes section), add a new route.

**Current code at the insertion point (lines 296-302):**
```python
# =============================================================================
# Shuffle Routes
# =============================================================================


@main.route("/shuffle/<playlist_id>", methods=["POST"])
def shuffle(playlist_id):
```

**Insert the following route BEFORE the Shuffle Routes section comment (between lines 294 and 296):**

```python
@main.route("/api/user-playlists")
def api_user_playlists():
    """Return the user's editable playlists as JSON for AJAX consumers."""
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    try:
        playlist_service = PlaylistService(client)
        playlists = playlist_service.get_user_playlists()

        # Return a lightweight list: id, name, track count, image
        result = []
        for p in playlists:
            result.append({
                "id": p["id"],
                "name": p["name"],
                "track_count": p.get("tracks", {}).get("total", 0),
                "image_url": p["images"][0]["url"] if p.get("images") else None,
            })

        logger.debug(f"API returned {len(result)} playlists")
        return jsonify({"success": True, "playlists": result})
    except PlaylistError as e:
        logger.error(f"Failed to fetch playlists for API: {e}")
        return json_error("Failed to fetch playlists.", 500)
```

**Why a new route instead of reusing `/refresh-playlists`:**
1. `/refresh-playlists` is POST (forces cache bypass with `skip_cache=True`). We want GET with normal caching.
2. The new route returns only the fields needed for the dropdown (id, name, track count, image). This is lighter than the full Spotify playlist objects.
3. Clear separation: the new `/api/user-playlists` endpoint is a read-only data API. The `/refresh-playlists` endpoint is an action (cache invalidation + reload).

**No import changes needed.** The route uses `require_auth()`, `PlaylistService`, `jsonify`, `json_error`, and `logger` -- all already imported.

### Step 2: Add Source Panel HTML to Workshop Template

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/workshop.html`

**Where:** After the closing `</div>` of the track list container (the `lg:col-span-2` div) and BEFORE the sidebar div (`lg:col-span-1`). Looking at the Phase 1 template, this is after the closing `</div>` on what would be line 423 of the Phase 1 template (the line `</div>` that closes the `<div class="lg:col-span-2">` block).

However, since the source panel should be below the track list but still within the left column, we place it INSIDE the `lg:col-span-2` div, after the track list's rounded container div.

**Current structure (Phase 1, simplified):**
```html
<!-- Track List (2/3 width on large screens) -->
<div class="lg:col-span-2">
    <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 overflow-hidden">
        <!-- Track List Header -->
        ...
        <!-- Scrollable Track List -->
        <div id="track-list" class="max-h-[65vh] overflow-y-auto workshop-scrollbar">
            ...
        </div>
    </div>
</div>  <!-- <-- This is the closing tag of lg:col-span-2 -->
```

**Change to:** Insert a new collapsible source panel section INSIDE the `lg:col-span-2` div, after the track list's rounded container:

```html
<!-- Track List (2/3 width on large screens) -->
<div class="lg:col-span-2">
    <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 overflow-hidden">
        <!-- Track List Header (unchanged) -->
        ...
        <!-- Scrollable Track List (unchanged) -->
        <div id="track-list" class="max-h-[65vh] overflow-y-auto workshop-scrollbar">
            ...
        </div>
    </div>

    <!-- Source Playlists Panel (Phase 3) -->
    <div class="mt-6">
        <button id="source-panel-toggle"
                onclick="toggleSourcePanel()"
                class="w-full flex items-center justify-between px-5 py-3 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 text-white font-bold text-lg hover:bg-white/15 transition duration-150"
                aria-expanded="false"
                aria-controls="source-panel-body">
            <span class="flex items-center">
                <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"></path>
                </svg>
                Source Playlists
            </span>
            <svg id="source-panel-chevron" class="w-5 h-5 transform transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
            </svg>
        </button>

        <div id="source-panel-body" class="hidden mt-2 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 overflow-hidden">
            <!-- Source Playlist Selector -->
            <div class="px-5 py-4 border-b border-white/10">
                <label for="source-playlist-select" class="block text-sm font-medium text-white/90 mb-2">
                    Load tracks from another playlist:
                </label>
                <div class="flex items-center space-x-2">
                    <select id="source-playlist-select"
                            class="flex-1 px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white focus:ring-2 focus:ring-white/30 focus:border-transparent"
                            disabled>
                        <option value="">Loading playlists...</option>
                    </select>
                    <button id="load-source-btn"
                            onclick="loadSourcePlaylist()"
                            disabled
                            class="px-4 py-2 rounded-lg bg-white/20 hover:bg-white/30 text-white font-semibold transition duration-150 disabled:opacity-40 disabled:cursor-not-allowed">
                        Load
                    </button>
                </div>
            </div>

            <!-- Source Track List -->
            <div id="source-tracks-container" class="hidden">
                <!-- Source Track List Header -->
                <div class="px-4 py-2 border-b border-white/10 flex items-center justify-between">
                    <div class="flex items-center text-white/60 text-xs uppercase tracking-wide font-semibold">
                        <span class="w-10"></span>
                        <span class="ml-3">Source Tracks</span>
                    </div>
                    <span id="source-track-count" class="text-white/50 text-xs">0 tracks</span>
                </div>

                <!-- Scrollable Source Track List -->
                <div id="source-track-list" class="max-h-[40vh] overflow-y-auto workshop-scrollbar">
                    <!-- Source tracks rendered dynamically by JS -->
                </div>
            </div>

            <!-- Empty state (shown when no source loaded) -->
            <div id="source-empty-state" class="px-5 py-6 text-center text-white/50 text-sm">
                Select a playlist above and click "Load" to browse its tracks.
            </div>

            <!-- Loading state -->
            <div id="source-loading-state" class="hidden px-5 py-6 text-center text-white/50 text-sm">
                <svg class="w-6 h-6 mx-auto mb-2 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                </svg>
                Loading tracks...
            </div>
        </div>
    </div>
</div>  <!-- End lg:col-span-2 -->
```

**Key design decisions in this HTML:**
- The panel is **collapsed by default** via `hidden` class on `#source-panel-body`. The toggle button shows/hides it.
- The source track list (`#source-track-list`) is a separate `div` from the main `#track-list`. SortableJS will be initialized on both with a shared `group` to enable cross-list dragging.
- The dropdown `<select>` starts disabled with a "Loading playlists..." placeholder. It gets populated by AJAX when the panel is first opened.
- Three states: empty (no source loaded), loading (spinner), and populated (track list visible).

### Step 3: Add Source Panel JavaScript

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/workshop.html`

**Where:** Inside the `<script>` block, after the existing Phase 1 JavaScript (after the `showNotification` function, before the closing `</script>` tag). Add new sections for source panel state and functions.

**3a. Add source panel state to workshopState:**

At the top of the script block, expand the `workshopState` object. Locate the existing declaration:

```javascript
const workshopState = {
    playlistId: {{ playlist.id | tojson }},
    savedUris: {{ playlist.tracks | map(attribute='uri') | list | tojson }},
    workingUris: {{ playlist.tracks | map(attribute='uri') | list | tojson }},
    isDirty: false,
    isSaving: false,
    isShuffling: false,
};
```

**Add these new properties:**

```javascript
const workshopState = {
    playlistId: {{ playlist.id | tojson }},
    savedUris: {{ playlist.tracks | map(attribute='uri') | list | tojson }},
    workingUris: {{ playlist.tracks | map(attribute='uri') | list | tojson }},
    isDirty: false,
    isSaving: false,
    isShuffling: false,
    // Phase 3: Source panel state
    sourcePlaylistId: null,
    sourcePlaylistName: null,
    sourceTracks: [],           // Array of track data objects from source
    playlistsLoaded: false,     // Whether the dropdown has been populated
    isLoadingSource: false,
};
```

**3b. Modify SortableJS initialization to use groups:**

In the `DOMContentLoaded` handler, the existing SortableJS `Sortable.create` call must be updated to use the `group` option. Locate:

```javascript
sortableInstance = Sortable.create(trackList, {
    animation: 200,
    handle: '.track-item',
    ghostClass: 'sortable-ghost',
    chosenClass: 'sortable-chosen',
    dragClass: 'sortable-drag',
    onEnd: function(evt) {
        updateWorkingUrisFromDOM();
        markDirty();
    },
});
```

**Replace with:**

```javascript
sortableInstance = Sortable.create(trackList, {
    animation: 200,
    handle: '.track-item',
    ghostClass: 'sortable-ghost',
    chosenClass: 'sortable-chosen',
    dragClass: 'sortable-drag',
    group: {
        name: 'workshop-tracks',
        pull: true,
        put: true,
    },
    onAdd: function(evt) {
        // A track was dragged FROM the source list INTO the main list
        handleSourceTrackAddedViaD(evt);
    },
    onEnd: function(evt) {
        updateWorkingUrisFromDOM();
        markDirty();
    },
});
```

**3c. Add all source panel JavaScript functions.** Append these after the `showNotification` function:

```javascript
// =============================================================================
// Source Panel — Toggle, Load, Render
// =============================================================================

let sourceSortableInstance = null;

function toggleSourcePanel() {
    const body = document.getElementById('source-panel-body');
    const chevron = document.getElementById('source-panel-chevron');
    const toggle = document.getElementById('source-panel-toggle');
    const isHidden = body.classList.contains('hidden');

    body.classList.toggle('hidden');
    chevron.classList.toggle('rotate-180');
    toggle.setAttribute('aria-expanded', String(isHidden));

    // Lazy-load playlists on first open
    if (isHidden && !workshopState.playlistsLoaded) {
        fetchUserPlaylists();
    }
}

function fetchUserPlaylists() {
    const select = document.getElementById('source-playlist-select');
    select.disabled = true;
    select.innerHTML = '<option value="">Loading playlists...</option>';

    fetch('/api/user-playlists', {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.message || 'Failed to load playlists.');
            });
        }
        return response.json();
    })
    .then(data => {
        if (!data.success || !data.playlists) {
            throw new Error('Unexpected response format.');
        }

        select.innerHTML = '<option value="">-- Select a playlist --</option>';

        data.playlists.forEach(p => {
            // Exclude the current playlist being edited
            if (p.id === workshopState.playlistId) return;

            const option = document.createElement('option');
            option.value = p.id;
            option.textContent = `${p.name} (${p.track_count} tracks)`;
            option.dataset.name = p.name;
            select.appendChild(option);
        });

        select.disabled = false;
        document.getElementById('load-source-btn').disabled = false;
        workshopState.playlistsLoaded = true;
    })
    .catch(error => {
        console.error('Error fetching playlists:', error);
        select.innerHTML = '<option value="">Failed to load playlists</option>';
        showNotification(error.message || 'Failed to load playlists.', 'error');
    });
}

function loadSourcePlaylist() {
    const select = document.getElementById('source-playlist-select');
    const playlistId = select.value;

    if (!playlistId) {
        showNotification('Please select a playlist first.', 'info');
        return;
    }

    if (workshopState.isLoadingSource) return;

    const selectedOption = select.options[select.selectedIndex];
    const playlistName = selectedOption.dataset.name || selectedOption.textContent;

    workshopState.isLoadingSource = true;
    workshopState.sourcePlaylistId = playlistId;
    workshopState.sourcePlaylistName = playlistName;

    // Show loading, hide others
    document.getElementById('source-empty-state').classList.add('hidden');
    document.getElementById('source-tracks-container').classList.add('hidden');
    document.getElementById('source-loading-state').classList.remove('hidden');

    const loadBtn = document.getElementById('load-source-btn');
    loadBtn.disabled = true;
    loadBtn.textContent = 'Loading...';

    fetch(`/playlist/${playlistId}`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
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
        workshopState.sourceTracks = data.tracks || [];
        renderSourceTracks();
    })
    .catch(error => {
        console.error('Error loading source playlist:', error);
        showNotification(error.message || 'Failed to load source playlist.', 'error');
        document.getElementById('source-loading-state').classList.add('hidden');
        document.getElementById('source-empty-state').classList.remove('hidden');
    })
    .finally(() => {
        workshopState.isLoadingSource = false;
        loadBtn.disabled = false;
        loadBtn.textContent = 'Load';
    });
}

function renderSourceTracks() {
    const container = document.getElementById('source-track-list');
    const tracks = workshopState.sourceTracks;

    container.innerHTML = '';

    if (tracks.length === 0) {
        document.getElementById('source-loading-state').classList.add('hidden');
        document.getElementById('source-tracks-container').classList.add('hidden');
        document.getElementById('source-empty-state').classList.remove('hidden');
        document.getElementById('source-empty-state').textContent =
            'This playlist has no tracks.';
        return;
    }

    tracks.forEach((track, index) => {
        const isDuplicate = workshopState.workingUris.includes(track.uri);

        const div = document.createElement('div');
        div.className = 'source-track-item flex items-center px-4 py-2 hover:bg-white/5 transition duration-150 border-b border-white/5 cursor-grab active:cursor-grabbing';
        div.dataset.uri = track.uri;
        div.dataset.trackId = track.id;

        const durationMin = Math.floor(track.duration_ms / 60000);
        const durationSec = Math.floor((track.duration_ms % 60000) / 1000);
        const durationStr = `${durationMin}:${String(durationSec).padStart(2, '0')}`;

        const artistsStr = Array.isArray(track.artists)
            ? track.artists.join(', ')
            : '';

        div.innerHTML = `
            <div class="w-10 h-10 rounded overflow-hidden flex-shrink-0 bg-black/20">
                ${track.album_image_url
                    ? `<img src="${track.album_image_url}" alt="" class="w-full h-full object-cover" loading="lazy">`
                    : `<div class="w-full h-full flex items-center justify-center text-white/30">
                        <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55C7.79 13 6 14.79 6 17s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
                       </div>`}
            </div>
            <div class="ml-3 flex-1 min-w-0">
                <p class="text-white text-sm font-medium truncate">${escapeHtml(track.name)}</p>
                <p class="text-white/50 text-xs truncate">${escapeHtml(artistsStr)}</p>
            </div>
            <span class="w-20 text-right text-white/50 text-sm hidden sm:block">${durationStr}</span>
            <button class="add-source-track-btn ml-2 w-8 h-8 flex items-center justify-center rounded-full ${isDuplicate ? 'bg-yellow-500/30 text-yellow-300' : 'bg-white/10 hover:bg-white/20 text-white/70 hover:text-white'} transition duration-150 flex-shrink-0"
                    onclick="addSourceTrackToWorking('${track.uri}')"
                    title="${isDuplicate ? 'Already in playlist (click to add duplicate)' : 'Add to playlist'}">
                ${isDuplicate
                    ? '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>'
                    : '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg>'}
            </button>
        `;

        container.appendChild(div);
    });

    // Update track count
    document.getElementById('source-track-count').textContent =
        `${tracks.length} tracks`;

    // Show track list, hide loading/empty
    document.getElementById('source-loading-state').classList.add('hidden');
    document.getElementById('source-empty-state').classList.add('hidden');
    document.getElementById('source-tracks-container').classList.remove('hidden');

    // Initialize SortableJS on source list for cross-list drag
    initSourceSortable();
}

function initSourceSortable() {
    const sourceList = document.getElementById('source-track-list');
    if (!sourceList) return;

    // Destroy previous instance if re-loading a different source
    if (sourceSortableInstance) {
        sourceSortableInstance.destroy();
    }

    sourceSortableInstance = Sortable.create(sourceList, {
        animation: 200,
        sort: false,            // Don't allow reordering within the source list
        group: {
            name: 'workshop-tracks',
            pull: 'clone',      // Clone the element (don't remove from source)
            put: false,         // Don't allow dropping into source list
        },
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        dragClass: 'sortable-drag',
    });
}

// =============================================================================
// Adding Source Tracks to Working Playlist
// =============================================================================

function addSourceTrackToWorking(uri) {
    const isDuplicate = workshopState.workingUris.includes(uri);

    if (isDuplicate) {
        // Show confirmation for duplicate
        if (!confirm('This track is already in the playlist. Add it again?')) {
            return;
        }
    }

    // Find track data from source tracks
    const trackData = workshopState.sourceTracks.find(t => t.uri === uri);
    if (!trackData) {
        showNotification('Track data not found.', 'error');
        return;
    }

    // Register in trackDataByUri if not already there
    if (!trackDataByUri[uri]) {
        trackDataByUri[uri] = {
            id: trackData.id,
            name: trackData.name,
            artists: trackData.artists,
            album_image_url: trackData.album_image_url || '',
            duration_ms: trackData.duration_ms,
            uri: trackData.uri,
        };
    }

    // Add URI to working list
    workshopState.workingUris.push(uri);

    // Create and append DOM element
    appendTrackElement(trackData);
    renumberTracks();
    markDirty();
    updateTrackCount();

    // Refresh source duplicate indicators
    refreshSourceDuplicateIndicators();

    showNotification(`Added "${trackData.name}" to playlist.`, 'success');
}

function handleSourceTrackAddedViaD(evt) {
    // Called by SortableJS onAdd when a track is dragged from source into main list
    const addedEl = evt.item;
    const uri = addedEl.dataset.uri;

    // The cloned element has the source-track-item class — we need to
    // transform it into a proper main-list track-item element
    const trackData = workshopState.sourceTracks.find(t => t.uri === uri);
    if (!trackData) {
        addedEl.remove();
        return;
    }

    // Register in trackDataByUri
    if (!trackDataByUri[uri]) {
        trackDataByUri[uri] = {
            id: trackData.id,
            name: trackData.name,
            artists: trackData.artists,
            album_image_url: trackData.album_image_url || '',
            duration_ms: trackData.duration_ms,
            uri: trackData.uri,
        };
    }

    // Check for duplicate
    const isDuplicate = workshopState.workingUris.includes(uri);
    if (isDuplicate) {
        showNotification(`Note: "${trackData.name}" is already in the playlist (duplicate added).`, 'info');
    }

    // Replace the cloned source element with a proper track-item element
    const newEl = createTrackElement(trackData, workshopState.workingUris.length + 1);
    addedEl.replaceWith(newEl);

    // Update state from DOM
    updateWorkingUrisFromDOM();
    markDirty();
    updateTrackCount();

    // Refresh source panel duplicate indicators
    refreshSourceDuplicateIndicators();
}

function appendTrackElement(trackData) {
    const container = document.getElementById('track-list');
    const index = workshopState.workingUris.length;
    const el = createTrackElement(trackData, index);
    container.appendChild(el);
}

function createTrackElement(trackData, index) {
    const durationMin = Math.floor(trackData.duration_ms / 60000);
    const durationSec = Math.floor((trackData.duration_ms % 60000) / 1000);
    const durationStr = `${durationMin}:${String(durationSec).padStart(2, '0')}`;

    const artistsStr = Array.isArray(trackData.artists)
        ? trackData.artists.join(', ')
        : '';

    const div = document.createElement('div');
    div.className = 'track-item flex items-center px-4 py-2 hover:bg-white/5 transition duration-150 border-b border-white/5 cursor-grab active:cursor-grabbing';
    div.dataset.uri = trackData.uri;
    div.dataset.trackId = trackData.id;

    div.innerHTML = `
        <span class="track-number w-10 text-center text-white/50 text-sm font-mono">${index}</span>
        <div class="w-10 h-10 rounded overflow-hidden flex-shrink-0 bg-black/20">
            ${trackData.album_image_url
                ? `<img src="${trackData.album_image_url}" alt="" class="w-full h-full object-cover" loading="lazy">`
                : `<div class="w-full h-full flex items-center justify-center text-white/30">
                    <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55C7.79 13 6 14.79 6 17s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
                   </div>`}
        </div>
        <div class="ml-3 flex-1 min-w-0">
            <p class="text-white text-sm font-medium truncate">${escapeHtml(trackData.name)}</p>
            <p class="text-white/50 text-xs truncate">${escapeHtml(artistsStr)}</p>
        </div>
        <span class="w-24 text-right text-white/50 text-sm hidden sm:block">${durationStr}</span>
        <span class="drag-handle w-8 text-center text-white/30 hover:text-white/60 cursor-grab active:cursor-grabbing ml-2">
            <svg class="w-5 h-5 mx-auto" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 6h2v2H8V6zm6 0h2v2h-2V6zM8 11h2v2H8v-2zm6 0h2v2h-2v-2zm-6 5h2v2H8v-2zm6 0h2v2h-2v-2z"/>
            </svg>
        </span>
    `;

    return div;
}

// =============================================================================
// Helper: Update Track Count Display
// =============================================================================

function updateTrackCount() {
    const count = workshopState.workingUris.length;
    document.getElementById('track-count').textContent = count;
    document.getElementById('info-track-count').textContent = count;
}

// =============================================================================
// Helper: Refresh Duplicate Indicators on Source Tracks
// =============================================================================

function refreshSourceDuplicateIndicators() {
    const sourceItems = document.querySelectorAll('#source-track-list .source-track-item');
    sourceItems.forEach(item => {
        const uri = item.dataset.uri;
        const isDuplicate = workshopState.workingUris.includes(uri);
        const btn = item.querySelector('.add-source-track-btn');
        if (!btn) return;

        if (isDuplicate) {
            btn.className = 'add-source-track-btn ml-2 w-8 h-8 flex items-center justify-center rounded-full bg-yellow-500/30 text-yellow-300 transition duration-150 flex-shrink-0';
            btn.title = 'Already in playlist (click to add duplicate)';
            btn.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>';
        } else {
            btn.className = 'add-source-track-btn ml-2 w-8 h-8 flex items-center justify-center rounded-full bg-white/10 hover:bg-white/20 text-white/70 hover:text-white transition duration-150 flex-shrink-0';
            btn.title = 'Add to playlist';
            btn.innerHTML = '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg>';
        }
    });
}

// =============================================================================
// Helper: Escape HTML to Prevent XSS
// =============================================================================

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
```

**3d. Modify `undoPreview()` to also refresh source duplicate indicators:**

Locate the existing `undoPreview()` function:

```javascript
function undoPreview() {
    workshopState.workingUris = [...workshopState.savedUris];
    rerenderTrackList();
    markDirty();
    showNotification('Reverted to last saved order.', 'success');
}
```

**Replace with:**

```javascript
function undoPreview() {
    workshopState.workingUris = [...workshopState.savedUris];
    rerenderTrackList();
    markDirty();
    updateTrackCount();
    refreshSourceDuplicateIndicators();
    showNotification('Reverted to last saved order.', 'success');
}
```

**3e. Modify `commitToSpotify()` success handler to update `savedUris` AND refresh source indicators:**

Inside the `.then(data => {` block of `commitToSpotify()`, after the line:
```javascript
workshopState.savedUris = [...workshopState.workingUris];
```

Add:
```javascript
refreshSourceDuplicateIndicators();
```

### Step 4: Add CSS for Source Panel

**File:** `/Users/chris/Projects/shuffify/shuffify/templates/workshop.html`

**Where:** Inside the existing `<style>` block at the bottom of the template, after the SortableJS ghost/chosen/drag styles, add:

```css
/* Source panel chevron rotation */
.rotate-180 {
    transform: rotate(180deg);
}

/* Source track items — slightly different bg to distinguish from main list */
.source-track-item {
    background: rgba(255, 255, 255, 0.02);
}
.source-track-item:hover {
    background: rgba(255, 255, 255, 0.06);
}
```

### Step 5: Create Tests

**File:** `/Users/chris/Projects/shuffify/tests/test_workshop_merge.py`

This is a brand-new file. It tests the new `/api/user-playlists` route and verifies the source panel integration points.

**Note on test fixtures:** Phase 1 introduced `authenticated_client` and `sample_user` fixtures in its test file via patching. Since the shared `conftest.py` does not have an `authenticated_client` fixture, this test file follows the same pattern as Phase 1: patching `AuthService.validate_session_token` to return `True` and using the existing `client` and `sample_user` fixtures from `conftest.py`.

```python
"""
Tests for the Workshop source playlist merging feature (Phase 3).

Covers the /api/user-playlists endpoint and the interaction between
source playlists and the existing /playlist/<id> endpoint.
"""

import json
from unittest.mock import patch, Mock

import pytest


# =============================================================================
# Helpers
# =============================================================================


def _sample_playlists():
    """Return a list of sample playlist dicts as returned by PlaylistService."""
    return [
        {
            "id": "playlist_main",
            "name": "Main Playlist",
            "owner": {"id": "user123"},
            "tracks": {"total": 25},
            "images": [{"url": "https://example.com/main.jpg"}],
            "collaborative": False,
        },
        {
            "id": "playlist_source",
            "name": "Source Playlist",
            "owner": {"id": "user123"},
            "tracks": {"total": 10},
            "images": [{"url": "https://example.com/source.jpg"}],
            "collaborative": False,
        },
        {
            "id": "playlist_no_art",
            "name": "No Art Playlist",
            "owner": {"id": "user123"},
            "tracks": {"total": 5},
            "images": [],
            "collaborative": False,
        },
    ]


# =============================================================================
# GET /api/user-playlists Tests
# =============================================================================


class TestApiUserPlaylists:
    """Tests for GET /api/user-playlists."""

    def test_returns_401_when_not_authenticated(self, client):
        """Unauthenticated request should return 401."""
        response = client.get("/api/user-playlists")
        assert response.status_code == 401
        data = response.get_json()
        assert data["success"] is False

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    def test_returns_playlist_list(
        self, mock_playlist_svc, mock_auth_svc, client, sample_token
    ):
        """Should return a JSON list of user playlists with id, name, track_count, image_url."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_ps_instance = Mock()
        mock_ps_instance.get_user_playlists.return_value = _sample_playlists()
        mock_playlist_svc.return_value = mock_ps_instance

        with client.session_transaction() as sess:
            sess["spotify_token"] = sample_token

        response = client.get("/api/user-playlists")
        assert response.status_code == 200

        data = response.get_json()
        assert data["success"] is True
        assert len(data["playlists"]) == 3

        # Verify lightweight format
        first = data["playlists"][0]
        assert "id" in first
        assert "name" in first
        assert "track_count" in first
        assert "image_url" in first

        # Verify the playlist with no art returns None for image_url
        no_art = next(p for p in data["playlists"] if p["id"] == "playlist_no_art")
        assert no_art["image_url"] is None

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    def test_returns_correct_track_count(
        self, mock_playlist_svc, mock_auth_svc, client, sample_token
    ):
        """Track count should be extracted from tracks.total."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_ps_instance = Mock()
        mock_ps_instance.get_user_playlists.return_value = _sample_playlists()
        mock_playlist_svc.return_value = mock_ps_instance

        with client.session_transaction() as sess:
            sess["spotify_token"] = sample_token

        response = client.get("/api/user-playlists")
        data = response.get_json()

        source = next(p for p in data["playlists"] if p["id"] == "playlist_source")
        assert source["track_count"] == 10

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    def test_handles_empty_playlist_list(
        self, mock_playlist_svc, mock_auth_svc, client, sample_token
    ):
        """Should return empty list when user has no playlists."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_ps_instance = Mock()
        mock_ps_instance.get_user_playlists.return_value = []
        mock_playlist_svc.return_value = mock_ps_instance

        with client.session_transaction() as sess:
            sess["spotify_token"] = sample_token

        response = client.get("/api/user-playlists")
        data = response.get_json()
        assert data["success"] is True
        assert data["playlists"] == []

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    def test_handles_service_error(
        self, mock_playlist_svc, mock_auth_svc, client, sample_token
    ):
        """Should return 500 when PlaylistService raises."""
        from shuffify.services import PlaylistError

        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_ps_instance = Mock()
        mock_ps_instance.get_user_playlists.side_effect = PlaylistError("API down")
        mock_playlist_svc.return_value = mock_ps_instance

        with client.session_transaction() as sess:
            sess["spotify_token"] = sample_token

        response = client.get("/api/user-playlists")
        assert response.status_code == 500
        data = response.get_json()
        assert data["success"] is False


# =============================================================================
# Source Playlist Loading Tests (via existing GET /playlist/<id>)
# =============================================================================


class TestSourcePlaylistLoading:
    """
    Tests that verify the existing GET /playlist/<id> endpoint
    works correctly when used for source playlist loading.

    These are integration-confidence tests — the endpoint itself is
    already tested in Phase 1, but we verify the data shape is suitable
    for the source panel JS.
    """

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    def test_playlist_endpoint_returns_tracks_with_required_fields(
        self, mock_playlist_svc, mock_auth_svc, client, sample_token
    ):
        """GET /playlist/<id> must return tracks with uri, name, artists, album_image_url, duration_ms."""
        from shuffify.models.playlist import Playlist

        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_playlist = Playlist(
            id="source_pl",
            name="Source",
            owner_id="user123",
            tracks=[
                {
                    "id": "t1",
                    "name": "Track One",
                    "uri": "spotify:track:t1",
                    "duration_ms": 200000,
                    "is_local": False,
                    "artists": ["Artist A"],
                    "artist_urls": ["https://open.spotify.com/artist/a1"],
                    "album_name": "Album A",
                    "album_image_url": "https://example.com/a1.jpg",
                    "track_url": "https://open.spotify.com/track/t1",
                },
            ],
        )

        mock_ps_instance = Mock()
        mock_ps_instance.get_playlist.return_value = mock_playlist
        mock_playlist_svc.return_value = mock_ps_instance

        with client.session_transaction() as sess:
            sess["spotify_token"] = sample_token

        response = client.get("/playlist/source_pl")
        assert response.status_code == 200

        data = response.get_json()
        assert "tracks" in data
        assert len(data["tracks"]) == 1

        track = data["tracks"][0]
        # These fields are required by the source panel JS
        assert "uri" in track
        assert "name" in track
        assert "artists" in track
        assert "album_image_url" in track
        assert "duration_ms" in track
        assert "id" in track
```

### Step 6: Update CHANGELOG.md

**File:** `/Users/chris/Projects/shuffify/CHANGELOG.md`

**Where:** Under the `## [Unreleased]` section, add or append to `### Added`:

```markdown
### Added
- **Source Playlists Panel** - Collapsible panel in Workshop for cross-playlist track merging
  - Dropdown to select from user's editable playlists (excluding current)
  - Browse source playlist tracks with album art, artist names, and duration
  - Click "+" button to cherry-pick individual tracks into the working playlist
  - Drag tracks from source panel directly into the main track list (SortableJS cross-list groups)
  - Visual duplicate detection: yellow warning icon on tracks already in the working playlist
  - Confirm dialog before adding duplicate tracks
  - New API endpoint: `GET /api/user-playlists` returns lightweight playlist list for AJAX consumers
```

---

## Test Plan

**New tests (file: `tests/test_workshop_merge.py`):**

| Test | What it verifies |
|------|-----------------|
| `test_returns_401_when_not_authenticated` | GET /api/user-playlists rejects unauthenticated requests |
| `test_returns_playlist_list` | Returns JSON with id, name, track_count, image_url for each playlist |
| `test_returns_correct_track_count` | track_count field extracted from tracks.total |
| `test_handles_empty_playlist_list` | Returns empty array when user has no playlists |
| `test_handles_service_error` | Returns 500 JSON error when PlaylistService raises |
| `test_playlist_endpoint_returns_tracks_with_required_fields` | Verifies GET /playlist/<id> data shape has all fields the source panel JS needs |

**Existing tests to verify still pass:** Run `pytest tests/ -v` to confirm no regressions in Phase 1 workshop tests and all existing tests.

**Manual verification steps:**
1. Start dev server with `python run.py`
2. Log in via Spotify OAuth
3. Navigate to any playlist's workshop page
4. Verify "Source Playlists" collapsible toggle appears below the track list
5. Click the toggle -- verify the panel expands showing a dropdown
6. Verify the dropdown populates with user's playlists, EXCLUDING the current one
7. Select a source playlist and click "Load"
8. Verify source tracks appear with album art, artist, duration
9. Click "+" on a track not in the working playlist -- verify it appears at the bottom of the main track list with a "success" notification
10. Verify the "+" icon on that source track changes to yellow warning icon (duplicate indicator)
11. Click "+" on a track that IS already in the playlist -- verify a `confirm()` dialog appears asking whether to add a duplicate
12. Drag a track from the source list into the main track list at a specific position -- verify it inserts correctly
13. After adding tracks, verify "Modified" badge shows and "Save to Spotify" is enabled
14. Click "Save to Spotify" -- verify added tracks persist to Spotify (open in Spotify app to confirm)
15. Click "Undo Changes" -- verify added tracks are removed and track count returns to original
16. Test with an empty source playlist -- verify "This playlist has no tracks" message

---

## Documentation Updates

**CHANGELOG.md** -- Update as described in Step 6 above.

No other documentation files need to be created or modified. The workshop feature documentation is covered by this phase plan document and the Phase 1 plan. If a `documentation/guides/workshop_usage.md` is created in the future, it should be updated to cover source playlists.

---

## Edge Cases

### 1. Source playlist is the same as the current playlist
- The `fetchUserPlaylists()` JS function filters out the current playlist by checking `if (p.id === workshopState.playlistId) return;`.
- If for some reason the filter fails, loading the same playlist as source would show its tracks. Adding from it would create duplicates, which is handled by the deduplication flow.

### 2. Duplicate tracks (same URI already in working list)
- The "+" button shows a yellow warning icon for tracks whose URI exists in `workshopState.workingUris`.
- Clicking the yellow button shows a native `confirm()` dialog. If confirmed, the duplicate is added (Spotify playlists legitimately support duplicates).
- For cross-list drag, a notification informs the user a duplicate was added but does not block the action (dragging is a more deliberate gesture).
- Duplicate indicators refresh after every add, undo, or commit operation.

### 3. Track exists in source but not in `trackDataByUri`
- Tracks added from the source are registered in `trackDataByUri` when they are added to the working list.
- This ensures `rerenderTrackList()` (which uses `trackDataByUri`) can find the track metadata for any newly added track.

### 4. Source panel opened before playlists API response returns
- The dropdown is disabled while loading. The "Load" button is disabled.
- `fetchUserPlaylists()` uses a loading placeholder in the select.

### 5. Network error while fetching source playlist tracks
- The `loadSourcePlaylist()` function has a `.catch()` handler that shows an error notification and returns to the empty state.
- The "Load" button re-enables in the `.finally()` block.

### 6. Very large source playlists (500+ tracks)
- The existing `GET /playlist/<playlist_id>` route returns ALL tracks (pagination is handled by `SpotifyAPI.get_playlist_tracks()`).
- Source tracks are rendered in a scrollable `max-h-[40vh]` container, shorter than the main track list.
- Adding many tracks individually is O(n) per add (DOM append + array push). Not a problem at typical scales.
- If a user wants to add ALL tracks from a source, they should do it track by track or use a future "Add All" button (not in scope for Phase 3).

### 7. Session expiry while source panel is open
- The AJAX calls to `/api/user-playlists` and `/playlist/<id>` return 401 if the session is expired.
- The `.catch()` handlers display the error message. The user can refresh or navigate to re-authenticate.

### 8. User has only one playlist
- If the user has only the current playlist, the dropdown will show only "-- Select a playlist --" with no options.
- The "Load" button will show an info notification: "Please select a playlist first."

### 9. `rerenderTrackList()` after undo removes added tracks from DOM
- When the user clicks "Undo Changes", `workshopState.workingUris` resets to `savedUris`.
- `rerenderTrackList()` moves DOM elements by URI. If a URI was added from a source and is not in `savedUris`, its DOM element is NOT moved (it stays orphaned at the end of the container). However, since `rerenderTrackList()` calls `container.appendChild(el)` for each URI in `workingUris`, only matching elements are re-appended. The orphaned elements remain but are not in the URI array.
- **Fix required:** After `rerenderTrackList()`, we must remove orphaned DOM elements. The Phase 1 `rerenderTrackList()` function needs a small modification for Phase 3. After the `forEach` loop that appends elements, add cleanup:

```javascript
function rerenderTrackList() {
    const container = document.getElementById('track-list');
    const existingElements = {};
    container.querySelectorAll('.track-item').forEach(el => {
        existingElements[el.dataset.uri] = el;
    });

    // Build a set of URIs that should be in the list
    const uriSet = new Set(workshopState.workingUris);

    workshopState.workingUris.forEach((uri, index) => {
        const el = existingElements[uri];
        if (el) {
            container.appendChild(el);
        }
    });

    // Remove orphaned elements (tracks that were added from source but removed via undo)
    container.querySelectorAll('.track-item').forEach(el => {
        if (!workshopState.workingUris.includes(el.dataset.uri)) {
            el.remove();
        }
    });

    renumberTracks();
}
```

**IMPORTANT NOTE about duplicate URIs:** The above cleanup uses `includes()` which returns true for any match. If there are duplicate URIs (same track added twice), we need a count-based approach. However, `rerenderTrackList()` already handles duplicates incorrectly in Phase 1 (it uses a `uri -> element` map which overwrites duplicates). This is a known Phase 1 limitation (documented in Phase 1 edge case 6). Phase 3 does not make this worse. A comprehensive fix for duplicate-URI DOM management would be a separate improvement.

**Practical mitigation:** Duplicate URIs from source playlists are uncommon. Users are warned before adding duplicates. The commit endpoint sends the full URI array including duplicates, which Spotify handles correctly.

---

## Verification Checklist

```bash
# 1. Lint check (REQUIRED)
flake8 shuffify/

# 2. All tests pass (REQUIRED)
pytest tests/ -v

# 3. New merge tests pass specifically
pytest tests/test_workshop_merge.py -v

# 4. Code formatting
black --check shuffify/

# 5. Quick combined check
flake8 shuffify/ && pytest tests/ -v && echo "Ready to push!"
```

Manual checks:
- [ ] Source panel toggle button appears below main track list
- [ ] Source panel is collapsed by default
- [ ] Clicking toggle expands/collapses panel with chevron rotation
- [ ] Dropdown populates with user's playlists on first expand
- [ ] Current playlist is NOT in the dropdown
- [ ] "Load" button fetches and renders source tracks
- [ ] Source tracks show album art, artist, duration
- [ ] "+" button adds track to bottom of main list
- [ ] Track count updates after adding
- [ ] "Modified" badge and "Save to Spotify" button enable after adding
- [ ] Duplicate tracks show yellow warning icon in source panel
- [ ] Clicking duplicate "+" shows confirm dialog
- [ ] Drag from source to main list inserts at drop position
- [ ] Dragging clones (does not remove from source list)
- [ ] "Undo Changes" removes added tracks and restores original order
- [ ] "Save to Spotify" persists added tracks to Spotify
- [ ] Empty source playlist shows appropriate message
- [ ] User with single playlist sees empty dropdown
- [ ] Unauthenticated access to /api/user-playlists returns 401
- [ ] No console errors during all operations

---

## What NOT To Do

1. **Do NOT modify the existing `GET /playlist/<playlist_id>` route.** It already returns the full track data needed by the source panel. Reuse it as-is.

2. **Do NOT modify the `POST /refresh-playlists` route.** The new `/api/user-playlists` endpoint is a separate, read-only GET endpoint. Do not change the existing POST endpoint's behavior.

3. **Do NOT add the source panel to the RIGHT sidebar.** Phase 2 adds search to the sidebar. The source panel goes below the main track list in the left `lg:col-span-2` column to avoid merge conflicts.

4. **Do NOT make source track additions write to Spotify immediately.** All additions are client-side until "Save to Spotify" is clicked. This is the core workshop staging principle.

5. **Do NOT silently block duplicate track additions.** Spotify playlists legitimately support duplicates. Show a warning and let the user decide via `confirm()`.

6. **Do NOT use `innerHTML` to rebuild the main track list after adding source tracks.** Use `appendChild` to add new elements and preserve existing SortableJS bindings and event listeners.

7. **Do NOT load the playlists dropdown on page load.** Lazy-load on first panel expansion to avoid unnecessary API calls when users never open the source panel.

8. **Do NOT store source playlist state in the Flask session.** Source browsing is entirely client-side. Only the final committed track order is persisted server-side.

9. **Do NOT allow dropping tracks from the main list INTO the source list.** The source list has `put: false` in its SortableJS group config. It is read-only from a drop perspective.

10. **Do NOT forget the `escapeHtml()` helper for dynamically rendered track names and artist names.** Source track data comes from the Spotify API and is rendered via `innerHTML`. Track names could theoretically contain HTML-like characters. Always escape.

11. **Do NOT remove the `event.stopPropagation()` from any Phase 1 click handlers in the dashboard.** This is unchanged from Phase 1 but is a reminder since Phase 3 touches the same codebase.

12. **Do NOT add a "Add All Tracks" button.** This is tempting but out of scope. Cherry-picking and drag-and-drop are the Phase 3 interaction patterns. Bulk operations can be added in a future enhancement.

---

### Critical Files for Implementation
- `/Users/chris/Projects/shuffify/shuffify/routes.py` - Add `GET /api/user-playlists` route (the only backend change)
- `/Users/chris/Projects/shuffify/shuffify/templates/workshop.html` - Add source panel HTML, SortableJS group config, all JS functions for source loading/adding/drag
- `/Users/chris/Projects/shuffify/tests/test_workshop_merge.py` - New test file covering the API endpoint and data shape validation
- `/Users/chris/Projects/shuffify/shuffify/services/playlist_service.py` - Existing service used by the new route (reference, not modified)
- `/Users/chris/Projects/shuffify/documentation/planning/phases/playlist-workshop_2026-02-10/01_workshop-core.md` - Phase 1 plan (reference for understanding workshop state model and DOM patterns)