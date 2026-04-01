# Phase 03: Rotation Tab + Archive Bug Fix

**Goal**: Move archive/rotation from sidebar + archive tracks from Track Inbox into the Rotation tab. Fixes the archive display bug.

**Depends on**: Phase 01

## File Modified

- `shuffify/templates/workshop.html`

## Bug Being Fixed

**Root cause**: `workshopState.archivePair` is only populated when the sidebar Archive tab is activated via `onArchiveTabActivated()` (line 4563). If Track Inbox > Archived is opened first, `archivePair` is null, so `fetchArchiveTracks()` (line 4345) early-returns empty.

**Fix**: The Rotation tab activation hook calls `loadArchivePairInfo()` which sets `workshopState.archivePair` BEFORE `fetchArchiveTracks()` runs.

## HTML Changes

### 1. Replace the rotation placeholder in `htab-content-rotation` with real content

Two-column responsive layout: config on left, archive tracks on right.

```html
<div id="htab-content-rotation" class="htab-content hidden" role="tabpanel" aria-labelledby="htab-btn-rotation">
    <div class="max-w-5xl mx-auto px-4 py-6">

        <!-- Loading state (reuse existing ID) -->
        <div id="archive-loading" class="hidden text-center py-12">...</div>

        <!-- Error state (reuse existing ID) -->
        <div id="archive-error" class="hidden ...">...</div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">

            <!-- Left column: Config -->
            <div class="space-y-4">
                <!-- No pair state (reuse existing ID) — move from sidebar lines 646-671 -->
                <div id="archive-no-pair" class="hidden">
                    <!-- Create New Archive button -->
                    <!-- Link existing playlist dropdown -->
                </div>

                <!-- Paired state (reuse existing ID) — move from sidebar lines 674-696 -->
                <div id="archive-paired" class="hidden">
                    <!-- Archive pair info card with name, track count, auto-archive toggle -->
                </div>

                <!-- Rotation Schedule Section (reuse existing ID) — move from sidebar lines 698-770 -->
                <div id="rotation-section" class="hidden">
                    <!-- No schedule: creation form -->
                    <!-- Existing schedule: status, controls, history -->
                </div>
            </div>

            <!-- Right column: Archive Tracks -->
            <div>
                <div class="flex items-center justify-between mb-3">
                    <h3 class="text-white font-bold text-lg">
                        Archive Tracks
                        <span id="inbox-archive-count" class="text-white/40 text-sm font-normal"></span>
                    </h3>
                </div>

                <!-- Loading (reuse existing ID) -->
                <div id="inbox-archive-loading" class="hidden text-center py-8">...</div>

                <!-- Empty state (reuse existing ID) -->
                <div id="inbox-archive-empty" class="hidden text-center py-8">
                    <svg class="w-12 h-12 mx-auto text-white/15 mb-3" ...></svg>
                    <p class="text-white/40 text-sm">No archived tracks</p>
                    <p class="text-white/25 text-xs mt-1">Link an archive playlist and remove tracks to see them here.</p>
                </div>

                <!-- Archive track list in full playlist view format (reuse existing ID) -->
                <div id="inbox-archive-list" class="space-y-1 max-h-[60vh] overflow-y-auto workshop-scrollbar">
                    <!-- Populated by renderInboxArchiveTracks() -->
                </div>

                <!-- Bulk actions (reuse existing ID) -->
                <div id="inbox-archive-actions" class="hidden flex gap-2 mt-3 pt-3 border-t border-white/10">
                    <button onclick="restoreAllArchiveTracks()" class="flex-1 px-3 py-2 rounded-lg bg-spotify-green/80 hover:bg-spotify-green text-white text-sm font-semibold transition duration-150">
                        Restore All to Playlist
                    </button>
                </div>
            </div>

        </div>
    </div>
</div>
```

### 2. Empty the sidebar's `sidebar-tab-archive` content

Replace content of `sidebar-tab-archive` (lines 631-790) with redirect notice.

### 3. Adapt config sections for full width

Same approach as Phase 02 — responsive widths for inputs and selects.

## JavaScript Changes

### 1. Update `workshopTabs._onTabActivated()` — THE BUG FIX

```javascript
_onTabActivated(tabName) {
    // ... existing raids logic from Phase 02 ...

    if (tabName === 'rotation') {
        if (!archiveState.isLoaded) {
            // Use inline backup data if available, otherwise fetch
            if (typeof inlineBackup !== 'undefined' && inlineBackup.loaded) {
                archiveState.isLoaded = true;
                if (inlineBackup.pair) {
                    workshopState.archivePair = inlineBackup.pair;
                }
                renderArchivePanel();
                loadArchiveTracks();
                loadRotationConfig();
                document.getElementById('archive-loading').classList.add('hidden');
            } else {
                loadArchivePairInfo();  // Sets workshopState.archivePair
            }
        }
        // Always attempt to load archive tracks (fetchArchiveTracks handles caching)
        loadArchiveTracksForInbox();
    }
}
```

This ensures `workshopState.archivePair` is set BEFORE `fetchArchiveTracks()` is called, fixing the early-return bug.

### 2. Remove `onArchiveTabActivated()`

Its logic is now in the tab activation hook above. Delete lines 4563-4584.

### 3. Update `renderInboxArchiveTracks()` for full playlist view format

Currently renders compact items. Update to match main track list styling:
- Include album art thumbnail
- Show track title, artist, duration
- Replace the small `+` button with a clearer `[+ Restore to Playlist]` button

### 4. Remove the sidebar's `archive-tracks-section` redirect

The existing code at line 772-780 shows "restore tracks via the Track Inbox" link. This section is no longer needed since archive tracks are directly visible in the Rotation tab.

### 5. Update notification messages

In `stageArchiveRestore()`:
```javascript
showNotification('Track staged for restore — switch to Playlist tab to see it', 'success');
```

## Backend Changes

None. All existing endpoints used:
- `GET /playlist/<id>/pair` — load archive pair info
- `POST /playlist/<id>/pair` — create pair
- `PATCH /playlist/<id>/pair` — update settings
- `DELETE /playlist/<id>/pair` — remove pair
- `GET /playlist/<id>/pair/archive-tracks` — list archived tracks
- `POST /playlist/<id>/pair/archive` — archive tracks
- `POST /playlist/<id>/pair/unarchive` — restore tracks
- `GET /playlist/<id>/rotation-status` — rotation schedule status

## Verification

1. **Bug fix**: Open Workshop → click Rotation tab (never opened sidebar) → archive tracks display correctly
2. Click Rotation tab → loads archive pair info and archive tracks on first activation
3. No archive pair → shows "Create New Archive" and "Link existing" options
4. Create archive pair → pair info card appears, archive tracks section loads
5. Auto-archive toggle works
6. Rotation schedule: create, toggle, run now, delete all work
7. Click "Restore to Playlist" on an archive track → track appears in Playlist tab track list
8. Click "Restore All" → all archive tracks move to Playlist tab
9. After restoring, switch to Playlist tab → restored tracks visible, dirty state active
10. Undo in Playlist tab reverts the restoration
11. `flake8 shuffify/` and `pytest tests/ -v` pass
