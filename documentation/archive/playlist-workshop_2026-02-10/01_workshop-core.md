# Phase 1: Playlist Workshop (Core)

**PR Title:** `feat: Add Playlist Workshop page with drag-and-drop reordering and shuffle preview`

**Risk Level:** Medium — introduces new routes and a new template, but does not modify existing shuffle or state logic. The commit endpoint writes to Spotify, so it is a production-affecting change.

**Estimated Effort:** 3-4 days for a mid-level engineer, 5-6 days for a junior engineer.

**Files Created:**
- `shuffify/templates/workshop.html` — New workshop template
- `tests/test_workshop.py` — New test file for workshop routes

**Files Modified:**
- `shuffify/routes.py` — Add 3 new routes (workshop page, preview-shuffle, commit)
- `shuffify/templates/dashboard.html` — Add "Open Workshop" button to each playlist card
- `shuffify/schemas/requests.py` — Add `WorkshopCommitRequest` schema
- `shuffify/schemas/__init__.py` — Export new schema
- `tests/conftest.py` — Add `authenticated_client` fixture
- `CHANGELOG.md` — Add entry under `[Unreleased]`

**Files Deleted:** None

---

## Context

Currently, Shuffify users interact with playlists entirely through the dashboard grid. They pick a shuffle algorithm and click "Shuffle" — the change goes directly to Spotify. Users never see individual tracks, cannot manually reorder them, and cannot preview what a shuffle will look like before committing.

The Workshop fills this gap by providing a dedicated `/workshop/<playlist_id>` page where:
1. All tracks load into a visible, scrollable list with album art, artist names, and duration.
2. Users can drag and drop to manually reorder tracks.
3. Users can apply a shuffle algorithm as a **preview** (client-side only) without touching Spotify.
4. Only when the user clicks "Save to Spotify" does the new order commit to the API.

This is the foundational feature that Phases 2-6 build upon.

---

## Dependencies

**Prerequisites (none — this is Phase 1):**
- All existing code is stable. The `GET /playlist/<playlist_id>` endpoint already returns full track data including name, artists, album art URL, duration_ms, and URI.

**What this unlocks:**
- Phase 2: Track Management (search & delete within workshop)
- Phase 3: Playlist Merging (source panel for cross-playlist operations)
- Phase 4: External Playlist Raiding (load any public playlist as source)
- Phase 5: User Database (persist workshop sessions)
- Phase 6: Scheduled Operations (automate workshop operations)

---

## Detailed Implementation Plan

### Step 1: Add `WorkshopCommitRequest` Pydantic Schema

**File:** `shuffify/schemas/requests.py`

**Where:** After the `PlaylistQueryParams` class (after line 153), add a new schema class for validating the commit request body.

**Code to add at the end of the file (after line 197):**

```python
class WorkshopCommitRequest(BaseModel):
    """Schema for committing workshop changes to Spotify."""

    track_uris: List[str] = Field(
        ..., min_length=0, description="Ordered list of track URIs to save"
    )

    @field_validator("track_uris")
    @classmethod
    def validate_track_uris(cls, v: List[str]) -> List[str]:
        """Ensure all URIs look like Spotify track URIs."""
        for uri in v:
            if not uri.startswith("spotify:track:"):
                raise ValueError(f"Invalid track URI format: {uri}")
        return v
```

**Import change needed at the top of the same file:** Add `List` to the typing imports on line 7. The current import is:

```python
from typing import Literal, Annotated, Any, Dict
```

Change to:

```python
from typing import Literal, Annotated, Any, Dict, List
```

### Step 2: Export the New Schema

**File:** `shuffify/schemas/__init__.py`

**Current state (line 9-18):**
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

**Change to:**
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
    parse_shuffle_request,
)
```

**Also add to the `__all__` list:** Add `"WorkshopCommitRequest",` after `"PlaylistQueryParams",`.

### Step 3: Add Workshop Routes to `routes.py`

**File:** `shuffify/routes.py`

**3a. Update imports (line 31):**

Current:
```python
from shuffify.schemas import parse_shuffle_request, PlaylistQueryParams
```

Change to:
```python
from shuffify.schemas import parse_shuffle_request, PlaylistQueryParams, WorkshopCommitRequest
```

**Also add a `ValidationError` import** (check if `pydantic` is already imported; if not, add near the top of the file):
```python
from pydantic import ValidationError
```

**3b. Add three new routes at the end of the file (after line 407).** Insert a new section comment and the three routes:

```python
# =============================================================================
# Workshop Routes
# =============================================================================


@main.route("/workshop/<playlist_id>")
def workshop(playlist_id):
    """Render the Playlist Workshop page."""
    if not is_authenticated():
        return redirect(url_for("main.index"))

    try:
        client = AuthService.get_authenticated_client(session["spotify_token"])
        user = AuthService.get_user_data(client)

        playlist_service = PlaylistService(client)
        playlist = playlist_service.get_playlist(playlist_id, include_features=False)

        algorithms = ShuffleService.list_algorithms()

        logger.info(
            f"User {user.get('display_name', 'Unknown')} opened workshop for "
            f"playlist '{playlist.name}' ({len(playlist)} tracks)"
        )

        return render_template(
            "workshop.html",
            playlist=playlist.to_dict(),
            user=user,
            algorithms=algorithms,
        )

    except (AuthenticationError, PlaylistError) as e:
        logger.error(f"Error loading workshop: {e}")
        return clear_session_and_show_login(
            "Your session has expired. Please log in again."
        )


@main.route("/workshop/<playlist_id>/preview-shuffle", methods=["POST"])
def workshop_preview_shuffle(playlist_id):
    """
    Run a shuffle algorithm on client-provided tracks and return the new order
    WITHOUT saving to Spotify or fetching from the API.

    Expects JSON body: { "algorithm": "BasicShuffle", "tracks": [...], ... algorithm params }
    Returns JSON: { "success": true, "shuffled_uris": [...] }
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Parse and validate via existing Pydantic schema
    shuffle_request = parse_shuffle_request(data)

    # Get algorithm instance
    algorithm = ShuffleService.get_algorithm(shuffle_request.algorithm)

    # Get validated parameters for this algorithm
    params = shuffle_request.get_algorithm_params()

    # Use tracks from client — no Spotify API call needed
    tracks = data.get("tracks")
    if not tracks or not isinstance(tracks, list):
        return json_error("Request must include 'tracks' array.", 400)

    # Execute shuffle (does NOT update Spotify)
    shuffled_uris = ShuffleService.execute(
        shuffle_request.algorithm, tracks, params
    )

    logger.info(
        f"Preview shuffle for playlist {playlist_id} with {shuffle_request.algorithm}"
    )

    return jsonify({
        "success": True,
        "shuffled_uris": shuffled_uris,
        "algorithm_name": algorithm.name,
    })


@main.route("/workshop/<playlist_id>/commit", methods=["POST"])
def workshop_commit(playlist_id):
    """
    Save the workshop's staged track order to Spotify.

    Expects JSON body: { "track_uris": ["spotify:track:...", ...] }
    """
    client = require_auth()
    if not client:
        return json_error("Please log in first.", 401)

    data = request.get_json()
    if not data:
        return json_error("Request body must be JSON.", 400)

    # Validate with Pydantic
    try:
        commit_request = WorkshopCommitRequest(**data)
    except ValidationError as e:
        return json_error(f"Invalid request: {e.error_count()} validation error(s).", 400)

    # Get current track URIs from Spotify for state tracking
    playlist_service = PlaylistService(client)
    playlist = playlist_service.get_playlist(playlist_id, include_features=False)
    current_uris = [track["uri"] for track in playlist.tracks]

    # Initialize state if needed
    StateService.ensure_playlist_initialized(session, playlist_id, current_uris)

    # Check if order actually changed
    if not ShuffleService.shuffle_changed_order(current_uris, commit_request.track_uris):
        return json_success("No changes to save — track order is unchanged.")

    # Update Spotify
    playlist_service.update_playlist_tracks(playlist_id, commit_request.track_uris)

    # Record new state for undo
    updated_state = StateService.record_new_state(
        session, playlist_id, commit_request.track_uris
    )

    logger.info(
        f"Workshop commit for playlist {playlist_id}: "
        f"{len(commit_request.track_uris)} tracks saved"
    )

    return json_success(
        "Playlist saved to Spotify!",
        playlist_state=updated_state.to_dict(),
    )
```

### Step 4: Create the Workshop Template

**File:** `shuffify/templates/workshop.html`

This is a brand-new file. It extends `base.html` and contains:
- A header bar with playlist name, track count, and navigation back to dashboard
- A track list rendered from the `playlist.tracks` data passed by the route
- SortableJS integration for drag-and-drop
- A shuffle controls panel (algorithm selector + "Preview Shuffle" button)
- A prominent "Save to Spotify" button
- An "Undo Preview" button to revert to the last known order
- Client-side state management in JavaScript

**Full template content:**

```html
{% extends "base.html" %}

{% block title %}Workshop: {{ playlist.name }} - Shuffify{% endblock %}

{% block content %}
<div class="min-h-screen bg-gradient-to-br from-spotify-green via-spotify-green/90 to-spotify-dark">
    <div class="absolute inset-0" style="background-image: url('/static/images/hero-pattern.svg'); opacity: 0.15; pointer-events: none;"></div>

    <!-- Workshop Header -->
    <div class="relative max-w-5xl mx-auto px-4 pt-8">
        <div class="p-6 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20">
            <div class="flex items-center justify-between flex-wrap gap-4">
                <div class="flex items-center min-w-0">
                    <a href="{{ url_for('main.index') }}"
                       class="mr-4 p-2 rounded-lg bg-white/10 hover:bg-white/20 transition duration-150 border border-white/20 flex-shrink-0"
                       title="Back to Dashboard">
                        <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path>
                        </svg>
                    </a>
                    <div class="min-w-0">
                        <h1 class="text-2xl font-bold text-white truncate">{{ playlist.name }}</h1>
                        <p class="text-white/70 text-sm">
                            <span id="track-count">{{ playlist.tracks|length }}</span> tracks
                            <span id="modified-badge" class="ml-2 px-2 py-0.5 rounded-full bg-yellow-500/80 text-xs font-semibold hidden">Modified</span>
                        </p>
                    </div>
                </div>
                <div class="flex items-center space-x-2 flex-shrink-0">
                    <button id="undo-preview-btn"
                            onclick="undoPreview()"
                            class="hidden inline-flex items-center px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white font-medium transition duration-150 border border-white/20"
                            title="Revert to last saved order">
                        <svg class="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h10a5 5 0 015 5v2M3 10l4-4M3 10l4 4"></path>
                        </svg>
                        Undo Changes
                    </button>
                    <button id="save-btn"
                            onclick="commitToSpotify()"
                            disabled
                            class="inline-flex items-center px-6 py-2 rounded-lg bg-white text-spotify-dark font-bold transition duration-150 hover:bg-green-100 disabled:opacity-40 disabled:cursor-not-allowed shadow-lg"
                            title="Save current order to Spotify">
                        <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path>
                        </svg>
                        Save to Spotify
                    </button>
                </div>
            </div>
        </div>
    </div>

    <!-- Main Workshop Area -->
    <div class="relative max-w-5xl mx-auto px-4 py-6">
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">

            <!-- Track List (2/3 width on large screens) -->
            <div class="lg:col-span-2">
                <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 overflow-hidden">
                    <!-- Track List Header -->
                    <div class="px-4 py-3 border-b border-white/10 flex items-center text-white/60 text-xs uppercase tracking-wide font-semibold">
                        <span class="w-10 text-center">#</span>
                        <span class="w-10"></span>
                        <span class="flex-1 ml-3">Title</span>
                        <span class="w-24 text-right hidden sm:block">Duration</span>
                        <span class="w-8"></span>
                    </div>

                    <!-- Scrollable Track List -->
                    <div id="track-list" class="max-h-[65vh] overflow-y-auto workshop-scrollbar">
                        <!-- Empty state -->
                        {% if not playlist.tracks %}
                        <div class="p-8 text-center text-white/60">
                            <p class="text-lg">This playlist has no tracks.</p>
                            <a href="{{ url_for('main.index') }}" class="text-white underline mt-2 inline-block">Back to Dashboard</a>
                        </div>
                        {% endif %}

                        {% for track in playlist.tracks %}
                        <div class="track-item flex items-center px-4 py-2 hover:bg-white/5 transition duration-150 border-b border-white/5 cursor-grab active:cursor-grabbing"
                             data-uri="{{ track.uri }}"
                             data-track-id="{{ track.id }}">
                            <!-- Track Number -->
                            <span class="track-number w-10 text-center text-white/50 text-sm font-mono">{{ loop.index }}</span>

                            <!-- Album Art -->
                            <div class="w-10 h-10 rounded overflow-hidden flex-shrink-0 bg-black/20">
                                {% if track.album_image_url %}
                                <img src="{{ track.album_image_url }}" alt="" class="w-full h-full object-cover" loading="lazy">
                                {% else %}
                                <div class="w-full h-full flex items-center justify-center text-white/30">
                                    <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55C7.79 13 6 14.79 6 17s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
                                </div>
                                {% endif %}
                            </div>

                            <!-- Track Info -->
                            <div class="ml-3 flex-1 min-w-0">
                                <p class="text-white text-sm font-medium truncate">{{ track.name }}</p>
                                <p class="text-white/50 text-xs truncate">{{ track.artists | join(', ') }}</p>
                            </div>

                            <!-- Duration -->
                            <span class="w-24 text-right text-white/50 text-sm hidden sm:block">
                                {{ '%d:%02d' | format(track.duration_ms // 60000, (track.duration_ms % 60000) // 1000) }}
                            </span>

                            <!-- Drag Handle -->
                            <span class="drag-handle w-8 text-center text-white/30 hover:text-white/60 cursor-grab active:cursor-grabbing ml-2">
                                <svg class="w-5 h-5 mx-auto" fill="currentColor" viewBox="0 0 24 24">
                                    <path d="M8 6h2v2H8V6zm6 0h2v2h-2V6zM8 11h2v2H8v-2zm6 0h2v2h-2v-2zm-6 5h2v2H8v-2zm6 0h2v2h-2v-2z"/>
                                </svg>
                            </span>
                        </div>
                        {% endfor %}
                    </div>
                </div>
            </div>

            <!-- Sidebar Controls (1/3 width on large screens) -->
            <div class="lg:col-span-1 space-y-6">

                <!-- Shuffle Controls Panel -->
                <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-5">
                    <h2 class="text-white font-bold text-lg mb-4">Shuffle Preview</h2>
                    <p class="text-white/60 text-sm mb-4">Preview a shuffle without affecting Spotify. Only saved when you click "Save to Spotify".</p>

                    <!-- Algorithm Selection -->
                    <div class="mb-4">
                        <label for="workshop-algorithm" class="block text-sm font-medium text-white/90 mb-2">
                            Algorithm:
                        </label>
                        <select id="workshop-algorithm"
                                name="algorithm"
                                class="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white focus:ring-2 focus:ring-white/30 focus:border-transparent">
                            {% for algo in algorithms %}
                            <option value="{{ algo.class_name }}"
                                    data-description="{{ algo.description }}"
                                    data-parameters='{{ algo.parameters | tojson }}'>
                                {{ algo.name }}
                            </option>
                            {% endfor %}
                        </select>
                        <p id="algorithm-description" class="mt-1 text-sm text-white/60">
                            {{ algorithms[0].description if algorithms else '' }}
                        </p>
                    </div>

                    <!-- Dynamic Algorithm Parameters -->
                    <div id="workshop-algorithm-params" class="space-y-4 mb-4">
                        <!-- Parameters dynamically inserted here -->
                    </div>

                    <!-- Preview Shuffle Button -->
                    <button id="preview-shuffle-btn"
                            onclick="previewShuffle()"
                            class="w-full px-4 py-2 rounded-lg bg-white/20 hover:bg-white/30 text-white font-semibold transition duration-150"
                            {% if not playlist.tracks %}disabled{% endif %}>
                        Preview Shuffle
                    </button>
                </div>

                <!-- Playlist Info Panel -->
                <div class="rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 p-5">
                    <h2 class="text-white font-bold text-lg mb-3">Playlist Info</h2>
                    <dl class="space-y-2 text-sm">
                        <div class="flex justify-between">
                            <dt class="text-white/60">Tracks</dt>
                            <dd class="text-white font-medium" id="info-track-count">{{ playlist.tracks | length }}</dd>
                        </div>
                        {% if playlist.description %}
                        <div>
                            <dt class="text-white/60 mb-1">Description</dt>
                            <dd class="text-white/80 text-xs">{{ playlist.description }}</dd>
                        </div>
                        {% endif %}
                    </dl>
                </div>
            </div>
        </div>
    </div>
</div>

<!-- SortableJS via CDN -->
<script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js"></script>

<script>
// =============================================================================
// Workshop State Management
// =============================================================================

const workshopState = {
    playlistId: {{ playlist.id | tojson }},
    savedUris: {{ playlist.tracks | map(attribute='uri') | list | tojson }},
    workingUris: {{ playlist.tracks | map(attribute='uri') | list | tojson }},
    isDirty: false,
    isSaving: false,
    isShuffling: false,
};

const trackDataByUri = {};
{% for track in playlist.tracks %}
trackDataByUri[{{ track.uri | tojson }}] = {
    id: {{ track.id | tojson }},
    name: {{ track.name | tojson }},
    artists: {{ track.artists | tojson }},
    album_name: {{ (track.album_name or '') | tojson }},
    album_image_url: {{ (track.album_image_url or '') | tojson }},
    duration_ms: {{ track.duration_ms | tojson }},
    uri: {{ track.uri | tojson }},
};
{% endfor %}


// =============================================================================
// SortableJS Initialization
// =============================================================================

let sortableInstance = null;

document.addEventListener('DOMContentLoaded', () => {
    const trackList = document.getElementById('track-list');
    if (!trackList || trackList.children.length === 0) return;

    sortableInstance = Sortable.create(trackList, {
        animation: 200,
        handle: '.drag-handle',
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        dragClass: 'sortable-drag',
        onEnd: function(evt) {
            updateWorkingUrisFromDOM();
            markDirty();
        },
    });

    updateWorkshopAlgorithmParams();
    document.getElementById('workshop-algorithm').addEventListener('change', updateWorkshopAlgorithmParams);
});


// =============================================================================
// DOM / State Sync
// =============================================================================

function updateWorkingUrisFromDOM() {
    const items = document.querySelectorAll('#track-list .track-item');
    workshopState.workingUris = Array.from(items).map(el => el.dataset.uri);
    renumberTracks();
}

function renumberTracks() {
    const items = document.querySelectorAll('#track-list .track-item .track-number');
    items.forEach((el, i) => { el.textContent = i + 1; });
}

function markDirty() {
    const changed = !arraysEqual(workshopState.savedUris, workshopState.workingUris);
    workshopState.isDirty = changed;

    const saveBtn = document.getElementById('save-btn');
    const undoBtn = document.getElementById('undo-preview-btn');
    const badge = document.getElementById('modified-badge');

    saveBtn.disabled = !changed || workshopState.isSaving;
    undoBtn.classList.toggle('hidden', !changed);
    badge.classList.toggle('hidden', !changed);
}

function arraysEqual(a, b) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
        if (a[i] !== b[i]) return false;
    }
    return true;
}


// =============================================================================
// Shuffle Preview
// =============================================================================

function previewShuffle() {
    if (workshopState.isShuffling) return;

    const select = document.getElementById('workshop-algorithm');
    const algorithmName = select.value;

    const paramsContainer = document.getElementById('workshop-algorithm-params');
    const paramInputs = paramsContainer.querySelectorAll('input, select');

    // Send full track objects so the server can shuffle without an API call
    const tracks = workshopState.workingUris.map(uri => trackDataByUri[uri]);
    const body = {
        algorithm: algorithmName,
        tracks: tracks,
    };
    paramInputs.forEach(input => {
        body[input.name] = input.value;
    });

    const btn = document.getElementById('preview-shuffle-btn');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Shuffling...';
    workshopState.isShuffling = true;

    fetch(`/workshop/${workshopState.playlistId}/preview-shuffle`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify(body),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => { throw new Error(data.message || 'Shuffle preview failed.'); });
        }
        return response.json();
    })
    .then(data => {
        if (data.success && data.shuffled_uris) {
            workshopState.workingUris = data.shuffled_uris;
            rerenderTrackList();
            markDirty();
            showNotification(`Preview applied: ${data.algorithm_name}`, 'success');
        } else {
            showNotification(data.message || 'Shuffle did not change order.', 'info');
        }
    })
    .catch(error => {
        console.error('Preview shuffle error:', error);
        showNotification(error.message || 'Failed to preview shuffle.', 'error');
    })
    .finally(() => {
        btn.disabled = false;
        btn.textContent = originalText;
        workshopState.isShuffling = false;
    });
}


// =============================================================================
// Commit to Spotify
// =============================================================================

function commitToSpotify() {
    if (workshopState.isSaving || !workshopState.isDirty) return;

    const btn = document.getElementById('save-btn');
    const originalHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `
        <svg class="w-5 h-5 mr-2 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
        </svg>
        Saving...
    `;
    workshopState.isSaving = true;

    fetch(`/workshop/${workshopState.playlistId}/commit`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({ track_uris: workshopState.workingUris }),
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => { throw new Error(data.message || 'Save failed.'); });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            workshopState.savedUris = [...workshopState.workingUris];
            markDirty();
            showNotification(data.message || 'Saved to Spotify!', 'success');
        } else {
            showNotification(data.message || 'Save returned unexpected response.', 'error');
        }
    })
    .catch(error => {
        console.error('Commit error:', error);
        showNotification(error.message || 'Failed to save to Spotify. Please try again.', 'error');
    })
    .finally(() => {
        btn.disabled = !workshopState.isDirty;
        btn.innerHTML = originalHTML;
        workshopState.isSaving = false;
    });
}


// =============================================================================
// Undo Preview (revert to last saved order)
// =============================================================================

function undoPreview() {
    workshopState.workingUris = [...workshopState.savedUris];
    rerenderTrackList();
    markDirty();
    showNotification('Reverted to last saved order.', 'success');
}


// =============================================================================
// Track List Rendering (after shuffle preview reorders URIs)
// =============================================================================

function rerenderTrackList() {
    const container = document.getElementById('track-list');
    const existingElements = {};
    container.querySelectorAll('.track-item').forEach(el => {
        existingElements[el.dataset.uri] = el;
    });

    workshopState.workingUris.forEach((uri, index) => {
        const el = existingElements[uri];
        if (el) {
            container.appendChild(el);
        }
    });

    renumberTracks();
}


// =============================================================================
// Algorithm Parameter UI (mirrors dashboard.html pattern)
// =============================================================================

function updateWorkshopAlgorithmParams() {
    const select = document.getElementById('workshop-algorithm');
    const paramsContainer = document.getElementById('workshop-algorithm-params');
    const selectedOption = select.options[select.selectedIndex];
    const parameters = JSON.parse(selectedOption.dataset.parameters);
    const description = selectedOption.dataset.description;

    document.getElementById('algorithm-description').textContent = description;
    paramsContainer.innerHTML = '';

    for (const [paramName, paramInfo] of Object.entries(parameters)) {
        const paramDiv = document.createElement('div');
        paramDiv.className = 'space-y-1';

        const label = document.createElement('label');
        label.className = 'block text-sm font-medium text-white/90';
        label.textContent = paramInfo.description;
        paramDiv.appendChild(label);

        if (paramInfo.type === 'string' && paramInfo.options) {
            const sel = document.createElement('select');
            sel.name = paramName;
            sel.className = 'w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white focus:ring-2 focus:ring-white/30 focus:border-transparent';
            for (const option of paramInfo.options) {
                const opt = document.createElement('option');
                opt.value = option;
                opt.textContent = option;
                if (option === paramInfo.default) opt.selected = true;
                sel.appendChild(opt);
            }
            paramDiv.appendChild(sel);
        } else {
            const input = document.createElement('input');
            input.type = paramInfo.type === 'float' ? 'number' :
                         paramInfo.type === 'integer' ? 'number' : 'text';
            input.name = paramName;
            input.className = 'w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white focus:ring-2 focus:ring-white/30 focus:border-transparent';
            input.value = paramInfo.default;
            if (paramInfo.min !== undefined) input.min = paramInfo.min;
            if (paramInfo.max !== undefined) input.max = paramInfo.max;
            if (paramInfo.type === 'float') input.step = '0.1';
            paramDiv.appendChild(input);
        }

        paramsContainer.appendChild(paramDiv);
    }
}


// =============================================================================
// Notifications
// =============================================================================

function showNotification(message, type) {
    const notification = document.createElement('div');
    const bgColor = type === 'success' ? 'bg-green-500/90' :
                    type === 'info' ? 'bg-blue-500/90' : 'bg-red-500/90';
    notification.className = `fixed bottom-4 right-4 px-6 py-3 rounded-lg shadow-lg backdrop-blur-md ${bgColor} text-white font-semibold transform transition duration-300 translate-y-16 opacity-0 z-50`;
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
        notification.classList.remove('translate-y-16', 'opacity-0');
    }, 100);

    setTimeout(() => {
        notification.classList.add('translate-y-16', 'opacity-0');
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}
</script>

<style>
.workshop-scrollbar::-webkit-scrollbar { width: 8px; }
.workshop-scrollbar::-webkit-scrollbar-thumb { background: rgba(20, 120, 60, 0.5); border-radius: 6px; }
.workshop-scrollbar::-webkit-scrollbar-track { background: transparent; }
.workshop-scrollbar { scrollbar-color: rgba(20, 120, 60, 0.5) transparent; scrollbar-width: thin; }

.sortable-ghost { opacity: 0.4; background: rgba(29, 185, 84, 0.2) !important; border-radius: 8px; }
.sortable-chosen { background: rgba(255, 255, 255, 0.08) !important; }
.sortable-drag { background: rgba(29, 185, 84, 0.3) !important; border-radius: 8px; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3); }
</style>
{% endblock %}
```

### Step 5: Add "Open Workshop" Button to Dashboard

**File:** `shuffify/templates/dashboard.html`

**Where:** Inside the playlist card info bar (lines 67-80). Currently shows playlist name, track count, and Spotify link.

**Current code (lines 67-80):**
```html
<div class="bg-spotify-green px-4 py-3 flex items-center justify-between">
    <div>
        <h3 class="text-white text-xl font-bold truncate">{{ playlist.name }}</h3>
        <p class="text-white/80 text-sm">{{ playlist.tracks.total }} tracks</p>
    </div>
    <a href="{{ playlist.external_urls.spotify }}"
       target="_blank"
       rel="noopener noreferrer"
       class="bg-black/50 rounded-full p-2 ml-2 transform transition-all duration-300 hover:scale-110 hover:bg-spotify-green">
        <svg class="w-6 h-6 text-white" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
        </svg>
    </a>
</div>
```

**Replace with:**
```html
<div class="bg-spotify-green px-4 py-3 flex items-center justify-between">
    <div>
        <h3 class="text-white text-xl font-bold truncate">{{ playlist.name }}</h3>
        <p class="text-white/80 text-sm">{{ playlist.tracks.total }} tracks</p>
    </div>
    <div class="flex items-center space-x-2 ml-2">
        <a href="{{ url_for('main.workshop', playlist_id=playlist.id) }}"
           class="inline-flex items-center px-3 py-1.5 rounded-lg bg-white/20 hover:bg-white/30 text-white text-sm font-semibold transition duration-150 border border-white/20"
           title="Open Playlist Workshop"
           onclick="event.stopPropagation();">
            <svg class="w-4 h-4 mr-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"></path>
            </svg>
            Workshop
        </a>
        <a href="{{ playlist.external_urls.spotify }}"
           target="_blank"
           rel="noopener noreferrer"
           class="bg-black/50 rounded-full p-2 transform transition-all duration-300 hover:scale-110 hover:bg-spotify-green"
           onclick="event.stopPropagation();">
            <svg class="w-6 h-6 text-white" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
            </svg>
        </a>
    </div>
</div>
```

**Key change:** The Spotify link anchor gets `onclick="event.stopPropagation();"` added, the Workshop link also gets it, and both are wrapped in a flex container. The `event.stopPropagation()` is critical because the card-tile click handler toggles the shuffle menu — without it, clicking "Workshop" would also toggle the menu.

### Step 6a: Add `authenticated_client` fixture to conftest.py

**File:** `tests/conftest.py`

The workshop tests depend on an `authenticated_client` fixture that provides a Flask test client with a valid session token. This fixture does not currently exist. Add it after the existing `client` fixture:

```python
@pytest.fixture
def authenticated_client(app):
    """Flask test client with a valid session token pre-set."""
    with app.test_client() as test_client:
        with test_client.session_transaction() as sess:
            sess["spotify_token"] = {
                "access_token": "test_access_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "test_refresh_token",
            }
        yield test_client
```

### Step 6b: Create Workshop Tests

**File:** `tests/test_workshop.py`

```python
"""
Tests for the Playlist Workshop routes.

Covers the workshop page render, preview-shuffle endpoint,
and commit endpoint.
"""

import json
from unittest.mock import patch, Mock

from shuffify.models.playlist import Playlist


# =============================================================================
# Fixtures
# =============================================================================

def _make_mock_playlist():
    """A Playlist model instance for workshop tests."""
    return Playlist(
        id="playlist123",
        name="Workshop Test Playlist",
        owner_id="user123",
        description="A test playlist",
        tracks=[
            {
                "id": f"track{i}",
                "name": f"Track {i}",
                "uri": f"spotify:track:track{i}",
                "duration_ms": 180000 + (i * 1000),
                "is_local": False,
                "artists": [f"Artist {i}"],
                "artist_urls": [f"https://open.spotify.com/artist/artist{i}"],
                "album_name": f"Album {i}",
                "album_image_url": f"https://example.com/album{i}.jpg",
                "track_url": f"https://open.spotify.com/track/track{i}",
            }
            for i in range(1, 6)
        ],
    )


# =============================================================================
# Workshop Page Route Tests
# =============================================================================


class TestWorkshopPage:
    """Tests for GET /workshop/<playlist_id>."""

    def test_workshop_redirects_when_not_authenticated(self, client):
        """Unauthenticated users should be redirected to index."""
        response = client.get("/workshop/playlist123")
        assert response.status_code in (302, 200)

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    @patch("shuffify.routes.ShuffleService")
    def test_workshop_renders_with_valid_playlist(
        self, mock_shuffle_svc, mock_playlist_svc, mock_auth_svc,
        authenticated_client, sample_user
    ):
        """Workshop page should render successfully with playlist data."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()
        mock_auth_svc.get_user_data.return_value = sample_user

        mock_ps_instance = Mock()
        mock_ps_instance.get_playlist.return_value = _make_mock_playlist()
        mock_playlist_svc.return_value = mock_ps_instance

        mock_shuffle_svc.list_algorithms.return_value = [
            {"name": "Basic", "class_name": "BasicShuffle",
             "description": "Random shuffle", "parameters": {}}
        ]

        response = authenticated_client.get("/workshop/playlist123")
        assert response.status_code == 200
        assert b"Workshop Test Playlist" in response.data
        assert b"Track 1" in response.data
        assert b"Save to Spotify" in response.data


# =============================================================================
# Preview Shuffle Route Tests
# =============================================================================


class TestWorkshopPreviewShuffle:
    """Tests for POST /workshop/<playlist_id>/preview-shuffle."""

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.ShuffleService")
    def test_preview_shuffle_returns_shuffled_uris(
        self, mock_shuffle_svc, mock_auth_svc,
        authenticated_client
    ):
        """Preview shuffle should return reordered URIs without saving or fetching from API."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_playlist = _make_mock_playlist()
        shuffled = [f"spotify:track:track{i}" for i in [3, 1, 5, 2, 4]]
        mock_shuffle_svc.execute.return_value = shuffled
        mock_shuffle_svc.get_algorithm.return_value = Mock(name="Basic")

        # Send tracks from client (no API call)
        response = authenticated_client.post(
            "/workshop/playlist123/preview-shuffle",
            data=json.dumps({
                "algorithm": "BasicShuffle",
                "tracks": mock_playlist.tracks,
            }),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert data["shuffled_uris"] == shuffled

    def test_preview_shuffle_requires_auth(self, client):
        """Unauthenticated preview should return 401."""
        response = client.post(
            "/workshop/playlist123/preview-shuffle",
            data=json.dumps({"algorithm": "BasicShuffle"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @patch("shuffify.routes.AuthService")
    def test_preview_shuffle_requires_json_body(
        self, mock_auth_svc, authenticated_client
    ):
        """Preview without JSON body should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/playlist123/preview-shuffle",
            data="not json",
            content_type="text/plain",
        )
        assert response.status_code == 400


# =============================================================================
# Commit Route Tests
# =============================================================================


class TestWorkshopCommit:
    """Tests for POST /workshop/<playlist_id>/commit."""

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    @patch("shuffify.routes.ShuffleService")
    @patch("shuffify.routes.StateService")
    def test_commit_saves_to_spotify(
        self, mock_state_svc, mock_shuffle_svc, mock_playlist_svc, mock_auth_svc,
        authenticated_client
    ):
        """Commit should call update_playlist_tracks on Spotify."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_playlist = _make_mock_playlist()
        mock_ps_instance = Mock()
        mock_ps_instance.get_playlist.return_value = mock_playlist
        mock_ps_instance.update_playlist_tracks.return_value = True
        mock_playlist_svc.return_value = mock_ps_instance

        mock_shuffle_svc.shuffle_changed_order.return_value = True

        mock_state_svc.ensure_playlist_initialized.return_value = Mock()
        mock_state_svc.record_new_state.return_value = Mock(
            to_dict=lambda: {"states": [], "current_index": 1}
        )

        new_uris = [f"spotify:track:track{i}" for i in [5, 4, 3, 2, 1]]
        response = authenticated_client.post(
            "/workshop/playlist123/commit",
            data=json.dumps({"track_uris": new_uris}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_ps_instance.update_playlist_tracks.assert_called_once_with(
            "playlist123", new_uris
        )

    @patch("shuffify.routes.AuthService")
    @patch("shuffify.routes.PlaylistService")
    @patch("shuffify.routes.ShuffleService")
    @patch("shuffify.routes.StateService")
    def test_commit_unchanged_order_returns_no_op(
        self, mock_state_svc, mock_shuffle_svc, mock_playlist_svc, mock_auth_svc,
        authenticated_client
    ):
        """Committing an unchanged order should return success without API call."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        mock_playlist = _make_mock_playlist()
        mock_ps_instance = Mock()
        mock_ps_instance.get_playlist.return_value = mock_playlist
        mock_playlist_svc.return_value = mock_ps_instance

        mock_shuffle_svc.shuffle_changed_order.return_value = False
        mock_state_svc.ensure_playlist_initialized.return_value = Mock()

        same_uris = [f"spotify:track:track{i}" for i in range(1, 6)]
        response = authenticated_client.post(
            "/workshop/playlist123/commit",
            data=json.dumps({"track_uris": same_uris}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        mock_ps_instance.update_playlist_tracks.assert_not_called()

    def test_commit_requires_auth(self, client):
        """Unauthenticated commit should return 401."""
        response = client.post(
            "/workshop/playlist123/commit",
            data=json.dumps({"track_uris": ["spotify:track:x"]}),
            content_type="application/json",
        )
        assert response.status_code == 401

    @patch("shuffify.routes.AuthService")
    def test_commit_validates_uri_format(
        self, mock_auth_svc, authenticated_client
    ):
        """Commit with invalid URI format should return 400."""
        mock_auth_svc.validate_session_token.return_value = True
        mock_auth_svc.get_authenticated_client.return_value = Mock()

        response = authenticated_client.post(
            "/workshop/playlist123/commit",
            data=json.dumps({"track_uris": ["not-a-valid-uri"]}),
            content_type="application/json",
        )
        assert response.status_code == 400
```

---

## Test Plan

**New tests (file: `tests/test_workshop.py`):**

| Test | What it verifies |
|------|-----------------|
| `test_workshop_redirects_when_not_authenticated` | Unauthenticated GET /workshop/x redirects |
| `test_workshop_renders_with_valid_playlist` | Page renders with playlist data, track names visible |
| `test_preview_shuffle_returns_shuffled_uris` | Preview shuffles client-provided tracks, no Spotify API call |
| `test_preview_shuffle_requires_auth` | 401 when not authenticated |
| `test_preview_shuffle_requires_json_body` | 400 when body is not JSON |
| `test_commit_saves_to_spotify` | Commit calls update_playlist_tracks and records state |
| `test_commit_unchanged_order_returns_no_op` | No Spotify API call when order unchanged |
| `test_commit_requires_auth` | 401 when not authenticated |
| `test_commit_validates_uri_format` | 400 when URIs are malformed |

**Existing tests to verify still pass:** Run `pytest tests/ -v` to verify no regressions.

**Manual verification steps:**
1. Start dev server with `python run.py`
2. Log in via Spotify OAuth
3. On the dashboard, verify each playlist card has a "Workshop" button
4. Click "Workshop" — verify the workshop page loads with all tracks visible
5. Drag a track to a new position — verify "Modified" badge appears and "Save to Spotify" enables
6. Click "Undo Changes" — verify the track snaps back and the button disables
7. Select a shuffle algorithm and click "Preview Shuffle" — verify tracks reorder visually
8. Click "Save to Spotify" — verify toast notification, then open playlist in Spotify to confirm
9. Navigate back to dashboard — verify dashboard still works correctly

---

## Documentation Updates

**CHANGELOG.md** — Add under `## [Unreleased]` / `### Added`:

```markdown
- **Playlist Workshop** - Dedicated `/workshop/<playlist_id>` page for interactive track management
  - Visual track list with album art thumbnails, artist names, and duration
  - Drag-and-drop reordering via SortableJS
  - Shuffle preview runs algorithm on client-provided tracks without any Spotify API call
  - "Save to Spotify" button commits staged changes with state tracking for undo
  - "Undo Changes" reverts to last saved order before committing
  - Dashboard playlist cards now include "Workshop" button for quick access
  - New routes: `GET /workshop/<id>`, `POST /workshop/<id>/preview-shuffle`, `POST /workshop/<id>/commit`
  - Pydantic validation for commit request (`WorkshopCommitRequest` schema)
```

---

## Edge Cases

### 1. Empty playlists (0 tracks)
- The workshop template handles this with an empty state message and a "Back to Dashboard" link
- The "Preview Shuffle" button is disabled when there are no tracks
- The commit endpoint receives an empty `track_uris` array; `shuffle_changed_order` returns `False`, so no API call is made

### 2. Very large playlists (500+ tracks)
- `get_playlist_tracks()` already paginates through all tracks (handles up to Spotify's 10,000-track limit)
- All tracks rendered server-side in Jinja; for 500+ tracks the HTML is ~200-300KB — acceptable
- SortableJS handles large lists well (DOM manipulation, not virtual DOM); minor jank possible at 1000+
- `max-h-[65vh]` with `overflow-y-auto` ensures scrollability
- `rerenderTrackList()` re-uses existing DOM elements (moves with `appendChild`) — performant for large lists
- Commit sends all URIs as JSON array; ~25KB for 500 tracks — well within POST body limits

### 3. Network errors during commit
- `commitToSpotify()` has a `.catch()` handler showing error notification
- Button re-enables after failure (`.finally()` block), allowing retry
- `savedUris` only updated on success, so dirty state is preserved on failure
- Backend `SpotifyAPI.update_playlist_tracks` has `@api_error_handler` with exponential backoff retry

### 4. Session expiry mid-workshop
- Next API call returns 401 from `require_auth()`
- JS `.catch()` handler displays the error message
- User can refresh or navigate to dashboard to re-authenticate
- Local JS state (unsaved changes) is lost on page reload — acceptable for Phase 1

### 5. Playlist modified externally while in workshop
- Workshop's `savedUris` will be stale
- On commit, backend fetches current Spotify state for undo system initialization
- Commit replaces all tracks with new order — tracks added externally between page load and commit will be lost
- Known limitation for Phase 1

### 6. Duplicate track URIs
- Spotify playlists can contain the same track multiple times
- Tracks identified by DOM position (SortableJS moves by index, not ID)
- `data-uri` may have duplicates but array order is the source of truth
- Works correctly because commit sends full ordered URI array including duplicates

---

## Verification Checklist

```bash
# 1. Lint check (REQUIRED)
flake8 shuffify/

# 2. All tests pass (REQUIRED)
pytest tests/ -v

# 3. New workshop tests pass specifically
pytest tests/test_workshop.py -v

# 4. Code formatting
black --check shuffify/

# 5. Quick combined check
flake8 shuffify/ && pytest tests/ -v && echo "Ready to push!"
```

Manual checks:
- [ ] Workshop page loads for a real playlist
- [ ] All tracks visible with album art, artist names, and duration
- [ ] Drag-and-drop reorders tracks
- [ ] "Modified" badge appears after reordering
- [ ] "Undo Changes" reverts the order
- [ ] "Preview Shuffle" reorders tracks visually
- [ ] "Save to Spotify" commits the order (verify in Spotify app)
- [ ] Dashboard "Workshop" button links correctly
- [ ] Dashboard card click-to-toggle still works (no regression)
- [ ] Empty playlist shows empty state
- [ ] Unauthenticated access redirects properly

---

## What NOT To Do

1. **Do NOT modify existing shuffle/undo routes.** The workshop routes are additive. The existing `/shuffle/<playlist_id>` and `/undo/<playlist_id>` routes must remain unchanged for dashboard backward compatibility.

2. **Do NOT store working state in the Flask session.** The workshop's "working copy" lives entirely in client-side JavaScript. Only the committed state gets recorded in the session (via `StateService.record_new_state`). Storing intermediate states in the session would bloat Redis and cause race conditions.

3. **Do NOT make the preview-shuffle endpoint modify Spotify.** The entire point of the workshop is that changes are staged locally. The preview endpoint must never call `update_playlist_tracks`.

4. **Do NOT add a build step for SortableJS.** Load it from CDN. The project has no build process, and adding one is out of scope.

5. **Do NOT use `id` attributes on track items for reordering.** Playlists can have duplicate tracks (same URI appearing multiple times). Use DOM order as the source of truth, not element IDs.

6. **Do NOT remove the `event.stopPropagation()` from the Workshop link in dashboard.** The card-tile has a click handler that toggles the shuffle menu. Without stopPropagation, clicking Workshop would also toggle the menu.

7. **Do NOT add `session.modified = True` in the workshop page route.** The page render route is read-only. Only the commit route should modify session state.

8. **Do NOT hardcode algorithm names in the workshop JavaScript.** Algorithms are passed from the backend via the `algorithms` template variable and read dynamically from `<option>` data attributes.

9. **Do NOT skip the Pydantic validation on the commit endpoint.** The `WorkshopCommitRequest` schema validates URI format. Without it, malformed URIs could reach the Spotify API.

10. **Do NOT use `innerHTML` to rebuild the track list after shuffle preview.** `rerenderTrackList()` moves existing DOM nodes. Recreating with `innerHTML` would destroy event listeners and SortableJS bindings.
