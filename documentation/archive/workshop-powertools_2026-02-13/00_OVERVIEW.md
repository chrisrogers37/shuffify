# Workshop Powertools Enhancement Suite

**Session Name:** workshop-powertools
**Date:** 2026-02-13
**Scope:** Workshop sidebar with power-user automation features
**User Goals:** "Set-and-forget" automation — smart raid scheduling, archive playlist pairing, better backup/restore UX, all accessible from the Workshop

---

## Session Context

### User's Stated Intent
- **Core frustration:** Power-user features (snapshots, raids, scheduling) exist but are either invisible or scattered across separate pages
- **Dream state:** "Set-and-forget automation" — configure once, playlist stays fresh forever
- **Key capabilities wanted:**
  1. Smart raid scheduling — point at playlists, auto-detect new tracks on a schedule
  2. Archive playlist pairing — production/archive pairs where removed tracks go to archive, with scheduled rotation
  3. Better backup/restore UX — surface the existing but invisible snapshot system

### Gap Analysis Summary
| # | Category | Area | Finding |
|---|----------|------|---------|
| 1 | Missing capability | Archive pairing | No paired playlists — tracks removed in workshop are gone forever |
| 2 | Missing capability | Scheduled rotation | No way to automatically cycle stale tracks out and fresh tracks in |
| 3 | Partial impl | Smart raid scheduling | Raid infrastructure exists but source ↔ schedule are disconnected |
| 4 | UX friction | Snapshot browsing | Full API exists (5 endpoints) but zero UI — completely invisible |
| 5 | Untapped potential | Upstream ↔ Schedule | Adding a raid source doesn't auto-create a schedule |
| 6 | UX friction | Raid setup | Multi-step manual process to set up a simple "watch this playlist" flow |

---

## Phase Documents

| Phase | Title | Risk | Effort | PR | Status |
|-------|-------|------|--------|----|--------|
| 01 | [Unified Workshop Sidebar](01_unified-workshop-sidebar.md) | Low | Small | #64 | ✅ Complete |
| 02 | [Snapshot Browser Panel](02_snapshot-browser-panel.md) | Low | Medium | #65 | ✅ Complete |
| 03 | [Archive Playlist Pairing](03_archive-playlist-pairing.md) | Medium | Medium | #66 | ✅ Complete |
| 04 | [Smart Raid Panel](04_smart-raid-panel.md) | Low | Medium | #67 | ✅ Complete |
| 05 | [Scheduled Rotation Job Type](05_scheduled-rotation-job-type.md) | Medium | Medium-High | #68 | ✅ Complete |

---

## Dependency Graph

```
Phase 1: Unified Workshop Sidebar
    ├── Phase 2: Snapshot Browser Panel (fills Snapshots tab)
    ├── Phase 3: Archive Playlist Pairing (fills Archive tab + new backend)
    │       └── Phase 5: Scheduled Rotation Job Type (requires PlaylistPair model)
    └── Phase 4: Smart Raid Panel (fills Raids tab + new backend)
```

### Sequential Dependencies
- **Phase 1 must complete first** — it provides the sidebar framework that all other phases inject content into
- **Phase 5 depends on Phase 3** — rotation requires the `PlaylistPair` model and `PlaylistPairService` from archive pairing

### Parallel Opportunities
After Phase 1 is merged:
- **Phases 2, 3, and 4 can run in parallel** — they touch mostly disjoint files:
  - Phase 2: `workshop.html` only (Snapshots tab content + JS)
  - Phase 3: New files (`playlist_pair_service.py`, `playlist_pairs.py`, `playlist_pair_requests.py`, migration) + `workshop.html` (Archive tab + `deleteTrack()` modification) + `db.py` + `enums.py`
  - Phase 4: New files (`raid_sync_service.py`, `raid_panel.py`, `raid_requests.py`) + `workshop.html` (Raids tab content + JS)
- **Conflict zone:** All three modify `workshop.html`, but in different sections (different tab panels, different JS blocks). Implementers should coordinate merge order.
- **Recommended order if sequential:** Phase 2 → Phase 3 → Phase 4 (simplest to most complex)

---

## Total Estimated Effort

| Phase | Effort |
|-------|--------|
| Phase 1 | 3-4 hours |
| Phase 2 | 3-4 hours |
| Phase 3 | 3-4 hours |
| Phase 4 | 4-6 hours |
| Phase 5 | 6-8 hours |
| **Total** | **~19-26 hours** |

---

## Files Touched Summary

### New Files Created
| File | Phase |
|------|-------|
| `shuffify/services/playlist_pair_service.py` | 3 |
| `shuffify/services/raid_sync_service.py` | 4 |
| `shuffify/routes/playlist_pairs.py` | 3 |
| `shuffify/routes/raid_panel.py` | 4 |
| `shuffify/schemas/playlist_pair_requests.py` | 3 |
| `shuffify/schemas/raid_requests.py` | 4 |
| `migrations/versions/*_add_playlist_pairs_table.py` | 3 |
| `tests/services/test_playlist_pair_service.py` | 3 |
| `tests/services/test_raid_sync_service.py` | 4 |
| `tests/services/test_job_executor_rotate.py` | 5 |
| `tests/routes/test_playlist_pairs_routes.py` | 3 |
| `tests/test_raid_panel_routes.py` | 4 |
| `tests/schemas/test_playlist_pair_requests.py` | 3 |
| `tests/schemas/test_raid_requests.py` | 4 |

### Modified Files
| File | Phases |
|------|--------|
| `shuffify/templates/workshop.html` | 1, 2, 3, 4, 5 |
| `shuffify/enums.py` | 3, 5 |
| `shuffify/models/db.py` | 3 |
| `shuffify/services/__init__.py` | 3, 4 |
| `shuffify/schemas/__init__.py` | 3, 4 |
| `shuffify/routes/__init__.py` | 3, 4 |
| `shuffify/spotify/api.py` | 5 |
| `shuffify/services/job_executor_service.py` | 5 |
| `shuffify/services/upstream_source_service.py` | 4 |
| `shuffify/schemas/schedule_requests.py` | 5 |
| `shuffify/routes/schedules.py` | 5 |
| `shuffify/templates/schedules.html` | 5 |
| `CHANGELOG.md` | 1, 2, 3, 4, 5 |
| `documentation/README.md` | 1 |

---

## Implementation Notes

- **Pre-push checklist** (required for every phase): `flake8 shuffify/ && pytest tests/ -v`
- **Branch naming**: `enhance/workshop-powertools/phase-NN-<slug>`
- **PR pattern**: One PR per phase, merged to `main` before starting the next dependent phase
- **Workshop HTML caution**: Multiple phases modify `workshop.html`. Each phase adds content in distinct sections (sidebar HTML, JS blocks, CSS). Review diffs carefully to avoid merge conflicts.
- **No new pip dependencies**: All phases use existing packages only
- **Database migration**: Only Phase 3 requires a migration (new `playlist_pairs` table)

---

*Generated by `/product-enhance` skill — Workshop Powertools session, 2026-02-13*
