# Phase 01: Fix Schedule Creation Error-But-Success Bug

**Status**: ✅ COMPLETE
**Started**: 2026-03-03
**Completed**: 2026-03-03

## Problem Statement

When a user creates a schedule, the schedule is successfully persisted to the database, but the user sees "An unexpected error occurred" (a generic 500 error). This happens because APScheduler's jobstore throws exception types other than `RuntimeError` when registering the job, and the route handler only catches `RuntimeError`. The unhandled exception propagates to the global 500 error handler, which returns a generic error message.

**User-visible symptoms**:
- User fills out the schedule creation form and submits it
- The UI shows an error toast: "An unexpected error occurred"
- The schedule IS actually created in the database
- On page reload, the user sees the schedule listed
- Confused users retry, creating duplicate schedules

## Root Cause Analysis

The `create_schedule` route in `schedules.py` follows this flow:

1. **Line 140-144**: Request JSON validated via Pydantic (`ScheduleCreateRequest`)
2. **Line 146-162**: `SchedulerService.create_schedule()` is called — this commits to DB via `safe_commit()`
3. **Line 164-177**: `add_job_for_schedule()` wrapped in `try/except RuntimeError`
4. **Lines 179-204**: Logging, activity logging, and `json_success()` response

The `add_job_for_schedule` function (`scheduler.py:183-222`) calls `_scheduler.add_job(...)` which uses the `SQLAlchemyJobStore`. The jobstore can throw:
- `ConflictingIdError` (subclass of `KeyError`)
- `sqlalchemy.exc.OperationalError` (connection issues)
- `pickle.PicklingError` (serialization failure)
- `TypeError` (trigger misconfiguration)

None of these are `RuntimeError`. They escape the catch block and hit `handle_internal_error` at `error_handlers.py:383-404`, returning "An unexpected error occurred." The schedule was already committed at step 2.

The same pattern exists in `update_schedule` (line 246) and `toggle_schedule` (line 331).

## Implementation Plan

### Step 1: Broaden except clause in `create_schedule`

**File**: `shuffify/routes/schedules.py`

Replace `except RuntimeError` with `except Exception` at line 173. Improve the warning log to include exception type for debugging.

```python
# Before (lines 164-177)
    try:
        from shuffify.scheduler import add_job_for_schedule
        add_job_for_schedule(schedule, current_app._get_current_object())
    except RuntimeError as e:
        logger.warning(f"Could not register schedule with APScheduler: {e}")

# After
    try:
        from shuffify.scheduler import add_job_for_schedule
        add_job_for_schedule(schedule, current_app._get_current_object())
    except Exception as e:
        logger.warning(
            "Could not register schedule %d with APScheduler: %s [type=%s]",
            schedule.id, e, type(e).__name__,
        )
```

**Rationale**: The schedule is already committed. APScheduler job registration is non-critical — the schedule will be picked up by `_register_existing_jobs` on next scheduler restart.

### Step 2: Apply same fix to `update_schedule`

**File**: `shuffify/routes/schedules.py`

Replace `except RuntimeError` with `except Exception` at line 246 in the update route.

### Step 3: Apply same fix to `toggle_schedule`

**File**: `shuffify/routes/schedules.py`

Replace `except RuntimeError` with `except Exception` at line 331 in the toggle route.

### ~~Step 4: Add explicit error handling around `create_schedule` service call~~ DROPPED

**Dropped during challenge round**: Phase 02 removes `ScheduleLimitError` entirely. Global error handlers already catch `ScheduleLimitError` (400) and `ScheduleError` (500), so duplicating in the route is unnecessary and would create a merge conflict with Phase 02.

## Test Plan

Add to `tests/routes/test_schedules_routes.py`:

1. **APScheduler non-RuntimeError does not mask success** — mock `add_job_for_schedule` to raise `KeyError`, verify 200 response
2. **APScheduler RuntimeError still handled** — mock RuntimeError, verify 200 response (regression)
3. **Update succeeds despite APScheduler error** — same pattern for update route
4. **Toggle succeeds despite APScheduler error** — same pattern for toggle route
5. **Warning logged on APScheduler failure** — verify logger.warning called

## Files Modified

| File | Change |
|------|--------|
| `shuffify/routes/schedules.py` | Broaden `except RuntimeError` to `except Exception` in 3 places |
| `tests/routes/test_schedules_routes.py` | Add 7 new tests |

## Risk Assessment

- **Catching too broadly**: Scoped only to APScheduler job registration (non-critical step). Warning log captures full exception info for debugging.
- **APScheduler job not registered**: Same as current RuntimeError behavior — `_register_existing_jobs` picks it up on restart.
- **Breaking existing tests**: Strictly broadening an except clause. RuntimeError is a subclass of Exception, so existing paths unchanged.
