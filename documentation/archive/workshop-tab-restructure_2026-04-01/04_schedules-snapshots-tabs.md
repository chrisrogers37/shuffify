# Phase 04: Schedules + Snapshots Tabs

**Goal**: Move the remaining two sidebar tabs into their horizontal tabs.

**Depends on**: Phase 01

## File Modified

- `shuffify/templates/workshop.html`

---

## Part A: Schedules Tab

### HTML Changes

Replace the schedules placeholder in `htab-content-schedules` with content moved from `sidebar-tab-schedules` (lines 1075-1127).

```html
<div id="htab-content-schedules" class="htab-content hidden" role="tabpanel" aria-labelledby="htab-btn-schedules">
    <div class="max-w-5xl mx-auto px-4 py-6">
        <div class="max-w-2xl">  <!-- Constrain width for readability -->

            <div class="flex items-center justify-between mb-4">
                <h3 class="text-white font-bold text-lg">Schedules for This Playlist</h3>
                <button onclick="playlistSchedules.loadSchedules()"
                        class="text-white/40 hover:text-white/60 transition duration-150" title="Refresh">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                    </svg>
                </button>
            </div>

            <!-- Loading (reuse existing ID) -->
            <div id="schedules-loading" class="hidden text-center py-8">...</div>

            <!-- Error (reuse existing ID) -->
            <div id="schedules-error" class="hidden ...">...</div>

            <!-- Empty (reuse existing ID) -->
            <div id="schedules-empty" class="hidden ...">
                <p class="text-white/50 text-sm mb-1">No schedules for this playlist</p>
                <p class="text-white/30 text-xs">Schedules automate shuffling and raiding on a timer. Create one from the Raids or Rotation tabs.</p>
            </div>

            <!-- Schedule List (reuse existing ID) -->
            <div id="schedules-list" class="hidden space-y-3">
                <!-- Populated by playlistSchedules.render() -->
            </div>

            <!-- Manage All Schedules link -->
            <div class="mt-6 pt-4 border-t border-white/10">
                <a href="/schedules" class="text-spotify-green hover:text-green-400 text-sm font-medium transition inline-flex items-center gap-1.5">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                    </svg>
                    Manage All Schedules
                </a>
            </div>
        </div>
    </div>
</div>
```

### JavaScript Changes

Update `workshopTabs._onTabActivated()`:
```javascript
if (tabName === 'schedules') {
    if (typeof playlistSchedules !== 'undefined' && !playlistSchedules.loaded && !playlistSchedules.isLoading) {
        playlistSchedules.loadSchedules();
    }
}
```

Empty the sidebar's `sidebar-tab-schedules` content with redirect notice.

---

## Part B: Snapshots Tab

### HTML Changes

Replace the snapshots placeholder in `htab-content-snapshots` with content moved from `sidebar-tab-snapshots` (lines 552-628). The snapshot timeline gets more room to breathe at full width.

```html
<div id="htab-content-snapshots" class="htab-content hidden" role="tabpanel" aria-labelledby="htab-btn-snapshots">
    <div class="max-w-5xl mx-auto px-4 py-6">
        <div class="max-w-3xl">  <!-- Wider than schedules for timeline -->

            <!-- Header with Take Snapshot button (reuse existing IDs) -->
            <div class="flex items-center justify-between mb-4">
                <h3 class="text-white font-bold text-lg">
                    Snapshots
                    <span id="snapshot-count-badge" class="hidden text-white/30 text-sm font-normal"></span>
                </h3>
                <button id="snapshot-take-btn" onclick="openManualSnapshotDialog()"
                        class="px-4 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-white/70 hover:text-white text-sm font-semibold transition duration-150 flex items-center gap-2"
                        title="Take a snapshot of the current playlist state">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"></path>
                    </svg>
                    Take Snapshot
                </button>
            </div>

            <!-- Manual Snapshot Form (reuse existing ID) -->
            <div id="snapshot-create-form" class="hidden mb-4">...</div>

            <!-- Loading State (reuse existing ID) -->
            <div id="snapshot-loading" class="hidden">...</div>

            <!-- Error State (reuse existing ID) -->
            <div id="snapshot-error" class="hidden">...</div>

            <!-- Empty State (reuse existing ID) -->
            <div id="snapshot-empty" class="hidden">...</div>

            <!-- Snapshot Timeline (reuse existing ID) -->
            <div id="snapshot-timeline" class="hidden">
                <div id="snapshot-list" class="space-y-2 snapshot-timeline-container"></div>
            </div>
        </div>
    </div>
</div>
```

### JavaScript Changes

Update `workshopTabs._onTabActivated()`:
```javascript
if (tabName === 'snapshots') {
    if (typeof snapshotState !== 'undefined' && !snapshotState.isLoaded) {
        loadSnapshots();
    }
}
```

Empty the sidebar's `sidebar-tab-snapshots` content with redirect notice.

**Note**: Snapshot restore/delete modals (`snapshot-restore-modal`, `snapshot-delete-modal`) are already at document top level (lines 1138-1178). They need no changes.

---

## Backend Changes

None. All existing endpoints used:
- `GET /playlist/<id>/schedules` — list schedules for playlist
- `PUT /schedules/<id>` — update schedule
- `POST /schedules/<id>/toggle` — toggle schedule
- `DELETE /schedules/<id>` — delete schedule
- `GET /playlist/<id>/snapshots` — list snapshots
- `POST /playlist/<id>/snapshots` — create manual snapshot
- `POST /snapshots/<id>/restore` — restore from snapshot
- `DELETE /snapshots/<id>` — delete snapshot

## Verification

### Schedules
1. Click Schedules tab → loads schedule list on first activation
2. Schedule cards render with correct type badges and status
3. Schedule edit (inline frequency/time changes) works
4. Schedule toggle (enable/disable) works
5. Schedule delete works
6. "Manage All Schedules" link navigates to /schedules page

### Snapshots
1. Click Snapshots tab → loads snapshot timeline on first activation
2. Snapshot timeline shows entries with type-based color coding
3. "Take Snapshot" button opens form, creates manual snapshot
4. Click restore on a snapshot → modal appears, restore works
5. Click delete on a snapshot → modal appears, delete works
6. After a shuffle preview or commit, snapshot count updates if tab is revisited

### General
7. All four tabs (Raids, Rotation, Schedules, Snapshots) work independently
8. Sidebar content shows "moved to tab" messages
9. `flake8 shuffify/` and `pytest tests/ -v` pass
