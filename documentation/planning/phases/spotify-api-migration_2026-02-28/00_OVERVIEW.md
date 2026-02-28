# Spotify API Migration — February 2026 Breaking Changes

**Session Date:** 2026-02-28
**Scope:** Full product adaptation to Spotify's February 2026 API breaking changes
**Deadline:** March 9, 2026 (existing app enforcement date)

---

## Context

Spotify announced breaking API changes effective February 2026, with enforcement for existing apps on March 9, 2026:

- **Endpoint renames**: `/playlists/{id}/tracks` → `/playlists/{id}/items`; response fields `tracks` → `items`, `track` → `item`
- **External playlist restriction**: Playlist items only returned for owned/collaborative playlists
- **Search limit reduction**: Max 50 → 10, default 20 → 5
- **User profile field removal**: `country`, `email`, `product`, `explicit_content` dropped from `GET /v1/me`
- **Development Mode cap**: 5-user limit, Premium required

The app depends on `spotipy==2.25.2`, which hardcodes old endpoint paths and has no update. `requests==2.32.5` is already a dependency.

### User Goals
- Preserve raid functionality as the highest priority feature
- Support multiple resolution pathways for external playlist content
- Graceful degradation over silent failure
- General greetings when display name is unavailable
- Prepare for Extended Quota Mode application

---

## Phase Summary

| # | Phase | Impact | Effort | Risk | PR Type |
|---|-------|--------|--------|------|---------|
| 01 | [Replace spotipy with Direct HTTP Client](01_direct-http-client.md) | Critical | High (6-10h) | High | `feat` | ✅ PR #106 |
| 02 | [Fix Search Endpoint Limits](02_search-limit-fix.md) | High | Low (1-2h) | Low | `fix` | ✅ PR #107 |
| 03 | [Raid System Pivot — Multi-Pathway Source Resolver](03_raid-system-pivot.md) | High | High (6-8h) | Medium | `feat` |
| 04 | [Workshop Graceful Degradation](04_workshop-graceful-degradation.md) | Medium | Medium (2-3h) | Low | `feat` | ✅ PR #108 |
| 05 | [User Profile Fallbacks](05_user-profile-fallbacks.md) | Medium | Low (1-2h) | Low | `fix` | ✅ PR #109 |
| 06 | [Extended Quota Mode Preparation](06_extended-quota-mode-prep.md) | Low | Low (docs + 1 change) | Low | Business/process |

**Total estimated effort:** 17-25 hours

---

## Dependency Graph

```
Phase 01 (HTTP Client)
  │
  ├──→ Phase 02 (Search Limits) — benefits from new client, but NOT blocked
  ├──→ Phase 03 (Raid Pivot) — benefits from new client, but NOT blocked
  ├──→ Phase 04 (Workshop Degradation) — independent
  ├──→ Phase 05 (User Profile) — independent
  └──→ Phase 06 (Quota Mode) — independent (scope reduction only)
```

**No hard blocking dependencies.** Phase 01 is the foundation — all other phases benefit from the new HTTP client but can be implemented against the existing spotipy layer if needed (the endpoint renames are what break things, and each phase handles its own concerns).

---

## Parallel Execution

These phases touch **disjoint file sets** and can safely run in parallel:

| Parallel Group | Phases | Rationale |
|---------------|--------|-----------|
| **Group A** | 01 | Standalone — rewrites `api.py`, `auth.py`, `client.py` |
| **Group B** | 02, 05, 06 | Each touches different files with no overlap |
| **Group C** | 03 | New `source_resolver/` package + `raid_executor.py` modifications |
| **Group D** | 04 | Workshop route + template changes |

**Recommended execution order:**
1. Phase 01 first (critical path — deadline is March 9)
2. Phases 02 + 05 + 06 in parallel (quick wins, low risk)
3. Phase 03 (largest new feature, medium risk)
4. Phase 04 (depends on understanding post-migration API behavior)

---

## Key Architectural Decisions

1. **Direct HTTP over spotipy**: spotipy 2.25.2 has no update. Rather than monkey-patching, we replace with `requests` calls (already a dependency) for a clean, maintainable solution.

2. **Multi-pathway source resolver for raids**: Three fallback strategies — Direct API (owned playlists), Search Discovery (always works), Public Web Scraping (last resort). Each pathway is independently testable.

3. **Preserve-on-absent pattern for user fields**: Distinguish between a field being absent (keep DB value) and a field being explicitly `None` (overwrite). This prevents login-triggered data loss.

4. **OAuth scopes retained**: `user-read-private` and `user-read-email` are NOT removed despite field removals, because removing scopes would invalidate all existing refresh tokens.

---

## Risk Summary

| Risk | Phase | Severity | Mitigation |
|------|-------|----------|------------|
| March 9 deadline missed | 01 | Critical | Phase 01 is priority #1; deploy by March 7 |
| spotipy removal breaks untested paths | 01 | High | 1296 existing tests + new HTTP client tests |
| Web scraping fragility | 03 | Medium | Scraping is last-resort fallback; search is primary |
| 250k MAU requirement blocks quota | 06 | High | Apply anyway; operate within 5-user limit if denied |
| Privacy policy inaccuracy flagged | 06 | Medium | Update before submitting application |
