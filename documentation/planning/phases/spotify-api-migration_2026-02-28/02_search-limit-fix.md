# Phase 02: Fix Search Endpoint Limits

`PENDING` Target: 2026-02-28

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `fix: Update search limits for Spotify Feb 2026 API changes` |
| **Risk Level** | Low |
| **Estimated Effort** | Low (1-2 hours) |
| **Dependencies** | None |
| **Blocks** | None |

---

## Motivation

Spotify's February 2026 API changes reduce the search endpoint's `limit` parameter:
- Maximum: 50 → 10
- Default: 20 → 5

Without this fix, every search request asking for more than 10 results will be rejected by Spotify once enforcement begins.

---

## Files to Modify

### `shuffify/schemas/requests.py` (line 223)

```python
# Before
limit: Annotated[int, Field(ge=1, le=50)] = 20

# After
limit: Annotated[int, Field(ge=1, le=10)] = 10
```

### `shuffify/spotify/api.py`

**`search_playlists()`** (line 477):
```python
# Before
limit = max(1, min(limit, 50))

# After
limit = max(1, min(limit, 10))
```

Update docstring (line 464): `(1-50, default 10)` → `(1-10, default 10)`

**`search_tracks()`** (lines 529, 553):
```python
# Before (L529)
limit: int = 20,
# After
limit: int = 10,

# Before (L553)
limit = max(1, min(limit, 50))
# After
limit = max(1, min(limit, 10))
```

Update docstring (line 539): `(1-50, default 20)` → `(1-10, default 10)`

### `shuffify/spotify/client.py`

**`search_tracks()`** (line 331):
```python
# Before
limit: int = 20,
# After
limit: int = 10,
```

Update docstrings for both `search_playlists()` and `search_tracks()`.

### `shuffify/templates/workshop.html` (line 1221)

```javascript
// Before
limit: 20,

// After
limit: 10,
```

The existing "Load More" button and pagination infrastructure (`searchSpotifyMore()`, offset tracking) work correctly with any limit value. No other JS changes needed.

---

## Test Updates

### `tests/spotify/test_api_search.py`

**`test_search_clamps_limit_max`** (lines 207-215):
```python
# Before: asserts limit=50
# After: asserts limit=10
```

### `tests/routes/test_workshop_search_routes.py`

**`test_valid_query`** (line 30): `assert req.limit == 20` → `assert req.limit == 10`

**`test_limit_above_maximum_rejected`** (lines 64-67): Test with `limit=11` instead of `limit=51`

**Add new boundary test:**
```python
def test_limit_at_new_maximum_accepted(self):
    req = WorkshopSearchRequest(query="test", limit=10)
    assert req.limit == 10
```

---

## UX Notes

- Users will see at most 10 results per search page (down from 20)
- The existing "Load More" button already provides seamless pagination
- We use 10 (Spotify's new max) as default rather than 5 (Spotify's new default) to maximize utility per API call
- The playlist search at `workshop.py` line 325 already uses `limit=10` — no change needed

---

## Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Spotify enforces before we deploy | Medium | Fix is small, deploy quickly |
| Users notice fewer results per page | Low | "Load More" pagination already exists |
| Cache key staleness | Very Low | Cache TTL is 120s; keys include limit parameter |
