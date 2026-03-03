# Phase 05: Scheduler Smart Routing and Workshop Linkage

**Status**: ✅ COMPLETE
**Started**: 2026-03-03
**Completed**: 2026-03-03

## Problem Statement

The Scheduler form shows user's own playlists as raid sources (should show UpstreamSource records from Workshop). Rotation form shows a static warning (should show actual pair status). No path from Scheduler to Workshop when configuration is missing.

After Phases 03-04 establish Workshop as the configuration hub, the Scheduler must consume that data.

## Dependencies

**Hard**: Phase 03 (Workshop raid config), Phase 04 (Workshop rotation config)

## Design

### Data Flow

```
schedules() route loads:
  1. user_schedules (existing)
  2. playlists (existing)
  3. algorithms (existing)
  4. upstream_sources_map  <-- NEW: {playlist_id: [source_dicts]}
  5. pairs_by_playlist     <-- NEW: {playlist_id: pair_dict}

Template embeds as JS:
  const UPSTREAM_SOURCES = {{ upstream_sources_map | tojson }};
  const PLAYLIST_PAIRS = {{ playlist_pairs_map | tojson }};

updateModalFields() reads from these maps on target playlist change.
```

### Data Contract

**Raid source checkboxes** submit `source_playlist_id` (Spotify playlist ID from UpstreamSource record), NOT the UpstreamSource database PK. This maintains backward compatibility with existing schedules and the executor pipeline, which operates on Spotify IDs.

### UI Changes

**Raid sources**: Dynamic rendering from `UPSTREAM_SOURCES[targetId]`. If sources exist: checkboxes with external badge. If none: amber box with "Set up in Workshop" link.

**Rotation pair**: Dynamic rendering from `PLAYLIST_PAIRS[targetId]`. If pair exists: green status with archive name, show mode/count controls. If none: amber box with "Set up in Workshop" link, Create button disabled.

**Workshop deep-link**: Links carry context — `/workshop/<playlist_id>?setup=raids` or `?setup=archive`. Workshop detects query param and auto-opens correct sidebar tab.

## Implementation Plan

### Step 1: Load Workshop data in schedules route

**File**: `shuffify/routes/schedules.py`

Import `UpstreamSourceService` and `PlaylistPairService`. Call `list_all_sources_for_user()` and `get_pairs_for_user()`. Build dicts, pass to template.

### Step 2: Embed data as JavaScript in template

**File**: `shuffify/templates/schedules.html`

Add `const UPSTREAM_SOURCES = ...` and `const PLAYLIST_PAIRS = ...` at top of script block.

### Step 3: Replace static source checkboxes with dynamic rendering

**File**: `shuffify/templates/schedules.html`

Remove Jinja `{% for pl in playlists %}` loop for sources. Add `renderRaidSources(targetId)` JS function.

### Step 4: Replace static rotation warning with dynamic pair status

**File**: `shuffify/templates/schedules.html`

Remove yellow warning box. Add `renderPairStatus(targetId)` JS function.

### Step 5: Rewrite `updateModalFields()`

**File**: `shuffify/templates/schedules.html`

Add target playlist `onchange` handler. Call `renderRaidSources()` and `renderPairStatus()`. Add `updateCreateButtonState()` to disable button when prerequisites missing.

### Step 6: Add Workshop deep-link support

**File**: `shuffify/templates/workshop.html`

In sidebar `init()`, detect `?setup=raids` or `?setup=archive` query params. Auto-open sidebar to correct tab.

### Step 7: Add backend validation (defense-in-depth)

**File**: `shuffify/routes/schedules.py`

In `create_schedule()`, verify raid source IDs exist as UpstreamSource records. Verify rotate target has a PlaylistPair.

### Step 8: Tests

- Route loads upstream sources and pairs (2 tests)
- Sources grouped by target correctly (1 test)
- Empty sources/pairs handled (2 tests)
- Backend raid source validation (1 test)
- Backend rotate pair validation (1 test)
- Manual E2E: dynamic form updates, Workshop redirect flow

## Files Modified

| File | Change |
|------|--------|
| `shuffify/routes/schedules.py` | Load services, pass data to template, add validation |
| `shuffify/templates/schedules.html` | Embed JS data, dynamic rendering, form logic |
| `shuffify/templates/workshop.html` | Add `?setup` query param detection |
| `tests/routes/test_schedules_routes.py` | Add ~7 tests |

## Risk Assessment

Low. Existing raid schedules continue working (store Spotify playlist IDs directly). Data sets are small (single-digit sources, few pairs). Workshop redirect is a standard navigation link.
