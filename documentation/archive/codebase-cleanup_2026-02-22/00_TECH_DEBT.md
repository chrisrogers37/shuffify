# Tech Debt Master Inventory: Codebase Cleanup
**Session:** codebase-cleanup_2026-02-22
**Date:** 2026-02-22
**Status:** Planning
**Total Findings:** 48
**Phases Planned:** 7

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Severity Scoring Methodology](#2-severity-scoring-methodology)
3. [Findings Inventory](#3-findings-inventory)
4. [Phase Summary & Prioritized Remediation Order](#4-phase-summary--prioritized-remediation-order)
5. [Dependency Matrix](#5-dependency-matrix)
6. [Parallel Execution Groups](#6-parallel-execution-groups)
7. [Risk Assessment](#7-risk-assessment)
8. [Files Affected by Phase](#8-files-affected-by-phase)

---

## 1. Executive Summary

A comprehensive tech debt scan of the Shuffify codebase identified **48 findings** across 7 remediation areas. The findings range from inconsistent database commit patterns (15 instances) and manual auth boilerplate (16 routes) to a 969-line monolithic service and 5,568 lines of undecomposed templates with zero macros or includes.

No findings are critical (production-breaking). The majority are **Medium** severity items that increase maintenance burden and defect risk. Remediation is organized into 7 phases that can be partially parallelized, with an estimated total effort of **Medium-High** across all phases.

### Key Metrics

| Metric | Value |
|--------|-------|
| Total findings | 48 |
| Files affected | 30+ |
| Services needing commit fixes | 7 |
| Routes needing auth decorator | 16 |
| Validation patterns to standardize | 8 |
| Template lines to reduce | ~2,068 (38% of 5,568) |
| Sentry SDK versions behind | 2 major |

---

## 2. Severity Scoring Methodology

Each finding is scored on three dimensions:

| Dimension | Low (1) | Medium (2) | High (3) |
|-----------|---------|------------|----------|
| **Blast Radius** | Single file, no consumers | Multiple files or one service boundary | Cross-cutting, affects many modules |
| **Complexity** | Mechanical change, no logic | Requires careful refactoring | Architectural change, many moving parts |
| **Risk** | No behavior change possible | Behavior change unlikely but possible | Behavior change likely without care |

**Composite Score** = Blast Radius + Complexity + Risk (range: 3-9)

| Score Range | Severity Label |
|-------------|---------------|
| 3-4 | LOW |
| 5-6 | MEDIUM |
| 7-9 | HIGH |

---

## 3. Findings Inventory

### 3.1 Database Commit Handling (15 findings)

The codebase has a `safe_commit()` helper in `shuffify/services/base.py:16-50` that wraps `db.session.commit()` with rollback-on-failure, logging, and exception re-raising. Five services use it correctly; seven files do not.

| # | File | Line(s) | Pattern | Issue | Blast | Cmplx | Risk | Sev |
|---|------|---------|---------|-------|-------|-------|------|-----|
| F01 | `user_service.py` | 137 | Manual try/except | Duplicates safe_commit logic | 1 | 1 | 1 | LOW |
| F02 | `activity_log_service.py` | 61 | Manual try/except | Returns None on failure (non-blocking); still should use safe_commit with custom handling | 1 | 2 | 1 | LOW |
| F03 | `login_history_service.py` | 137 | Manual try/except | Already imports safe_commit but does not use it here | 1 | 1 | 1 | LOW |
| F04 | `playlist_pair_service.py` | 59 | Bare commit | `create_pair()` -- no error handling at all | 1 | 1 | 2 | LOW |
| F05 | `playlist_pair_service.py` | 98 | Bare commit | `delete_pair()` -- no error handling | 1 | 1 | 2 | LOW |
| F06 | `scheduler_service.py` | 123 | Bare commit | `create_schedule()` -- no error handling | 1 | 1 | 2 | LOW |
| F07 | `scheduler_service.py` | 181 | Bare commit | `update_schedule()` -- no error handling | 1 | 1 | 2 | LOW |
| F08 | `scheduler_service.py` | 219 | Bare commit | `delete_schedule()` -- no error handling | 1 | 1 | 2 | LOW |
| F09 | `scheduler_service.py` | 250 | Manual try/except | `toggle_schedule()` -- has try/except but not safe_commit | 1 | 1 | 1 | LOW |
| F10 | `job_executor_service.py` | 111 | Bare commit | `_create_execution_record()` | 1 | 1 | 2 | LOW |
| F11 | `job_executor_service.py` | 134 | Bare commit | `_record_success()` | 1 | 1 | 2 | LOW |
| F12 | `job_executor_service.py` | 209 | Bare within try | `_record_failure()` | 1 | 1 | 2 | LOW |
| F13 | `job_executor_service.py` | 323 | Bare commit | Token rotation commit | 1 | 1 | 2 | LOW |
| F14 | `playlist_snapshot_service.py` | 248 | Bare commit | `cleanup_old_snapshots()` -- already imports safe_commit | 1 | 1 | 1 | LOW |
| F15 | `routes/core.py` | 265 | Bare within try | OAuth callback token storage | 2 | 1 | 2 | MEDIUM |

### 3.2 Route Auth & Validation (24 findings)

The codebase has a `@require_auth_and_db` decorator in `shuffify/routes/__init__.py:103-143` that validates authentication, database connectivity, and user existence, then injects `client` and `user` as kwargs. 24 routes in newer modules use it; 16 routes in older modules use manual inline auth checks. Additionally, 8 Pydantic validation blocks use 4 different error-handling patterns.

#### 3.2.1 Routes Missing `@require_auth_and_db` (16 findings)

| # | File | Route | Line | Blast | Cmplx | Risk | Sev |
|---|------|-------|------|-------|-------|------|-----|
| F16 | `playlists.py` | `refresh_playlists()` | 19-21 | 2 | 1 | 2 | MEDIUM |
| F17 | `playlists.py` | `get_playlist()` | 47-49 | 2 | 1 | 2 | MEDIUM |
| F18 | `playlists.py` | `get_playlist_stats()` | 65-67 | 2 | 1 | 2 | MEDIUM |
| F19 | `playlists.py` | `api_user_playlists()` | 77-79 | 2 | 1 | 2 | MEDIUM |
| F20 | `shuffle.py` | `shuffle()` | 35-37 | 2 | 2 | 2 | MEDIUM |
| F21 | `shuffle.py` | `undo()` | 165-167 | 2 | 2 | 2 | MEDIUM |
| F22 | `workshop.py` | `workshop_preview_shuffle()` | 99-101 | 2 | 1 | 2 | MEDIUM |
| F23 | `workshop.py` | `workshop_search()` | 268-270 | 2 | 1 | 2 | MEDIUM |
| F24 | `workshop.py` | `workshop_search_playlists()` | 338-340 | 2 | 1 | 2 | MEDIUM |
| F25 | `workshop.py` | `workshop_load_external_playlist()` | 481-483 | 2 | 1 | 2 | MEDIUM |
| F26 | `workshop.py` | `list_workshop_sessions()` | 517-519 | 2 | 1 | 2 | MEDIUM |
| F27 | `workshop.py` | `save_workshop_session()` | 549-551 | 2 | 1 | 2 | MEDIUM |
| F28 | `workshop.py` | `load_workshop_session()` | 629-631 | 2 | 1 | 2 | MEDIUM |
| F29 | `workshop.py` | `update_workshop_session()` | 665-667 | 2 | 1 | 2 | MEDIUM |
| F30 | `workshop.py` | `delete_workshop_session()` | 724-726 | 2 | 1 | 2 | MEDIUM |
| F31 | `settings.py` | `update_settings()` | 80-81 | 2 | 1 | 2 | MEDIUM |

#### 3.2.2 Inconsistent Pydantic Validation (8 findings)

| # | File | Line | Pattern | Issue | Blast | Cmplx | Risk | Sev |
|---|------|------|---------|-------|-------|-------|------|-----|
| F32 | `workshop.py` | 208-215 | `error_count()` | Pattern A -- inconsistent format | 2 | 1 | 1 | LOW |
| F33 | `workshop.py` | 276-283 | `error_count()` | Pattern A | 2 | 1 | 1 | LOW |
| F34 | `snapshots.py` | 62-69 | `error_count()` | Pattern A | 2 | 1 | 1 | LOW |
| F35 | `playlist_pairs.py` | 63-66 | `errors()[0]["msg"]` | Pattern B -- different format | 2 | 1 | 1 | LOW |
| F36 | `raid_panel.py` | 68-70 | `errors()[0]["msg"]` | Pattern B | 2 | 1 | 1 | LOW |
| F37 | `settings.py` | 132-137 | `errors()[0]["msg"]` | Pattern B | 2 | 1 | 1 | LOW |
| F38 | `workshop.py` | 490-492 | Bare Exception | Pattern C -- catches Exception, not ValidationError | 2 | 1 | 2 | MEDIUM |
| F39 | `schedules.py` | 118 | No error handling | Pattern D -- no try/except at all | 2 | 1 | 3 | MEDIUM |

### 3.3 Job Executor Monolith (1 finding)

| # | File | Lines | Issue | Blast | Cmplx | Risk | Sev |
|---|------|-------|-------|-------|-------|------|-----|
| F40 | `job_executor_service.py` | 969 total | Single 969-line file handling shuffle, raid, and rotate execution with 4 bare commits (F10-F13) | 2 | 3 | 2 | HIGH |

**Proposed split:** 4 modules under `shuffify/services/executors/` (base, shuffle, raid, rotate) with backward-compatible re-export from `__init__.py`.

### 3.4 Spotify API Error Handling (1 finding)

| # | File | Lines | Issue | Blast | Cmplx | Risk | Sev |
|---|------|-------|-------|-------|-------|------|-----|
| F41 | `spotify/api.py` | 41-233 | ~200 lines of error-handling code (6 functions + 3 constants) mixed with API data methods; should be extracted to `spotify/error_handling.py` | 1 | 1 | 1 | LOW |

### 3.5 Template Decomposition (1 finding, multiple sub-items)

| # | Scope | Issue | Blast | Cmplx | Risk | Sev |
|---|-------|-------|-------|-------|------|-----|
| F42 | 6 templates, 5,568 lines | Zero macros or includes; 25+ repeated glass-card patterns, 27 repeated form fields, 18 repeated state displays, 3x duplicated `showNotification()` JS, 2,300+ lines of inline JS in workshop.html | 3 | 3 | 2 | HIGH |

**Sub-items:**
- Glass card pattern: 25+ occurrences across all templates
- Form field patterns: 27 occurrences (select, input, toggle)
- State display patterns: 18 occurrences (loading, empty, error)
- Modal dialog pattern: 3 occurrences (2 workshop, 1 schedules)
- `showNotification()` JS: identical in 3 templates (48 lines total)
- Workshop inline JS: 2,300+ lines that should be in `.js` files

### 3.6 Service & Code Cleanup (5 findings)

| # | File | Line(s) | Issue | Blast | Cmplx | Risk | Sev |
|---|------|---------|-------|-------|-------|------|-----|
| F43 | `scheduler_service.py` | 64-74 | `get_schedule()` manually queries + checks ownership instead of using `get_owned_entity()` helper | 1 | 1 | 1 | LOW |
| F44 | `scheduler_service.py` | 212, 274 | Duplicate local import of `JobExecution` -- should be module-level | 1 | 1 | 1 | LOW |
| F45 | `routes/core.py` | 224 | Unused variable `client` from `AuthService.authenticate_and_get_user()` | 1 | 1 | 1 | LOW |
| F46 | `shuffle_algorithms/artist_spacing.py` | 118-119 | Unused variable `tiebreaker` from heap extraction | 1 | 1 | 1 | LOW |
| F47 | `tests/` (root) | 7 files | Route test files in `tests/` root instead of `tests/routes/` subdirectory | 1 | 1 | 1 | LOW |

### 3.7 Sentry SDK Version (1 finding)

| # | File | Line | Issue | Blast | Cmplx | Risk | Sev |
|---|------|------|-------|-------|-------|------|-----|
| F48 | `requirements/prod.txt` | 4 | `sentry-sdk==1.45.1` is 2 major versions behind (latest: 2.53.0); v2.x has breaking API changes | 2 | 2 | 2 | MEDIUM |

---

## 4. Phase Summary & Prioritized Remediation Order

Phases are ordered by a combination of: (a) dependency position (foundations first), (b) risk-to-effort ratio, and (c) blast radius.

| Phase | Slug | Title | Findings | Risk | Effort | Priority |
|-------|------|-------|----------|------|--------|----------|
| 01 | `standardize-db-commits` | Standardize DB Commit Handling | F01-F15 | Low | Medium | 1 (foundation) |
| 02 | `route-auth-validation` | Route Auth & Validation Helpers | F16-F39 | Medium | Medium | 1 (foundation) |
| 03 | `split-job-executor` | Split Job Executor Service | F40 | Medium | High | 2 (depends on 01) |
| 04 | `spotify-error-extraction` | Spotify API Error Extraction | F41 | Low | Low | 1 (independent) |
| 05 | `template-decomposition` | Template Decomposition & Macros | F42 | Low | High | 1 (independent) |
| 06 | `service-code-cleanup` | Service & Code Cleanup | F43-F47 | Low | Low | 3 (depends on 01+03) |
| 07 | `sentry-sdk-update` | Sentry SDK Major Version Update | F48 | Medium | Low | 1 (independent) |

### Recommended Execution Order

```
Week 1-2:  Phase 01, 02, 04, 07 (all independent, run in parallel)
Week 2-3:  Phase 05 (independent but high effort, can overlap with above)
Week 3-4:  Phase 03 (depends on Phase 01 completion)
Week 4:    Phase 06 (depends on Phases 01 + 03 completion)
```

---

## 5. Dependency Matrix

```
Phase 01 ──────────────────────┐
(standardize-db-commits)       │
    │                          │
    ├──► Phase 03              │
    │   (split-job-executor)   │
    │       │                  │
    │       ├──► Phase 06 ◄────┘
    │       │   (service-code-cleanup)
    │       │
    └───────┘

Phase 02 ──────── (independent)
(route-auth-validation)

Phase 04 ──────── (independent)
(spotify-error-extraction)

Phase 05 ──────── (independent)
(template-decomposition)

Phase 07 ──────── (independent)
(sentry-sdk-update)
```

### Dependency Details

| Phase | Depends On | Reason | Unlocks |
|-------|-----------|--------|---------|
| 01 | None | Foundation: establishes safe_commit as the universal pattern | 03, 06 |
| 02 | None | Foundation: establishes auth decorator and validation helper as universal patterns | None |
| 03 | Phase 01 | Job executor split must use safe_commit in all new modules; bare commits (F10-F13) fixed in Phase 01 would be moved during split | 06 |
| 04 | None | Self-contained extraction within `spotify/` package | None |
| 05 | None | Template-only changes, no Python service dependencies | None |
| 06 | Phases 01, 03 | Cleanup items (F43-F44) touch `scheduler_service.py` which is modified in Phase 01; test reorganization (F47) should happen after Phase 03 test updates | None |
| 07 | None | Dependency update with isolated test surface | None |

---

## 6. Parallel Execution Groups

Phases are grouped by dependency constraints. All phases within a group can safely execute in parallel (they touch disjoint file sets).

### Group A: Independent Foundations (Phases 01, 02, 04, 05, 07)

| Phase | Primary Files Modified | No Overlap Verified |
|-------|----------------------|-------------------|
| 01 | `user_service.py`, `activity_log_service.py`, `login_history_service.py`, `playlist_pair_service.py`, `scheduler_service.py`, `job_executor_service.py`, `playlist_snapshot_service.py`, `routes/core.py` | Yes -- no overlap with 02, 04, 05, 07 |
| 02 | `routes/playlists.py`, `routes/shuffle.py`, `routes/workshop.py`, `routes/settings.py`, `routes/snapshots.py`, `routes/schedules.py`, `routes/playlist_pairs.py`, `routes/raid_panel.py`, `routes/__init__.py` | Yes -- overlaps with 01 only on `routes/core.py` but different lines |
| 04 | `spotify/api.py`, `spotify/error_handling.py` (new) | Yes -- no overlap |
| 05 | `templates/**`, `static/js/**`, `static/css/**` | Yes -- no overlap |
| 07 | `requirements/prod.txt`, Sentry initialization code | Yes -- no overlap |

**Overlap note (01 vs 02):** Phase 01 modifies `routes/core.py:265` (bare commit in OAuth callback). Phase 02 does NOT modify `routes/core.py` (the OAuth callback is not an auth-decorated route). No conflict.

### Group B: Depends on Phase 01 (Phase 03)

| Phase | Primary Files Modified |
|-------|----------------------|
| 03 | `services/job_executor_service.py` (split into `services/executors/`), `tests/services/test_job_executor_service.py`, `tests/services/test_job_executor_rotate.py` |

**Must wait for:** Phase 01 to finish modifying bare commits in `job_executor_service.py` (F10-F13). The split in Phase 03 will reorganize those already-fixed commit calls into the new module structure.

### Group C: Depends on Phases 01 + 03 (Phase 06)

| Phase | Primary Files Modified |
|-------|----------------------|
| 06 | `scheduler_service.py` (ownership helper, duplicate imports), `routes/core.py` (unused variable), `shuffle_algorithms/artist_spacing.py` (unused variable), `tests/` (file moves) |

**Must wait for:**
- Phase 01: modifies `scheduler_service.py` commit patterns (lines 123, 181, 219, 250)
- Phase 03: modifies test files that Phase 06 will reorganize

---

## 7. Risk Assessment

### Overall Risk: LOW-MEDIUM

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Regression from auth decorator migration (Phase 02) | Medium | Medium | Each route has existing tests; run full suite after each route conversion |
| Behavior change in job executor split (Phase 03) | Low | High | Public API (`execute`, `execute_now`) unchanged; only 2 external callers to verify |
| Template rendering breakage (Phase 05) | Medium | Medium | Visual regression testing; incremental rollout (macros first, then includes) |
| Sentry v2.x breaking changes (Phase 07) | Medium | Low | Sentry has migration guide; changes are primarily import path and config API |
| Merge conflicts between parallel phases | Low | Low | File sets are disjoint by design; verified in Section 6 |
| `activity_log_service.py` commit behavior change (Phase 01, F02) | Low | Medium | Current behavior returns None on failure (non-blocking); safe_commit re-raises by default. Must preserve non-blocking semantics with try/except wrapper around safe_commit. |

### Phase-Level Risk Summary

| Phase | Risk Level | Key Concern |
|-------|-----------|-------------|
| 01 | Low | Mechanical replacements; F02 needs special handling for non-blocking semantics |
| 02 | Medium | 16 route signature changes; must verify all route tests pass |
| 03 | Medium | Architectural split of 969-line file; must preserve public API exactly |
| 04 | Low | Pure extraction, no behavior change; decorator import path stays the same for consumers |
| 05 | Low | Template-only; no Python logic changes; visual regression is the main concern |
| 06 | Low | Small mechanical fixes; test file moves are safe (no import changes needed) |
| 07 | Medium | Major version bump; breaking changes in Sentry SDK v2.x API |

---

## 8. Files Affected by Phase

### Phase 01: Standardize DB Commit Handling
**Files modified: 7 | Files created: 0**
| File | Changes |
|------|---------|
| `shuffify/services/user_service.py` | Replace manual try/except with safe_commit (line 137) |
| `shuffify/services/activity_log_service.py` | Replace manual try/except with safe_commit + non-blocking wrapper (line 61) |
| `shuffify/services/login_history_service.py` | Replace manual try/except with safe_commit (line 137) |
| `shuffify/services/playlist_pair_service.py` | Add safe_commit to bare commits (lines 59, 98) |
| `shuffify/services/scheduler_service.py` | Add safe_commit to bare commits (lines 123, 181, 219) and manual try/except (line 250) |
| `shuffify/services/job_executor_service.py` | Add safe_commit to bare commits (lines 111, 134, 209, 323) |
| `shuffify/services/playlist_snapshot_service.py` | Replace bare commit with existing safe_commit import (line 248) |

### Phase 02: Route Auth & Validation Helpers
**Files modified: 5-6 | Files created: 0**
| File | Changes |
|------|---------|
| `shuffify/routes/__init__.py` | Add `validate_json()` helper function |
| `shuffify/routes/playlists.py` | Convert 4 routes to `@require_auth_and_db` |
| `shuffify/routes/shuffle.py` | Convert 2 routes to `@require_auth_and_db` |
| `shuffify/routes/workshop.py` | Convert 9 routes to `@require_auth_and_db`; standardize 3 validation blocks |
| `shuffify/routes/settings.py` | Convert 1 route to `@require_auth_and_db`; standardize 1 validation block |
| `shuffify/routes/schedules.py` | Add validation error handling (line 118) |

### Phase 03: Split Job Executor Service
**Files modified: 3 | Files created: 4**
| File | Changes |
|------|---------|
| `shuffify/services/executors/__init__.py` | NEW: Re-export `JobExecutorService` for backward compatibility |
| `shuffify/services/executors/base_executor.py` | NEW: Core execution logic (~250 lines) |
| `shuffify/services/executors/shuffle_executor.py` | NEW: Shuffle execution (~120 lines) |
| `shuffify/services/executors/raid_executor.py` | NEW: Raid execution (~150 lines) |
| `shuffify/services/executors/rotate_executor.py` | NEW: Rotation execution (~320 lines) |
| `shuffify/services/job_executor_service.py` | Replaced with thin re-export or deleted |
| `tests/services/test_job_executor_service.py` | Update import paths |
| `tests/services/test_job_executor_rotate.py` | Update import paths |

### Phase 04: Spotify API Error Extraction
**Files modified: 1 | Files created: 1**
| File | Changes |
|------|---------|
| `shuffify/spotify/error_handling.py` | NEW: 6 functions + 3 constants (~200 lines) extracted from api.py |
| `shuffify/spotify/api.py` | Remove error-handling code; add `from .error_handling import api_error_handler` |

### Phase 05: Template Decomposition & Macros
**Files modified: 6 | Files created: 5-10**
| File | Changes |
|------|---------|
| `shuffify/templates/macros/cards.html` | NEW: `glass_card`, `feature_card`, `step_card`, `schedule_card`, `stat_card` |
| `shuffify/templates/macros/forms.html` | NEW: `select_field`, `input_field`, `number_field`, `toggle_field`, `checkbox_group`, `form_section` |
| `shuffify/templates/macros/states.html` | NEW: `state_loading`, `state_empty`, `state_error` |
| `shuffify/templates/macros/modals.html` | NEW: `modal_confirmation`, `modal_base` |
| `shuffify/templates/settings.html` | Refactor to use macros (243 -> ~100 lines) |
| `shuffify/templates/dashboard.html` | Refactor to use macros (623 -> ~450 lines) |
| `shuffify/templates/schedules.html` | Refactor to use macros (465 -> ~250 lines) |
| `shuffify/templates/workshop.html` | Refactor to use macros and includes (3,102 -> ~1,800 lines) |
| `shuffify/templates/index.html` | Refactor to use macros (877 lines, minor reduction) |
| `shuffify/templates/base.html` | Extract CSS to static file (258 -> ~180 lines) |
| `shuffify/static/js/notifications.js` | NEW: Shared `showNotification()` function |
| `shuffify/static/css/base.css` | NEW: Extracted styles from base.html |

### Phase 06: Service & Code Cleanup
**Files modified: 3-4 | Files moved: 7**
| File | Changes |
|------|---------|
| `shuffify/services/scheduler_service.py` | Use `get_owned_entity()` (lines 64-74); move duplicate import to module level (lines 212, 274) |
| `shuffify/routes/core.py` | Fix unused variable (line 224): `client` -> `_` |
| `shuffify/shuffle_algorithms/artist_spacing.py` | Fix unused variable (lines 118-119): `tiebreaker` -> `_` |
| `tests/test_settings_route.py` | Move to `tests/routes/` |
| `tests/test_snapshot_routes.py` | Move to `tests/routes/` |
| `tests/test_raid_panel_routes.py` | Move to `tests/routes/` |
| `tests/test_workshop.py` | Move to `tests/routes/` |
| `tests/test_workshop_external.py` | Move to `tests/routes/` |
| `tests/test_workshop_merge.py` | Move to `tests/routes/` |
| `tests/test_workshop_search.py` | Move to `tests/routes/` |

### Phase 07: Sentry SDK Major Version Update
**Files modified: 2-3 | Files created: 0**
| File | Changes |
|------|---------|
| `requirements/prod.txt` | Update `sentry-sdk==1.45.1` to `sentry-sdk>=2.53.0,<3.0` |
| `shuffify/__init__.py` | Update Sentry initialization if v2.x API differs |
| `config.py` | Update Sentry configuration if v2.x config format changed |

---

*This document is the authoritative inventory for the codebase-cleanup session. Individual phase documents (01-07) contain detailed implementation plans with exact code changes, test plans, and verification checklists.*
