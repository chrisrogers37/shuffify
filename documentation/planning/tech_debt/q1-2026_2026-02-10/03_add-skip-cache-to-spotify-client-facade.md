# Phase 3: Add skip_cache to SpotifyClient Facade

| Attribute | Value |
|-----------|-------|
| **PR Title** | `refactor: Add skip_cache parameter to SpotifyClient, remove private API access` |
| **Risk Level** | Low |
| **Estimated Effort** | 20 minutes |
| **Files Modified** | 2 |
| **Dependencies** | None |
| **Blocks** | Nothing |

---

## Overview

`PlaylistService.get_user_playlists()` currently reaches through the `SpotifyClient` facade to access its private `_api` attribute in order to pass `skip_cache=True`. This PR adds `skip_cache` as a proper parameter to `SpotifyClient.get_user_playlists()` so the service layer doesn't need to know about internal implementation.

**Why:** Accessing `_api` directly is fragile — if `SpotifyClient` internals change, `PlaylistService` breaks silently. The fix is to expose `skip_cache` through the public API.

---

## Files Modified

| File | Change Type |
|------|-------------|
| `shuffify/spotify/client.py` | Add `skip_cache` parameter to `get_user_playlists()` |
| `shuffify/services/playlist_service.py` | Remove `_api` access; use public `skip_cache` parameter |

---

## Step-by-Step Implementation

### Step 1: Add skip_cache to SpotifyClient.get_user_playlists()

**File:** `shuffify/spotify/client.py`

**Current code (lines 214-227):**
```python
    def get_user_playlists(self) -> List[Dict[str, Any]]:
        """
        Get all playlists the user can edit.

        Returns:
            List of playlist dictionaries.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_authenticated()
        return self._api.get_user_playlists()
```

**Replace with:**
```python
    def get_user_playlists(self, skip_cache: bool = False) -> List[Dict[str, Any]]:
        """
        Get all playlists the user can edit.

        Args:
            skip_cache: If True, bypass cache and fetch fresh data from Spotify.

        Returns:
            List of playlist dictionaries.

        Raises:
            RuntimeError: If not authenticated.
            SpotifyAPIError: If the request fails.
        """
        self._ensure_authenticated()
        return self._api.get_user_playlists(skip_cache=skip_cache)
```

**What changed:** Added `skip_cache: bool = False` parameter and passed it through to `self._api.get_user_playlists()`. The `SpotifyAPI.get_user_playlists()` method already accepts `skip_cache` (see `shuffify/spotify/api.py:287`), so this is simply exposing an existing capability.

### Step 2: Update PlaylistService to use the public API

**File:** `shuffify/services/playlist_service.py`

**Current code (lines 46-72):**
```python
    def get_user_playlists(
        self, skip_cache: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetch all playlists the user can edit.

        Args:
            skip_cache: If True, bypass cache and fetch fresh data.

        Returns:
            List of playlist dictionaries (owned or collaborative).

        Raises:
            PlaylistError: If fetching playlists fails.
        """
        try:
            if skip_cache and hasattr(self._client, "_api") and self._client._api:
                playlists = self._client._api.get_user_playlists(
                    skip_cache=True
                )
            else:
                playlists = self._client.get_user_playlists()
            logger.debug(f"Retrieved {len(playlists)} user playlists")
            return playlists
        except Exception as e:
            logger.error(f"Failed to get user playlists: {e}", exc_info=True)
            raise PlaylistError(f"Failed to fetch playlists: {e}")
```

**Replace with:**
```python
    def get_user_playlists(
        self, skip_cache: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Fetch all playlists the user can edit.

        Args:
            skip_cache: If True, bypass cache and fetch fresh data.

        Returns:
            List of playlist dictionaries (owned or collaborative).

        Raises:
            PlaylistError: If fetching playlists fails.
        """
        try:
            playlists = self._client.get_user_playlists(skip_cache=skip_cache)
            logger.debug(f"Retrieved {len(playlists)} user playlists")
            return playlists
        except Exception as e:
            logger.error(f"Failed to get user playlists: {e}", exc_info=True)
            raise PlaylistError(f"Failed to fetch playlists: {e}")
```

**What changed:** Replaced the `if skip_cache and hasattr(self._client, "_api")...` block with a single call to `self._client.get_user_playlists(skip_cache=skip_cache)`. The branching logic and private access are gone.

---

## Verification Checklist

- [ ] `grep -rn "_api" shuffify/services/` returns no results (no more private access from services)
- [ ] All 479 tests pass: `pytest tests/ -v`
- [ ] Lint passes: `flake8 shuffify/`
- [ ] Manual test: Click "Refresh Playlists" button in the UI and verify playlists refresh (this exercises the `skip_cache=True` path)
- [ ] Manual test: Load dashboard normally and verify playlists load (this exercises the `skip_cache=False` default path)

---

## What NOT To Do

- **Do NOT add `skip_cache` to other SpotifyClient methods** in this PR. Only `get_user_playlists()` needs it right now. Other methods can be updated if/when needed.
- **Do NOT change `SpotifyAPI`** — it already supports `skip_cache`. This PR only changes the facade and its consumer.
- **Do NOT remove the `_api` attribute** from `SpotifyClient`. It's a private implementation detail that the client uses internally. We're just stopping *other classes* from accessing it.
- **Do NOT change the route** (`routes.py:249-267`). The route calls `playlist_service.get_user_playlists(skip_cache=True)`, which is the same interface — no changes needed there.
