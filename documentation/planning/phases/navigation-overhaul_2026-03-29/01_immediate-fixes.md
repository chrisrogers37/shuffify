# Phase 01: Immediate Fixes

**Status**: PENDING

## Objective

Four independent fixes: dropdown ordering, drag-to-reorder for favorites, workshop back button, and algorithm grid spacing.

## Files to Modify

- `shuffify/routes/playlists.py`
- `shuffify/templates/dashboard.html`
- `shuffify/templates/workshop.html`
- `shuffify/templates/macros/cards.html`

## Changes

### 1a. Dropdown ordering — respect playlist preferences

**File**: `shuffify/routes/playlists.py` (lines 85-116)

The `api_user_playlists()` function returns playlists in Spotify's raw order. The workshop dropdown and other consumers get unsorted results.

**Fix**: Import `PlaylistPreferenceService` and apply preferences before building the result list. Follow the same pattern used in `core.py:91-104` and `workshop.py:89-104`.

```python
# In api_user_playlists(), after fetching playlists:
from shuffify.services.playlist_preference_service import PlaylistPreferenceService

preferences = PlaylistPreferenceService.get_user_preferences(user.id)
if preferences:
    favs, visible, _hidden = PlaylistPreferenceService.apply_preferences(playlists, preferences)
    playlists = favs + visible
```

Build the `result` list from this ordered `playlists` instead of the raw Spotify response. Hidden playlists are excluded.

### 1b. Homepage drag-to-reorder — extend to favorites

**File**: `shuffify/templates/dashboard.html`

Three JS functions need updates:

**`toggleManageMode()` (line 667)**: Currently only sets `draggable` on `#playlist-grid .card-tile` (line 687). Add the same for `#favorites-grid .card-tile`:

```javascript
// Add after existing #playlist-grid line:
document.querySelectorAll('#favorites-grid .card-tile').forEach(tile => {
    tile.draggable = manageMode;
    tile.classList.toggle('manage-mode-active', manageMode);
});
```

**`saveCurrentOrder()` (line 891)**: Currently reads only from `#playlist-grid`. Read from both grids in order:

```javascript
function saveCurrentOrder() {
    const favTiles = document.querySelectorAll('#favorites-grid .card-tile');
    const regTiles = document.querySelectorAll('#playlist-grid .card-tile');
    const playlistIds = [
        ...Array.from(favTiles).map(t => t.dataset.playlistId),
        ...Array.from(regTiles).map(t => t.dataset.playlistId)
    ];
    // ... rest unchanged
}
```

**`initDragAndDrop()` (line 918)**: Currently only binds to `#playlist-grid`. Bind to both grids. Refactor to accept a grid element and call for both:

```javascript
function initDragAndDrop() {
    const grids = [
        document.getElementById('favorites-grid'),
        document.getElementById('playlist-grid')
    ].filter(Boolean);

    let draggedTile = null;

    grids.forEach(grid => {
        grid.addEventListener('dragstart', (e) => { /* same logic */ });
        grid.addEventListener('dragover', (e) => { /* same logic */ });
        grid.addEventListener('dragleave', (e) => { /* same logic */ });
        grid.addEventListener('drop', (e) => {
            // Same drop logic but use grid variable
            // Call saveCurrentOrder() after drop
        });
        grid.addEventListener('dragend', (e) => { /* same logic */ });
    });
}
```

Note: Cross-grid dragging (favorites to regular) is not needed for now — each grid reorders independently. The combined order is captured by `saveCurrentOrder()`.

### 1c. Workshop back button

**File**: `shuffify/templates/workshop.html` (line 13, inside the flex container)

Add a home button as the first element inside the `<div class="flex items-center min-w-0">` container, before the prev-playlist arrow:

```html
<a href="{{ url_for('main.index') }}"
   class="mr-2 p-2 rounded-lg bg-white/10 hover:bg-white/20 transition duration-150 border border-white/20 flex-shrink-0"
   title="Back to dashboard">
    <svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"></path>
    </svg>
</a>
```

Uses the same styling as the prev/next arrows but with a home icon. Placed before `{% if prev_playlist_id %}`.

### 1d. Algorithm grid uniform spacing

**File**: `shuffify/templates/macros/cards.html` (line 205)

**Grid layout**: Change `grid-cols-3` to `grid-cols-4`:

```html
<div class="grid grid-cols-4 gap-1 my-0.5">
```

This gives a 4+3 layout (4 in first row, 3 in second row) which is visually balanced.

**Add NewestFirstShuffle icon**: After the `AlbumSequenceShuffle` elif block (line 238-241), add:

```html
{% elif algo.class_name == 'NewestFirstShuffle' %}
<svg class="w-5 h-5 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
</svg>
```

Uses a clock icon (matches "newest first" concept — time-based sorting).

**Button sizing**: With 4 columns, buttons are narrower. Reduce icon size if needed: `w-4 h-4` instead of `w-5 h-5`, or reduce padding from `py-1.5` to `py-1`. Test visually.

## Verification

1. **Dropdown order**: Open workshop → click playlist name → favorites appear at top, hidden playlists excluded
2. **Drag-to-reorder**: Dashboard → Manage → drag tiles in favorites grid → order saves → drag tiles in regular grid → order saves → reload confirms persistence
3. **Back button**: Workshop → click home icon → navigates to dashboard
4. **Algorithm grid**: Hover any playlist card → 4+3 grid layout, Newest First has clock icon, all buttons evenly spaced
5. **Run**: `flake8 shuffify/` (0 errors)
6. **Run**: `pytest tests/ -v` (all pass)

## CHANGELOG Entry

```markdown
### Fixed
- **Workshop Playlist Dropdown** - Playlists now sorted by user preferences (favorites first, hidden excluded)
- **Algorithm Grid Spacing** - Changed to 4-column layout for even distribution, added missing Newest First icon

### Added
- **Workshop Home Button** - Added dashboard navigation button in workshop header
- **Favorites Drag-to-Reorder** - Extended drag-and-drop reordering to favorites section on dashboard
```
