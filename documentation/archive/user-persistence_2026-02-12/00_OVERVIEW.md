# User Persistence Enhancement Suite

## Session Context

- **Session Name:** user-persistence
- **Date:** 2026-02-12
- **Scope:** Transform Shuffify from session-based ephemeral state to persistent user-centric architecture
- **User's Goal:** "Be able to have persistent users, maintain a user dimension table, and link activity back to a fixed user" -- enabling settings, playlist snapshots, activity logging, and a personalized dashboard.

## Key Constraint

There are **no existing users and no existing database** -- this is a clean slate. The User model exists in code but no persistent database has been deployed. This dramatically simplifies all phases (no data migration needed).

## Phase Summary

| Phase | Document | PR Title | Risk | Effort | Status |
|-------|----------|----------|------|--------|--------|
| **0** | [00_persistent-postgresql-database.md](00_persistent-postgresql-database.md) | `feat: Add PostgreSQL support with Alembic migrations` | Medium | 6-8 hrs | ✅ COMPLETED (PR #56) |
| **1** | [01_user-dimension-table.md](01_user-dimension-table.md) | `feat: Enrich User model with login tracking and Spotify profile fields` | Low | 2-3 days | ✅ COMPLETED (PR #57) |
| **2** | [02_login-session-tracking.md](02_login-session-tracking.md) | `feat: Add LoginHistory model and service for sign-in event tracking` | Low | 3-4 days | ✅ COMPLETED (PR #58) |
| **3** | [03_user-settings-preferences.md](03_user-settings-preferences.md) | `feat: Add UserSettings model, service, and settings page` | Low | 8-10 hrs | ✅ COMPLETED (PR #59) |
| **4** | [04_playlist-snapshots.md](04_playlist-snapshots.md) | `feat: Add PlaylistSnapshot model with auto/manual snapshot capture` | Medium | 2-3 days | ✅ COMPLETED (PR #60) |
| **5** | [05_activity-log.md](05_activity-log.md) | `feat: Add ActivityLog model and service for unified activity tracking` | Low-Medium | 4-6 hrs | ✅ COMPLETED (PR #61) |
| **6** | [06_personalized-dashboard.md](06_personalized-dashboard.md) | `feat: Add personalized dashboard with activity feed and stats` | Medium | 1.5-2 days | ✅ COMPLETED (PR #62) |

## Dependency Graph

```
Phase 0: PostgreSQL + Alembic
    │
    ├── Phase 1: User Dimension Table (depends on Phase 0)
    │       │
    │       ├── Phase 2: Login History (depends on Phase 1)
    │       │
    │       ├── Phase 3: User Settings (depends on Phase 1)
    │       │
    │       └── Phase 4: Playlist Snapshots (depends on Phase 1)
    │               │
    │               └── Phase 5: Activity Log (depends on Phases 1-4)
    │                       │
    │                       └── Phase 6: Dashboard (depends on Phase 5)
```

## Implementation Order

Phases must be implemented in numeric order (0 through 6). Each phase builds on the prior phase's models, services, and patterns.

### Sequential Dependencies (must complete in order)
- **Phase 0 -> Phase 1**: Phase 1 generates an Alembic migration; requires Phase 0's Flask-Migrate setup
- **Phase 1 -> Phase 2**: LoginHistory has FK to User; relies on the enriched User model
- **Phase 1 -> Phase 3**: UserSettings has FK to User; auto-creation hooks into upsert_from_spotify
- **Phase 1 -> Phase 4**: PlaylistSnapshot has FK to User; uses spotify_id from enriched User
- **Phases 1-4 -> Phase 5**: ActivityLog hooks into routes/services from all prior phases
- **Phase 5 -> Phase 6**: Dashboard displays activity feed from Phase 5's ActivityLog

### Parallel Opportunities
After Phase 1 is complete, **Phases 2, 3, and 4 can theoretically run in parallel** since they touch disjoint files:
- Phase 2: `login_history_service.py`, `LoginHistory` model, callback/logout routes
- Phase 3: `user_settings_service.py`, `UserSettings` model, `/settings` route + template
- Phase 4: `playlist_snapshot_service.py`, `PlaylistSnapshot` model, snapshot routes + template

**However**, running them in parallel requires careful coordination on shared files:
- `shuffify/models/db.py` -- all three add new models
- `shuffify/services/__init__.py` -- all three add new exports
- `CHANGELOG.md` -- all three add entries

**Recommendation:** Run sequentially (0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6) to avoid merge conflicts. The effort savings from parallelism don't justify the coordination overhead.

## Total Estimated Effort

~8-12 working days for the full suite (junior engineer pace), with each phase representing exactly 1 PR.

## Models Introduced

| Phase | Model | Table | Purpose |
|-------|-------|-------|---------|
| 0 | *(none)* | *(none)* | Database infrastructure only |
| 1 | `User` (enhanced) | `users` | +6 columns: login tracking, Spotify profile |
| 2 | `LoginHistory` | `login_history` | Per-session login/logout event records |
| 3 | `UserSettings` | `user_settings` | User preferences (1:1 with User) |
| 4 | `PlaylistSnapshot` | `playlist_snapshots` | Point-in-time playlist backup/restore |
| 5 | `ActivityLog` | `activity_log` | Unified audit trail of all user actions |
| 6 | *(none)* | *(none)* | Read-only aggregation service |

## Services Introduced

| Phase | Service | Purpose |
|-------|---------|---------|
| 1 | `UserService` (enhanced) | `UpsertResult` dataclass, login tracking |
| 2 | `LoginHistoryService` | Record/query login events |
| 3 | `UserSettingsService` | CRUD for user preferences |
| 4 | `PlaylistSnapshotService` | CRUD + auto-snapshot + restore |
| 5 | `ActivityLogService` | Non-blocking activity logging + queries |
| 6 | `DashboardService` | Aggregate stats, activity, schedules |

## Design Principles

1. **Non-blocking**: All DB operations in auth flow wrapped in try/except. Login always succeeds even if DB fails.
2. **Additive-only**: No existing columns/tables are renamed or dropped. All changes add new capabilities.
3. **Graceful degradation**: Every new feature degrades silently -- snapshot failures don't block shuffles, logging failures don't block operations.
4. **Service layer pattern**: All business logic in static service methods, consistent with existing architecture.
5. **Test coverage**: Each phase includes 15-30 new tests. Estimated ~140+ new tests across all phases.
