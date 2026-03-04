# Phase 03: Workshop as Raid Configuration Hub

**Status**: ✅ COMPLETE
**Started**: 2026-03-03
**Completed**: 2026-03-03
**PR**: #126

## Problem Statement

Raid source configuration is disconnected from the Workshop. The schedule form populates "Source Playlists" from the user's own Spotify playlists (`schedules.html:174` iterates over `playlists`), which is wrong — raid sources should be EXTERNAL playlists from curators and tastemakers. The Workshop needs a URL-based input where users paste Spotify playlist URLs to register external upstream sources.

The system already has a `PublicScraperPathway` that extracts track URIs from Spotify's public web pages (handling the Feb 2026 API restriction), an `UpstreamSource` model, and `UpstreamSourceService` for CRUD. These just need to be wired into the Workshop UI.

## Current Architecture

- **Raid Panel** (Workshop sidebar): "Quick Watch" dropdown populated from user's own playlists
- **UpstreamSourceService**: CRUD for `UpstreamSource` records
- **RaidSyncService**: Orchestrates watch/unwatch with schedule auto-creation
- **PublicScraperPathway**: Scrapes embed/public Spotify pages for track URIs (3-tier resolver chain)
- **URL Parser**: `shuffify/spotify/url_parser.py` accepts URLs, URIs, and bare IDs
- **UpstreamSource model**: Stores `source_playlist_id`, `source_url`, `source_type`, `source_name`

## Design

### User Flow

1. User opens Workshop, clicks Raids tab
2. At top: "Add External Source" section with URL text input
3. User pastes `https://open.spotify.com/playlist/XXXXX`
4. System validates URL, checks it's external (not user's own), probes via scraper for track count
5. Creates `UpstreamSource` with `source_type="external"`
6. Source appears in watched list with name and track count
7. ~~Existing "Watch Own Playlist" dropdown moves to collapsed secondary section~~ REMOVED — "Watch Own Playlist" dropdown removed entirely (raids are external-only; use rotation for own playlists)

### UI Layout

```
+------------------------------------------+
| ADD EXTERNAL SOURCE                      |
| [Paste Spotify playlist URL...     ] [+] |
| (validation feedback area)               |
|                                          |
| WATCHED SOURCES (3/10)                   |
| | Discover Weekly    external [Remove] | |
| |   47 tracks                          | |
| | RapCaviar          external [Remove] | |
|                                          |
+------------------------------------------+
```

### New Endpoint: `POST /playlist/<id>/raid-add-url`

**Request**: `{ "url": "https://open.spotify.com/playlist/XXX" }`

**Flow**:
1. Parse URL via `parse_spotify_playlist_url()`
2. Guard: not self-referencing
3. Guard: source count < 10 per target
4. Get playlist metadata via `PlaylistService.get_playlist()`
5. Guard: owner is not current user (external-only)
6. Best-effort probe via `PublicScraperPathway` for track count (non-blocking — failure does not prevent add)
7. Register via `RaidSyncService.watch_playlist()` with `source_type="external"`

**Responses**: 200 success with source + track_count (nullable), 400 for validation errors, 404 for missing playlist

## Implementation Plan

### Step 1: Add `AddRaidUrlRequest` Pydantic schema

**File**: `shuffify/schemas/raid_requests.py`

New schema with `url`, `auto_schedule`, `schedule_value` fields. URL validator ensures non-empty, max 1024 chars.

### Step 2: Add source count limit to UpstreamSourceService

**File**: `shuffify/services/upstream_source_service.py`

Add `MAX_SOURCES_PER_TARGET = 10`, `count_sources()` method, and limit enforcement in `add_source()`.

### Step 3: Add `raid-add-url` route

**File**: `shuffify/routes/raid_panel.py`

New `POST /playlist/<id>/raid-add-url` with URL parsing, ownership check, scraper validation, and `RaidSyncService.watch_playlist()` call.

### Step 4: Add `source_type` parameter to `RaidSyncService.watch_playlist()`

**File**: `shuffify/services/raid_sync_service.py`

Add `source_type="own"` default parameter, pass through to `UpstreamSourceService.add_source()`. Also add `max_sources` to `get_raid_status()` response.

### Step 5: Add `last_track_count` column to UpstreamSource

**File**: `shuffify/models/db.py`

Add nullable integer column. Update `to_dict()`. Generate Alembic migration.

### Step 6: Restructure Raids tab HTML

**File**: `shuffify/templates/workshop.html`

Replace "Quick Watch" section with URL input as primary action. Remove own-playlist dropdown entirely (challenge round decision: raids are external-only). Add source count badge.

### Step 7: Add JavaScript for `addExternalUrl()`

**File**: `shuffify/templates/workshop.html`

Client-side URL pre-validation, fetch to new endpoint, feedback display, Enter key support. Update `render()` for source count badge and limit state.

### Step 8: Update `renderSources()` for track counts

**File**: `shuffify/templates/workshop.html`

Display `last_track_count` in source cards when available.

## Test Plan

- Schema validation tests (5 tests)
- Source count enforcement tests (4 tests)
- Route tests for `raid-add-url` (12 tests covering auth, validation, self-reference, ownership, success, duplicates)
- Model migration test (2 tests)
- Manual E2E testing of URL paste flow

## Files Modified

| File | Change |
|------|--------|
| `shuffify/schemas/raid_requests.py` | Add `AddRaidUrlRequest` |
| `shuffify/schemas/__init__.py` | Export new schema |
| `shuffify/services/upstream_source_service.py` | Add limit + count method |
| `shuffify/services/raid_sync_service.py` | Add `source_type` param, `max_sources` in status |
| `shuffify/routes/raid_panel.py` | Add `raid-add-url` route |
| `shuffify/models/db.py` | Add `last_track_count` to UpstreamSource |
| `shuffify/templates/workshop.html` | Restructure raids tab, add JS |
| `migrations/versions/xxx_*.py` | Alembic migration |
| Tests: schemas, services, routes | ~23 new tests |

## Risk Assessment

Low-Medium. Scraper may fail for some playlists but we still allow adding (resolver chain retries during execution). Migration is nullable column addition (zero downtime). `source_type` parameter defaults to `"own"` for backward compatibility.
