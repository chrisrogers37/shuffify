# Phase 03: Raid System Pivot — Multi-Pathway Source Resolution

`✅ COMPLETE` Started: 2026-03-01 | Completed: 2026-03-01 | PR: #110

---

## Overview

| Field | Value |
|-------|-------|
| **PR Title** | `feat: Multi-pathway raid source resolver with search and scraping fallbacks` |
| **Risk Level** | Medium |
| **Estimated Effort** | High (6-8 hours) |
| **Dependencies** | Phase 01 (HTTP client) recommended but not blocking |
| **Blocks** | None |

---

## Motivation

Spotify's February 2026 API restriction removes the `items` field from playlist responses for playlists the user does not own or collaborate on. This kills the core raid use case: pulling tracks from curator/artist/genre playlists into a user's target playlist.

The `_fetch_raid_sources()` function in `raid_executor.py` calls `api.get_playlist_tracks(source_id)` for each source. After the API change, external sources will return zero tracks with no error.

The user has stated: "Raid is super valuable. We should have several pathways that we test."

---

## Architecture

### Current Flow
```
_fetch_raid_sources()
  → for each source_id:
      api.get_playlist_tracks(source_id)  ← BREAKS for external playlists
      → deduplicate against target
  → return new_uris
```

### New Flow
```
_fetch_raid_sources()
  → SourceResolver.resolve_all(sources, api)
      → for each source:
           if source.source_type == "search_query":
             SearchPathway.resolve(source, api)
           else:
             DirectAPIPathway.resolve(source, api)
               → success? return tracks
               → items missing? fall through
             PublicScraperPathway.resolve(source)
               → success? return tracks
               → failed? return []
      → deduplicate against target
  → return new_uris
```

### Pathway Priority Matrix

| Pathway | When It Works | Reliability | Spotify TOS Risk |
|---------|--------------|-------------|-----------------|
| **1. Direct API** | User owns or collaborates on playlist | High | None |
| **2. Search Discovery** | Always (search API unaffected) | High | None |
| **3. Public Web Scraping** | Playlist is public on open.spotify.com | Medium | Low-Medium |

---

## Database Changes

### New columns on `UpstreamSource` (in `shuffify/models/db.py`)

```python
search_query = db.Column(db.String(500), nullable=True)
last_resolved_at = db.Column(db.DateTime, nullable=True)
last_resolve_pathway = db.Column(db.String(30), nullable=True)
last_resolve_status = db.Column(db.String(20), nullable=True)  # "success", "partial", "failed"
```

Update `to_dict()` to include all four new fields.

### Migration

```bash
flask db migrate -m "add search_query and resolver tracking to upstream_sources"
flask db upgrade
```

Non-destructive: three nullable column additions.

### Source type expansion

`UpstreamSourceService.add_source()` (line 62): expand validation from `("own", "external")` to `("own", "external", "search_query")`.

---

## Files to Create

### `shuffify/services/source_resolver/` (NEW PACKAGE)

```
source_resolver/
  __init__.py                 # Exports SourceResolver, ResolveResult
  base.py                     # ResolveResult dataclass, ResolvePathway protocol
  resolver.py                 # SourceResolver — orchestrates pathways
  direct_api_pathway.py       # Pathway 1: api.get_playlist_tracks()
  search_pathway.py           # Pathway 2: api.search_tracks(query)
  public_scraper_pathway.py   # Pathway 3: HTTP scrape of open.spotify.com
```

### `base.py` — ResolveResult and protocol

```python
@dataclass
class ResolveResult:
    track_uris: List[str]
    pathway_name: str
    success: bool
    partial: bool = False
    error_message: Optional[str] = None

class ResolvePathway(Protocol):
    @property
    def name(self) -> str: ...
    def can_handle(self, source: UpstreamSource) -> bool: ...
    def resolve(self, source, api=None) -> ResolveResult: ...
```

### `resolver.py` — SourceResolver

Orchestrates pathways in priority order. Key methods:
- `resolve(source, api)` → tries each applicable pathway until one succeeds
- `resolve_all(sources, api, exclude_uris)` → resolves all sources, deduplicating

**Stateless design:** The resolver returns `ResolveResult` objects only. It does NOT write to the database. The caller (raid_executor) is responsible for updating `UpstreamSource` tracking fields (`last_resolved_at`, `last_resolve_pathway`, `last_resolve_status`) after resolution completes.

### `direct_api_pathway.py` — Pathway 1

- Calls `api.get_playlist_tracks(source.source_playlist_id)`
- Returns success if tracks are non-empty
- Returns failure (no error) if tracks are empty (triggers fallthrough to next pathway)
- Raises through on `SpotifyNotFoundError`
- `can_handle()`: True for `"own"` and `"external"` source types

### `search_pathway.py` — Pathway 2

- Calls `api.search_tracks(query=source.search_query, limit=10)`
- Paginates to collect up to 20 tracks (2 pages of 10)
- Returns `partial=True` (search results are inherently a subset)
- `can_handle()`: True only for `"search_query"` source type

### `public_scraper_pathway.py` — Pathway 3

Attempts to extract track URIs from Spotify's public web interface:

**Strategy 1: Embed endpoint** — `https://open.spotify.com/embed/playlist/{id}`
- Fetch HTML, extract `spotify:track:XXXX` patterns from embedded JSON/script blocks

**Strategy 2: Public page HTML parsing** — `https://open.spotify.com/playlist/{id}`
- Fetch HTML, extract track URIs and `/track/ID` patterns

**Extraction regex:**
```python
# Pattern 1: spotify:track:ID in JSON
r'"(spotify:track:[a-zA-Z0-9]{22})"'

# Pattern 2: /track/ID in URLs
r'/track/([a-zA-Z0-9]{22})'
```

**Caching:** Results cached via `SpotifyCache` with 1-hour TTL using the `scrape` namespace (key pattern: `shuffify:cache:scrape:{playlist_id}`). This reuses the existing cache infrastructure's error resilience and key management.

**Safety:**
- `timeout=10` on all requests
- Custom User-Agent identifying Shuffify
- Sequential requests (no parallel scraping)
- `can_handle()`: True for `"own"` and `"external"` source types

---

## Files to Modify

### `shuffify/services/executors/raid_executor.py`

Replace `_fetch_raid_sources()` body (lines 144-176) to use `SourceResolver`:

```python
def _fetch_raid_sources(api, source_ids, target_uris, user_id=None):
    from shuffify.services.source_resolver import SourceResolver
    from shuffify.models.db import UpstreamSource

    resolver = SourceResolver()

    # Load full UpstreamSource records if user_id available
    if user_id:
        sources = UpstreamSource.query.filter(
            UpstreamSource.source_playlist_id.in_(source_ids),
            UpstreamSource.user_id == user_id,
        ).all()
        # Include any source_ids not in DB as synthetic sources
        found_ids = {s.source_playlist_id for s in sources}
        for sid in source_ids:
            if sid not in found_ids:
                sources.append(UpstreamSource(source_playlist_id=sid, source_type="external"))
    else:
        sources = [UpstreamSource(source_playlist_id=sid, source_type="external") for sid in source_ids]

    return resolver.resolve_all(sources, api, exclude_uris=target_uris)
```

Update call site in `execute_raid()` (line 59) to pass `user_id=schedule.user_id`.

### `shuffify/services/raid_sync_service.py`

**Fix latent bug** (line 323): `_execute_raid_inline` calls `JobExecutorService._fetch_raid_sources()` which no longer exists. Replace with:
```python
from shuffify.services.executors.raid_executor import _fetch_raid_sources
new_uris = _fetch_raid_sources(api, source_playlist_ids, target_uris)
```

**Add** `watch_search_query()` method for search-based sources.

### `shuffify/services/upstream_source_service.py`

- Expand `source_type` validation: `("own", "external")` → `("own", "external", "search_query")`
- Add `add_search_source()` method:
  - Sets `source_playlist_id` to `NULL` (search sources have no Spotify playlist ID)
  - Deduplicates on `(user_id, target_playlist_id, search_query)`
  - Stores query in new `search_query` column

### `shuffify/schemas/raid_requests.py`

Add `WatchSearchQueryRequest` schema:
```python
class WatchSearchQueryRequest(BaseModel):
    search_query: str
    source_name: Optional[str] = None
    auto_schedule: bool = True
    schedule_value: str = "daily"
```

### `shuffify/routes/raid_panel.py`

Add route: `POST /playlist/<playlist_id>/raid-watch-search`
- Validates `WatchSearchQueryRequest`
- Delegates to `RaidSyncService.watch_search_query()`
- Logs activity

---

## Step-by-Step Implementation (Commit Order)

### Commit 1: DB migration — add resolver fields to UpstreamSource
- `shuffify/models/db.py` — add 3 columns, update `to_dict()`
- Generate and apply migration

### Commit 2: Create source resolver package with DirectAPIPathway
- `shuffify/services/source_resolver/` — `__init__.py`, `base.py`, `resolver.py`, `direct_api_pathway.py`
- `tests/services/source_resolver/test_direct_api_pathway.py`
- `tests/services/source_resolver/test_resolver.py`

### Commit 3: Add SearchPathway
- `shuffify/services/source_resolver/search_pathway.py`
- `tests/services/source_resolver/test_search_pathway.py`

### Commit 4: Add PublicScraperPathway
- `shuffify/services/source_resolver/public_scraper_pathway.py`
- `tests/services/source_resolver/test_public_scraper_pathway.py`

### Commit 5: Wire resolver into raid_executor, fix raid_sync_service bug
- `shuffify/services/executors/raid_executor.py` — replace `_fetch_raid_sources` body
- `shuffify/services/raid_sync_service.py` — fix broken import

### Commit 6: Expand UpstreamSource for search_query sources
- `shuffify/services/upstream_source_service.py` — expand validation, add `add_search_source()`
- `shuffify/services/raid_sync_service.py` — add `watch_search_query()`
- `shuffify/schemas/raid_requests.py` — add `WatchSearchQueryRequest`

### Commit 7: Add route for search-query raid sources
- `shuffify/routes/raid_panel.py` — add `raid_watch_search` route

---

## Testing Strategy

### Per-pathway unit tests

**DirectAPIPathway**: happy path, empty response (restriction), NotFoundError, `can_handle()` logic

**SearchPathway**: happy path, no query configured, empty results, `partial=True` flag, `can_handle()` logic

**PublicScraperPathway**: URI extraction from HTML (both regex patterns), embed endpoint, HTML parsing, fallback chain, Redis cache hit/miss, HTTP failures

### Resolver integration tests

- Owned playlist → DirectAPIPathway succeeds
- External playlist (empty API) → falls through to PublicScraperPathway
- Search query source → routes to SearchPathway
- `resolve_all()` deduplication
- `resolve_all()` returns both new_uris and per-source results

### Regression

All existing tests in `test_raid_sync_service.py` (21 tests), `test_raid_panel_routes.py` (9 tests), and `test_raid_requests.py` must pass unchanged.

---

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Scraping is fragile (page structure changes) | Medium | Scraper is last-resort; search and direct API are primary. Cache aggressively. |
| Spotify blocks server-side requests | Medium | Start simple, escalate headers if needed. Non-critical pathway. |
| Latent bug in `_execute_raid_inline` | Confirmed | Fixed in Commit 5 |
| Migration on production DB | Low | Nullable columns are lock-free on PostgreSQL |

---

## What NOT To Do

- Do NOT change the `Schedule` model or `source_playlist_ids` field
- Do NOT remove the direct API pathway (it's primary for owned playlists)
- Do NOT make HTTP requests without timeouts
- Do NOT scrape in parallel
- Do NOT change the `execute_raid()` function signature
- Do NOT store scraped HTML in the database (only cache parsed URIs in Redis)
