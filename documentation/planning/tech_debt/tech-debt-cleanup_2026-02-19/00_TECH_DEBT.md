# Tech Debt Inventory & Remediation Plan

**Date**: 2026-02-19
**Codebase**: Shuffify (1081 tests, 32,574 LOC Python)
**Session**: `tech-debt-cleanup_2026-02-19`

---

## Executive Summary

The Shuffify codebase is in strong shape: zero lint errors, zero TODO/FIXME comments, 100% service and algorithm test coverage, and clean separation of concerns. However, five areas of technical debt have accumulated across the rapid feature development phases (Playlist Workshop, User Persistence, Workshop Powertools). This document inventories all findings and defines a 5-phase remediation plan.

---

## Complete Inventory

### Finding 1: Route Auth/DB Boilerplate Duplication

**Severity**: Medium | **Blast Radius**: 10 route modules | **Complexity**: Medium

Nearly every authenticated route repeats an 8-15 line block checking `require_auth()`, `is_db_available()`, and `get_db_user()`. This pattern appears **20+ times** across `snapshots.py` (5x), `playlist_pairs.py` (6x), `upstream_sources.py` (3x), `raid_panel.py` (2x), and `schedules.py` (4x+).

Additionally, ~23 routes manually construct `jsonify()` responses instead of using existing `json_success()`/`json_error()` helpers, and ~10 routes repeat a try/except activity logging pattern.

**Risk**: Inconsistent error responses if one route forgets a check. Maintenance burden when changing auth flow.

---

### Finding 2: Service Layer CRUD Pattern Duplication

**Severity**: Medium | **Blast Radius**: 6 service files | **Complexity**: Low-Medium

Nine locations across 6 services repeat identical try/commit/rollback/log patterns for database operations. Eight locations across 2 services repeat identical `User.query.filter_by(spotify_id=...)` lookups. Three services repeat identical ownership-check patterns.

**Affected services**: upstream_source_service, playlist_snapshot_service, user_settings_service, user_service, login_history_service, workshop_session_service.

**Risk**: Inconsistent error handling if one service diverges. Copy-paste bugs when adding new services.

---

### Finding 3: Overly Complex Functions

**Severity**: Medium-High | **Blast Radius**: 4 files | **Complexity**: High

Seven functions exceed healthy complexity thresholds:

| Function | File | Lines | Primary Issue |
|----------|------|-------|---------------|
| `_execute_rotate()` | job_executor_service.py | 151 | 3 rotation mode branches, 4-level nesting |
| `execute()` | job_executor_service.py | 138 | Multiple responsibilities, deep nesting |
| `_execute_raid()` | job_executor_service.py | 123 | Nested loops with dedup logic |
| `workshop_load_external_playlist()` | routes/workshop.py | 113 | Dual-mode (URL vs search) |
| `workshop_commit()` | routes/workshop.py | 111 | Auto-snapshot + validation + logging |
| `raid_now()` | raid_sync_service.py | 110 | Schedule vs inline branching |
| `api_error_handler()` | spotify/api.py | 87 | 11+ error type branches |

**Risk**: Hard to test specific paths, high bug surface area, difficult onboarding for new contributors.

---

### Finding 4: Missing Route-Level Tests

**Severity**: High | **Blast Radius**: 5 route modules, 31 endpoints | **Complexity**: Medium

Only 5 of 10 route modules have dedicated test files. Missing route tests:

| Route Module | Endpoints | Impact |
|--------------|-----------|--------|
| `routes/shuffle.py` | 2 (shuffle, undo) | **Core feature** untested at HTTP level |
| `routes/playlists.py` | 4 (refresh, get, stats, API) | **Core feature** untested at HTTP level |
| `routes/schedules.py` | 8 (CRUD, toggle, run, history) | Schedule management untested |
| `routes/upstream_sources.py` | 3 (list, add, delete) | Raid source management untested |
| `routes/core.py` | 7 (scattered tests, no dedicated file) | Needs consolidation |

**Note**: Service-layer tests exist for all underlying logic. The gap is specifically HTTP request/response handling, status codes, auth guards, and error responses.

**Risk**: Regressions in HTTP behavior (status codes, error messages, auth redirects) go undetected.

---

### Finding 5: Missing Schema Tests, Dead Code & Outdated Dependencies

**Severity**: Low-Medium | **Blast Radius**: Mixed | **Complexity**: Low

**Missing schema tests**:
- `schemas/settings_requests.py` — `UserSettingsUpdateRequest` validators untested
- `schemas/snapshot_requests.py` — `ManualSnapshotRequest` URI validation untested

**Dead code**:
- `upstream_source_service.py:211` — `count_sources_for_target()` defined but never called

**Outdated dependencies** (50 packages behind latest):
- Key production packages: gunicorn 23→25, certifi (security certs), Flask 3.1.2→3.1.3
- Key dev packages: flake8 6→7 (major), pytest 8→9 (major)

**Risk**: Dead code confuses contributors. Outdated deps miss security patches.

---

## Dependency Matrix

```
Phase 1: Route Infrastructure & Boilerplate Cleanup
    ↓ (routes are cleaner, making them easier to test)
Phase 4: Missing Route Tests
    (independent — tests only, no source changes)

Phase 2: Service Layer Deduplication
    ↓ (services are cleaner, making them easier to decompose)
Phase 3: Complex Function Decomposition
    (builds on cleaner service patterns)

Phase 5: Schema Tests, Dead Code & Dependencies
    (fully independent — can run anytime)
```

### Parallel Safety

| Phase | Can Run In Parallel With | Reason |
|-------|--------------------------|--------|
| 1 | 2, 5 | Touches routes/ only; 2 touches services/; 5 touches tests/ + requirements/ |
| 2 | 1, 5 | Touches services/ only |
| 3 | 4, 5 | Touches different files than route tests |
| 4 | 3, 5 | New test files only, no source changes |
| 5 | 1, 2, 3, 4 | Touches tests/schemas/, one line in service, requirements/ |

### Recommended Execution Order

**Sequential pairs for safety**:
1. Phase 1 → Phase 4 (clean routes before testing them)
2. Phase 2 → Phase 3 (clean services before decomposing functions)

**Can run anytime**: Phase 5

**Optimal order**: 1 → 2 → 3 → 4 → 5
(But 5 can be started at any point, and 1/2 can run in parallel)

---

## Prioritized Remediation Order

| Priority | Phase | Finding | Effort | PR |
|----------|-------|---------|--------|-----|
| 1 | Phase 1 | Route Infrastructure & Boilerplate Cleanup | Medium | 1 PR |
| 2 | Phase 2 | Service Layer Deduplication | Medium | 1 PR |
| 3 | Phase 3 | Complex Function Decomposition | High | 1 PR |
| 4 | Phase 4 | Missing Route Tests | Medium | 1 PR |
| 5 | Phase 5 | Schema Tests, Dead Code & Dependencies | Low | 1 PR |

**Total**: 5 PRs, each independently reviewable and deployable.

---

## Severity Scoring

| Finding | Blast Radius (1-5) | Complexity (1-5) | Risk (1-5) | Total |
|---------|--------------------|--------------------|------------|-------|
| Missing Route Tests | 5 | 3 | 4 | 12 |
| Complex Functions | 4 | 4 | 3 | 11 |
| Route Boilerplate | 4 | 2 | 2 | 8 |
| Service Duplication | 3 | 2 | 2 | 7 |
| Schema Tests/Dead Code/Deps | 2 | 1 | 2 | 5 |

---

## Phase Plan Documents

| Document | Phase | Status |
|----------|-------|--------|
| [01_route-infrastructure-cleanup.md](01_route-infrastructure-cleanup.md) | Route Infrastructure & Boilerplate Cleanup | ✅ COMPLETE (PR #83) |
| [02_service-layer-deduplication.md](02_service-layer-deduplication.md) | Service Layer Deduplication | ✅ COMPLETE (PR #84) |
| [03_complex-function-decomposition.md](03_complex-function-decomposition.md) | Complex Function Decomposition | ✅ COMPLETE (PR #85) |
| [04_missing-route-tests.md](04_missing-route-tests.md) | Missing Route Tests | 🔧 IN PROGRESS |
| [05_schema-tests-dead-code-deps.md](05_schema-tests-dead-code-deps.md) | Schema Tests, Dead Code & Dependencies | Planned |
