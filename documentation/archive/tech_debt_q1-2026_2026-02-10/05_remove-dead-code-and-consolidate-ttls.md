# Phase 5: Remove Dead Code & Consolidate TTL Configuration

> **Status:** ✅ COMPLETE
> **Started:** 2026-02-10
> **Completed:** 2026-02-10
> **PR:** #41

| Attribute | Value |
|-----------|-------|
| **PR Title** | `chore: Remove unused Playlist methods and consolidate cache TTL defaults` |
| **Risk Level** | Low |
| **Estimated Effort** | 20 minutes |
| **Files Modified** | 2 |
| **Dependencies** | None |
| **Blocks** | Phase 6 (tests need to match the updated model API) |

---

## Overview

This PR addresses two cleanup items:
1. **Remove unused methods** from `Playlist` model that have no callers in the codebase
2. **Remove TTL class constants** from `SpotifyCache` that duplicate values already in `config.py`

**Why:**
- Unused methods create maintenance burden — developers wonder if they're safe to change or remove, and they must be considered during refactors.
- TTL values defined in two places (`cache.py` class constants and `config.py`) can drift. The `config.py` values are what actually get used in production (passed via `__init__.py` factory). The class constants are misleading defaults.

---

## Files Modified

| File | Change Type |
|------|-------------|
| `shuffify/models/playlist.py` | Remove 3 unused methods |
| `shuffify/spotify/cache.py` | Remove TTL class constants; require TTLs in constructor |

---

## Step-by-Step Implementation

### Step 1: Remove unused methods from Playlist model

**File:** `shuffify/models/playlist.py`

Before making changes, verify that these methods are truly unused by running:
```bash
grep -rn "get_track(" shuffify/ --include="*.py" | grep -v "test_" | grep -v "__pycache__"
grep -rn "get_track_with_features" shuffify/ --include="*.py" | grep -v "test_" | grep -v "__pycache__"
grep -rn "get_tracks_with_features" shuffify/ --include="*.py" | grep -v "test_" | grep -v "__pycache__"
```

If any of these return results in production code (not tests), do NOT remove that method.

**Assuming all are confirmed unused, remove these three methods:**

#### 1a. Remove `get_tracks_with_features()` (lines 81-89)

**Current code:**
```python
    def get_tracks_with_features(self) -> List[Dict[str, Any]]:
        """Return tracks, attaching audio features when available."""
        enriched_tracks = []
        for track in self.tracks:
            track_id = track["id"]
            enriched_track = {**track}
            enriched_track["features"] = self.audio_features.get(track_id, {})
            enriched_tracks.append(enriched_track)
        return enriched_tracks
```

Delete this entire method.

#### 1b. Remove `get_track()` (lines 91-93)

**Current code:**
```python
    def get_track(self, uri: str) -> Optional[Dict[str, Any]]:
        """Retrieve a track by its URI."""
        return next((track for track in self.tracks if track.get("uri") == uri), None)
```

Delete this entire method.

#### 1c. Remove `get_track_with_features()` (lines 95-101)

**Current code:**
```python
    def get_track_with_features(self, uri: str) -> Optional[Dict[str, Any]]:
        """Retrieve a track and its features."""
        track = self.get_track(uri)
        if not track:
            return None
        track_id = track.get("id")
        return {**track, "features": self.audio_features.get(track_id, {})}
```

Delete this entire method.

#### 1d. Clean up imports if needed

After removing the methods, check if `Optional` is still used in the file. The remaining methods that use `Optional` are:
- `description: Optional[str]` in the dataclass fields (line 16)

So `Optional` is still needed. **Do not remove the `Optional` import.**

### Step 2: Consolidate TTL configuration in cache.py

**File:** `shuffify/spotify/cache.py`

#### 2a. Remove class-level TTL constants

**Current code (lines 36-40):**
```python
class SpotifyCache:
    """..."""

    # Default TTL values in seconds
    DEFAULT_TTL = 300  # 5 minutes
    PLAYLIST_TTL = 60  # 1 minute (changes frequently)
    USER_TTL = 600  # 10 minutes
    AUDIO_FEATURES_TTL = 86400  # 24 hours (rarely change)
```

**Replace with:**
```python
class SpotifyCache:
    """..."""
```

Delete the four TTL class constants and their comment.

#### 2b. Update __init__ to use inline defaults

**Current code (lines 42-50):**
```python
    def __init__(
        self,
        redis_client: redis.Redis,
        key_prefix: str = "shuffify:cache:",
        default_ttl: int = DEFAULT_TTL,
        playlist_ttl: int = PLAYLIST_TTL,
        user_ttl: int = USER_TTL,
        audio_features_ttl: int = AUDIO_FEATURES_TTL,
    ):
```

**Replace with:**
```python
    def __init__(
        self,
        redis_client: redis.Redis,
        key_prefix: str = "shuffify:cache:",
        default_ttl: int = 300,
        playlist_ttl: int = 60,
        user_ttl: int = 600,
        audio_features_ttl: int = 86400,
    ):
```

**Why inline values instead of class constants:** The class constants served as documentation, but they created a second source of truth alongside `config.py`. The `config.py` values (lines 38-41) are the canonical source. Using inline defaults in `__init__` makes it clear that these are just fallbacks — the real values come from `config.py` via the `get_spotify_cache()` factory in `__init__.py`.

**Alternative considered:** We could remove the defaults entirely and require all TTL values to be passed. However, this would break tests that create `SpotifyCache` directly without specifying TTLs. Keeping inline defaults preserves backward compatibility.

#### 2c. Verify the factory still works

**File:** `shuffify/__init__.py` (READ ONLY — do not modify)

Confirm that `get_spotify_cache()` (around line 42-75) passes config values to `SpotifyCache()`:
```python
return SpotifyCache(
    _redis_client,
    key_prefix=config.get("CACHE_KEY_PREFIX", "shuffify:cache:"),
    default_ttl=config.get("CACHE_DEFAULT_TTL", 300),
    playlist_ttl=config.get("CACHE_PLAYLIST_TTL", 60),
    user_ttl=config.get("CACHE_USER_TTL", 600),
    audio_features_ttl=config.get("CACHE_AUDIO_FEATURES_TTL", 86400),
)
```

This confirms that `config.py` values are always passed in production. The `SpotifyCache` inline defaults are only used when the cache is instantiated directly (e.g., in tests).

---

## Verification Checklist

- [ ] `grep -rn "get_track(" shuffify/models/playlist.py` returns only `get_track_uris` (no standalone `get_track`)
- [ ] `grep -rn "get_tracks_with_features\|get_track_with_features" shuffify/models/playlist.py` returns no results
- [ ] `grep -rn "DEFAULT_TTL\|PLAYLIST_TTL\|USER_TTL\|AUDIO_FEATURES_TTL" shuffify/spotify/cache.py` returns no results (class constants removed)
- [ ] `SpotifyCache.__init__` still has inline default values for all TTL parameters
- [ ] All 479 tests pass: `pytest tests/ -v`
- [ ] Specifically run cache tests: `pytest tests/spotify/test_cache.py -v`
- [ ] Lint passes: `flake8 shuffify/`

---

## What NOT To Do

- **Do NOT remove `Playlist.get_track_uris()`** — this IS used by `PlaylistService.get_track_uris()`. Even though `PlaylistService.get_track_uris()` itself may have limited usage, removing it is out of scope for this PR.
- **Do NOT remove `Playlist.has_features()`** — it is a simple utility that may be used in templates or future code.
- **Do NOT remove `Playlist.get_feature_stats()`** — it IS used by `PlaylistService.get_playlist_stats()`.
- **Do NOT modify `config.py`** — it is the canonical source of TTL values and should not change.
- **Do NOT modify `shuffify/__init__.py`** — the factory function correctly passes config values already.
- **Do NOT remove the `T = TypeVar("T")` line** from `cache.py` (line 16) even though it appears unused. It may be used for type hints in future development. If flake8 flags it, then remove it.
- **Do NOT change the `_serialize`/`_deserialize` methods** or any cache get/set logic. Only the TTL constants are changing.
