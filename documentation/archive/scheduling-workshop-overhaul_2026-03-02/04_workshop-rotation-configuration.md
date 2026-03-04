# Phase 04: Workshop as Rotation Configuration Hub

**Status**: ✅ COMPLETE
**Started**: 2026-03-03
**Completed**: 2026-03-03
**PR**: #126

## Problem Statement

Rotation (cycling tracks between a production playlist and an archive) requires an archive pair, but pair creation and rotation scheduling are disconnected. The Workshop sidebar's "Archive" tab handles pairing, while the Scheduler handles rotation schedules. Users must leave the Workshop to configure rotation parameters, and the Scheduler shows a static warning about needing a pair with no way to create one.

## Current Architecture

- **Archive Tab** (Workshop sidebar): Create/delete archive pairs, browse archived tracks. Only supports auto-creation (no "select existing playlist" option).
- **Rotation Tab** (Schedules tab in sidebar): Read-only status, links out to `/schedules`
- **PlaylistPairService**: Full CRUD for pairs, `create_archive_playlist()` auto-creates with "[Archive]" suffix
- **Rotate Executor**: Validates pair exists at execution time, supports ARCHIVE_OLDEST/REFRESH/SWAP modes
- **Schedule CRUD**: Existing POST/PUT/DELETE `/schedules` endpoints

## Design

### Enhanced Archive Tab

Rename to "Archive & Rotation". Add three improvements:

1. **"Link Existing Playlist"** option alongside auto-create (uses existing `CreatePairRequest` schema which supports `archive_playlist_id`)
2. **Auto-archive toggle** for `auto_archive_on_remove` (new PATCH endpoint)
3. **Inline Rotation Schedule section** — create/edit/pause/delete rotation schedules directly in the sidebar, using existing `/schedules` API endpoints

### Inline Rotation Schedule

- **No schedule exists**: Compact form with mode dropdown, count input, frequency dropdown, "Enable Rotation" button
- **Schedule exists**: Summary card with mode/count/frequency, Active/Paused badge, Edit/Pause/Delete buttons

### Simplified Rotation Tab

Replace detailed status with a pointer: "Configure in Archive tab" button that switches sidebar tabs.

## Implementation Plan

### Step 1: Enhance Archive tab header and pair card

**File**: `shuffify/templates/workshop.html`

Rename heading. Add status indicator, auto-archive toggle.

### Step 2: Add "Link Existing Playlist" option

**File**: `shuffify/templates/workshop.html`

Add playlist select dropdown (populated from `/api/user-playlists`) alongside auto-create button. New `linkExistingArchive()` JS function POSTs to `POST /playlist/<id>/pair` with `create_new: false`.

### Step 3: Add inline rotation schedule configuration

**File**: `shuffify/templates/workshop.html`

Add "Rotation Schedule" section within `archive-paired` div with creation form and summary card modes. JS functions: `loadRotationConfig()`, `createRotationSchedule()`, `updateRotationSchedule()`, `toggleRotationSchedule()`, `deleteRotationSchedule()`. All use existing `/schedules` API endpoints.

### Step 4: Add PATCH endpoint for auto_archive_on_remove

**File**: `shuffify/routes/playlist_pairs.py`

New `PATCH /playlist/<id>/pair` endpoint.

**File**: `shuffify/services/playlist_pair_service.py`

New `update_pair()` static method.

**File**: `shuffify/schemas/playlist_pair_requests.py`

New `UpdatePairRequest` model.

### Step 5: Simplify Rotation tab

**File**: `shuffify/templates/workshop.html`

Replace detailed status with brief summary + "Configure in Archive tab" button.

### Step 6: Wire data flow

**File**: `shuffify/templates/workshop.html`

`loadArchivePairInfo()` also calls `loadRotationConfig()` when pair found. Store `workshopState.rotationSchedule`.

## Test Plan

- PATCH endpoint tests (4 tests)
- UpdatePairRequest schema tests (3 tests)
- PlaylistPairService.update_pair() tests (3 tests)
- Manual E2E: create pair both ways, toggle auto-archive, create/edit/pause/delete rotation, tab switching
- Total: ~12 new automated tests

## Files Modified

| File | Change |
|------|--------|
| `shuffify/templates/workshop.html` | Major: restructure archive tab, add rotation config, simplify rotation tab, ~200 lines JS |
| `shuffify/routes/playlist_pairs.py` | Add PATCH route |
| `shuffify/services/playlist_pair_service.py` | Add `update_pair()` |
| `shuffify/schemas/playlist_pair_requests.py` | Add `UpdatePairRequest` |
| Tests: routes, schemas, services | ~12 new tests |

## Risk Assessment

Low. Schedule CRUD uses existing endpoints. PATCH endpoint is a straightforward single-field update. No database migration needed. Workshop template grows by ~200 lines (acceptable for this phase).
