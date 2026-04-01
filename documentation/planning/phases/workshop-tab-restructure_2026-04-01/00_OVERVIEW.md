# Workshop Tab Restructure — Overview

**Date**: 2026-04-01
**Scope**: Replace Workshop sidebar + Track Inbox with horizontal tabs
**Primary file**: `shuffify/templates/workshop.html` (5968 lines)

## Context

The Workshop page hides powerful features behind a slide-out sidebar ("Powertools") and a collapsible "Track Inbox" that require multiple clicks through nested menus. The recent global navigation restructure (Tiles, Workshop, Schedules, Activity, Settings) established a horizontal tab pattern. This project applies the same pattern *within* the Workshop page.

**Additionally fixes**: Archive tracks not displaying in Track Inbox (archivePair null before sidebar activation).

## Phases

| # | Title | Impact | Effort | Dependencies |
|---|-------|--------|--------|--------------|
| 01 | Horizontal Tab Bar + Playlist Tab Wrapper | Structural foundation | Low | None | ✅ |
| 02 | Raids Tab | Feature move + track display | Medium | Phase 01 | ✅ |
| 03 | Rotation Tab + Archive Bug Fix | Feature move + bug fix | Medium | Phase 01 | ✅ |
| 04 | Schedules + Snapshots Tabs | Feature move | Low-Medium | Phase 01 | ✅ |
| 05 | Remove Sidebar + Track Inbox + Cleanup | Code removal | Medium | Phases 02-04 | ✅ |

## Dependency Graph

```
Phase 01 (Tab Bar)
  ├── Phase 02 (Raids)      ─┐
  ├── Phase 03 (Rotation)    ├── Phase 05 (Cleanup)
  └── Phase 04 (Sched+Snap) ─┘
```

**Phases 02, 03, and 04 can run in parallel** — they touch disjoint HTML sections and JavaScript namespaces. Phase 05 depends on all three being complete.

## Key Decisions

- **Search**: Lives in the Playlist tab as a float-over search menu
- **Track display**: Raids and Rotation tabs use full playlist view format (album art, title, artist, duration) with contextual action buttons
- **No backend changes**: All existing API endpoints are sufficient
- **Element IDs preserved**: Existing JS functions work without modification during migration

## Estimated Net Impact

- ~1560 lines removed, ~70 lines added
- **Net reduction: ~1490 lines** (5968 → ~4480)
