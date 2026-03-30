# Navigation Overhaul — Overview

## Session Context

- **Date**: 2026-03-29
- **Scope**: Full site navigation redesign — from single-page dashboard to multi-section app
- **Trigger**: User wants Workshop as a standalone hub, proper nav bar, and dedicated Activity Log
- **Goal**: 5 top-level sections (Tiles, Workshop, Schedules, Activity, Settings) with persistent navigation

## Phase Summary

| Phase | Title | Impact | Effort | Dependencies |
|-------|-------|--------|--------|--------------|
| 01 | Immediate Fixes | High | Low | None |
| 02 | Navigation Bar | High | Medium | Phase 1 |
| 03 | Workshop Hub | High | Medium | Phase 2 |
| 04 | Activity Log Page | Medium | Medium | Phase 2 |
| 05 | Settings Sidebar | Medium | Medium | Phase 2 |

## Dependency Graph

```
Phase 1 (Fixes)
    └── Phase 2 (Nav Bar)
            ├── Phase 3 (Workshop Hub)     ← parallel
            ├── Phase 4 (Activity Log)     ← parallel
            └── Phase 5 (Settings Sidebar) ← parallel
```

Phases 3, 4, and 5 can run in parallel after Phase 2 ships.

## Data Model

No data model changes needed. Existing models cover all requirements:
- `PlaylistPreference` — sort_order, is_pinned, is_hidden
- `ActivityLog` — 17 activity types, timestamps, playlist references
- `DashboardService._get_quick_stats()` — KPI aggregation
