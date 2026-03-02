# Phase 01: Replace spotipy with Direct HTTP Client

`✅ COMPLETE` Target: 2026-03-07 (2 days before March 9 deadline)
Started: 2026-02-28
Completed: 2026-02-28

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `feat: Replace spotipy with direct HTTP client for Spotify API v2 endpoint migration` |
| **Risk Level** | High |
| **Estimated Effort** | High (6-10 hours) |
| **Dependencies** | None |
| **Blocks** | All other phases benefit from this but are not strictly blocked |
| **Deadline** | March 9, 2026 (Spotify endpoint deprecation) |

---

## Motivation

Spotify's February 2026 API changes rename all playlist endpoints:
- `/v1/playlists/{id}/tracks` → `/v1/playlists/{id}/items`
- Response field `tracks` in playlist objects → `items`
- Nested `track` key in playlist item responses → `item`

The project depends on `spotipy==2.25.2`, which hardcodes the old endpoint paths. spotipy has not released an update. The deadline is March 9, 2026.

Because `SpotifyAPI` in `shuffify/spotify/api.py` is the sole Spotify API abstraction, and `requests==2.32.5` is already an explicit dependency, the cleanest fix is to replace spotipy with direct `requests` calls using the new endpoints.

---

## Scope of spotipy Usage

### Production Code (3 files + 2 files with `_sp` access)

| File | Usage |
|------|-------|
| `shuffify/spotify/api.py` | `import spotipy`; `spotipy.Spotify(auth=...)` — all 9 API methods call `self._sp.*` |
| `shuffify/spotify/auth.py` | `from spotipy.oauth2 import SpotifyOAuth` — OAuth URL, token exchange, refresh |
| `shuffify/spotify/error_handling.py` | `spotipy.SpotifyException` — error classification and retry logic |
| `shuffify/services/executors/base_executor.py` (L377) | `api._sp.playlist_add_items()` — direct spotipy access |
| `shuffify/services/playlist_pair_service.py` | Takes raw spotipy client as `sp` parameter |
| `shuffify/routes/playlist_pairs.py` | Passes `client._sp` to playlist_pair_service |

### Test Files (4 files)

| File | Pattern |
|------|---------|
| `tests/spotify/test_api.py` | ~30 patches of `spotipy.Spotify` |
| `tests/spotify/test_api_search.py` | ~4 patches |
| `tests/spotify/test_client.py` | ~18 patches |
| `tests/test_integration.py` | ~5 patches |

---

## Files to Create

### `shuffify/spotify/http_client.py` (NEW — ~180 lines)

Lightweight HTTP client wrapping `requests.Session` for Spotify Web API.

**Responsibilities:**
- Attach `Authorization: Bearer <token>` to every request
- On 401 → call `on_token_refresh` callback, update token, retry once
- On 429 → respect `Retry-After` header with exponential backoff
- On 5xx / network errors → retry with exponential backoff
- On 404 → raise `SpotifyNotFoundError` immediately
- Parse JSON response bodies
- Provide `get_all_pages()` helper for paginated endpoints

**Key interface:**
```python
class SpotifyHTTPClient:
    def __init__(self, access_token, on_token_refresh=None): ...
    def update_token(self, access_token): ...
    def get(self, path, params=None) -> Any: ...
    def put(self, path, json=None) -> Any: ...
    def post(self, path, json=None) -> Any: ...
    def delete(self, path, json=None) -> Any: ...
    def get_all_pages(self, path, params=None) -> List[dict]: ...
    def close(self): ...
```

Base URL: `https://api.spotify.com/v1`

Retry constants: `MAX_RETRIES=4`, `BASE_DELAY=2`, `MAX_DELAY=16`

---

## Files to Modify

### `shuffify/spotify/api.py` (MAJOR REWRITE)

**Remove:**
- `import spotipy` (L14)
- `logging.getLogger("spotipy").setLevel(logging.WARNING)` (L28)
- `self._sp = spotipy.Spotify(auth=...)` (L94, L126)

**Add:**
- `from .http_client import SpotifyHTTPClient`
- Token refresh callback wiring in `__init__`

**Method-by-method rewrite:**

| Method | Old (spotipy) | New (direct HTTP) |
|--------|--------------|-------------------|
| `__init__` | `self._sp = spotipy.Spotify(auth=token)` | `self._http = SpotifyHTTPClient(token, on_token_refresh=callback)` |
| `_ensure_valid_token` | `self._sp = spotipy.Spotify(auth=new_token)` | `self._http.update_token(new_token)` |
| `get_current_user` | `self._sp.current_user()` | `self._http.get("/me")` |
| `get_user_playlists` | `self._sp.current_user_playlists()` + `self._sp.next()` | `self._http.get_all_pages("/me/playlists")` |
| `get_playlist` | `self._sp.playlist(id)` | `self._http.get(f"/playlists/{id}")` |
| `get_playlist_tracks` | `self._sp.playlist_items(id)` + `self._sp.next()` | `self._http.get_all_pages(f"/playlists/{id}/items")` |
| `update_playlist_tracks` | `self._sp.playlist_replace_items()` / `playlist_add_items()` | `self._http.put(f"/playlists/{id}/items", json={"uris": ...})` / `.post(...)` |
| `playlist_remove_items` | `self._sp.playlist_remove_all_occurrences_of_items()` | `self._http.delete(f"/playlists/{id}/items", json={"tracks": ...})` |
| `get_audio_features` | `self._sp.audio_features(batch)` | `self._http.get("/audio-features", params={"ids": ",".join(batch)})` |
| `search_playlists` | `self._sp.search(type="playlist")` | `self._http.get("/search", params={...})` |
| `search_tracks` | `self._sp.search(type="track")` | `self._http.get("/search", params={...})` |

**Response field renames:**
- `get_playlist_tracks`: `item.get("track")` → `item.get("item")`
- `search_playlists`: Use defensive fallback for total count — `item.get("items", item.get("tracks", {})).get("total", 0)` — because it's unclear whether search response playlist summaries rename `"tracks"` to `"items"`. The fallback handles both cases safely. Verify against the live API and remove the fallback once confirmed.

**New public methods to add** (replacing direct `_sp` access):
- `playlist_add_items(playlist_id, track_uris)` — batched append
- `create_user_playlist(user_id, name, public, description)` — create playlist
- `get_playlist_items_raw(playlist_id, fields, limit)` — raw items with field filtering. **Note:** Spotify's `fields` query parameter syntax also renames `track(...)` → `item(...)` in the filter expression (e.g., `"items(item(id,name,uri))"` not `"items(track(...))"`).

### `shuffify/spotify/auth.py` (MODERATE)

**Remove:** `from spotipy.oauth2 import SpotifyOAuth` (L14) and `_create_oauth()` method

**Rewrite 3 methods to use direct `requests` calls:**

- `get_auth_url()` → Construct URL manually: `https://accounts.spotify.com/authorize?{urlencode(params)}`
- `exchange_code()` → `requests.post("https://accounts.spotify.com/api/token", data={grant_type: "authorization_code", ...}, auth=(client_id, client_secret))`
- `refresh_token()` → `requests.post("https://accounts.spotify.com/api/token", data={grant_type: "refresh_token", ...}, auth=(client_id, client_secret))`

**Important:** On refresh, Spotify may not return a new `refresh_token`. Preserve the old one:
```python
if "refresh_token" not in new_token_data:
    new_token_data["refresh_token"] = token_info.refresh_token
```

### `shuffify/spotify/error_handling.py` (SIMPLIFY)

**Remove:** `import spotipy`, all `spotipy.SpotifyException` references, retry logic (moved to `http_client.py`)

**Keep:** Simplified `api_error_handler` decorator that catches unexpected exceptions and wraps in `SpotifyAPIError`. Custom exception types (`SpotifyNotFoundError`, `SpotifyRateLimitError`, `SpotifyTokenExpiredError`) pass through unchanged since `SpotifyHTTPClient` raises them directly.

### `shuffify/spotify/client.py` (MINOR)

Add `api` property for clean access:
```python
@property
def api(self) -> Optional[SpotifyAPI]:
    return self._api
```

### `shuffify/services/executors/base_executor.py` (MINOR — L377)

Replace `api._sp.playlist_add_items(playlist_id, batch)` with `api.playlist_add_items(playlist_id, uris)` (new public method handles batching internally).

### `shuffify/services/playlist_pair_service.py` (MODERATE)

Refactor `archive_tracks(sp, ...)`, `unarchive_tracks(sp, ...)`, `create_archive_playlist(sp, ...)` to accept `SpotifyAPI` instead of raw spotipy client. Replace:
- `sp.playlist_add_items()` → `api.playlist_add_items()`
- `sp.playlist_remove_all_occurrences_of_items()` → `api.playlist_remove_items()`
- `sp.user_playlist_create()` → `api.create_user_playlist()`

### `shuffify/routes/playlist_pairs.py` (MODERATE — 4 locations)

Replace all `client._sp` references with `client.api`. **Note:** L243 uses `fields="items(track(id,name,uri,...))"` — update the field filter to use `item(...)` instead of `track(...)` per the API field renames.

### `requirements/base.txt` (L3)

Remove `spotipy==2.25.2`.

---

## Step-by-Step Implementation (Commit Order)

**4 commits.** Each commit leaves the codebase in a passing state.

### Commit 1: Create `SpotifyHTTPClient` with tests
- `shuffify/spotify/http_client.py` — Full implementation
- `tests/spotify/test_http_client.py` — ~25 tests (requests, retries, pagination, errors, token refresh)

### Commit 2: Rewrite `SpotifyAuthManager` to use direct `requests`
- `shuffify/spotify/auth.py` — Remove SpotifyOAuth, rewrite 3 methods
- `tests/spotify/test_auth.py` — Replace `_create_oauth` mocks with `requests.post` mocks

### Commit 3: Rewrite `SpotifyAPI`, simplify error handling, update all consumers
Commits 3 and 4 from the original plan are merged into one. Removing `self._sp` from `api.py` would break `base_executor.py:377` and `playlist_pairs.py:70/155/200/243` if they landed in separate commits.

**Core rewrite:**
- `shuffify/spotify/api.py` — Full rewrite: `self._sp` → `self._http`, new endpoints, field renames, add `playlist_add_items()`, `create_user_playlist()`, `get_playlist_items_raw()` public methods
- `shuffify/spotify/error_handling.py` — Simplify decorator, remove spotipy references and retry logic (now in `http_client.py`)

**Downstream consumers:**
- `shuffify/spotify/client.py` — Add `api` property
- `shuffify/services/executors/base_executor.py` — Replace `api._sp.playlist_add_items` with `api.playlist_add_items`
- `shuffify/services/playlist_pair_service.py` — Change `sp` → `api` parameter
- `shuffify/routes/playlist_pairs.py` — Replace `client._sp` with `client.api`, update field filter syntax (`track(...)` → `item(...)`)

**All test migrations:**
- `tests/spotify/test_api.py` — Migrate ~30 mock patches from `spotipy.Spotify` to `SpotifyHTTPClient`
- `tests/spotify/test_api_search.py` — Migrate ~4 patches, update `"tracks"` → `"items"` in fixtures
- `tests/spotify/test_client.py` — Migrate ~18 mock patches
- `tests/test_integration.py` — Migrate ~5 mock patches
- `tests/services/test_playlist_pair_service.py` — Change `mock_sp` to mock `SpotifyAPI`

### Commit 4: Remove spotipy dependency
- `requirements/base.txt` — Remove `spotipy==2.25.2`
- Verify: `grep -r "spotipy" shuffify/ tests/ requirements/` returns zero results

### Post-commit verification
```bash
pip install -r requirements/dev.txt
flake8 shuffify/
pytest tests/ -v
```

---

## Testing Strategy

### New tests: `tests/spotify/test_http_client.py` (~25 tests)
- GET/PUT/POST/DELETE with correct headers
- Token refresh on 401 (callback, retry once)
- Rate limit on 429 (Retry-After, backoff)
- 404 → SpotifyNotFoundError
- 5xx → retry then SpotifyAPIError
- Network errors → retry then SpotifyAPIError
- Pagination via `get_all_pages`
- 204 returns None

### Tests to update (mock migration — all in Commit 3)
- `tests/spotify/test_api.py` — `spotipy.Spotify` → `SpotifyHTTPClient`; `"track"` → `"item"` in fixtures
- `tests/spotify/test_api_search.py` — Same pattern; use defensive fallback for `"tracks"/"items"` total
- `tests/spotify/test_auth.py` — `_create_oauth` → `requests.post` (Commit 2)
- `tests/spotify/test_client.py` — `spotipy.Spotify` → `SpotifyHTTPClient`
- `tests/test_integration.py` — Same pattern
- `tests/services/test_playlist_pair_service.py` — `mock_sp` → mock `SpotifyAPI`

---

## Rollback Plan

1. Create tag: `git tag pre-spotipy-migration`
2. If migration fails: `git reset --hard pre-spotipy-migration`
3. If post-merge issues: revert PR, re-add `spotipy==2.25.2`

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Spotify response format differs from docs | Low | High | Test against real API before deploying |
| Missing spotipy behavior (pagination edge cases) | Medium | Medium | Comprehensive test coverage per method |
| Auth token format differences | Low | Medium | `TokenInfo.from_dict()` handles flexible dicts |
| `playlist_pair_service` callers break | Medium | Medium | Refactor to `SpotifyAPI` public methods |
| Response field `"track"` → `"item"` missed | High (certain) | High | Explicit rename + grep for all `item.get("track")` |
| Search response `"tracks"` key may NOT rename | Medium | Medium | Defensive fallback: `item.get("items", item.get("tracks", {}))` handles both |
| Field filter syntax `track(...)` not updated | Medium | High | `get_playlist_items_raw` and `playlist_pairs.py:243` must use `item(...)` |
| Test coverage gaps after mock migration | Medium | Medium | Compare test counts before/after |

---

## Verification Checklist

- [x] `grep -r "spotipy" shuffify/ tests/ requirements/` returns only stale .pyc files
- [x] `grep -r "_sp\b" shuffify/` returns zero results
- [x] `flake8 shuffify/` passes
- [x] `pytest tests/ -v` passes all 1323 tests
- [x] Test count 1323 >= 1296 + 27 new
- [x] Playlist items endpoint uses `/items` not `/tracks`
- [x] Response parsing uses `"item"` not `"track"` for nested track data
- [x] Search playlists uses defensive fallback for `"items"/"tracks"` total count
- [x] Field filter syntax in `get_playlist_items_raw` uses `item(...)` not `track(...)`
