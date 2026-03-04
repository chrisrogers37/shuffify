# Phase 02: Remove Hardcoded Schedule Limit

**Status**: ✅ COMPLETE
**Started**: 2026-03-03
**Completed**: 2026-03-03

## Problem Statement

The scheduler enforces a hardcoded limit of 5 schedules per user (`MAX_SCHEDULES_PER_USER = 5`). This is arbitrarily restrictive — power users with many playlists need more scheduled operations. The limit exists in the service, config, UI, error handlers, and tests.

## Current Implementation

| Location | What |
|----------|------|
| `scheduler_service.py:39` | `MAX_SCHEDULES_PER_USER = 5` class constant |
| `scheduler_service.py:89-100` | Enforcement logic in `create_schedule()` |
| `config.py:76` | `SCHEDULER_MAX_SCHEDULES_PER_USER = 5` (unused) |
| `schedules.py:88-90` | Passes `max_schedules` to template |
| `schedules.html:26` | Displays "X / 5 schedules configured" |
| `schedules.html:32-33` | Disables button at limit |
| `error_handlers.py:260-267` | `handle_schedule_limit()` handler |
| `error_handlers.py:462` | Handler registration |
| `error_handlers.py:34` | Import of `ScheduleLimitError` |
| `services/__init__.py:93,201` | Re-export of `ScheduleLimitError` |
| `raid_panel.py:28-29,98,151` | Import and two catch blocks |
| `test_scheduler_service.py:82-108` | Limit enforcement test |

## Implementation Plan

### Step 1: Remove limit constant and enforcement from SchedulerService

**File**: `shuffify/services/scheduler_service.py`

- Delete `MAX_SCHEDULES_PER_USER = 5` (line 39)
- Delete the enforcement block in `create_schedule()` (lines 89-100)
- Update docstring to remove `ScheduleLimitError` from Raises
- Delete `ScheduleLimitError` class definition entirely (can be re-added trivially if ever needed)

### Step 2: Remove config constant

**File**: `config.py`

Delete `SCHEDULER_MAX_SCHEDULES_PER_USER = 5` (line 76).

### Step 3: Update route to stop passing max_schedules

**File**: `shuffify/routes/schedules.py`

Remove `max_schedules=SchedulerService.MAX_SCHEDULES_PER_USER` from `render_template()` call.

### Step 4: Update template

**File**: `shuffify/templates/schedules.html`

- Change "X / 5 schedules configured" to "X schedule(s) configured" with proper pluralization
- Remove disabled/opacity logic from "New Schedule" button

### Step 5: Remove error handler

**File**: `shuffify/error_handlers.py`

Remove `ScheduleLimitError` import, handler function, and registration entry.

### Step 6: Remove from services exports

**File**: `shuffify/services/__init__.py`

Remove `ScheduleLimitError` from imports and `__all__`.

### Step 7: Remove catch blocks from raid_panel

**File**: `shuffify/routes/raid_panel.py`

Remove `ScheduleLimitError` import and two `except ScheduleLimitError` blocks.

### Step 8: Update tests

**File**: `tests/services/test_scheduler_service.py`

- Remove `ScheduleLimitError` import
- Delete `test_create_schedule_limit_enforced` test
- Add `test_create_many_schedules_succeeds` (create 10 schedules, all succeed)

**File**: `tests/services/test_raid_sync_service.py`

Remove unused `ScheduleLimitError` import.

## Files Modified

| File | Change |
|------|--------|
| `shuffify/services/scheduler_service.py` | Remove constant, enforcement, update docstring |
| `config.py` | Remove config constant |
| `shuffify/routes/schedules.py` | Remove `max_schedules` from template context |
| `shuffify/templates/schedules.html` | Update display, remove disabled logic |
| `shuffify/error_handlers.py` | Remove handler, import, registration |
| `shuffify/services/__init__.py` | Remove re-export |
| `shuffify/routes/raid_panel.py` | Remove import and catch blocks |
| `tests/services/test_scheduler_service.py` | Remove limit test, add no-limit test |
| `tests/services/test_raid_sync_service.py` | Remove unused import |

## Risk Assessment

**Low risk.** This is purely subtractive — removing a restriction. Natural throttle comes from Spotify API rate limits and APScheduler's thread pool capacity.
