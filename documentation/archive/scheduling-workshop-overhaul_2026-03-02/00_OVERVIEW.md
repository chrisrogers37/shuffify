# Scheduling & Workshop Overhaul

**Session**: scheduling-workshop-overhaul
**Date**: 2026-03-02
**Scope**: Fix scheduling bugs, make Workshop the central configuration hub for raid and rotation, connect Scheduler to Workshop data, and harden scheduler architecture for multi-user scaling.

## Context

User-reported issues with the scheduling system revealed bugs (schedule creation showing errors despite success), design flaws (raid sources populated from user's own playlists), feature gaps (rotation pair setup disconnected from scheduler), and scalability concerns (single-process APScheduler with 3-thread pool).

The overarching design philosophy: **Workshop configures, Scheduler executes.** All playlist configuration (raid sources, archive pairs) lives in the Workshop. The Scheduler references that configuration and redirects users to the Workshop when setup is needed.

## Phase Summary

| Phase | Title | Impact | Effort | Dependencies |
|-------|-------|--------|--------|--------------|
| 01 | Fix Schedule Creation Error-But-Success Bug | High | Low | None | ✅ PR #125 |
| 02 | Remove Hardcoded Schedule Limit | Medium | Low | None | ✅ PR #125 |
| 03 | Workshop as Raid Configuration Hub | High | Medium | None | ✅ PR #126 |
| 04 | Workshop as Rotation Configuration Hub | High | Medium | None | ✅ PR #126 |
| 05 | Scheduler Smart Routing & Workshop Linkage | High | Medium | Phases 03, 04 | ✅ PR #127 |
| 06 | Scheduler Scaling Architecture | Medium | High | None (but best after 01-05) | ✅ PR #128 |

## Dependency Graph

```
Phase 01 (bug fix) ──────────────────────────────────┐
Phase 02 (limit removal) ────────────────────────────┤
Phase 03 (workshop raid) ──────┐                      │
Phase 04 (workshop rotation) ──┼── Phase 05 (linkage) │
                               │                      │
Phase 06 (scaling) ────────────┴──────────────────────┘
```

## Parallelization

- **Phases 01 + 02**: Independent, can run in parallel. Touch `schedules.py` and `scheduler_service.py` respectively (disjoint changes).
- **Phases 03 + 04**: Independent, can run in parallel. Phase 03 touches `raid_panel.py` + Workshop raids tab. Phase 04 touches `playlist_pairs.py` + Workshop archive tab.
- **Phase 05**: Depends on 03 and 04. Must wait for both.
- **Phase 06**: Independent of all others, but best done last since it modifies `scheduler.py` which Phase 01 also touches.

## Recommended Execution Order

1. Phases 01 + 02 (parallel) — quick bug fixes
2. Phases 03 + 04 (parallel) — Workshop enhancements
3. Phase 05 — Scheduler-Workshop linkage
4. Phase 06 — Scaling architecture

## Total Estimated Files Modified

~25 files across all phases (some overlap between phases on shared files like `schedules.py`, `workshop.html`).
