# Reorder API Migration: Preserving `added_at` Timestamps

**Status**: Research complete, ready for live API testing
**Date**: 2026-04-12

---

## Problem

The current shuffle pipeline resets every track's `added_at` timestamp to "just now." This destroys meaningful metadata used by:

- **NewestFirstShuffle algorithm** — surfaces recently added tracks; broken when all tracks appear "new"
- **Rotation/drip logic** — identifies what's new vs. established
- **Spotify client UI** — "Date Added" column shows misleading data

## Root Cause

`SpotifyAPI.update_playlist_tracks()` at `shuffify/spotify/api.py:358-372`:

```python
# Step 1: PUT replaces ALL tracks with first 100 — PRESERVES added_at
self._http.put(f"/playlists/{playlist_id}/items", json={"uris": track_uris[:100]})

# Step 2: POST adds remaining tracks in batches — RESETS added_at
for i in range(100, len(track_uris), 100):
    self._http.post(f"/playlists/{playlist_id}/items", json={"uris": batch})
```

**Key insight from Spotify API research**: The PUT (replace) endpoint **preserves** `added_at` for tracks that already existed in the playlist. This was confirmed by Spotify staff in [web-api#1409](https://github.com/spotify/web-api/issues/1409) as intentional behavior. However, the POST (add items) in step 2 creates **new** entries, resetting `added_at` for tracks 101+.

**Impact by playlist size**:
| Playlist Size | Tracks with Preserved `added_at` | Tracks with Reset `added_at` |
|---------------|----------------------------------|------------------------------|
| 1-100         | All (via PUT replace)            | None                         |
| 200           | First 100                        | Last 100                     |
| 500           | First 100                        | Last 400                     |

Playlists with **100 or fewer tracks already work correctly** with the current code.

## Current Architecture

All playlist modifications funnel through a single method:

```
Routes/Executors
  ├─ POST /shuffle/<id>          → PlaylistService.update_playlist_tracks()
  ├─ POST /workshop/<id>/commit  → PlaylistService.update_playlist_tracks()
  ├─ POST /undo/<id>             → PlaylistService.update_playlist_tracks()
  └─ execute_shuffle() (sched)   → SpotifyAPI.update_playlist_tracks()
                                          ↓
                                   PUT first 100 + POST remaining batches
```

**Single point of change**: `SpotifyAPI.update_playlist_tracks()` is the only method that writes track order to Spotify. Fixing this method fixes all four code paths.

## Available Approaches

### Approach A: Reorder API (range_start/insert_before)

Uses `PUT /playlists/{id}/items` with `range_start`, `range_length`, `insert_before` to move tracks one range at a time.

**Pros**: Guaranteed `added_at` preservation. No metadata changes whatsoever.
**Cons**: O(N) API calls. A 500-track playlist needs up to 499 calls (~3 min at rate limits). A 1000-track playlist needs ~999 calls (~6 min).

### Approach B: Replace-Only (PUT with full URI list) ✅ RECOMMENDED

Uses only `PUT /playlists/{id}/items` with `{"uris": [...]}`. For playlists >100 tracks, split into batches but use a different strategy than POST.

**Key fact**: PUT replace preserves `added_at` for existing tracks. The bug is the POST step, not the PUT step.

**Strategy**: Instead of PUT first 100 + POST the rest, we can:
1. PUT the desired final order of the first 100 tracks (preserves `added_at`)
2. For tracks 101+, use the **reorder API** to move them into position

But this is complex. A simpler variant:

### Approach C: Hybrid — Replace + Reorder ✅ BEST FIT

1. **If playlist ≤ 100 tracks**: Single PUT with full URI list. Done. `added_at` preserved.
2. **If playlist > 100 tracks**: Use the reorder API (range_start/insert_before) to transform the current order into the target order, preserving all `added_at` timestamps.

**Optimization for reorder**: Instead of naive selection sort (N moves), use an algorithm that computes the minimum set of reorder operations:

- Find the longest subsequence of tracks already in the correct relative order
- Only move tracks that are NOT in this subsequence
- In practice, a random shuffle of N tracks requires ~N moves (most tracks move), but partial shuffles or algorithms like BalancedShuffle may need far fewer

**Rate limit math** (worst case, full random shuffle):
| Playlist Size | API Calls | Time at 180 req/min |
|---------------|-----------|---------------------|
| 100           | 1 (PUT)   | instant             |
| 200           | ~100      | ~33 seconds         |
| 300           | ~200      | ~67 seconds         |
| 500           | ~400      | ~2.2 minutes        |
| 1000          | ~900      | ~5 minutes          |

### Approach D: Sequence of PUT-replaces (creative alternative)

What if we could do multiple PUT-replace calls? Each PUT replaces the ENTIRE playlist. For >100 tracks:

1. PUT tracks [0:100] — replaces playlist with just these 100, preserving their `added_at`
2. POST tracks [100:200] — adds these as NEW entries (resets `added_at`) ❌

This doesn't work. PUT can only accept 100 URIs max. There's no way to do a full replace of >100 tracks in a single call.

## Recommended Design: Approach C (Hybrid)

### Algorithm

```python
def update_playlist_tracks(self, playlist_id, track_uris):
    if len(track_uris) <= 100:
        # Single PUT replace — preserves added_at for all tracks
        self._http.put(f"/playlists/{playlist_id}/items", json={"uris": track_uris})
        return True

    # For >100 tracks: use reorder API to transform current → target order
    current_uris = self._get_current_track_uris(playlist_id)
    moves = compute_reorder_moves(current_uris, track_uris)

    snapshot_id = None
    for move in moves:
        result = self._http.put(
            f"/playlists/{playlist_id}/items",
            json={
                "range_start": move.from_pos,
                "range_length": move.length,
                "insert_before": move.to_pos,
                "snapshot_id": snapshot_id,  # Optimistic concurrency
            },
        )
        snapshot_id = result.get("snapshot_id")

    return True
```

### Computing Reorder Moves

The `compute_reorder_moves()` function translates from current order → target order:

```python
def compute_reorder_moves(current_uris, target_uris):
    """
    Compute a sequence of (range_start, range_length, insert_before)
    operations that transform current_uris into target_uris.

    Uses selection-sort-like approach:
    - For each position i in the target order:
      - Find where target[i] currently sits
      - If it's not at position i, move it there
    """
    moves = []
    working = list(current_uris)

    for target_pos, target_uri in enumerate(target_uris):
        current_pos = working.index(target_uri)
        if current_pos != target_pos:
            # Move track from current_pos to target_pos
            moves.append(ReorderMove(
                from_pos=current_pos,
                range_length=1,
                to_pos=target_pos,
            ))
            # Update working list to reflect the move
            track = working.pop(current_pos)
            working.insert(target_pos, track)

    return moves
```

**Note on the `insert_before` semantic**: Spotify's reorder uses `insert_before` (the position to insert BEFORE), which differs slightly from "move to position." The exact calculation:
- If moving forward (from < to): `insert_before = to + 1` (insert after the target position)
- If moving backward (from > to): `insert_before = to`

This needs careful testing.

### Handling Edge Cases

**Tracks added/removed between fetch and reorder (race condition)**:
- Use `snapshot_id` on each reorder call for optimistic concurrency
- If Spotify returns 409 (conflict), refetch current state and recompute
- The current implementation ignores `snapshot_id` entirely — this is an improvement

**Duplicate URIs in playlist**:
- Spotify allows duplicate tracks. The `working.index()` approach finds the first occurrence.
- Need to handle this by tracking positions, not just URIs
- Use a position-aware mapping instead of `.index()`

**Tracks that exist in target but not in current (added during shuffle)**:
- Fall back to POST for genuinely new tracks (these have no `added_at` to preserve anyway)

**Empty playlist or clearing all tracks**:
- Current behavior (PUT with empty array) is correct and unchanged

### Changes Required

| File | Change |
|------|--------|
| `shuffify/spotify/api.py` | Rewrite `update_playlist_tracks()` with hybrid logic |
| `shuffify/spotify/api.py` | Add `_get_current_track_uris()` helper (may already exist) |
| `shuffify/spotify/api.py` | Add `_reorder_tracks()` method for the reorder API calls |
| `shuffify/spotify/api.py` (or new util) | Add `compute_reorder_moves()` algorithm |
| No other files change | All callers use the same `update_playlist_tracks()` interface |

### Undo Compatibility

The undo system stores URI lists in the Flask session and replays them through `update_playlist_tracks()`. Since we're not changing the method signature, undo works identically. The improvement: undo operations on >100 track playlists will now also preserve `added_at`.

### Track Lock Compatibility

Track locks are handled BEFORE `update_playlist_tracks()` is called — the shuffle service already produces the final URI list with locked tracks in place. No changes needed.

### Snapshot Compatibility

The current code never uses `snapshot_id`. The new reorder approach chains `snapshot_id` between moves for optimistic concurrency — a strict improvement.

## Performance Considerations

### Worst Case: Full Random Shuffle of 500 Tracks

- Current approach: 1 PUT + 4 POST = **5 API calls**, but timestamps destroyed
- New approach: ~400 reorder calls = **~400 API calls**, timestamps preserved
- Time: ~2.2 minutes at rate limits

### Mitigation Strategies

1. **Batch adjacent moves**: If multiple consecutive tracks need to move together, use `range_length > 1` to move them as a group in a single API call
2. **Skip already-correct positions**: Only move tracks that are out of place (the LIS optimization)
3. **Accept the trade-off**: For scheduled shuffles running overnight, 2-5 minutes is fine
4. **Progress feedback**: For on-demand shuffles, consider showing a progress indicator for large playlists
5. **Threshold option**: Offer a user setting — "Preserve dates (slower) vs. Fast shuffle (resets dates)"

### LIS Optimization (Longest Increasing Subsequence)

Instead of moving every track, find the longest subsequence of tracks that are already in the correct relative order. Only tracks NOT in this subsequence need to move.

For a 500-track playlist:
- Random shuffle: LIS is ~√(2*500) ≈ 32 tracks stay, ~468 moves
- Modest reorder (e.g., BalancedShuffle): Many more tracks stay in place, far fewer moves
- Already in order: 0 moves (LIS = N)

## Live API Test Plan

### Prerequisites
- A test playlist the user doesn't care about
- At least 5 tracks with known, different `added_at` dates (add tracks on different days)
- Ideally >100 tracks to test the batching behavior

### Test 1: Verify Current Bug (Baseline)

```python
# 1. Fetch current tracks with added_at timestamps
tracks = api.get_playlist_tracks(test_playlist_id)
original_timestamps = {t['uri']: t['added_at'] for t in tracks}
print("Before shuffle:", original_timestamps)

# 2. Reverse the track order (deterministic "shuffle")
reversed_uris = [t['uri'] for t in reversed(tracks)]

# 3. Apply using current method (PUT + POST)
api.update_playlist_tracks(test_playlist_id, reversed_uris)

# 4. Fetch tracks again
tracks_after = api.get_playlist_tracks(test_playlist_id)
new_timestamps = {t['uri']: t['added_at'] for t in tracks_after}
print("After shuffle:", new_timestamps)

# 5. Compare — expect timestamps to be reset for tracks 101+
for uri in original_timestamps:
    if original_timestamps[uri] != new_timestamps.get(uri):
        print(f"RESET: {uri}")
```

### Test 2: Verify PUT-Only Preserves Timestamps (≤100 tracks)

```python
# Use a playlist with exactly 100 or fewer tracks
# 1. Record timestamps
# 2. Reverse order via single PUT
api._http.put(f"/playlists/{id}/items", json={"uris": reversed_uris})
# 3. Fetch and compare — expect ALL timestamps preserved
```

### Test 3: Verify Reorder Preserves Timestamps

```python
# 1. Record timestamps
# 2. Swap track 0 and track 1 using reorder API
api._http.put(f"/playlists/{id}/items", json={
    "range_start": 0,
    "range_length": 1,
    "insert_before": 2,  # Move track 0 to after track 1
})
# 3. Fetch and compare — expect ALL timestamps preserved
```

### Test 4: Full Reorder of >100 Track Playlist

```python
# 1. Record timestamps for all tracks
# 2. Compute reorder moves for reversed order
# 3. Execute all moves
# 4. Fetch and compare — expect ALL timestamps preserved
# 5. Time the operation to validate performance assumptions
```

### Test Script Location

Create a standalone script at `scripts/test_reorder_api.py` that:
1. Authenticates via existing OAuth flow (reuse session token)
2. Runs all 4 tests against a user-specified playlist
3. Reports pass/fail with timestamp comparisons
4. Does NOT modify any production code

## Community Research (2026-04-12)

### How Others Handle This

Surveyed ~15 open-source Spotify shuffle/sort projects. None have solved this better:

| Strategy | Projects | `added_at` | Performance |
|----------|----------|------------|-------------|
| Create new playlist | bell345/real-randomizer, valentjn/shufflr, AlexJF/shufflify, sheagcraig/actually_random | Lost entirely | Fast |
| PUT+POST batches | gordody/spotify-playlist-shuffle, Lightningtow/Spotify_Shuffler | Reset for 101+ | Fast |
| Reorder move-by-move | dvd-z/shuffly, noahc3/Spoofy | Preserved | Slow (O(N)) |

**No project implements LIS optimization or batch-adjacent-move grouping.** We'd be first.

Spotify rejected batch reorder requests ([#660](https://github.com/spotify/web-api/issues/660), [#1167](https://github.com/spotify/web-api/issues/1167), [#773](https://github.com/spotify/web-api/issues/773)). The API won't change.

### PUT Replace: Conflicting Reports

[web-api#1409](https://github.com/spotify/web-api/issues/1409) has a Spotify staff member saying PUT replace preserves `added_at`, but community members report otherwise. **Live testing is essential before committing.**

### Rate Limits

- No fixed limit; rolling 30-second window per app
- Empirical: ~180 req/min (~3 req/s) commonly reported
- Dev Mode apps may be throttled more aggressively post-Feb 2026
- 429 responses include `Retry-After` header (our SpotifyHTTPClient already handles this)
- Reorder calls are lightweight (tiny JSON body), may get more lenient treatment

## Refined Recommendation: Algorithm-Aware Strategy

Instead of a user-facing toggle, make the decision **per-shuffle** based on whether the algorithm depends on `added_at`:

| Algorithm | Needs `added_at`? | Strategy | Calls (500 tracks) |
|-----------|-------------------|----------|---------------------|
| NewestFirstShuffle | **Yes** | Reorder API | ~455 |
| Rotation/drip executors | **Yes** | Reorder API | ~455 |
| BasicShuffle | No | PUT+POST (current) | 5 |
| BalancedShuffle | No | PUT+POST (current) | 5 |
| ArtistSpacingShuffle | No | PUT+POST (current) | 5 |
| AlbumSequenceShuffle | No | PUT+POST (current) | 5 |
| PercentageShuffle | No | PUT+POST (current) | 5 |
| StratifiedShuffle | No | PUT+POST (current) | 5 |

**Implementation**: Add a `preserves_added_at: bool` flag to `update_playlist_tracks()`. The caller decides based on context. Default: `False` (fast path). Shuffle service sets it to `True` when the algorithm has `requires_added_at = True` or when the caller is the drip/rotation executor.

### Stacking optimizations (for the slow path)

1. **LIS (Longest Increasing Subsequence)** — O(N log N), skip tracks already in correct relative order. Saves 10% for random shuffles, 30-70% for partial reorders.
2. **Batch adjacent moves** — `range_length > 1` when consecutive source tracks map to consecutive target positions.
3. **`snapshot_id` chaining** — each reorder response returns new snapshot_id, no re-fetching between moves.
4. **Short-circuit** — if LIS = N (no change), skip all API calls.

### Estimated performance with optimizations

| Playlist Size | Naive Moves | LIS-Optimized | With Batching (est.) | Time @ 3 req/s |
|---------------|-------------|---------------|----------------------|----------------|
| 100 | 0 (PUT) | 0 | 0 | instant |
| 200 | ~180 | ~160 | ~120-140 | ~45 seconds |
| 500 | ~455 | ~410 | ~300-350 | ~2 minutes |
| 1000 | ~940 | ~860 | ~600-700 | ~4 minutes |

## Decision Points for User

1. **Should we run the live test first?** (Recommendation: yes — Test 2 and 3 are quick and will confirm whether PUT replace and reorder preserve `added_at` before committing.)

2. **Algorithm-aware vs. user toggle?** The algorithm-aware approach means no UX change — it "just works." A user toggle adds complexity but gives control. Can start with algorithm-aware and add a toggle later if users ask.

3. **Progress UX for large playlists**: For on-demand shuffles with the slow path, show "Reordering... this may take a moment for large playlists" with a progress indicator. Scheduled shuffles run in the background — no UX concern.

4. **Scope**: All paths through `update_playlist_tracks()` get the fix, but the `preserve_added_at` flag defaults to `False`. Only callers that need it opt in.
