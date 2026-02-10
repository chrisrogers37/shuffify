# Tech Debt Inventory — Q1 2026

**Scan Date:** 2026-02-10
**Codebase Version:** `07de5fd` (main)
**Tests:** 513 passing | 0 failing
**Lint:** Clean (flake8, black)

---

## Executive Summary

The Shuffify codebase is in good health. The four-layer architecture (Routes → Services → Business Logic → External APIs) is well-enforced, test coverage is strong at 479 tests, and there are no TODO/FIXME markers or lint violations. The issues identified are maintenance-oriented — they slow down feature development and increase the risk of bugs when adding new algorithms.

The most impactful issue is **triple-defined algorithm lists** across 3 files, which means adding a new shuffle algorithm requires coordinated changes in 3 places with no compiler or test enforcement if one is missed.

---

## Complete Inventory

### Finding 1: Triple-Defined Algorithm List (3 Sources of Truth)

| Attribute | Value |
|-----------|-------|
| **Severity** | High |
| **Blast Radius** | Any new algorithm addition |
| **Complexity** | Low |
| **Risk if Unaddressed** | Silent validation mismatch when adding algorithms |

**Locations:**
- `shuffify/shuffle_algorithms/registry.py:15-23` — `_algorithms` dict (authoritative)
- `shuffify/schemas/requests.py:101-109` — `valid_algorithms` set in validator
- `shuffify/services/shuffle_service.py:45-53` — `VALID_ALGORITHMS` set

**Problem:** All three must stay in sync manually. If a new algorithm is added to the registry but not to the schema or service, the request will be rejected at validation time with no clear error pointing to the root cause.

**Remediation:** Phase 2

---

### Finding 2: Private API Access in PlaylistService

| Attribute | Value |
|-----------|-------|
| **Severity** | High |
| **Blast Radius** | PlaylistService, SpotifyClient |
| **Complexity** | Low |
| **Risk if Unaddressed** | Breaks if SpotifyClient internals change |

**Location:** `shuffify/services/playlist_service.py:62`

```python
if skip_cache and hasattr(self._client, "_api") and self._client._api:
    playlists = self._client._api.get_user_playlists(skip_cache=True)
```

**Problem:** Reaches through the SpotifyClient facade to access the private `_api` attribute. This violates encapsulation and creates a fragile coupling between the service layer and the internal implementation of the client.

**Remediation:** Phase 3

---

### Finding 3: Duplicate Algorithm Registration

| Attribute | Value |
|-----------|-------|
| **Severity** | High |
| **Blast Radius** | Registry module |
| **Complexity** | Trivial |
| **Risk if Unaddressed** | Confusion, masked intent |

**Location:** `shuffify/shuffle_algorithms/registry.py:82-89`

**Problem:** Algorithms are defined in the `_algorithms` dict (lines 15-23) AND redundantly registered again via `.register()` calls (lines 82-89). The second registration overwrites identical entries, doing nothing useful.

**Remediation:** Phase 1

---

### Finding 4: Shuffle Algorithm Code Duplication

| Attribute | Value |
|-----------|-------|
| **Severity** | Medium |
| **Blast Radius** | 5 algorithm files |
| **Complexity** | Low |
| **Risk if Unaddressed** | Inconsistent bug fixes across algorithms |

**Duplicated Patterns:**
1. **URI extraction** — `[t["uri"] for t in tracks if t.get("uri")]` in 5 files
2. **keep_first split** — identical 10-line block in basic.py, balanced.py, percentage.py, stratified.py
3. **Section calculation** — identical logic in balanced.py and stratified.py

**Remediation:** Phase 4

---

### Finding 5: Missing Direct Test Coverage

| Attribute | Value |
|-----------|-------|
| **Severity** | Medium |
| **Blast Radius** | error_handlers.py, models/playlist.py, routes.py |
| **Complexity** | Medium |
| **Risk if Unaddressed** | Regressions in error formatting, model transforms |

**Untested Modules:**
- `shuffify/error_handlers.py` (193 lines) — no direct tests
- `shuffify/models/playlist.py` (159 lines) — no direct tests
- `shuffify/routes.py` (407 lines) — no direct route-level tests

All are tested *indirectly* via integration tests, but edge cases (malformed data, boundary conditions) are not covered.

**Remediation:** Phase 6

---

### Finding 6: 8-Branch elif Chain in get_algorithm_params()

| Attribute | Value |
|-----------|-------|
| **Severity** | Medium |
| **Blast Radius** | requests.py |
| **Complexity** | Low |
| **Risk if Unaddressed** | Grows linearly with each new algorithm |

**Location:** `shuffify/schemas/requests.py:120-145`

**Problem:** Manual if-elif chain mapping algorithm names to their parameter sets. Coupled to Finding 1 (triple-defined algorithm list).

**Remediation:** Phase 2 (combined with algorithm list consolidation)

---

### Finding 7: TTL Values Defined in Two Places

| Attribute | Value |
|-----------|-------|
| **Severity** | Medium |
| **Blast Radius** | cache.py, config.py |
| **Complexity** | Low |
| **Risk if Unaddressed** | Config drift between code defaults and config values |

**Locations:**
- `shuffify/spotify/cache.py:37-40` — class constants (DEFAULT_TTL, PLAYLIST_TTL, etc.)
- `config.py:38-41` — config values (CACHE_DEFAULT_TTL, CACHE_PLAYLIST_TTL, etc.)

The `__init__.py` factory passes config values to the cache constructor, but the cache class also has its own defaults. If someone instantiates `SpotifyCache` without config, they get different values than what config.py defines.

**Remediation:** Phase 5

---

### Finding 8: Debug Artifacts in Templates

| Attribute | Value |
|-----------|-------|
| **Severity** | Low |
| **Blast Radius** | Frontend templates |
| **Complexity** | Trivial |
| **Risk if Unaddressed** | Code clarity only |

**Locations:**
- `shuffify/templates/base.html:141,145,166-168,181` — `// Uncomment for debugging` comments
- `shuffify/templates/dashboard.html:134-137` — debugging div

**Remediation:** Phase 1

---

### Finding 9: Potentially Unused Playlist Model Methods

| Attribute | Value |
|-----------|-------|
| **Severity** | Low |
| **Blast Radius** | models/playlist.py |
| **Complexity** | Trivial |
| **Risk if Unaddressed** | Dead code maintenance burden |

**Methods with no callers found:**
- `Playlist.get_track(uri)` — line 91
- `Playlist.get_track_with_features(uri)` — line 95
- `Playlist.get_tracks_with_features()` — line 81
- `PlaylistService.get_track_uris()` — line 165

**Remediation:** Phase 5

---

### Finding 10: Inconsistent Route Error Handling

| Attribute | Value |
|-----------|-------|
| **Severity** | Low |
| **Blast Radius** | routes.py |
| **Complexity** | Low |
| **Risk if Unaddressed** | Inconsistent error responses |

**Location:** `shuffify/routes.py:270-282` — `get_playlist` and `get_playlist_stats` routes have no try/catch, unlike all other routes. They rely on the global error handler, which works but is inconsistent.

**Remediation:** Not phased (acceptable as-is; global handler covers these)

---

### Finding 11: Port/Redirect URI Configuration Coupling

| Attribute | Value |
|-----------|-------|
| **Severity** | Low |
| **Blast Radius** | config.py |
| **Complexity** | Low |
| **Risk if Unaddressed** | OAuth failure if PORT changed without updating REDIRECT_URI |

**Location:** `config.py:20,45`

**Remediation:** Not phased (documented risk; requires explicit config in production already)

---

## Dependency Matrix

```
Phase 1 ─────────────────────────────────── (independent)
Phase 2 ─────────────────────────────────── (independent)
Phase 3 ─────────────────────────────────── (independent)
Phase 4 ─────────────────────────────────── (independent)
Phase 5 ─────────────────────────────────── (independent)
Phase 6 ──── depends on Phase 5 ─────────── (dead code removal changes model API)
```

Phases 1–5 touch **completely disjoint file sets** and can safely run in parallel.
Phase 6 must wait for Phase 5 (removed methods must be absent before writing tests).

---

## Prioritized Remediation Order

| Order | Phase | PR Title | Effort | Files Changed |
|-------|-------|----------|--------|---------------|
| 1 | Phase 1 | Remove duplicate registration & debug artifacts | Trivial | 3 |
| 2 | Phase 2 | Consolidate algorithm lists into single source of truth | Low | 2 |
| 3 | Phase 3 | Add skip_cache to SpotifyClient facade | Low | 2 |
| 4 | Phase 4 | Extract shuffle algorithm shared utilities | Low | 6 |
| 5 | Phase 5 | Remove dead code & consolidate TTL config | Low | 2 |
| 6 | Phase 6 | Add tests for error handlers and Playlist model | Medium | 2 (new) |

**Total estimated PRs:** 6
**Parallel-safe groups:** [1, 2, 3, 4, 5] then [6]
