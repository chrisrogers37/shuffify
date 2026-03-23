# Raid Playlist Redesign

**Date**: 2026-03-23
**Status**: Design Draft

---

## Problem Statement

Rotation is a clean, inspectable system: two real Spotify playlists (production ↔ archive) linked via `PlaylistPair`. Users can open the archive in Spotify and see exactly what's there.

Raid's staging area is invisible outside the app. Raided tracks live in a `PendingRaidTrack` database table, only surfacing through the web UI's Track Inbox. You can't browse your raid pipeline from your phone.

## Design Goal

Mirror Rotation's playlist-pairing model for Raid:
- A **Raid Playlist** (real Spotify playlist) linked to the target, just like Archive
- Multiple upstream sources feed into the Raid Playlist, each with configurable track counts
- A configurable **drip rate** moves tracks from Raid Playlist into the target
- DB provenance tracking runs in parallel for metadata and deduplication
- Full snapshot support

---

## System Overview

```
┌──────────────────────────────────────────────────────┐
│  UPSTREAM (Ingest)                                    │
│                                                       │
│  Source A (raid_count=3) ──┐                          │
│  Source B (raid_count=5) ──┼──► [Raid Playlist]       │
│  Source C (raid_count=2) ──┘    (real Spotify playlist)│
│                                  + PendingRaidTrack DB │
└──────────────────────┬───────────────────────────────┘
                       │ drip_count=N per execution
                       ▼
┌──────────────────────────────────────────────────────┐
│  TARGET PLAYLIST (Production)                         │
│  New tracks added to top                              │
└──────────────────────┬───────────────────────────────┘
                       │ rotation swaps
                       ▼
┌──────────────────────────────────────────────────────┐
│  ARCHIVE PLAYLIST (Rotation downstream)               │
└──────────────────────────────────────────────────────┘
```

### Full Linked System for Deduplication

```
Upstream Sources → Raid Playlist → Target Playlist → Archive Playlist
                   ▲                                       │
                   └── dedupe checks entire chain ──────────┘
```

---

## Data Model Changes

### 1. New Model: `RaidPlaylistLink`

Links a target playlist to its raid staging playlist. Separate from `PlaylistPair` because the configuration and behavior are different.

```python
class RaidPlaylistLink(db.Model):
    __tablename__ = "raid_playlist_links"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # The playlist being fed into
    target_playlist_id = db.Column(db.String(255), nullable=False)
    target_playlist_name = db.Column(db.String(255))

    # The raid staging playlist (real Spotify playlist)
    raid_playlist_id = db.Column(db.String(255), nullable=False)
    raid_playlist_name = db.Column(db.String(255))

    # Drip configuration
    drip_count = db.Column(db.Integer, default=3, nullable=False)  # tracks per drip execution
    drip_enabled = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "target_playlist_id", name="uq_raid_link_user_target"),
    )
```

### 2. Modified Model: `UpstreamSource`

Add per-source raid count.

```python
# NEW FIELD on UpstreamSource:
raid_count = db.Column(db.Integer, default=5, nullable=False)
# Number of tracks to pull from this source per raid execution
```

### 3. `PendingRaidTrack` — Keep As-Is

The DB table continues to track provenance (which source a track came from, when it was staged, promote/dismiss status). Tracks exist in BOTH the Raid Playlist AND the DB.

### 4. New Enum Values

```python
# SnapshotType
AUTO_PRE_DRIP = "auto_pre_drip"

# JobType
DRIP = "drip"

# ActivityType
RAID_DRIP = "raid_drip"
RAID_LINK_CREATE = "raid_link_create"
RAID_LINK_DELETE = "raid_link_delete"
```

---

## Execution Flows

### Flow 1: Raid Execution (Ingest to Raid Playlist)

Triggered by schedule (JobType.RAID) or manual "Raid Now".

```
For each UpstreamSource linked to target:
  1. Fetch source tracks
  2. Build exclusion set = union of:
     - Target playlist tracks
     - Raid playlist tracks
     - Archive playlist tracks (if rotation pair exists)
     - Already-dismissed PendingRaidTrack URIs
  3. Select up to source.raid_count new tracks (not in exclusion set)
  4. Add selected tracks to Raid Playlist (Spotify API)
  5. Insert PendingRaidTrack records (status=PENDING) with source provenance
  6. Update source tracking fields (last_resolved_at, etc.)

Return: { tracks_added: total, tracks_total: raid_playlist_size }
```

### Flow 2: Drip Execution (Raid Playlist → Target)

Triggered by schedule (JobType.DRIP) or manual "Drip Now". Runs independently from raid.

```
1. Snapshot raid playlist (AUTO_PRE_DRIP) if enabled
2. Snapshot target playlist (AUTO_PRE_DRIP) if enabled
3. Fetch raid playlist tracks (ordered by position — FIFO)
4. Select first N tracks (N = drip_count from RaidPlaylistLink)
5. Dedupe against target (safety check)
6. Add selected tracks to TOP of target playlist
7. Remove selected tracks from raid playlist
8. Update PendingRaidTrack status → PROMOTED, set resolved_at
9. Log ActivityType.RAID_DRIP

Return: { tracks_dripped: count, raid_remaining: count, target_total: count }
```

### Flow 3: Manual Promote/Dismiss (Keep existing)

Users can still manually promote or dismiss specific tracks from the Track Inbox UI. When promoting:
- Add to target playlist
- Remove from raid playlist
- Update PendingRaidTrack status

When dismissing:
- Remove from raid playlist
- Update PendingRaidTrack status → DISMISSED

---

## Deduplication Strategy

Dedupe must check the **entire linked system**:

```python
def build_full_exclusion_set(api, target_id, user_id):
    """Build exclusion set across the full playlist chain."""
    exclusion = set()

    # 1. Target playlist tracks
    exclusion |= get_track_uris(api, target_id)

    # 2. Raid playlist tracks (if raid link exists)
    raid_link = RaidPlaylistLink.query.filter_by(
        user_id=user_id, target_playlist_id=target_id
    ).first()
    if raid_link:
        exclusion |= get_track_uris(api, raid_link.raid_playlist_id)

    # 3. Archive playlist tracks (if rotation pair exists)
    pair = PlaylistPair.query.filter_by(
        user_id=user_id, production_playlist_id=target_id
    ).first()
    if pair:
        exclusion |= get_track_uris(api, pair.archive_playlist_id)

    # 4. Dismissed tracks (prevent re-staging dismissed tracks)
    dismissed = PendingRaidTrack.query.filter_by(
        user_id=user_id,
        target_playlist_id=target_id,
        status=PendingRaidStatus.DISMISSED,
    ).all()
    exclusion |= {t.track_uri for t in dismissed}

    return exclusion
```

---

## Scheduling

### Two Independent Schedules

| Schedule | JobType | Purpose | Typical Frequency |
|----------|---------|---------|-------------------|
| Raid | `RAID` | Pull from upstream → Raid Playlist | Weekly / Every 3 days |
| Drip | `DRIP` | Move from Raid Playlist → Target | Daily |

These are independent because:
- You might raid weekly (big batch ingest) but drip daily (slow feed)
- You might pause drip while still accumulating raids
- Different frequencies make sense for different use cases

### Schedule Configuration

```json
// Raid schedule (existing, updated)
{
  "job_type": "raid",
  "target_playlist_id": "spotify_id",
  "algorithm_params": {}
  // per-source raid_count is on UpstreamSource model, not here
}

// Drip schedule (new)
{
  "job_type": "drip",
  "target_playlist_id": "spotify_id",
  "algorithm_params": {
    "drip_count": 3  // override RaidPlaylistLink default if needed
  }
}
```

---

## Snapshot Support

| Event | SnapshotType | Playlist Snapshotted |
|-------|-------------|---------------------|
| Before raid ingest | `AUTO_PRE_RAID` | Raid Playlist |
| Before drip | `AUTO_PRE_DRIP` | Both Raid Playlist + Target |
| Manual snapshot | `MANUAL` | User chooses |

---

## Comparison: Rotation vs Raid (New Design)

| Aspect | Rotation | Raid (New) |
|--------|----------|------------|
| **Direction** | Downstream (target → archive) | Upstream (sources → raid → target) |
| **Linked playlist** | Archive (via PlaylistPair) | Raid Playlist (via RaidPlaylistLink) |
| **Sources** | Target playlist itself | 1-N UpstreamSource records |
| **Per-source config** | N/A | `raid_count` per source |
| **Transfer rate** | `rotation_count` per execution | `drip_count` per execution |
| **Track placement** | Random swap | Added to top of target |
| **Scheduling** | Single schedule (ROTATE) | Two schedules (RAID + DRIP) |
| **DB tracking** | N/A | PendingRaidTrack (provenance) |
| **Dedupe scope** | Archive ↔ Target | Full chain (sources → raid → target → archive) |
| **Manual control** | N/A (fully automatic) | Promote/dismiss individual tracks |
| **Spotify visible** | Yes (archive playlist) | Yes (raid playlist) |
| **Snapshots** | AUTO_PRE_ROTATE | AUTO_PRE_RAID, AUTO_PRE_DRIP |

---

## New Services / Executors

### `raid_link_service.py` (new)

Mirrors `playlist_pair_service.py`:
- `create_link(user_id, target_id, raid_playlist_id, ...)` — Link raid playlist
- `create_raid_playlist(user_id, target_id, name)` — Create `{name} [Raids]` on Spotify + link
- `get_link_for_playlist(user_id, target_id)` — Retrieve link
- `get_links_for_user(user_id)` — List all links
- `update_link(link_id, ...)` — Update drip_count, drip_enabled
- `delete_link(link_id)` — Remove link (optionally delete Spotify playlist)

### `drip_executor.py` (new)

In `shuffify/services/executors/`:
- `execute_drip(schedule, api)` — Main drip execution
- Mirrors rotate_executor pattern (snapshot → fetch → move → verify)

### Updated `raid_executor.py`

- Raid now adds to Raid Playlist instead of only DB staging
- Uses `build_full_exclusion_set()` for chain-wide dedupe
- Per-source `raid_count` controls volume

### Updated `raid_sync_service.py`

- `watch_playlist()` — Also checks/creates RaidPlaylistLink
- `raid_now()` — Adds to Raid Playlist + DB
- `get_raid_status()` — Includes raid playlist info, drip config
- New: `drip_now()` — Manual drip trigger

---

## Route Changes

### Updated `raid_panel.py`

```
POST /playlist/<id>/raid-link          — Create raid playlist link
DELETE /playlist/<id>/raid-link        — Remove raid playlist link
PUT /playlist/<id>/raid-link           — Update drip_count, drip_enabled

POST /playlist/<id>/drip-now           — Manual drip trigger
POST /playlist/<id>/drip-schedule-toggle — Enable/disable drip schedule

# Existing promote/dismiss endpoints continue to work
# but now also remove from Raid Playlist (Spotify)
```

---

## Migration Plan

### Phase 1: Data Model
- Add `RaidPlaylistLink` model
- Add `raid_count` column to `UpstreamSource` (default=5)
- Add new enum values (DRIP, AUTO_PRE_DRIP, RAID_DRIP, etc.)
- Alembic migration

### Phase 2: Services
- Create `raid_link_service.py`
- Create `drip_executor.py`
- Update `raid_executor.py` to write to Raid Playlist
- Update `build_full_exclusion_set()` for chain-wide dedupe
- Update `raid_sync_service.py` for new flow

### Phase 3: Routes & UI
- Add raid link CRUD endpoints
- Add drip endpoints
- Update raid panel template for raid playlist management
- Update promote/dismiss to sync with Raid Playlist

### Phase 4: Scheduling
- Register DRIP job type in executor dispatch
- Add drip schedule creation in raid_sync_service
- Update scheduler startup to handle DRIP jobs

### Phase 5: Snapshots & Testing
- Snapshot support for raid playlist + drip events
- Full test suite for new models, services, executors, routes
- Integration tests for the full chain

---

## Design Decisions (Resolved)

1. **Raid playlist naming**: Auto-name as `{Target Name} [Raids]` — mirrors `{Name} [Archive]` convention
2. **Drip selection**: Random selection from raid playlist (not FIFO)
3. **Raid playlist size cap**: No cap — let it grow freely
4. **Dismissed tracks**: Removed from raid playlist + marked DISMISSED in DB. Blocked from re-staging **permanently** by default (dedupe checks dismissed URIs). A future "clear dismissed" action can reset if needed.
5. **Existing PendingRaidTrack data**: No automatic migration. Existing pending tracks stay in DB-only inbox. New flow activates when user creates a RaidPlaylistLink. No surprise playlist creation on upgrade.

## Promote/Dismiss with Raid Playlist

The raid panel continues to show track-level controls, but now backed by the real Spotify playlist:

| Action | Raid Playlist (Spotify) | PendingRaidTrack (DB) | Target Playlist |
|--------|------------------------|-----------------------|-----------------|
| **Track raided** | Added | Inserted (PENDING) | — |
| **Promote** | Removed | Status → PROMOTED | Added to top |
| **Dismiss** | Removed | Status → DISMISSED | — |
| **Drip (auto)** | Removed (random N) | Status → PROMOTED | Added to top |

The DB layer gives us:
- **Provenance**: Which source brought this track, when
- **Dedupe**: Dismissed URIs never re-raid
- **History**: Full audit trail of what was promoted/dismissed/dripped
