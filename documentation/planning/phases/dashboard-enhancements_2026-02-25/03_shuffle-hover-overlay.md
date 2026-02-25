# Phase 03: Redesign Shuffle Interaction as Hover Overlay with Icons

**Status:** ✅ COMPLETE
**Started:** 2026-02-25
**Completed:** 2026-02-25

## Header

| Field | Value |
|---|---|
| **PR Title** | Redesign shuffle as hover overlay with algorithm icons on playlist tiles |
| **Risk Level** | High — complete rewrite of tile interaction model, JS event handling, and CSS |
| **Estimated Effort** | High (6-8 hours) |
| **Files Modified** | 1 (`shuffify/templates/dashboard.html`) |
| **Files Created** | 1 (`tests/templates/test_dashboard_overlay.py`) |
| **Files Deleted** | 0 |

---

## Context

The current dashboard playlist tile interaction requires clicking a tile to expand a panel below the info bar. This panel contains a dropdown menu for algorithm selection, dynamic parameter inputs, a Shuffle button, and an Undo button. The expansion uses a `max-height` / `opacity` CSS transition with a 500ms cubic-bezier animation.

The user wants a fundamentally different interaction: hovering over a playlist tile causes a semi-transparent overlay to fade in directly on top of the album artwork (the `h-48` div). The overlay presents algorithm choices as individual icon buttons in a grid rather than a dropdown. Workshop moves from the info bar to the overlay. The info bar simplifies to just name, track count, and Spotify link.

This phase completely rewrites the tile structure in `dashboard.html`, replacing approximately 100 lines of tile HTML and 100 lines of JavaScript with new markup and event handling.

**Why this matters:** The current click-to-expand pattern hides shuffle options behind an opaque interaction. Users must click, scan a dropdown, configure parameters, and then click Shuffle. The hover overlay with icon buttons reduces this to: hover, click the algorithm icon. For algorithms with non-trivial parameters, a gear icon provides access. This is a significant UX improvement for the core action of the application.

---

## Dependencies

- **Phase 02** (Tile Layout Fix) should be completed first. Phase 02 adds `min-w-0` to the left div of the info bar at line 270. This phase rewrites the entire tile structure and will incorporate that fix directly in the new markup. If Phase 02 is not complete, this phase's new markup must include the `min-w-0` fix (which it does in the plan below).

---

## Detailed Implementation Plan

### Step 1: Understand the Current Algorithm Metadata

The template receives `algorithms` from `core.py:99` as a list of dicts from `ShuffleService.list_algorithms()`. Each dict has:

```python
{
    "name": "Basic",           # Display name
    "class_name": "BasicShuffle",  # Value to send in form
    "description": "Randomly shuffle...",
    "parameters": {            # Dict of param_name -> param_info
        "keep_first": {
            "type": "integer",
            "description": "Number of tracks to keep at start",
            "default": 0,
            "min": 0
        }
    }
}
```

The 6 visible algorithms and their parameter complexity:

| Algorithm | class_name | Has params beyond defaults? | Params |
|-----------|------------|---------------------------|--------|
| Basic | BasicShuffle | `keep_first` only | `keep_first` (int, default 0) |
| Percentage | PercentageShuffle | Yes — 2 params | `shuffle_percentage` (float, default 50), `shuffle_location` (string, front/back) |
| Balanced | BalancedShuffle | Yes — 2 params | `keep_first` (int), `section_count` (int, default 4) |
| Stratified | StratifiedShuffle | Yes — 2 params | `keep_first` (int), `section_count` (int, default 5) |
| Artist Spacing | ArtistSpacingShuffle | 1 param | `min_spacing` (int, default 1) |
| Album Sequence | AlbumSequenceShuffle | 1 param | `shuffle_within_albums` (string, no/yes) |

**Design decision:** Every algorithm has at least one parameter. For the icon-based approach, clicking an icon will trigger a shuffle with **default parameters** for a one-click experience. A small gear/settings icon on each algorithm button will open a parameter popover for customization. This preserves full functionality while making the common case (default shuffle) a single click.

### Step 2: Define SVG Icons for Each Algorithm

Each algorithm needs a distinct, recognizable icon. These will be inline SVGs (consistent with the project's existing pattern of inline SVGs throughout `dashboard.html` and `base.html`).

The icon assignments and their SVG paths:

```html
<!-- BasicShuffle: crossing arrows -->
<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M8 7h12m0 0l-4-4m4 4l-4 4m-8 6H4m0 0l4-4m-4 4l4 4"/>
</svg>

<!-- PercentageShuffle: percentage -->
<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M19 5L5 19M6.5 9a2.5 2.5 0 100-5 2.5 2.5 0 000 5zm11 11a2.5 2.5 0 100-5 2.5 2.5 0 000 5z"/>
</svg>

<!-- BalancedShuffle: scale/balance -->
<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M12 3v18m-7-4l3-8h8l3 8M5 17h4m6 0h4"/>
</svg>

<!-- StratifiedShuffle: layers/stack -->
<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M4 6h16M4 10h16M4 14h16M4 18h16"/>
</svg>

<!-- ArtistSpacingShuffle: users/people -->
<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"/>
</svg>

<!-- AlbumSequenceShuffle: disc/album -->
<svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M12 2a10 10 0 100 20 10 10 0 000-20zm0 7a3 3 0 100 6 3 3 0 000-6z"/>
</svg>
```

### Step 3: Rewrite the Tile HTML Structure

Replace lines 256-349 of `shuffify/templates/dashboard.html` with the new tile structure. The current tile structure is:

```
.card-tile (rounded-2xl container)
  ├── .relative.h-48 (artwork area)
  │    └── <img> or placeholder
  ├── .bg-spotify-green (info bar)
  │    ├── <div> name + track count
  │    └── <div> Workshop button + Spotify link
  └── .shuffle-menu (hidden expand panel)
       ├── <form> algorithm select + params + shuffle button
       └── <form> undo button
```

The new structure will be:

```
.card-tile (rounded-2xl container)
  ├── .relative.h-48 (artwork area — now has overlay)
  │    ├── <img> or placeholder
  │    └── .shuffle-overlay (absolute inset-0, hidden by default)
  │         ├── .overlay-top: "keep first N" stepper
  │         ├── .overlay-center: 3x2 grid of algorithm icon buttons
  │         ├── .overlay-bottom: Workshop button + Undo button
  │         └── Hidden <form> for AJAX submission
  └── .bg-spotify-green (info bar — simplified)
       ├── <div class="min-w-0"> name + track count
       └── <div> Spotify link only
```

**Here is the exact new HTML for the tile, replacing lines 255-349:**

```html
{% for playlist in playlists %}
    <div class="rounded-2xl shadow-xl bg-spotify-green/90 border border-white/20 overflow-hidden transform transition duration-300 hover:scale-105 hover:shadow-2xl relative card-tile"
         data-playlist-id="{{ playlist.id }}">
        <!-- Playlist Artwork with Hover Overlay -->
        <div class="relative h-48 group">
            {% if playlist.images %}
                <img src="{{ playlist.images[0].url }}" alt="{{ playlist.name }}"
                     class="w-full h-full object-cover rounded-t-2xl">
            {% else %}
                <div class="w-full h-full bg-black/20 flex items-center justify-center rounded-t-2xl">
                    <span class="text-4xl">&#127925;</span>
                </div>
            {% endif %}

            <!-- Shuffle Overlay (appears on hover/tap) -->
            <div class="shuffle-overlay absolute inset-0 bg-black/70 backdrop-blur-sm flex flex-col justify-between p-3 rounded-t-2xl opacity-0 pointer-events-none transition-opacity duration-200"
                 aria-label="Shuffle options for {{ playlist.name }}">

                <!-- Top: Keep First N Stepper -->
                <div class="flex items-center justify-center space-x-2">
                    <label class="text-white/80 text-xs font-medium" for="keep-first-{{ playlist.id }}">Keep first</label>
                    <button type="button"
                            class="keep-first-decrement w-6 h-6 rounded bg-white/20 hover:bg-white/30 text-white text-sm font-bold flex items-center justify-center transition"
                            data-playlist-id="{{ playlist.id }}"
                            aria-label="Decrease tracks to keep">-</button>
                    <input type="number"
                           id="keep-first-{{ playlist.id }}"
                           class="keep-first-input w-10 h-6 text-center text-white text-sm bg-white/10 border border-white/20 rounded focus:ring-1 focus:ring-white/40"
                           value="0" min="0" max="{{ playlist.tracks.total }}"
                           data-playlist-id="{{ playlist.id }}">
                    <button type="button"
                            class="keep-first-increment w-6 h-6 rounded bg-white/20 hover:bg-white/30 text-white text-sm font-bold flex items-center justify-center transition"
                            data-playlist-id="{{ playlist.id }}"
                            aria-label="Increase tracks to keep">+</button>
                </div>

                <!-- Center: Algorithm Icon Grid (3x2) -->
                <div class="grid grid-cols-3 gap-2 my-2">
                    {% for algo in algorithms %}
                    <div class="relative algo-button-wrapper">
                        <button type="button"
                                class="algo-icon-btn w-full aspect-square rounded-lg bg-white/15 hover:bg-white/30 border border-white/20 hover:border-white/40 flex flex-col items-center justify-center transition duration-150 group/algo"
                                data-algorithm="{{ algo.class_name }}"
                                data-playlist-id="{{ playlist.id }}"
                                data-parameters='{{ algo.parameters|tojson }}'
                                data-algo-name="{{ algo.name }}"
                                title="{{ algo.name }}: {{ algo.description }}"
                                aria-label="Shuffle with {{ algo.name }} algorithm">
                            <!-- Algorithm Icon -->
                            <span class="algo-icon">
                                {% if algo.class_name == 'BasicShuffle' %}
                                <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m-8 6H4m0 0l4-4m-4 4l4 4"/>
                                </svg>
                                {% elif algo.class_name == 'PercentageShuffle' %}
                                <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 5L5 19M6.5 9a2.5 2.5 0 100-5 2.5 2.5 0 000 5zm11 11a2.5 2.5 0 100-5 2.5 2.5 0 000 5z"/>
                                </svg>
                                {% elif algo.class_name == 'BalancedShuffle' %}
                                <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v18m-7-4l3-8h8l3 8M5 17h4m6 0h4"/>
                                </svg>
                                {% elif algo.class_name == 'StratifiedShuffle' %}
                                <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 10h16M4 14h16M4 18h16"/>
                                </svg>
                                {% elif algo.class_name == 'ArtistSpacingShuffle' %}
                                <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"/>
                                </svg>
                                {% elif algo.class_name == 'AlbumSequenceShuffle' %}
                                <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 2a10 10 0 100 20 10 10 0 000-20zm0 7a3 3 0 100 6 3 3 0 000-6z"/>
                                </svg>
                                {% endif %}
                            </span>
                            <!-- Algorithm Name (tiny label) -->
                            <span class="text-white/70 text-[10px] mt-0.5 leading-tight text-center">{{ algo.name }}</span>
                        </button>
                        <!-- Gear icon for algorithms with configurable params -->
                        {% if algo.parameters|length > 1 or (algo.parameters|length == 1 and 'keep_first' not in algo.parameters) %}
                        <button type="button"
                                class="algo-settings-btn absolute -top-1 -right-1 w-5 h-5 rounded-full bg-white/30 hover:bg-white/50 flex items-center justify-center transition z-10"
                                data-algorithm="{{ algo.class_name }}"
                                data-playlist-id="{{ playlist.id }}"
                                data-parameters='{{ algo.parameters|tojson }}'
                                title="Configure {{ algo.name }} settings"
                                aria-label="Settings for {{ algo.name }}">
                            <svg class="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/>
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
                            </svg>
                        </button>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>

                <!-- Bottom: Workshop + Undo -->
                <div class="flex items-center space-x-2">
                    <a href="{{ url_for('main.workshop', playlist_id=playlist.id) }}"
                       class="flex-1 inline-flex items-center justify-center px-3 py-1.5 rounded-lg bg-spotify-green/80 hover:bg-spotify-green text-white text-sm font-semibold transition duration-150 border border-white/20"
                       title="Open Playlist Workshop"
                       onclick="event.stopPropagation();">
                        <svg class="w-4 h-4 mr-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                        </svg>
                        Workshop
                    </a>
                    <button type="button"
                            id="undo-overlay-btn-{{ playlist.id }}"
                            class="undo-overlay-btn hidden flex-shrink-0 px-3 py-1.5 rounded-lg bg-black/40 hover:bg-black/60 text-white/90 text-sm font-semibold transition duration-150 border border-white/10"
                            data-playlist-id="{{ playlist.id }}"
                            title="Undo last shuffle">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h10a5 5 0 010 10H9m-6-10l4-4m-4 4l4 4"/>
                        </svg>
                    </button>
                </div>
            </div>

            <!-- Algorithm Parameter Popover (hidden, positioned absolute) -->
            <div class="algo-params-popover hidden absolute inset-x-3 top-1/2 -translate-y-1/2 bg-black/90 backdrop-blur-md rounded-lg border border-white/20 p-3 z-20 shadow-xl"
                 data-playlist-id="{{ playlist.id }}">
                <div class="flex items-center justify-between mb-2">
                    <h4 class="algo-popover-title text-white text-sm font-bold"></h4>
                    <button type="button" class="algo-popover-close text-white/60 hover:text-white transition">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
                        </svg>
                    </button>
                </div>
                <div class="algo-popover-params space-y-2">
                    <!-- Dynamic params rendered by JS -->
                </div>
                <button type="button"
                        class="algo-popover-shuffle w-full mt-2 px-3 py-1.5 rounded-lg bg-spotify-green hover:bg-spotify-green/80 text-white text-sm font-bold transition"
                        data-playlist-id="{{ playlist.id }}">
                    Shuffle with Settings
                </button>
            </div>

            <!-- Hidden form for AJAX shuffle submission -->
            <form class="shuffle-form hidden"
                  action="{{ url_for('main.shuffle', playlist_id=playlist.id) }}"
                  method="POST"
                  data-playlist-id="{{ playlist.id }}">
                <input type="hidden" name="algorithm" class="shuffle-algorithm-input" value="BasicShuffle">
            </form>

            <!-- Hidden form for AJAX undo submission -->
            <form class="undo-form hidden"
                  id="undo-form-{{ playlist.id }}"
                  action="{{ url_for('main.undo', playlist_id=playlist.id) }}"
                  method="POST"
                  data-playlist-id="{{ playlist.id }}">
            </form>
        </div>

        <!-- Playlist Info Bar (simplified: name, count, Spotify link) -->
        <div class="bg-spotify-green px-4 py-3 flex items-center justify-between">
            <div class="min-w-0">
                <h3 class="text-white text-xl font-bold truncate">{{ playlist.name }}</h3>
                <p class="text-white/80 text-sm">{{ playlist.tracks.total }} tracks</p>
            </div>
            <div class="flex items-center ml-2">
                <a href="{{ playlist.external_urls.spotify }}"
                   target="_blank"
                   rel="noopener noreferrer"
                   class="bg-black/50 rounded-full p-2 transform transition-all duration-300 hover:scale-110 hover:bg-spotify-green"
                   onclick="event.stopPropagation();"
                   aria-label="Open {{ playlist.name }} on Spotify">
                    <svg class="w-6 h-6 text-white" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.601.18-1.2.72-1.381 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.299.421-1.02.599-1.559.3z"/>
                    </svg>
                </a>
            </div>
        </div>
    </div>
{% endfor %}
```

**Key differences from current code:**
1. Lines 256-349 are fully replaced. The `.shuffle-menu` div is gone.
2. The info bar loses the Workshop button. It now has just name/count (left, with `min-w-0` from Phase 02) and Spotify link (right).
3. The artwork `div.relative.h-48` gains the `group` class and contains the new `.shuffle-overlay` div.
4. Forms are hidden inside the artwork area, used only for AJAX submission.
5. The `keep_first` stepper is in the overlay top area.
6. Algorithm icons are in a 3x2 grid in the overlay center.
7. Workshop and Undo buttons are at the overlay bottom.
8. A parameter popover element is positioned absolutely within the artwork area.

### Step 4: Rewrite the CSS

Replace lines 568-601 of `dashboard.html` (the `<style>` block). Remove the old `.shuffle-menu` and `.menu-open .shuffle-menu` rules. Add overlay transition rules.

**Remove these CSS rules entirely (lines 577-601):**
```css
.shuffle-scrollbar::-webkit-scrollbar { ... }
.shuffle-scrollbar::-webkit-scrollbar-thumb { ... }
.shuffle-scrollbar::-webkit-scrollbar-track { ... }
.shuffle-scrollbar { ... }
.menu-open .shuffle-menu { ... }
.shuffle-menu { ... }
```

**Add these new CSS rules:**

```css
/* Shuffle overlay: fade in on hover (desktop) or active state (mobile) */
.card-tile:hover .shuffle-overlay,
.card-tile.overlay-active .shuffle-overlay {
    opacity: 1 !important;
    pointer-events: auto !important;
}

/* Smooth opacity transition for overlay */
.shuffle-overlay {
    transition: opacity 200ms ease-in-out;
}

/* Algorithm icon button hover effect */
.algo-icon-btn:hover {
    transform: scale(1.05);
}
.algo-icon-btn:active {
    transform: scale(0.95);
}

/* Loading state for algorithm buttons */
.algo-icon-btn.shuffling {
    opacity: 0.5;
    pointer-events: none;
}
.algo-icon-btn.shuffling .algo-icon {
    animation: spin 1s linear infinite;
}

@keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
}

/* Popover slide-in */
.algo-params-popover {
    transition: opacity 150ms ease, transform 150ms ease;
}
.algo-params-popover.hidden {
    opacity: 0;
    transform: translate(0, -50%) scale(0.95);
}

/* Keep first stepper: hide spin buttons on number input */
.keep-first-input::-webkit-outer-spin-button,
.keep-first-input::-webkit-inner-spin-button {
    -webkit-appearance: none;
    margin: 0;
}
.keep-first-input[type=number] {
    -moz-appearance: textfield;
}

/* Accessibility: respect reduced motion */
@media (prefers-reduced-motion: reduce) {
    .shuffle-overlay {
        transition: none !important;
    }
    .algo-icon-btn {
        transform: none !important;
    }
    .algo-icon-btn.shuffling .algo-icon {
        animation: none !important;
    }
}
```

The dashboard scrollbar CSS (lines 570-573) and `.rotate-180` (line 575) should be **kept** as they are used by the activity feed section.

### Step 5: Rewrite the JavaScript

Replace all JavaScript in the `<script>` block (lines 356-565) except `toggleActivityFeed()` (lines 358-367) and `refreshPlaylists()` (lines 496-528). The following functions are **removed**:

- `updateAlgorithmParams()` (lines 369-434) — replaced by popover logic
- `handlePlaylistAction()` (lines 436-494) — replaced by new overlay handler
- The `DOMContentLoaded` handler (lines 531-564) — replaced entirely

The following functions are **kept as-is**:
- `toggleActivityFeed()` (lines 358-367)
- `refreshPlaylists()` (lines 496-528)

**New JavaScript to add** (after the kept functions):

```javascript
/**
 * Shuffle Overlay Interaction System
 *
 * Handles: overlay visibility on mobile (tap-to-toggle),
 * algorithm icon click-to-shuffle, keep-first stepper,
 * algorithm parameter popover, undo button.
 */

/* --- Keep First Stepper --- */
function getKeepFirstValue(playlistId) {
    const input = document.querySelector(`#keep-first-${playlistId}`);
    return input ? parseInt(input.value, 10) || 0 : 0;
}

function setKeepFirstValue(playlistId, value) {
    const input = document.querySelector(`#keep-first-${playlistId}`);
    if (input) {
        const max = parseInt(input.max, 10) || 999;
        input.value = Math.max(0, Math.min(value, max));
    }
}

/* --- Algorithm Shuffle via AJAX --- */
function shuffleWithAlgorithm(playlistId, algorithmName, extraParams) {
    const form = document.querySelector(
        `.shuffle-form[data-playlist-id="${playlistId}"]`
    );
    if (!form) return;

    // Set algorithm
    form.querySelector('.shuffle-algorithm-input').value = algorithmName;

    // Remove any previously added dynamic hidden inputs
    form.querySelectorAll('.dynamic-param').forEach(el => el.remove());

    // Add keep_first (always included for algorithms that accept it)
    const keepFirst = getKeepFirstValue(playlistId);
    if (keepFirst > 0) {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'keep_first';
        input.value = keepFirst;
        input.className = 'dynamic-param';
        form.appendChild(input);
    }

    // Add extra params from popover (if any)
    if (extraParams) {
        for (const [key, value] of Object.entries(extraParams)) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = key;
            input.value = value;
            input.className = 'dynamic-param';
            form.appendChild(input);
        }
    }

    // Find the clicked algorithm button and show loading state
    const algoBtn = document.querySelector(
        `.algo-icon-btn[data-algorithm="${algorithmName}"][data-playlist-id="${playlistId}"]`
    );
    if (algoBtn) {
        algoBtn.classList.add('shuffling');
    }

    // Disable all algo buttons for this playlist during request
    document.querySelectorAll(
        `.algo-icon-btn[data-playlist-id="${playlistId}"]`
    ).forEach(btn => btn.disabled = true);

    // Submit via AJAX
    fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(response => {
        if (!response.ok) {
            return response.json().catch(() => {
                throw new Error(`Server responded with ${response.status}: ${response.statusText}`);
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            showNotification(data.message, 'success');
            // Update undo button visibility
            if (data.playlist_state) {
                const undoBtn = document.getElementById(`undo-overlay-btn-${playlistId}`);
                const canUndo = data.playlist_state.current_index > 0;
                if (undoBtn) {
                    undoBtn.classList.toggle('hidden', !canUndo);
                }
            }
        } else {
            showNotification(data.message || 'Shuffle did not change the order.', data.category || 'info');
        }
    })
    .catch(error => {
        console.error('Shuffle error:', error);
        showNotification(error.message || 'An error occurred. Please try again.', 'error');
    })
    .finally(() => {
        if (algoBtn) {
            algoBtn.classList.remove('shuffling');
        }
        document.querySelectorAll(
            `.algo-icon-btn[data-playlist-id="${playlistId}"]`
        ).forEach(btn => btn.disabled = false);
    });
}

/* --- Undo via AJAX --- */
function undoShuffle(playlistId) {
    const form = document.getElementById(`undo-form-${playlistId}`);
    if (!form) return;

    const undoBtn = document.getElementById(`undo-overlay-btn-${playlistId}`);
    if (undoBtn) {
        undoBtn.disabled = true;
        undoBtn.classList.add('opacity-50');
    }

    fetch(form.action, {
        method: 'POST',
        body: new FormData(form),
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(response => {
        if (!response.ok) {
            return response.json().catch(() => {
                throw new Error(`Server responded with ${response.status}`);
            });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            showNotification(data.message, 'success');
            if (data.playlist_state) {
                const canUndo = data.playlist_state.current_index > 0;
                if (undoBtn) {
                    undoBtn.classList.toggle('hidden', !canUndo);
                }
            }
        } else {
            showNotification(data.message || 'Undo failed.', 'error');
        }
    })
    .catch(error => {
        console.error('Undo error:', error);
        showNotification(error.message || 'Undo failed. Please try again.', 'error');
    })
    .finally(() => {
        if (undoBtn) {
            undoBtn.disabled = false;
            undoBtn.classList.remove('opacity-50');
        }
    });
}

/* --- Algorithm Parameters Popover --- */
function openAlgoPopover(playlistId, algorithmName, parameters) {
    const popover = document.querySelector(
        `.algo-params-popover[data-playlist-id="${playlistId}"]`
    );
    if (!popover) return;

    // Set title
    const algoBtn = document.querySelector(
        `.algo-icon-btn[data-algorithm="${algorithmName}"][data-playlist-id="${playlistId}"]`
    );
    const algoName = algoBtn ? algoBtn.dataset.algoName : algorithmName;
    popover.querySelector('.algo-popover-title').textContent = `${algoName} Settings`;

    // Build parameter form fields
    const paramsContainer = popover.querySelector('.algo-popover-params');
    paramsContainer.innerHTML = '';

    for (const [paramName, paramInfo] of Object.entries(parameters)) {
        // Skip keep_first — handled by the stepper at the top of the overlay
        if (paramName === 'keep_first') continue;

        const wrapper = document.createElement('div');

        const label = document.createElement('label');
        label.className = 'block text-white/80 text-xs font-medium mb-0.5';
        label.textContent = paramInfo.description;
        wrapper.appendChild(label);

        if (paramInfo.type === 'string' && paramInfo.options) {
            const select = document.createElement('select');
            select.name = paramName;
            select.className = 'w-full px-2 py-1 bg-white/10 border border-white/20 rounded text-white text-sm focus:ring-1 focus:ring-white/40';
            for (const option of paramInfo.options) {
                const opt = document.createElement('option');
                opt.value = option;
                opt.textContent = option;
                if (option === paramInfo.default) opt.selected = true;
                select.appendChild(opt);
            }
            wrapper.appendChild(select);
        } else {
            const input = document.createElement('input');
            input.type = paramInfo.type === 'float' ? 'number' :
                         paramInfo.type === 'integer' ? 'number' : 'text';
            input.name = paramName;
            input.value = paramInfo.default;
            input.className = 'w-full px-2 py-1 bg-white/10 border border-white/20 rounded text-white text-sm focus:ring-1 focus:ring-white/40';
            if (paramInfo.min !== undefined) input.min = paramInfo.min;
            if (paramInfo.max !== undefined) input.max = paramInfo.max;
            if (paramInfo.type === 'float') input.step = '0.1';
            wrapper.appendChild(input);
        }

        paramsContainer.appendChild(wrapper);
    }

    // Store current algorithm on the shuffle button
    popover.querySelector('.algo-popover-shuffle').dataset.algorithm = algorithmName;

    // Show popover
    popover.classList.remove('hidden');
}

function closeAlgoPopover(playlistId) {
    const popover = document.querySelector(
        `.algo-params-popover[data-playlist-id="${playlistId}"]`
    );
    if (popover) {
        popover.classList.add('hidden');
    }
}

/* --- Event Delegation Setup --- */
document.addEventListener('DOMContentLoaded', () => {
    // Mobile tap-to-toggle: on touch devices, tap the artwork area to toggle overlay
    document.querySelectorAll('.card-tile').forEach(tile => {
        const overlay = tile.querySelector('.shuffle-overlay');
        if (!overlay) return;

        tile.addEventListener('click', (e) => {
            // If the click is on an interactive element inside the overlay, let it through
            if (e.target.closest('.algo-icon-btn, .algo-settings-btn, .algo-popover-close, .algo-popover-shuffle, .undo-overlay-btn, a, button, input, select')) {
                return;
            }
            // If click is on the Spotify link in the info bar, let it through
            if (e.target.closest("a[href*='spotify.com']")) return;

            // Toggle overlay-active for mobile/touch
            const isActive = tile.classList.contains('overlay-active');

            // Close all other active overlays
            document.querySelectorAll('.card-tile.overlay-active').forEach(other => {
                if (other !== tile) {
                    other.classList.remove('overlay-active');
                    closeAlgoPopover(other.dataset.playlistId);
                }
            });

            tile.classList.toggle('overlay-active', !isActive);
            if (isActive) {
                closeAlgoPopover(tile.dataset.playlistId);
            }
        });
    });

    // Algorithm icon click: trigger shuffle with default params
    document.querySelectorAll('.algo-icon-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const playlistId = btn.dataset.playlistId;
            const algorithm = btn.dataset.algorithm;
            closeAlgoPopover(playlistId);
            shuffleWithAlgorithm(playlistId, algorithm, null);
        });
    });

    // Gear/settings icon click: open parameter popover
    document.querySelectorAll('.algo-settings-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const playlistId = btn.dataset.playlistId;
            const algorithm = btn.dataset.algorithm;
            const parameters = JSON.parse(btn.dataset.parameters);
            openAlgoPopover(playlistId, algorithm, parameters);
        });
    });

    // Popover close button
    document.querySelectorAll('.algo-popover-close').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const popover = btn.closest('.algo-params-popover');
            const playlistId = popover.dataset.playlistId;
            closeAlgoPopover(playlistId);
        });
    });

    // Popover shuffle button: collect params and shuffle
    document.querySelectorAll('.algo-popover-shuffle').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const playlistId = btn.dataset.playlistId;
            const algorithm = btn.dataset.algorithm;
            const popover = btn.closest('.algo-params-popover');
            const paramsContainer = popover.querySelector('.algo-popover-params');
            const extraParams = {};
            paramsContainer.querySelectorAll('input, select').forEach(input => {
                extraParams[input.name] = input.value;
            });
            closeAlgoPopover(playlistId);
            shuffleWithAlgorithm(playlistId, algorithm, extraParams);
        });
    });

    // Undo buttons
    document.querySelectorAll('.undo-overlay-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            undoShuffle(btn.dataset.playlistId);
        });
    });

    // Keep first stepper: increment/decrement
    document.querySelectorAll('.keep-first-increment').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const playlistId = btn.dataset.playlistId;
            setKeepFirstValue(playlistId, getKeepFirstValue(playlistId) + 1);
        });
    });
    document.querySelectorAll('.keep-first-decrement').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const playlistId = btn.dataset.playlistId;
            setKeepFirstValue(playlistId, getKeepFirstValue(playlistId) - 1);
        });
    });

    // Prevent keep-first input clicks from toggling overlay
    document.querySelectorAll('.keep-first-input').forEach(input => {
        input.addEventListener('click', (e) => e.stopPropagation());
    });
});
```

### Step 6: Remove the `handlePlaylistAction` override in dashboard.html

The current `dashboard.html` defines its own `handlePlaylistAction` function at lines 436-494 which shadows the one in `base.html`. The new code does not use `handlePlaylistAction` at all — it uses `shuffleWithAlgorithm()` and `undoShuffle()` instead.

**Do NOT remove the `handlePlaylistAction` from `base.html`.** Only the dashboard-specific one in `dashboard.html` is removed as part of the complete JS rewrite.

### Step 7: Algorithm Gear Icon Visibility Logic

The gear icon appears **only** on algorithms that have parameters beyond `keep_first` (since `keep_first` is handled by the stepper). Using the Jinja2 condition in the template:

```jinja2
{% if algo.parameters|length > 1 or (algo.parameters|length == 1 and 'keep_first' not in algo.parameters) %}
```

This evaluates to show the gear icon for:
- **PercentageShuffle** — has `shuffle_percentage` and `shuffle_location` (no `keep_first`) — **show gear**
- **BalancedShuffle** — has `keep_first` and `section_count` (length > 1) — **show gear**
- **StratifiedShuffle** — has `keep_first` and `section_count` (length > 1) — **show gear**
- **ArtistSpacingShuffle** — has `min_spacing` only (length == 1, no `keep_first`) — **show gear**
- **AlbumSequenceShuffle** — has `shuffle_within_albums` only (length == 1, no `keep_first`) — **show gear**
- **BasicShuffle** — has `keep_first` only (length == 1, `keep_first` in parameters) — **no gear**

### Step 8: Handle `keep_first` for algorithms that don't support it

When `keep_first > 0` is sent to an algorithm that doesn't use it (PercentageShuffle, ArtistSpacingShuffle, AlbumSequenceShuffle), the parameter is silently ignored. The `ShuffleRequest` Pydantic schema at `shuffify/schemas/requests.py:80` always accepts `keep_first` regardless of algorithm, and `get_algorithm_params()` only passes params relevant to the selected algorithm. **No backend changes needed.**

The stepper is shown for all algorithms for layout consistency. This is acceptable because:
1. It keeps the overlay layout consistent
2. Most users will leave it at 0 anyway
3. Adding conditional visibility adds complexity for minimal benefit

### Step 9: Summary of all changes to `dashboard.html`

| Lines | Action | What changes |
|-------|--------|-------------|
| 255-349 | **Replace** | Entire `{% for playlist %}` loop with new tile structure |
| 369-434 | **Remove** | `updateAlgorithmParams()` function |
| 436-494 | **Remove** | `handlePlaylistAction()` function |
| 531-564 | **Replace** | `DOMContentLoaded` handler with new event delegation |
| 577-601 | **Replace** | CSS rules: remove shuffle-menu styles, add overlay styles |

Lines **kept unchanged**:

| Lines | What |
|-------|------|
| 1-254 | Everything above the playlist grid (header, stats, activity feed) |
| 350-352 | Closing tags for grid and container div |
| 358-367 | `toggleActivityFeed()` function |
| 496-528 | `refreshPlaylists()` function |
| 568-575 | Dashboard scrollbar CSS and `.rotate-180` |

---

## Test Plan

### New Tests to Write

Create `tests/templates/test_dashboard_overlay.py`:

**Test cases:**

1. **test_overlay_renders_for_each_playlist** — Verify that the dashboard response HTML contains `.shuffle-overlay` elements, one per playlist tile.

2. **test_algorithm_icons_render** — Verify that 6 `.algo-icon-btn` buttons exist per playlist (one per visible algorithm). Each should have `data-algorithm` matching the algorithm class name.

3. **test_gear_icon_present_for_parameterized_algorithms** — Verify `.algo-settings-btn` appears for PercentageShuffle, BalancedShuffle, StratifiedShuffle, ArtistSpacingShuffle, AlbumSequenceShuffle but NOT for BasicShuffle.

4. **test_keep_first_stepper_renders** — Verify `#keep-first-{playlist_id}` input exists with `value="0"` and `max` set to the playlist track count.

5. **test_workshop_link_on_overlay** — Verify the Workshop link is inside `.shuffle-overlay`, not in the info bar div.

6. **test_info_bar_simplified** — Verify the info bar (`.bg-spotify-green`) contains playlist name and Spotify link but NOT the Workshop button.

7. **test_hidden_forms_present** — Verify `.shuffle-form` and `.undo-form` hidden forms exist for each playlist.

8. **test_shuffle_endpoint_accepts_new_form_format** — The existing `/shuffle/<id>` endpoint should still accept POSTs with `algorithm=BasicShuffle` and `keep_first=0` fields.

9. **test_undo_button_hidden_by_default** — Verify `#undo-overlay-btn-{playlist_id}` has the `hidden` class initially.

10. **test_spotify_link_in_info_bar** — Verify the Spotify external link is in the info bar, not behind the overlay.

### Existing Tests to Verify (no modifications needed)

- `tests/routes/test_shuffle_routes.py` — All existing shuffle endpoint tests must continue to pass.
- `tests/routes/test_core_routes.py` — Dashboard rendering tests should still pass since the route passes the same data.

### Manual Verification Steps

1. **Hover behavior (desktop):** Hover over a tile. Overlay fades in smoothly (200ms). Move away — fades out.
2. **Tap behavior (mobile):** Tap tile to toggle overlay. Tap different tile closes first.
3. **Algorithm shuffle:** Click any algorithm icon. Verify spinning animation, success notification, undo button appears.
4. **Keep first stepper:** Set to 3, click BasicShuffle. Verify POST includes `keep_first=3`.
5. **Gear icon / popover:** Click gear on PercentageShuffle. Verify popover with params. "Shuffle with Settings" triggers shuffle.
6. **Undo:** After shuffle, click undo. Verify restoration.
7. **Workshop link:** Navigates to workshop page.
8. **Spotify link:** Opens Spotify in new tab.
9. **No album art:** Emoji placeholder displays correctly, overlay still works.
10. **Reduced motion:** Overlay appears/disappears without transition.

---

## Documentation Updates

### CHANGELOG.md

Add under `## [Unreleased]`:

```markdown
### Changed
- **Dashboard Shuffle UX** - Replaced click-to-expand shuffle panel with hover overlay on playlist artwork
  - Algorithm selection changed from dropdown to icon grid (6 visible algorithms)
  - Each algorithm has a distinct icon for quick identification
  - One-click shuffle with default parameters; gear icon for parameter customization
  - Workshop button moved from info bar to overlay
  - Undo button appears on overlay after shuffle
  - "Keep first N tracks" stepper integrated at top of overlay
  - Mobile support via tap-to-toggle fallback
  - Info bar simplified to playlist name, track count, and Spotify link
```

---

## Stress Testing & Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Empty playlist (0 tracks) | Stepper has max=0. Shuffle returns backend error gracefully. |
| Very long playlist name | Info bar uses `truncate` with `min-w-0`. Truncation works. |
| Rapid clicking | All algo buttons disabled during pending request. `.finally()` re-enables. |
| Network failure | `.catch()` shows error notification. `.finally()` resets button state. |
| Overlay + popover interaction | Popover is inside artwork area, part of hover target. |
| Multiple overlays | Only one overlay-active at a time (mobile). CSS `:hover` natural (desktop). |
| Session with existing undo state | Undo button hidden on page load (existing limitation, not a regression). |

---

## Verification Checklist

- [ ] `flake8 shuffify/` passes with 0 errors (no Python changes, but verify)
- [ ] `pytest tests/ -v` — all existing tests pass
- [ ] New tests in `tests/templates/test_dashboard_overlay.py` pass
- [ ] Manual: hover overlay appears/disappears smoothly on desktop
- [ ] Manual: tap-to-toggle works on mobile (Chrome DevTools device emulation)
- [ ] Manual: each of the 6 algorithm icons triggers a successful shuffle
- [ ] Manual: gear icon opens parameter popover for PercentageShuffle
- [ ] Manual: "Shuffle with Settings" from popover sends correct params
- [ ] Manual: keep-first stepper increments/decrements and value included in shuffle
- [ ] Manual: undo button appears after shuffle and works correctly
- [ ] Manual: Workshop link navigates correctly
- [ ] Manual: Spotify link opens in new tab
- [ ] Manual: playlist without artwork shows emoji and overlay works
- [ ] Manual: reduced motion preference disables transitions
- [ ] CHANGELOG.md updated

---

## What NOT To Do

1. **Do NOT modify any backend Python files.** This phase is entirely a frontend/template rewrite. The shuffle endpoint, undo endpoint, algorithm registry, and schema validation are unchanged.

2. **Do NOT remove the `handlePlaylistAction` function from `base.html`.** Other templates may rely on it. Only remove the dashboard-specific override within `dashboard.html`.

3. **Do NOT use CSS `:hover` as the sole overlay trigger.** Touch devices do not support hover. The `overlay-active` class toggle via JavaScript click handler is essential for mobile.

4. **Do NOT add `@media (hover: hover)` to conditionally apply hover styles.** The simpler approach (CSS hover + JS click toggle) works on all devices.

5. **Do NOT create a separate JavaScript file for the overlay logic.** The existing pattern is inline `<script>` blocks within templates. Keep it consistent.

6. **Do NOT change the `algorithms` variable name or structure.** The template still receives the same `algorithms` list from `core.py:99`.

7. **Do NOT add new CSS classes to `base.html` or `tailwind.config`.** All new styles are scoped within the `dashboard.html` `<style>` block.

8. **Do NOT add event listeners with inline `onclick` handlers on the new overlay elements.** Use event delegation in `DOMContentLoaded`. The only exception is `onclick="event.stopPropagation()"` on Workshop and Spotify links.

9. **Do NOT forget to include `aria-hidden="true"` on decorative SVG icons.** The icon SVGs are decorative (the button has `aria-label`).

10. **Do NOT use `transition: all` on the overlay.** Only transition `opacity`.
