# Phase 04: Workshop Graceful Degradation for External Playlists

`✅ COMPLETE` Completed: 2026-02-28

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `feat: Graceful degradation when external playlist tracks are restricted` |
| **Risk Level** | Low |
| **Estimated Effort** | Medium (2-3 hours) |
| **Dependencies** | None |
| **Blocks** | None |

---

## Motivation

The Workshop's "Load External Playlist" flow lets users search for public playlists, preview their tracks, and cherry-pick additions. After Spotify's February 2026 API changes, `get_playlist_tracks()` on non-owned playlists returns empty (the `items` field is absent). Without this fix, clicking any non-owned playlist result silently returns zero tracks with no explanation.

---

## Detection Strategy

The heuristic: if `playlist.tracks` is empty but the playlist metadata reports a non-zero track count, and the playlist owner differs from the current user, the playlist is **restricted**.

This requires propagating `total_tracks` from the playlist metadata response (available from `playlist.tracks.total` → `playlist.items.total` in the new API) into the `Playlist` model.

---

## Files to Modify

### `shuffify/models/playlist.py`

Add `total_tracks` field to the `Playlist` dataclass:

```python
@dataclass
class Playlist:
    id: str
    name: str
    owner_id: str
    description: Optional[str] = None
    total_tracks: Optional[int] = None   # NEW
    tracks: List[Dict[str, Any]] = field(default_factory=list)
    audio_features: Dict[str, Dict[str, Any]] = field(default_factory=dict)
```

Populate from `from_spotify()`:
```python
total_tracks=playlist_data.get("tracks", {}).get("total"),
```

Include in `to_dict()`.

### `shuffify/spotify/api.py`

Add `owner_id` to `search_playlists()` return dict (after the `owner_display_name` line):

```python
"owner_id": item.get("owner", {}).get("id", ""),
```

### `shuffify/routes/workshop.py`

#### Update `_load_playlist_by_url()` signature

Add `current_user_id` parameter. Update call site in `workshop_load_external_playlist()` to pass `user.spotify_id`.

#### Add restriction detection

After loading the playlist:

```python
is_restricted = (
    len(playlist.tracks) == 0
    and (playlist.total_tracks or 0) > 0
    and playlist.owner_id != current_user_id
)
```

#### Return `restricted` mode response

When restricted:
```python
return jsonify({
    "success": True,
    "mode": "restricted",
    "playlist": {
        "id": playlist.id,
        "name": playlist.name,
        "owner_id": playlist.owner_id,
        "description": playlist.description,
        "track_count": playlist.total_tracks,
    },
    "tracks": [],
    "message": "Track listing unavailable for this playlist. You can still search for individual tracks to add.",
    "suggested_search": playlist.name,
})
```

When not restricted, add `"mode": "tracks"` to the existing response.

### `shuffify/templates/workshop.html`

#### Handle `mode: "restricted"` in `loadExternalPlaylist()` response

Add new branch in the `.then(data => { ... })` handler:

```javascript
} else if (data.success && data.mode === 'restricted') {
    displayRestrictedPlaylist(data.playlist, data.message, data.suggested_search);
    input.value = '';
}
```

#### New `displayRestrictedPlaylist()` function

Displays:
- Yellow warning card with lock icon
- Playlist name, owner, and declared track count
- Explanation message
- "Search tracks by [playlist name]" button that auto-fills the search input and triggers a search

```javascript
function displayRestrictedPlaylist(playlistInfo, message, suggestedSearch) {
    // Yellow warning card with lock icon and playlist metadata
    // "Search tracks" button that auto-fills search input with playlist name
}
```

#### Lock icon on non-owned search results

In `displayExternalSearchResults()`, compare `pl.owner_id` against `workshopState.currentUserId`. Show a small yellow lock SVG icon next to non-owned playlist names.

#### Pass current user ID to frontend

Add to `workshopState` initialization:
```javascript
currentUserId: {{ user.id | tojson }},
```

---

## Testing

### New tests in `tests/routes/test_workshop_external_routes.py`

| Test | Scenario |
|------|----------|
| `test_restricted_playlist_returns_restricted_mode` | Non-owned playlist, 50 declared tracks, 0 returned → `mode: "restricted"` |
| `test_genuinely_empty_playlist_returns_tracks_mode` | Non-owned playlist, 0 declared tracks, 0 returned → `mode: "tracks"` |
| `test_owned_playlist_with_tracks_returns_tracks_mode` | Owned playlist, 5 tracks → `mode: "tracks"` |
| `test_owned_empty_playlist_returns_tracks_mode` | Owned playlist, 0 tracks → `mode: "tracks"` |
| `test_restricted_playlist_updates_session_history` | Session history updated with `track_count` from `total_tracks` |
| `test_search_playlists_includes_owner_id` | Search results include `owner_id` field |

### Regression

Existing tests for owned playlist editing, source playlist loading, and search must pass unchanged.

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Spotify returns partial tracks instead of empty | Low | Detection checks `len(tracks) == 0`; partial data still works |
| `total_tracks` metadata also removed | Very Low | Playlist appears genuinely empty; acceptable degradation |
| Collaborative non-owned playlists falsely flagged | Low | `get_playlist_tracks()` still works for collaborators; if tracks load, `is_restricted` is `False` |
| `total_tracks` field on Playlist dataclass breaks serialization | Very Low | Field has default `None`; all existing constructors continue working |
