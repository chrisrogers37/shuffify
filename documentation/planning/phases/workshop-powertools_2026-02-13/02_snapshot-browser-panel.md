# Phase 2: Snapshot Browser Panel -- Implementation Plan

**Status:** ✅ COMPLETE
**Started:** 2026-02-14
**Completed:** 2026-02-14
**PR:** #65

## 1. Header

| Field | Value |
|---|---|
| **PR Title** | `feat: Add snapshot browser panel to workshop sidebar (#phase-2)` |
| **Phase** | Workshop Powertools Phase 2 of 5 |
| **Risk Level** | Low -- frontend-only changes consuming existing backend API |
| **Estimated Effort** | 3-4 hours |
| **Depends On** | Phase 1 (Sidebar Framework with Snapshots tab placeholder) |
| **Files Modified** | `shuffify/templates/workshop.html`, `CHANGELOG.md` |
| **Files Created** | None |
| **Files Deleted** | None |
| **Backend Changes** | None -- all five snapshot API endpoints already exist |

---

## 2. Context

The snapshot system was built end-to-end in the User Persistence Enhancement Suite (Phase 4, PR #59). It includes a `PlaylistSnapshot` database model, a `PlaylistSnapshotService` with full CRUD and retention enforcement, five JSON API endpoints in `shuffify/routes/snapshots.py`, and automatic snapshot creation before shuffles, raids, and commits. However, the entire system is invisible to users. There is no UI for browsing, creating, restoring, or deleting snapshots.

This phase surfaces the hidden infrastructure as a Snapshot Browser panel inside the workshop sidebar's "Snapshots" tab. Users will finally be able to see their backup history, take manual snapshots, preview track differences, restore previous states, and manage snapshot retention -- all without leaving the workshop.

---

## 3. Dependencies

**Hard dependency**: Phase 1 must be complete. Phase 1 introduces the sidebar panel framework with tab switching. This phase replaces the Snapshots tab placeholder content with the real snapshot browser.

**Phase 1 contract**: After Phase 1, the workshop template will contain:
- A sidebar `<div>` with tab navigation including a "Snapshots" tab
- A `<div id="sidebar-tab-snapshots">` container that holds placeholder content
- A `workshopSidebar.switchTab(tabName)` function
- Tab activation hooks called from `switchTab()` when tabs become active

**Existing API Endpoints consumed** (all in `shuffify/routes/snapshots.py`):
- `GET /playlist/<playlist_id>/snapshots?limit=20` -- List snapshots (JSON)
- `POST /playlist/<playlist_id>/snapshots` -- Create manual snapshot (JSON body: `playlist_name`, `track_uris`, `trigger_description`)
- `GET /snapshots/<snapshot_id>` -- View snapshot details (JSON)
- `POST /snapshots/<snapshot_id>/restore` -- Restore snapshot (replaces current playlist tracks on Spotify)
- `DELETE /snapshots/<snapshot_id>` -- Delete snapshot

**Pydantic schema** for manual snapshot creation (`shuffify/schemas/snapshot_requests.py`):
```python
class ManualSnapshotRequest(BaseModel):
    playlist_name: str  # 1-255 chars, required
    track_uris: List[str]  # list of "spotify:track:*" URIs, required
    trigger_description: Optional[str]  # max 500 chars, optional
```

**Snapshot types** (from `shuffify/enums.py`):
- `auto_pre_shuffle`, `auto_pre_raid`, `auto_pre_commit`, `manual`, `scheduled_pre_execution`

---

## 4. Detailed Implementation Plan

All changes are in a single file: `shuffify/templates/workshop.html`.

### 4.1 Add `playlistName` to workshopState

The existing `workshopState` object does not store the playlist name. The manual snapshot creation endpoint (`POST /playlist/<id>/snapshots`) requires `playlist_name` in its request body. We need to add it.

**In the `workshopState` initialization block** (currently at line 355 of `workshop.html`), add one property:

```javascript
const workshopState = {
    playlistId: {{ playlist.id | tojson }},
    playlistName: {{ playlist.name | tojson }},  // <-- ADD THIS LINE
    savedUris: {{ playlist.tracks | map(attribute='uri') | list | tojson }},
    workingUris: {{ playlist.tracks | map(attribute='uri') | list | tojson }},
    // ... rest of existing properties
};
```

### 4.2 Replace Snapshot Tab Placeholder with Real Panel HTML

Replace the placeholder content inside the `#sidebar-tab-snapshots` container (provided by Phase 1) with the full snapshot browser panel. See the complete HTML in the implementation (includes Take Snapshot button, description input, snapshot list container with loading/empty/error states, and snapshot timeline).

### 4.3 Add Confirmation Modals

Add restore and delete confirmation modals (z-50) at the end of the `{% block content %}` section, following the same pattern used in `schedules.html`.

### 4.4 Complete JavaScript Implementation

Add a new JavaScript section with:
- `snapshotState` object for tracking panel state
- `SNAPSHOT_TYPE_CONFIG` mapping for color-coded type badges (blue=Pre-Shuffle, yellow=Pre-Raid, purple=Pre-Commit, green=Manual, orange=Scheduled)
- `formatRelativeTime()` and `formatFullDate()` utility functions
- `loadSnapshots()` -- fetches from `GET /playlist/<id>/snapshots?limit=50`
- `renderSnapshotTimeline()` -- builds snapshot cards with type badges, timestamps, track count diffs, and action buttons
- `openManualSnapshotDialog()`, `cancelManualSnapshot()`, `confirmManualSnapshot()` -- manual snapshot creation flow
- `showRestoreModal()`, `hideRestoreModal()`, `executeRestore()` -- restore with confirmation
- `showDeleteModal()`, `hideDeleteModal()`, `executeDelete()` -- delete with confirmation
- `onSnapshotsTabActivated()` -- lazy-loading hook called by Phase 1's tab system

### 4.5 Integration Points

1. **Tab activation**: `onSnapshotsTabActivated()` is called by Phase 1's `switchTab()` method when the Snapshots tab is selected. Loads data on first activation only.
2. **Post-commit refresh**: Add one line after successful `commitToSpotify()`: `if (snapshotState.isLoaded) { setTimeout(function() { loadSnapshots(); }, 500); }` -- refreshes to show new auto-snapshot.
3. **Post-restore behavior**: Page reloads after successful restore to get fresh track data from Spotify.

### 4.6 CSS Additions

Add snapshot card hover effects and timeline bottom fade gradient to the existing `<style>` block.

---

## 5. Test Plan

### Backend Tests (Already Passing)
No new backend tests needed. Run existing snapshot tests to confirm: `pytest tests/test_snapshot_routes.py -v`

### Full Test Suite
`pytest tests/ -v` -- verify no regressions.

### Lint Check
`flake8 shuffify/`

### Manual Verification Steps (16 scenarios)
1. Empty state renders correctly for playlists with no snapshots
2. Loading spinner shows while fetching
3. Snapshot timeline displays with correct type badges and colors
4. Track count diff shows correctly ("+3 vs current" / "-5 vs current")
5. Manual snapshot creation with description
6. Manual snapshot with empty description (uses default)
7. Cancel manual snapshot flow
8. Restore snapshot confirmation modal
9. Restore snapshot execution (page reloads)
10. Delete snapshot confirmation modal
11. Delete snapshot execution (list refreshes)
12. Auto-refresh after commit
13. Error handling for network failures
14. Error handling for expired sessions
15. All existing workshop features still work
16. Accessibility attributes present

---

## 6. Documentation Updates

### CHANGELOG.md
```markdown
### Added
- **Snapshot Browser Panel** - Workshop sidebar panel for browsing, creating, restoring, and deleting playlist snapshots
  - Chronological timeline with color-coded type badges (manual, pre-shuffle, pre-raid, pre-commit, scheduled)
  - Manual snapshot creation with optional description
  - Restore confirmation modal with track count diff preview
  - Delete confirmation modal with safety prompt
  - Auto-refresh after snapshot operations and playlist commits
  - Empty state, loading state, and error state handling
```

---

## 7. Stress Testing and Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| 50 snapshots (max retention) | Timeline scrolls, gradient mask indicates more content |
| Restoring while workshop has unsaved changes | Restore modal warns about replacement; page reloads |
| Creating manual snapshot with empty playlist | Creates snapshot with track_count=0 |
| Rapid-fire snapshot creation | `isCreating` flag prevents double-submission |
| Restoring tracks that no longer exist on Spotify | Spotify silently ignores invalid URIs |
| Concurrent tab usage | No cross-tab sync (acceptable); refreshes on tab activation |
| Database unavailable | 503 error shown with "Try again" button |

---

## 8. Verification Checklist

- [ ] `flake8 shuffify/` passes with 0 errors
- [ ] `pytest tests/ -v` passes all tests
- [ ] Phase 1 Snapshots tab placeholder is fully replaced
- [ ] `workshopState.playlistName` added and correctly rendered from Jinja2
- [ ] `onSnapshotsTabActivated()` wired into Phase 1's tab switching
- [ ] Snapshot list loads on first tab activation (lazy loading)
- [ ] Snapshot list refreshes after create, restore, and delete operations
- [ ] Snapshot list refreshes after successful commit-to-Spotify
- [ ] All five API endpoints called with correct URLs and HTTP methods
- [ ] Restore and delete confirmation modals show before destructive actions
- [ ] Page reloads after successful restore
- [ ] All snapshot type badges have distinct colors
- [ ] CHANGELOG.md updated

---

## 9. What NOT To Do

1. **Do NOT create new backend routes.** All five snapshot endpoints exist and are tested.
2. **Do NOT add new Python dependencies.**
3. **Do NOT modify `shuffify/routes/snapshots.py`** or any other Python file.
4. **Do NOT use `window.confirm()`** for restore or delete. Use proper modals.
5. **Do NOT attempt to update the track list in-place after restore.** Use full page reload.
6. **Do NOT make the snapshot panel poll on a timer.** Load on tab activation + auto-refresh after operations.
7. **Do NOT store snapshot data in the Flask session.**
8. **Do NOT bypass Pydantic validation** for manual snapshot creation.
9. **Do NOT render `track_uris` in snapshot cards.** Only use `track_count` for display.
10. **Do NOT break existing workshop functionality.** Test all features after changes.
