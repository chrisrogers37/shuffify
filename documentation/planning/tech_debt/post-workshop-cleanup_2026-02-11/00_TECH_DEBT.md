# Tech Debt: Post-Workshop Cleanup

**Session:** post-workshop-cleanup
**Date:** 2026-02-11
**Scope:** Technical debt accumulated during the Playlist Workshop Enhancement Suite (Phases 1-6)
**Codebase State:** 690 tests passing, flake8 clean, all 6 phases merged

---

## Executive Summary

The Playlist Workshop Enhancement Suite rapidly added 6 phases of functionality (track management, playlist merging, external raiding, user database, scheduled operations). While all features work correctly and have test coverage, the rapid development left behind several categories of debt:

1. A monolithic 1509-line `routes.py` that should be split into Blueprints
2. Missing test coverage for 3 modules (schedule schemas, scheduler integration, algorithm utils)
3. Scattered string literals for job types and schedule values that should be enums
4. Housekeeping items (dependency updates, missing `.env.example`)

---

## Inventory

| # | Item | Severity | Blast Radius | Files Affected | Phase |
|---|------|----------|-------------|----------------|-------|
| 1 | Missing tests: schedule_requests.py | High | Narrow | tests/ only | 01 |
| 2 | Missing tests: scheduler.py | High | Narrow | tests/ only | 01 |
| 3 | Missing tests: shuffle_algorithms/utils.py | Medium | Narrow | tests/ only | 01 |
| 4 | Hardcoded job type strings ("shuffle", "raid") | Medium | Wide (6 files) | schemas/, services/, models/, scheduler.py | 02 |
| 5 | Hardcoded schedule value strings ("daily", "weekly") | Medium | Wide (4 files) | schemas/, models/, scheduler.py | 02 |
| 6 | routes.py is 1509 lines / 35 routes | Medium | Wide | routes.py, __init__.py, new route files | 03 |
| 7 | Outdated dependencies | Low | System-wide | requirements/ | 04 |
| 8 | No .env.example file | Low | Narrow | root | 04 |

---

## Severity Scoring

### Blast Radius
- **Narrow**: Failure affects one module, caught by tests
- **Medium**: Failure affects a feature area (e.g., scheduling)
- **Wide**: Failure affects multiple features or the entire app

### Complexity
- **Low**: Mechanical changes, clear patterns
- **Medium**: Requires careful refactoring, multiple files
- **High**: Architectural changes, risk of regression

### Risk
- **Low**: Test-only changes, additive changes
- **Medium**: Refactoring existing code with test safety net
- **High**: Structural changes to core files

---

## Dependency Matrix

```
Phase 01: Add missing tests ──────────────────────┐
  (tests/ only — no code changes)                  │
  CAN RUN IN PARALLEL with Phase 04                │
                                                   │
Phase 02: Extract enums ───────────────────────────┤
  (schemas/, services/, models/, scheduler.py)     │
  MUST COMPLETE BEFORE Phase 03                    │
  CAN RUN IN PARALLEL with Phase 01                │
                                                   │
Phase 03: Split routes into Blueprints ────────────┤
  (routes.py → routes/, __init__.py)               │
  BLOCKED BY Phase 02                              │
                                                   │
Phase 04: Dependency updates + .env.example ───────┘
  (requirements/, root)
  CAN RUN IN PARALLEL with Phase 01
```

### Parallel Execution Safety

| Phase Pair | Safe in Parallel? | Reason |
|------------|-------------------|--------|
| 01 + 02 | YES | Phase 01 touches only tests/, Phase 02 touches only shuffify/ |
| 01 + 04 | YES | Completely disjoint file sets |
| 02 + 04 | YES | Phase 02 touches shuffify/, Phase 04 touches requirements/ and root |
| 02 + 03 | NO | Both modify files in shuffify/services/ and shuffify/models/ |
| 03 + 04 | YES | Phase 03 touches shuffify/routes/, Phase 04 touches requirements/ |

### Recommended Execution Order

```
         ┌── Phase 01 (tests) ──────────────────────┐
START ───┤                                          ├─── Phase 03 (routes) ─── DONE
         └── Phase 02 (enums) ──────────────────────┘
                                                 ↑
         ┌── Phase 04 (deps) ── anytime ─────────┘
```

Phases 01, 02, and 04 can all start immediately (parallel).
Phase 03 must wait for Phase 02 to merge.

---

## Remediation Plans

| Phase | Document | PR Title | Risk | Effort |
|-------|----------|----------|------|--------|
| 01 | [01_add-missing-tests.md](01_add-missing-tests.md) | test: Add coverage for schedule schemas, scheduler, and algorithm utils | Low | ~2 hours | 🔧 IN PROGRESS |
| 02 | [02_extract-enums.md](02_extract-enums.md) | refactor: Extract job type and schedule value string literals to enums | Medium | ~2 hours |
| 03 | [03_split-routes.md](03_split-routes.md) | refactor: Split routes.py into feature-based Blueprint modules | Medium | ~3 hours |
| 04 | [04_housekeeping.md](04_housekeeping.md) | chore: Update dependencies and add .env.example | Low | ~1 hour |

---

## Items NOT Addressed (Deferred)

These items were identified but intentionally deferred:

1. **42 broad `except Exception` catches** — Many are appropriate top-level error boundaries. Narrowing them requires deep domain knowledge of what each Spotify/DB call can raise. Risk of breaking error handling outweighs benefit. Defer until a specific bug is caused by a masked exception.

2. **SpotifyClient facade (375 lines of delegation)** — Works correctly, is well-tested, and provides a clean API. Removing it would require updating all consumers. Not worth the churn now.

3. **`dev_guides/` gitignored files** — Local-only documentation. Moving to `documentation/` is cosmetic and can be done anytime.

---

*Generated by /techdebt scan on 2026-02-11*
