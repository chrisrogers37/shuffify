# Dashboard Enhancements — Overview

## Session Context

| Field | Value |
|-------|-------|
| **Session Name** | dashboard-enhancements |
| **Date** | 2026-02-25 |
| **Scope** | Dashboard UX: error handling, tile layout, shuffle interaction, playlist management |
| **Entry Point** | Triage (user-reported issues and feature requests) |
| **Enhancements Planned** | 4 |

## User Goals

The user identified 6 issues with the dashboard experience:
1. Workshop button pushed off-screen by long playlist names
2. Shuffle menu interaction is opaque (click-to-expand, dropdown)
3. No ability to reorder, hide, or pin playlist tiles
4. Schedules page returns raw JSON error
5. Settings page returns raw JSON error
6. Refresh button shows error toast

These were consolidated into 4 enhancements addressing all 6 issues.

---

## Phase Summary

| Phase | Title | Impact | Effort | Risk | PR |
|-------|-------|--------|--------|------|----|
| 01 | [Fix Error Handling & Route Resilience](01_error-handling-resilience.md) | High | Medium (3-4h) | Low | #101 ✅ |
| 02 | [Fix Playlist Tile Layout Overflow](02_tile-layout-overflow.md) | Medium | Low (15min) | Low | #102 ✅ |
| 03 | [Redesign Shuffle as Hover Overlay](03_shuffle-hover-overlay.md) | High | High (6-8h) | High | ✅ |
| 04 | [Playlist Tile Management](04_playlist-tile-management.md) | High | High (3-4 days) | Medium | — |

---

## Dependency Graph

```
Phase 01 (Error Handling)     Phase 02 (Tile Layout)
      │                              │
      │                              │
      ▼                              ▼
  (independent)              Phase 03 (Shuffle Overlay)
                                     │
                                     │
                                     ▼
                             Phase 04 (Tile Management)
```

### Execution Order

- **Phases 01 and 02 can run in parallel** — they touch completely disjoint files.
  - Phase 01: `error_handlers.py`, `routes/schedules.py`, `routes/settings.py`, `routes/playlists.py`, `templates/errors/500.html`, minimal JS fix in `dashboard.html`
  - Phase 02: `dashboard.html` (2 CSS class additions on lines 270, 274 only)
- **Phase 03 must wait for Phase 02** — Phase 03 rewrites the tile structure and incorporates the Phase 02 fix.
- **Phase 04 must wait for Phase 03** — Phase 04 builds management controls on top of Phase 03's new tile structure.

### Recommended Implementation Order

1. Phase 01 + Phase 02 (parallel)
2. Phase 03 (after Phase 02 merges)
3. Phase 04 (after Phase 03 merges)

---

## Files Touched Per Phase

| File | Ph 01 | Ph 02 | Ph 03 | Ph 04 |
|------|:-----:|:-----:|:-----:|:-----:|
| `shuffify/error_handlers.py` | M | | | |
| `shuffify/routes/schedules.py` | M | | | |
| `shuffify/routes/settings.py` | M | | | |
| `shuffify/routes/playlists.py` | M | | | |
| `shuffify/routes/core.py` | | | | M |
| `shuffify/routes/__init__.py` | | | | M |
| `shuffify/routes/playlist_preferences.py` | | | | C |
| `shuffify/models/db.py` | | | | M |
| `shuffify/services/playlist_preference_service.py` | | | | C |
| `shuffify/services/__init__.py` | | | | M |
| `shuffify/schemas/playlist_preference_requests.py` | | | | C |
| `shuffify/schemas/__init__.py` | | | | M |
| `shuffify/templates/dashboard.html` | M* | M | M | M |
| `shuffify/templates/errors/500.html` | C | | | |
| `tests/routes/test_error_page_rendering.py` | C | | | |
| `tests/templates/test_dashboard_overlay.py` | | | C | |
| `tests/services/test_playlist_preference_service.py` | | | | C |
| `tests/routes/test_playlist_preferences_routes.py` | | | | C |
| `tests/schemas/test_playlist_preference_requests.py` | | | | C |
| `tests/models/test_playlist_preference_model.py` | | | | C |

**Legend:** M = Modified, C = Created, M* = Minimal targeted change (JS function only)

---

## Total Estimated Effort

| Category | Estimate |
|----------|----------|
| Backend (routes, services, models, schemas) | ~2 days |
| Frontend (templates, JS, CSS) | ~2-3 days |
| Tests | ~1-2 days |
| Documentation | ~2 hours |
| **Total** | **~5-7 days** |
