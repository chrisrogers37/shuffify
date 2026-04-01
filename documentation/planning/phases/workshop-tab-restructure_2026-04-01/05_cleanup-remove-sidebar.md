# Phase 05: Remove Sidebar + Track Inbox + Cleanup

**Goal**: Delete all obsolete sidebar, Track Inbox, and inline panel code. Move Search into the Playlist tab. Estimated net reduction: ~1490 lines.

**Depends on**: Phases 02, 03, and 04 (all sidebar content must be moved first)

## File Modified

- `shuffify/templates/workshop.html`

## HTML Removals

### 1. Delete Sidebar Toggle Button (lines ~459-473)
```
<button id="sidebar-toggle-btn" ...>TOOLS</button>
```

### 2. Delete Sidebar Panel (lines ~475-1129)
The entire `<div id="sidebar-panel" ...>` including:
- Vertical tab bar (close button, 4 tab buttons)
- All 4 tab content areas (now containing only redirect notices from Phases 2-4)
- ~650 lines of HTML

### 3. Delete Sidebar Backdrop (lines ~1131-1135)
```
<div id="sidebar-backdrop" ...></div>
```

### 4. Delete Track Inbox from Playlist tab (lines ~287-448)
The entire `<div id="track-inbox" ...>` including:
- Toggle button
- Raided Tracks sub-tab content (now in Raids tab)
- Archived sub-tab content (now in Rotation tab)
- Search sub-tab content (being moved to Playlist tab standalone section)
- ~160 lines of HTML

### 5. Add Search section to Playlist tab

After the track list area, add a float-over search section. This moves the Search UI from Track Inbox into the Playlist tab:

```html
<!-- Search & Add Tracks (collapsible) -->
<div class="mt-4">
    <button id="search-toggle-btn"
            onclick="toggleSearchSection()"
            class="w-full flex items-center justify-between px-5 py-3 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 text-white font-bold text-sm hover:bg-white/15 transition duration-150"
            aria-expanded="false" aria-controls="search-body">
        <span class="flex items-center">
            <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
            </svg>
            Search & Add Tracks
        </span>
        <svg id="search-chevron" class="w-5 h-5 transform transition-transform duration-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
        </svg>
    </button>

    <div id="search-body" class="hidden mt-2 rounded-2xl shadow-xl backdrop-blur-md bg-white/10 border border-white/20 overflow-hidden p-4">
        <!-- Search Spotify (moved from Track Inbox search-content) -->
        <div class="mb-4">
            <div class="flex items-center space-x-2 mb-3">
                <input id="search-input" type="text" placeholder="Search tracks, artists..."
                       class="flex-1 px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white placeholder-white/40 focus:ring-2 focus:ring-white/30 focus:border-transparent text-sm"
                       onkeydown="if(event.key==='Enter'){event.preventDefault(); searchSpotify();}"
                       maxlength="200" autocomplete="off">
                <button onclick="searchSpotify()" class="px-3 py-2 rounded-lg bg-white/20 hover:bg-white/30 text-white transition duration-150 flex-shrink-0" title="Search">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
                </button>
            </div>
            <div id="search-results" class="space-y-1 max-h-[40vh] overflow-y-auto workshop-scrollbar">
                <p id="search-placeholder" class="text-white/40 text-sm text-center py-4">Type a query and press Enter</p>
            </div>
            <button id="search-load-more" onclick="searchSpotifyMore()"
                    class="hidden w-full mt-2 px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white/70 text-sm transition duration-150">
                Load more results
            </button>
        </div>

        <!-- Load External Playlist (moved from Track Inbox) -->
        <div class="pt-4 border-t border-white/10">
            <h3 class="text-white font-semibold text-sm mb-2">Load External Playlist</h3>
            <!-- ... same HTML as current external playlist section ... -->
        </div>
    </div>
</div>
```

## JavaScript Removals

### 1. Delete `workshopSidebar` namespace (~170 lines)
The entire `workshopSidebar` object and its DOMContentLoaded initializer. Functions:
- `toggle()`, `open()`, `close()`
- `switchTab()`
- `_applyTabState()`
- `_saveState()`

### 2. Delete `toggleTrackInbox()` and `switchInboxTab()` (~45 lines)
These managed the Track Inbox toggle and sub-tab switching.

### 3. Delete inline panel stubs (~630 lines total)
- `inlineRaid` object (~230 lines) — was bootstrapping raid panel from inline data
- `inlineBackup` object (~300 lines) — was bootstrapping archive panel from inline data
- `inlineSnapshots` object (~100 lines) — was bootstrapping snapshots from inline data

### 4. Delete tab activation hooks
- `onArchiveTabActivated()` — logic now in `workshopTabs._onTabActivated('rotation')`
- `onSnapshotsTabActivated()` — logic now in `workshopTabs._onTabActivated('snapshots')`
- `onRaidsTabActivated()` — logic now in `workshopTabs._onTabActivated('raids')`
- `onSchedulesTabActivated()` — logic now in `workshopTabs._onTabActivated('schedules')`

### 5. Delete `toggleCollapsiblePanel()` (~10 lines)
No longer used — sidebar collapsible panels removed.

### 6. Clean up dead references
- Remove references to `inbox-pending-badge` (Track Inbox badge) — replaced by tab badge
- Remove references to `inbox-toggle`, `inbox-body`, `inbox-chevron`
- Remove `sidebar-panel`, `sidebar-toggle-btn`, `sidebar-backdrop` references
- Remove localStorage keys: `shuffify_sidebar_open`, `shuffify_sidebar_tab`

## JavaScript Additions

### 1. `toggleSearchSection()` function
```javascript
function toggleSearchSection() {
    var body = document.getElementById('search-body');
    var chevron = document.getElementById('search-chevron');
    var btn = document.getElementById('search-toggle-btn');
    var isHidden = body.classList.contains('hidden');

    body.classList.toggle('hidden');
    chevron.classList.toggle('rotate-180');
    btn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
}
```

### 2. Tab badge functions
```javascript
function updateRaidsTabBadge(count) {
    updateTabBadge('raids', count, 'bg-yellow-500/80');
}
function updateRotationTabBadge(count) {
    updateTabBadge('rotation', count, 'bg-blue-500/80');
}
function updateTabBadge(tabName, count, colorClass) {
    var btn = document.getElementById('htab-btn-' + tabName);
    var badge = btn.querySelector('.htab-badge');
    if (count > 0) {
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'htab-badge ml-1.5 px-1.5 py-0.5 rounded-full text-xs font-semibold ' + colorClass;
            btn.appendChild(badge);
        }
        badge.textContent = count;
    } else if (badge) {
        badge.remove();
    }
}
```

Wire `updateRaidsTabBadge()` into `renderPendingRaids()` and `updateRotationTabBadge()` into `renderInboxArchiveTracks()`.

## CSS Removals

Delete sidebar-specific styles:
- `.sidebar-scrollbar` custom scrollbar (6px, white/15 opacity)
- `#sidebar-toggle-btn` hover effects
- Sidebar media queries (responsive breakpoints for sidebar)

## Backend Changes

None.

## Verification

### Functional
1. Page loads without console errors
2. Search works in Playlist tab — type query, see results, click to add track
3. External playlist loading works in Playlist tab
4. All 5 tabs function correctly (regression from Phases 1-4)
5. Tab badges update correctly for Raids (pending count) and Rotation (archive count)
6. No sidebar toggle button visible anywhere
7. No Track Inbox visible anywhere
8. Deep link `?setup=raids` opens Raids tab, `?setup=archive` opens Rotation tab

### Code Quality
9. No remaining references to deleted element IDs (search for: `sidebar-panel`, `sidebar-toggle-btn`, `sidebar-backdrop`, `inbox-toggle`, `inbox-body`, `inbox-tab-`, `sidebar-tab-btn`, `sidebar-tab-content`)
10. No remaining references to deleted JS objects (search for: `workshopSidebar`, `inlineRaid`, `inlineBackup`, `inlineSnapshots`, `toggleTrackInbox`, `switchInboxTab`, `toggleCollapsiblePanel`)
11. No dead localStorage keys being read/written

### Responsive
12. Mobile: tab bar scrolls horizontally
13. Mobile: all tab content is full-width
14. No fixed-position sidebar elements remain

### Tests
15. `flake8 shuffify/` passes
16. `pytest tests/ -v` passes
