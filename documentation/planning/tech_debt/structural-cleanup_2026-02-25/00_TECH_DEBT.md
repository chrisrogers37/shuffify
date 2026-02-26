# Structural Cleanup — Tech Debt Remediation

**Session**: structural-cleanup
**Date**: 2026-02-25
**Scope**: Full codebase scan — code structure, duplication, complexity, test gaps
**Status**: 📋 PENDING

---

## Executive Summary

The Shuffify codebase is in good health overall (1296 tests, zero TODO/FIXME markers, clean service layer abstractions). The primary tech debt is **structural**: overly large files, embedded JavaScript in templates, duplicated test fixtures, and a few monster functions. None of these are bugs or security issues — they are maintainability concerns that increase cognitive load and slow future development.

---

## Inventory

### High Priority

| # | Finding | Location | Blast Radius | Complexity | Risk |
|---|---------|----------|-------------|------------|------|
| H1 | Embedded JS in templates (5,200+ lines across 3 files) | `templates/workshop.html`, `dashboard.html`, `index.html` | High | Medium | Low |
| H2 | `register_error_handlers` — 373-line single function | `error_handlers.py` | Medium | Low | Low |
| H3 | OAuth `callback` — 147 lines, 7+ nesting levels | `routes/core.py` | Medium | Medium | Medium |

### Medium Priority

| # | Finding | Location | Blast Radius | Complexity | Risk |
|---|---------|----------|-------------|------------|------|
| M1 | Test fixture duplication (~580 lines across 13+ files) | `tests/routes/test_*_routes.py` | Low | Low | Low |
| M2 | `models/db.py` — 1,027 lines (11 models in one file) | `models/db.py` | Medium | Medium | Medium |
| M3 | Unused ActivityType enum values (6 defined, never logged) | `enums.py`, services, routes | Low | Low | Low |
| M4 | `create_app` factory — 225 lines | `__init__.py` | Medium | Low | Low |

### Low Priority

| # | Finding | Location | Blast Radius | Complexity | Risk |
|---|---------|----------|-------------|------------|------|
| L1 | Missing tests for `spotify/error_handling.py` and `exceptions.py` | `tests/spotify/` | Low | Low | Low |
| L2 | Large route files (workshop 650, schedules 443, core 396) | `routes/` | Low | Medium | Low |
| L3 | 49 outdated dependencies (patch-level) | `requirements/` | Low | Low | Low |

---

## Severity Scoring

| Finding | Blast Radius (1-5) | Complexity (1-5) | Risk (1-5) | Total | Priority |
|---------|-------------------|-------------------|------------|-------|----------|
| H1 Embedded JS | 5 | 3 | 2 | 10 | 1 |
| M1 Test fixtures | 2 | 1 | 1 | 4 | 2 |
| H2 Error handlers | 3 | 2 | 1 | 6 | 3 |
| M4 create_app | 3 | 2 | 1 | 6 | 3 |
| H3 OAuth callback | 3 | 3 | 3 | 9 | 4 |
| M2 db.py split | 3 | 3 | 3 | 9 | 5 |
| M3 Unused enums | 1 | 1 | 1 | 3 | 6 |
| L1 Missing tests | 1 | 1 | 1 | 3 | 7 |

---

## Remediation Phases

Each phase = 1 PR. Ordered by impact-to-effort ratio, dependencies first.

| Phase | Title | Depends On | Blocks | Effort | Risk |
|-------|-------|-----------|--------|--------|------|
| 01 | Consolidate test fixtures into conftest.py | — | — | Low | Low |
| 02 | Decompose monster functions (error handlers, create_app) | — | — | Low | Low |
| 03 | Extract callback & core route helpers | 02 | — | Medium | Medium |
| 04 | Extract template JavaScript to static files | — | — | High | Low |
| 05 | Wire up unused ActivityType enums | — | — | Low | Low |
| 06 | Add missing Spotify module tests | — | — | Low | Low |

**Not planned** (deferred):
- **db.py split**: High risk (touches every import site), moderate reward. Defer until model count exceeds 15.
- **Route file splits**: Not urgent at current sizes. Revisit when any route file exceeds 800 lines.
- **Dependency updates**: Routine maintenance, handle via Dependabot or periodic batch update.

---

## Dependency Matrix

```
Phase 01 ──── (independent)
Phase 02 ──── blocks Phase 03
Phase 03 ──── blocked by Phase 02
Phase 04 ──── (independent)
Phase 05 ──── (independent)
Phase 06 ──── (independent)
```

Phases 01, 02, 04, 05, 06 can all run in parallel. Phase 03 must wait for Phase 02.

---

## Phase Documents

- [01 — Consolidate Test Fixtures](01_consolidate-test-fixtures.md) `✅ COMPLETE` PR #105
- [02 — Decompose Monster Functions](02_decompose-monster-functions.md) `📋 PENDING`
- [03 — Extract Core Route Helpers](03_extract-core-route-helpers.md) `📋 PENDING`
- [04 — Extract Template JavaScript](04_extract-template-javascript.md) `📋 PENDING`
- [05 — Wire Up Activity Logging](05_wire-up-activity-logging.md) `📋 PENDING`
- [06 — Add Spotify Module Tests](06_add-spotify-module-tests.md) `📋 PENDING`
