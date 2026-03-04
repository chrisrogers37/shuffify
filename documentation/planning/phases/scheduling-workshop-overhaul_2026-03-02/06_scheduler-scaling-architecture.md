# Phase 06: Scheduler Scaling Architecture

**Status**: ✅ COMPLETE
**Started**: 2026-03-03
**Completed**: 2026-03-03

## Problem Statement

Shuffify's scheduler uses APScheduler's `BackgroundScheduler` with `ThreadPoolExecutor(max_workers=3)`. All users share 3 threads. Each job makes 3-8 Spotify API calls (I/O-bound, 3-15 seconds per job). At 50+ active schedules with overlapping windows, jobs queue and delay. The Gunicorn `--preload` flag for single-instance guarantee is fragile and undocumented.

## Current Architecture

- Single-process APScheduler with 3 worker threads
- SQLAlchemy jobstore sharing application DB connection pool
- Werkzeug reloader guard for development
- `--preload` for production Gunicorn (fragile)
- `coalesce=True`, `max_instances=1`, `misfire_grace_time=3600`

## Options Analysis

| Option | Description | Effort | Verdict |
|--------|------------|--------|---------|
| A: More threads | Increase to 10-15 workers | 1 hour | Stopgap only |
| B: ProcessPoolExecutor | True parallelism | 8-12 hours | Poor fit (I/O-bound jobs, pickle issues) |
| C: Celery + Redis | Industry-standard queue | 20-30 hours | Premature for current scale |
| D: Redis Queue (RQ) | Simple job queue | 12-16 hours | Good middle-ground |
| **E: Enhanced APScheduler** | **Configurable pool + DB lock + metrics** | **8-12 hours** | **Best fit now** |

## Recommended Approach: Option E

Evolutionary enhancement of APScheduler:
1. Configurable thread pool (default 10)
2. Separate jobstore DB engine with explicit pool_size=2
3. PostgreSQL advisory lock for single-instance guarantee
4. Scheduler health metrics (basic counters + scheduler_running flag)
5. Stale job cleanup

> **Note**: HTTP request timeout (Step 4 in original plan) removed — `http_client.py` already hardcodes `timeout=30` at the request level, which is the correct default.

## Implementation Plan

### Step 1: Configurable thread pool size

**Files**: `config.py`, `shuffify/scheduler.py`

Add `SCHEDULER_THREAD_POOL_SIZE = 10` config. Read in `init_scheduler()`.

### Step 2: Separate jobstore database engine

**File**: `shuffify/scheduler.py`

Create dedicated `create_engine()` for jobstore with `pool_size=2`, preventing connection contention with the application's main connection pool.

### Step 3: Database advisory lock

**File**: `shuffify/scheduler.py`

Add `_try_acquire_scheduler_lock(db_url)` using `pg_try_advisory_lock`. Fail-open for safety. SQLite bypass (single-process by nature).

### Step 4: Scheduler health metrics

**Files**: `shuffify/scheduler.py`, `shuffify/routes/core.py`

Track `jobs_executed`, `jobs_failed`, `jobs_missed`, `last_execution_at` as module-level counters (incremented in existing event listeners). Add `scheduler_running` boolean. Expose via `/health` endpoint under a `scheduler` key.

### Step 5: Stale job cleanup

**Files**: `shuffify/services/executors/base_executor.py`, `shuffify/scheduler.py`

Add `cleanup_stale_executions(max_age_minutes=30)` to mark stuck "running" records as failed on startup.

## Migration Strategy

All changes are config-driven with backward-compatible defaults. No new processes. No Docker changes. Rollback: set `SCHEDULER_THREAD_POOL_SIZE=3` to restore original behavior.

## Future Scaling Path

When Shuffify reaches 200+ concurrent schedules, migrate to RQ:
1. Add `rq` dependency
2. Change `_execute_scheduled_job` to `queue.enqueue(JobExecutorService.execute, schedule_id)`
3. Run `rq worker` as separate Docker service
4. APScheduler remains for scheduling; entire executor pipeline untouched

## Test Plan

~11 new tests covering: configurable pool, advisory lock (acquire + fail-open + SQLite bypass), separate engine, metrics counters, scheduler_running in /health, stale cleanup.

## Files Modified

| File | Change |
|------|--------|
| `config.py` | Add `SCHEDULER_THREAD_POOL_SIZE` config var |
| `shuffify/scheduler.py` | Configurable pool, separate engine, advisory lock, metrics counters |
| `shuffify/services/executors/base_executor.py` | Add stale cleanup |
| `shuffify/routes/core.py` | Include scheduler metrics in `/health` |
| `tests/test_scheduler.py` | ~8 new tests |
| `tests/test_health_db.py` or `tests/routes/test_core_routes.py` | ~3 new tests |

## Risk Assessment

Low. All changes are additive with safe defaults. Advisory lock is fail-open. Increased thread count is well within I/O-bound job capacity. HTTP timeout is per-request (not per-job), bounded by retry logic.
