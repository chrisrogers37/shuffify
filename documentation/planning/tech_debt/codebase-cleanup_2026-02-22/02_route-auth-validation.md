# Phase 02: Route Auth & Validation Helpers
**Status:** COMPLETE
**Started:** 2026-02-23
**Completed:** 2026-02-23
**PR:** #TBD

## Header

| Field | Value |
|-------|-------|
| **PR Title** | Refactor: Standardize route auth with decorator and add validate_json helper |
| **Risk Level** | Low |
| **Effort** | Low-Medium (2-3 hours) |
| **Files Modified** | 7 route files + 1 new test file |
| **Files Created** | 1 (tests/test_validate_json.py) |
| **Files Deleted** | 0 |
| **Dependencies** | None |

---

## Context

The codebase has two inconsistency patterns in route files:

### Part A: JSON Validation
11 routes across 6 files use Pydantic validation with 4 different error handling patterns:
- Pattern A: `e.error_count()` format (workshop.py, snapshots.py)
- Pattern B: `e.errors()[0]["msg"]` format (playlist_pairs.py, raid_panel.py, settings.py)
- Pattern C: Bare `Exception` catch (workshop.py)
- Pattern D: No error handling (schedules.py)

A `validate_json()` helper standardizes all to: `"Validation error: {message}"`.

### Part B: Auth Decorator Migration
16 routes across 4 files use manual `require_auth()` + None check instead of the existing `@require_auth_and_db` decorator. Migration eliminates boilerplate and ensures consistent 401/503 error responses.

---

## Detailed Implementation Plan

### Part A: validate_json() helper

Add helper to `shuffify/routes/__init__.py` and update 6 route files.

### Part B: Auth decorator migration

Migrate 16 routes from manual auth pattern to `@require_auth_and_db`:
- playlists.py: 4 routes
- shuffle.py: 2 routes
- workshop.py: 9 routes
- settings.py: 1 route

---

## Test Plan

New test file: `tests/test_validate_json.py` covering:
- Valid input returns parsed model
- Missing JSON body returns 400
- Pydantic validation error returns 400 with message
- Extra fields ignored (Pydantic default)

All existing tests must pass unchanged.
