# Phase 3: Archive Playlist Pairing — Implementation Plan

## PR Title
`feat: Add archive playlist pairing for workshop track removal recovery (#phase-3)`

## Risk Level: Medium
- New database model with migration
- New Spotify API calls (create playlist, add items, remove items)
- Modifies workshop JavaScript (critical user-facing path)
- New route module and service

## Effort: ~3-4 hours

## Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `migrations/versions/<hash>_add_playlist_pairs_table.py` | Alembic migration for `playlist_pairs` table |
| `shuffify/services/playlist_pair_service.py` | CRUD and archive/unarchive logic |
| `shuffify/routes/playlist_pairs.py` | REST endpoints for pair management |
| `shuffify/schemas/playlist_pair_requests.py` | Pydantic validation schemas |
| `tests/services/test_playlist_pair_service.py` | Service layer tests |
| `tests/routes/test_playlist_pairs_routes.py` | Route integration tests |
| `tests/schemas/test_playlist_pair_requests.py` | Schema validation tests |

### Modified Files
| File | Change |
|------|--------|
| `shuffify/models/db.py` | Add `PlaylistPair` model, add `playlist_pairs` relationship to `User` |
| `shuffify/enums.py` | Add `ARCHIVE_TRACKS`, `UNARCHIVE_TRACKS`, `PAIR_CREATE`, `PAIR_DELETE` to `ActivityType` |
| `shuffify/services/__init__.py` | Export `PlaylistPairService` and exceptions |
| `shuffify/schemas/__init__.py` | Export new schemas |
| `shuffify/routes/__init__.py` | Import `playlist_pairs` route module |
| `shuffify/templates/workshop.html` | Add archive queue to JS state, modify `deleteTrack()`, add Archive tab UI, add commit-time archive logic |
| `CHANGELOG.md` | Add entry under `[Unreleased]` |

---

## Context

When users remove tracks in the Playlist Workshop today, those tracks are gone forever. There is no recovery path short of remembering the track and manually re-adding it. For "set-and-forget" playlist curation workflows -- where a user periodically trims stale songs -- this means valuable tracks are lost.

Archive Playlist Pairing solves this by linking a "production" playlist to a companion "archive" playlist. When a track is removed from the production playlist in the workshop, it is automatically queued to be added to the archive playlist on commit. Users can later browse the archive and unarchive tracks back into production.

---

## Dependencies

- **Phase 1 (Sidebar Tabs)**: The archive UI lives in the workshop sidebar's Archive tab.
- **Alembic migrations**: The most recent migration is `05ca11d7c80b` (activity_log table).

---

## Detailed Implementation

### Step 1: PlaylistPair Model (`shuffify/models/db.py`)

Add a `PlaylistPair` model with: `user_id` (FK), `production_playlist_id`, `production_playlist_name`, `archive_playlist_id`, `archive_playlist_name`, `auto_archive_on_remove` (boolean, default True), `created_at`, `updated_at`. Unique constraint on `(user_id, production_playlist_id)`. Add `playlist_pairs` relationship to `User` model with `back_populates`.

### Step 2: New ActivityType Enums (`shuffify/enums.py`)

Add four new members: `ARCHIVE_TRACKS`, `UNARCHIVE_TRACKS`, `PAIR_CREATE`, `PAIR_DELETE`.

### Step 3: Alembic Migration

Generate with `flask db migrate -m "Add playlist_pairs table"`. Creates `playlist_pairs` table with index on `user_id`.

### Step 4: Pydantic Schemas (`shuffify/schemas/playlist_pair_requests.py`)

- `CreatePairRequest`: Supports two modes -- `create_new=True` (create new Spotify playlist) or `archive_playlist_id` + `archive_playlist_name` (use existing). Mutually exclusive with cross-field validation.
- `ArchiveTracksRequest`: List of `spotify:track:*` URIs (min 1), validated format.
- `UnarchiveTracksRequest`: Same validation as ArchiveTracksRequest.

### Step 5: PlaylistPairService (`shuffify/services/playlist_pair_service.py`)

Static methods: `create_pair()`, `get_pair_for_playlist()`, `get_pairs_for_user()`, `delete_pair()`, `archive_tracks()`, `unarchive_tracks()`, `create_archive_playlist()`.

Key design:
- `archive_tracks()` does NOT remove from production (workshop commit handles that via full track list replacement)
- `unarchive_tracks()` does BOTH: adds to production AND removes from archive
- `create_archive_playlist()` creates a private Spotify playlist named `"{name} [Archive]"`
- All Spotify API calls batched in groups of 100
- Custom exceptions: `PlaylistPairError`, `PlaylistPairNotFoundError`, `PlaylistPairExistsError`

### Step 6: Routes (`shuffify/routes/playlist_pairs.py`)

Six endpoints on the `main` Blueprint:
- `GET /playlist/<id>/pair` -- Get pair info (returns `paired: true/false`)
- `POST /playlist/<id>/pair` -- Create pair (create new or link existing)
- `DELETE /playlist/<id>/pair` -- Remove pairing (does NOT delete Spotify playlist)
- `POST /playlist/<id>/pair/archive` -- Archive specific tracks
- `POST /playlist/<id>/pair/unarchive` -- Unarchive tracks back to production
- `GET /playlist/<id>/pair/archive-tracks` -- List tracks in archive playlist

All endpoints require auth, check `is_db_available()`, log activities.

### Step 7: Workshop Template Changes

- Add `archiveQueue: []`, `archivePair: null`, `archiveTracks: []` to `workshopState`
- Modify `deleteTrack()`: queue URI to `archiveQueue` when pair exists and `auto_archive_on_remove` is true
- Modify `commitToSpotify()`: call `flushArchiveQueue()` after successful commit
- Modify `undoPreview()`: clear `archiveQueue` and update badge
- Add Archive panel UI in sidebar tab with: pair creation button, archive info display, archive track list with restore buttons
- Add `loadArchivePairInfo()`, `createArchivePair()`, `deleteArchivePair()`, `loadArchiveTracks()`, `flushArchiveQueue()`, `unarchiveTrack()`, `updateArchiveQueueBadge()`, `renderArchivePanel()`, `renderArchiveTrackList()` functions

---

## Test Plan

### Service Tests (~30 tests)
- Create pair (success, duplicate raises, different playlists, different users)
- Get pair (found, not found, wrong user)
- List pairs (multiple, empty, scoped to user)
- Delete pair (success, wrong user, nonexistent)
- Archive tracks (success, empty list, wrong user, large batch of 150+)
- Unarchive tracks (success, empty list, wrong user)
- Create archive playlist (success, API error)
- PlaylistPair.to_dict() serialization

### Schema Tests (~12 tests)
- CreatePairRequest: create_new mode, existing playlist mode, both modes raises, neither raises, missing name raises, whitespace handling, extra fields ignored
- ArchiveTracksRequest: valid URIs, single URI, empty list raises, invalid format raises
- UnarchiveTracksRequest: valid URIs, empty list raises, invalid format raises

### Route Tests (~8 tests)
- Authentication required on all endpoints
- GET pair returns paired=false when no pair exists
- POST pair with invalid body returns 400
- DELETE pair unauthenticated returns 401
- Archive/unarchive unauthenticated returns 401

---

## Stress Testing and Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| User removes 200+ tracks then commits | Archive queue batches in groups of 100 |
| User removes track, undoes, then commits | `undoPreview()` clears queue -- reverted tracks NOT archived |
| Pair deleted before commit | `flushArchiveQueue()` checks pair exists, silently discards if null |
| Archive playlist deleted on Spotify externally | API error caught and shown; production commit still succeeds |
| Same track in both playlists | Allowed (Spotify supports duplicates) |
| No DB available | All pair endpoints return 503; workshop works normally |
| Special characters in playlist name | `escapeHtml()` in JS; parameterized queries in SQL |
| User navigates away without committing | Archive queue lost (client-side only, intentional) |

---

## Verification Checklist

- [ ] `flake8 shuffify/` passes
- [ ] `pytest tests/ -v` passes
- [ ] Migration applies cleanly (`flask db upgrade`) and downgrades cleanly
- [ ] PlaylistPair model has `to_dict()`, `__repr__()`, unique constraint
- [ ] All routes require auth and check `is_db_available()`
- [ ] `deleteTrack()` queues to archive only when pair exists with auto_archive enabled
- [ ] `undoPreview()` clears archive queue
- [ ] `commitToSpotify()` flushes archive queue after successful commit only
- [ ] Archive flush failure puts URIs back in queue
- [ ] Activity log entries for all four new activity types
- [ ] CHANGELOG.md updated

---

## What NOT To Do

1. **Do NOT remove tracks from production in `archive_tracks()`.** Workshop commit handles this.
2. **Do NOT archive at delete-button-click time.** Archive at COMMIT time only.
3. **Do NOT use `playlist_replace_items()` for the archive.** Use `playlist_add_items()` to append.
4. **Do NOT create the archive playlist inside `create_pair()`.** Route handler calls `create_archive_playlist()` first, then passes result to `create_pair()`.
5. **Do NOT make archive blocking for commit.** Archive is best-effort, runs after commit succeeds.
6. **Do NOT use `backref`** on the User relationship. Use `back_populates`.
7. **Do NOT forget null checks** for `workshopState.archivePair` in all JS functions.
8. **Do NOT store archive queue in session/database.** Client-side JS only.
9. **Do NOT create a new Blueprint.** Use existing `main` Blueprint.
10. **Do NOT import `PlaylistPair` directly in routes.** Use `PlaylistPairService`.
