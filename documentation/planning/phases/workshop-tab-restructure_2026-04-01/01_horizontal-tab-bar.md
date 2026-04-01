# Phase 01: Horizontal Tab Bar + Playlist Tab Wrapper

**Status**: 🔧 IN PROGRESS
**Started**: 2026-04-01

**Goal**: Add the tab bar UI and wrap existing content as the "Playlist" tab. Zero functional change — the page works identically, just with a new tab bar visible.

## File Modified

- `shuffify/templates/workshop.html`

## HTML Changes

### 1. Add horizontal tab bar (after line ~136, inside `{% if playlist %}`)

Insert immediately after the Workshop Header closing `</div>`:

```html
<!-- Workshop Horizontal Tab Bar -->
<div class="relative max-w-5xl mx-auto px-4 pt-4">
    <div class="flex border-b border-white/20 overflow-x-auto whitespace-nowrap" role="tablist" aria-label="Workshop sections">
        <button id="htab-btn-playlist" class="htab-btn px-5 py-2.5 text-sm font-semibold transition duration-150 border-b-2 border-spotify-green text-white flex-shrink-0"
                onclick="workshopTabs.switchTab('playlist')" role="tab" aria-selected="true" aria-controls="htab-content-playlist">
            <svg class="w-4 h-4 inline mr-1.5 -mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"></path></svg>
            Playlist
        </button>
        <button id="htab-btn-raids" class="htab-btn px-5 py-2.5 text-sm font-semibold transition duration-150 border-b-2 border-transparent text-white/60 hover:text-white flex-shrink-0"
                onclick="workshopTabs.switchTab('raids')" role="tab" aria-selected="false" aria-controls="htab-content-raids">
            <svg class="w-4 h-4 inline mr-1.5 -mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
            Raids
        </button>
        <button id="htab-btn-rotation" class="htab-btn px-5 py-2.5 text-sm font-semibold transition duration-150 border-b-2 border-transparent text-white/60 hover:text-white flex-shrink-0"
                onclick="workshopTabs.switchTab('rotation')" role="tab" aria-selected="false" aria-controls="htab-content-rotation">
            <svg class="w-4 h-4 inline mr-1.5 -mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
            Rotation
        </button>
        <button id="htab-btn-schedules" class="htab-btn px-5 py-2.5 text-sm font-semibold transition duration-150 border-b-2 border-transparent text-white/60 hover:text-white flex-shrink-0"
                onclick="workshopTabs.switchTab('schedules')" role="tab" aria-selected="false" aria-controls="htab-content-schedules">
            <svg class="w-4 h-4 inline mr-1.5 -mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            Schedules
        </button>
        <button id="htab-btn-snapshots" class="htab-btn px-5 py-2.5 text-sm font-semibold transition duration-150 border-b-2 border-transparent text-white/60 hover:text-white flex-shrink-0"
                onclick="workshopTabs.switchTab('snapshots')" role="tab" aria-selected="false" aria-controls="htab-content-snapshots">
            <svg class="w-4 h-4 inline mr-1.5 -mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"></path></svg>
            Snapshots
        </button>
    </div>
</div>
```

### 2. Wrap existing content as Playlist tab

Wrap the shuffle bar + main workshop area + Track Inbox (lines ~139-448) in:

```html
<div id="htab-content-playlist" class="htab-content" role="tabpanel" aria-labelledby="htab-btn-playlist">
    <!-- existing shuffle bar -->
    <!-- existing main workshop area with track list -->
    <!-- existing Track Inbox (still present, removed in Phase 5) -->
</div>
```

### 3. Add placeholder tab panels (after the Playlist tab wrapper)

```html
<div id="htab-content-raids" class="htab-content hidden" role="tabpanel" aria-labelledby="htab-btn-raids">
    <div class="max-w-5xl mx-auto px-4 py-8 text-center">
        <p class="text-white/40">Raids — coming in next phase</p>
    </div>
</div>
<div id="htab-content-rotation" class="htab-content hidden" role="tabpanel" aria-labelledby="htab-btn-rotation">
    <div class="max-w-5xl mx-auto px-4 py-8 text-center">
        <p class="text-white/40">Rotation — coming in next phase</p>
    </div>
</div>
<div id="htab-content-schedules" class="htab-content hidden" role="tabpanel" aria-labelledby="htab-btn-schedules">
    <div class="max-w-5xl mx-auto px-4 py-8 text-center">
        <p class="text-white/40">Schedules — coming in next phase</p>
    </div>
</div>
<div id="htab-content-snapshots" class="htab-content hidden" role="tabpanel" aria-labelledby="htab-btn-snapshots">
    <div class="max-w-5xl mx-auto px-4 py-8 text-center">
        <p class="text-white/40">Snapshots — coming in next phase</p>
    </div>
</div>
```

## JavaScript Changes

### Add `workshopTabs` namespace (in the main `<script>` block)

```javascript
// =============================================================================
// Workshop Horizontal Tabs
// =============================================================================

const workshopTabs = {
    activeTab: 'playlist',
    tabs: ['playlist', 'raids', 'rotation', 'schedules', 'snapshots'],

    switchTab(tabName) {
        if (!this.tabs.includes(tabName)) return;

        // Deactivate all
        this.tabs.forEach(t => {
            const btn = document.getElementById('htab-btn-' + t);
            const panel = document.getElementById('htab-content-' + t);
            if (btn) {
                btn.classList.remove('border-spotify-green', 'text-white');
                btn.classList.add('border-transparent', 'text-white/60');
                btn.setAttribute('aria-selected', 'false');
            }
            if (panel) panel.classList.add('hidden');
        });

        // Activate selected
        const btn = document.getElementById('htab-btn-' + tabName);
        const panel = document.getElementById('htab-content-' + tabName);
        if (btn) {
            btn.classList.add('border-spotify-green', 'text-white');
            btn.classList.remove('border-transparent', 'text-white/60');
            btn.setAttribute('aria-selected', 'true');
        }
        if (panel) panel.classList.remove('hidden');

        this.activeTab = tabName;

        this._onTabActivated(tabName);
    },

    _onTabActivated(tabName) {
        // Lazy-load hooks — populated in Phases 2-4
    },

    init() {
        // Check query params for deep-link, otherwise always default to Playlist
        const params = new URLSearchParams(window.location.search);
        const setup = params.get('setup');
        if (setup === 'raids') {
            this.switchTab('raids');
        } else if (setup === 'archive' || setup === 'rotation') {
            this.switchTab('rotation');
        } else {
            this.switchTab('playlist');
        }
    }
};

// Add workshopTabs.init() to the existing DOMContentLoaded listener
// that runs workshopSidebar.init() (line ~3439)
```

## Backend Changes

None.

## Verification

1. Page loads without errors, Playlist tab is active by default
2. All existing functionality works: shuffle, drag-reorder, preview, save, undo, search, Track Inbox
3. Clicking other tabs shows placeholder text and hides Playlist content
4. Sidebar still works (not yet removed)
5. `?setup=raids` in URL opens Raids tab
6. `?setup=archive` in URL opens Rotation tab
7. Tab state persists in localStorage across page reloads
8. Mobile: tab bar scrolls horizontally if needed
9. `flake8 shuffify/` and `pytest tests/ -v` pass
