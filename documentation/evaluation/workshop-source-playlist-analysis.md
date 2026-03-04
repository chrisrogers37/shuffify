# Workshop "Source Playlists" — Role Analysis

**Date**: 2026-03-04
**Status**: Under Review

---

## Problem Statement

The workshop sidebar currently shows a "Source Playlists" section that implies a core workflow of **Source Playlist → Target Playlist**. This framing is misleading — the foundational workshop flows are:

1. **Raid Sources → Review → Target Playlist** (discover and import new tracks)
2. **Archive/Rotation → Target Playlist** (cycle tracks from a linked backup)

"Source Playlists" overlaps with raiding but lacks the persistence, scheduling, and review workflow that makes raiding valuable.

---

## Current Implementation

### What "Source Playlists" Does Today

- Session-only browsing tool (stored in `session['external_playlist_history']`)
- Load another playlist's tracks into the sidebar to browse and manually add to target
- No database persistence — disappears when the session ends
- No automation, no scheduling, no review queue

### Routes Involved

- `workshop_load_external_playlist()` — load by URL or search query
- `workshop_search_playlists()` — search Spotify catalog

### Where It Lives

- Workshop sidebar, currently alongside Raid Sources and Archive/Backup
- Uses `PlaylistService` directly (no dedicated service)

---

## The Three Workshop Import Mechanisms — Compared

| Feature | Source Playlists | Raid Sources | Archive/Rotation |
|---------|-----------------|--------------|------------------|
| **Scope** | Session-only | Persistent (DB) | Persistent (DB) |
| **Storage** | Browser session | `UpstreamSource` table | `PlaylistPair` table |
| **Persistence** | Temporary | Permanent until removed | Permanent until unlinked |
| **Automation** | Manual only | Scheduled via APScheduler | Scheduled via APScheduler |
| **Review step** | None (direct add) | Tracks appear for review | Rotation managed automatically |
| **Core use case** | Quick one-off browsing | Continuous track discovery | Track lifecycle management |
| **Dedicated service** | None | `RaidSyncService`, `UpstreamSourceService` | `PlaylistPairService` |

---

## Analysis

### Why "Source Playlists" Feels Out of Place

1. **Redundant with Raids**: Raiding already covers "pull tracks from other playlists" with better UX, persistence, and automation
2. **Misleading name**: "Source Playlist" implies a directional pipeline (source → target) that isn't the actual mental model. The workshop is about **curating a target playlist** using tools
3. **No persistence**: Being session-only means users can't build up a workflow around it
4. **No review step**: Tracks are added directly without the review/approval step that raids provide

### What It Does Well

- **Low friction**: No setup required, just paste a URL and browse
- **Exploration**: Good for one-off "let me see what's in this playlist" moments
- **No commitment**: Doesn't create database records or watch relationships

---

## Options for Resolution

### Option A: Remove from Workshop Sidebar

Remove "Source Playlists" entirely. Raids already serve the "import from other playlists" use case with superior UX. Users who want quick one-off imports can add a raid source and remove it after.

**Pros**: Cleaner UI, less confusion, fewer concepts to learn
**Cons**: Loses the low-friction browsing capability

### Option B: Rename and Reframe

Rename to "Browse Playlist" or "Quick Import" and move it to a secondary/utility location. Make it clear this is a lightweight tool, not a core workflow.

**Pros**: Preserves utility, reduces confusion
**Cons**: Still some conceptual overlap with raids

### Option C: Merge into Raids as "Temporary Source"

Add a "one-time import" mode to raids — watch a source, pull tracks once, auto-unwatch. Gets the low-friction benefit with the raid review workflow.

**Pros**: Single concept for all external imports, consistent UX
**Cons**: Adds complexity to raid system

### Option D: Evolve into "Random Sampler"

Transform into a distinct tool: pick a source playlist → randomly sample N tracks → add to target. This would be genuinely different from raids (which pull all new tracks).

**Pros**: Unique capability, clear differentiation from raids
**Cons**: New feature scope, may not be needed

---

## Recommendation

**TBD** — awaiting decision on preferred direction.

---

## Related Code

- `shuffify/routes/workshop.py` — workshop route handlers
- `shuffify/templates/workshop.html` — workshop UI template
- `shuffify/services/raid_sync_service.py` — raid orchestration
- `shuffify/services/upstream_source_service.py` — raid source management
- `shuffify/services/playlist_pair_service.py` — archive pairing
