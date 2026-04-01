# Phase 02: Raids Tab

**Goal**: Move raid panel from sidebar + raid tracks from Track Inbox into the Raids horizontal tab with full playlist view format.

**Depends on**: Phase 01

## File Modified

- `shuffify/templates/workshop.html`

## HTML Changes

### 1. Replace the raids placeholder in `htab-content-raids` with real content

Use a responsive two-column layout: config on left, pending tracks on right (stacked on mobile).

```html
<div id="htab-content-raids" class="htab-content hidden" role="tabpanel" aria-labelledby="htab-btn-raids">
    <div class="max-w-5xl mx-auto px-4 py-6">

        <!-- Loading state (reuse existing ID) -->
        <div id="raid-loading" class="hidden text-center py-12">
            <svg class="w-8 h-8 mx-auto text-white/30 animate-spin" ...></svg>
            <p class="text-white/40 text-sm mt-2">Loading raid status...</p>
        </div>

        <!-- Error state (reuse existing ID) -->
        <div id="raid-error" class="hidden ...">...</div>

        <!-- Main raid content (reuse existing ID) -->
        <div id="raid-panel-content" class="hidden">
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">

                <!-- Left column: Config -->
                <div class="space-y-4">
                    <!-- Section 1: Raid Playlist (move from sidebar lines 816-864) -->
                    <!-- Section 2: Add External Source (move from sidebar lines 866-884) -->
                    <!-- Section 3: Watched Sources (move from sidebar lines 887-902) -->
                    <!-- Section 4: Drip to Playlist (move from sidebar lines 904-932) -->
                    <!-- Section 5: Schedule (move from sidebar lines 934-1028) -->
                    <!-- Section 6: Actions (move from sidebar lines 1031-1061) -->
                </div>

                <!-- Right column: Pending Raid Tracks -->
                <div>
                    <div class="flex items-center justify-between mb-3">
                        <h3 class="text-white font-bold text-lg">
                            Pending Tracks
                            <span id="inbox-raid-count" class="text-white/40 text-sm font-normal"></span>
                        </h3>
                    </div>

                    <!-- Loading (reuse existing ID) -->
                    <div id="inbox-raids-loading" class="hidden text-center py-8">...</div>

                    <!-- Empty state (reuse existing ID) -->
                    <div id="inbox-raids-empty" class="hidden text-center py-8">
                        <svg class="w-12 h-12 mx-auto text-white/15 mb-3" ...></svg>
                        <p class="text-white/40 text-sm">No pending raided tracks</p>
                        <p class="text-white/25 text-xs mt-1">Add sources and click "Raid Now" to discover new tracks.</p>
                    </div>

                    <!-- Track list in full playlist view format (reuse existing ID) -->
                    <div id="inbox-raids-list" class="space-y-1 max-h-[60vh] overflow-y-auto workshop-scrollbar">
                        <!-- Populated by loadPendingRaids() / renderPendingRaids() -->
                    </div>

                    <!-- Bulk actions (reuse existing ID) -->
                    <div id="inbox-raids-actions" class="hidden flex gap-2 mt-3 pt-3 border-t border-white/10">
                        <button onclick="promoteAllPendingRaids()" class="flex-1 px-3 py-2 rounded-lg bg-spotify-green/80 hover:bg-spotify-green text-white text-sm font-semibold transition duration-150">
                            Add All to Playlist
                        </button>
                        <button onclick="dismissAllPendingRaids()" class="px-3 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white/60 text-sm font-semibold transition duration-150 border border-white/20">
                            Dismiss All
                        </button>
                    </div>
                </div>

            </div>
        </div>
    </div>
</div>
```

### 2. Empty the sidebar's `sidebar-tab-raids` content

Replace the content of `sidebar-tab-raids` (lines 793-1072) with:
```html
<div id="sidebar-tab-raids" class="sidebar-tab-content hidden p-5" role="tabpanel">
    <p class="text-white/40 text-sm text-center py-4">Raids have moved to the Raids tab above.</p>
</div>
```

### 3. Adapt config sections for full width

The sidebar sections used 320px width with tight padding. For the new layout:
- Change rounded-xl cards to use full available width
- Input fields and selects get responsive widths (`w-full` instead of fixed)
- Button layouts stay the same (already flex-based)

## JavaScript Changes

### 1. Update `workshopTabs._onTabActivated()`

Add raids activation logic:

```javascript
_onTabActivated(tabName) {
    if (tabName === 'raids') {
        // Load raid status if not already loaded
        if (typeof raidPanel !== 'undefined' && !raidPanel.loaded && !raidPanel.isLoading) {
            raidPanel.loadStatus();
        }
        // Load pending raids
        loadPendingRaids();
    }
}
```

### 2. Update `renderPendingRaids()` for full playlist view format

Currently renders compact items. Update to match main track list styling:
- Include album art thumbnail
- Show track title, artist, duration
- Replace the existing small `+` button with a clearer `[+ Add to Playlist]` button
- Keep the dismiss `X` button

The track item should match `createTrackElement()` visual layout but with action buttons instead of drag handles.

### 3. Update notification messages

In `promotePendingRaid()`, update the notification to mention the Playlist tab:
```javascript
showNotification('Track added to playlist', 'success');
```

### 4. Update tab badge

After `renderPendingRaids()` completes, update the Raids tab badge:
```javascript
function updateRaidsTabBadge(count) {
    const btn = document.getElementById('htab-btn-raids');
    let badge = btn.querySelector('.htab-badge');
    if (count > 0) {
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'htab-badge ml-1.5 px-1.5 py-0.5 rounded-full bg-yellow-500/80 text-xs font-semibold';
            btn.appendChild(badge);
        }
        badge.textContent = count;
    } else if (badge) {
        badge.remove();
    }
}
```

Call this from `renderPendingRaids()` and after promote/dismiss operations.

## Backend Changes

None. All existing endpoints used:
- `GET /playlist/<id>/raid-status` — loads raid panel state
- `POST /playlist/<id>/raid-link` — create/manage raid playlist link
- `POST|DELETE /playlist/<id>/watch-*` — manage sources
- `POST /playlist/<id>/raid-now` — trigger raid

## Verification

1. Click Raids tab → loads raid status and pending tracks on first activation
2. Add external source via URL → source appears in watched list
3. Remove source → disappears from list
4. Click "Raid Now" → new tracks appear in pending list (if sources have new tracks)
5. Click "Add to Playlist" on a pending track → track appears in Playlist tab track list, pending list updates
6. Click "Add All to Playlist" → all pending tracks move to Playlist tab
7. Click "Dismiss All" → pending list clears
8. Raid schedule creation/toggle/delete works
9. Drip config toggle and count change works
10. Tab badge shows pending count, updates after promote/dismiss
11. `flake8 shuffify/` and `pytest tests/ -v` pass
