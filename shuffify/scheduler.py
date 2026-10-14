"""
APScheduler integration for Shuffify.

Manages the background scheduler lifecycle: initialization,
job registration, and graceful shutdown. Jobs persist in the
database via SQLAlchemyJobStore.

IMPORTANT:
- Must be initialized AFTER Flask app creation (in create_app).
- In development with Werkzeug reloader, only start in main process.
- In production with Gunicorn, use --preload for single scheduler.
- PostgreSQL advisory lock prevents duplicate scheduler instances.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from shuffify.enums import IntervalValue, ScheduleType

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler: Optional[BackgroundScheduler] = None

# Flask app reference (stored at module level to avoid pickling)
_app = None

# Advisory lock connection (kept alive for lock duration)
_lock_connection = None

# Scheduler health metrics
_metrics = {
    "jobs_executed": 0,
    "jobs_failed": 0,
    "jobs_missed": 0,
    "last_execution_at": None,
}


def get_scheduler_metrics() -> dict:
    """Return a copy of current scheduler health metrics."""
    return {
        **_metrics,
        "scheduler_running": (_scheduler is not None and _scheduler.running),
    }


def _on_job_executed(event):
    """Listener for successful job execution."""
    _metrics["jobs_executed"] += 1
    _metrics["last_execution_at"] = datetime.now(timezone.utc).isoformat()
    logger.info(f"Job {event.job_id} executed successfully")


def _on_job_error(event):
    """Listener for failed job execution."""
    _metrics["jobs_failed"] += 1
    _metrics["last_execution_at"] = datetime.now(timezone.utc).isoformat()
    logger.error(
        f"Job {event.job_id} failed with exception: {event.exception}",
        exc_info=event.traceback,
    )


def _on_job_missed(event):
    """Listener for missed job execution."""
    _metrics["jobs_missed"] += 1
    logger.warning(f"Job {event.job_id} missed its scheduled run time")


def _try_acquire_scheduler_lock(db_url: str, fail_open: bool = False) -> bool:
    """
    Attempt to acquire a PostgreSQL advisory lock to guarantee
    only one scheduler instance runs across all processes.

    Uses pg_try_advisory_lock with a fixed lock ID. The lock is
    held for the lifetime of the connection and released
    automatically when the connection closes.

    Args:
        db_url: The database URL.
        fail_open: When True (development), start the scheduler anyway if the
            lock cannot be verified. When False (production, the default), fail
            CLOSED — skip scheduler init on any lock-check error so a transient
            DB blip at boot can't spawn duplicate schedulers that run every
            job N times.

    Returns True if the lock was acquired (or on a non-PostgreSQL DB).
    On a lock-check error, returns ``fail_open``.
    """
    global _lock_connection

    if not db_url.startswith("postgresql"):
        # SQLite is single-process by nature — no lock needed
        return True

    try:
        from sqlalchemy import create_engine, text

        # Fixed lock ID for scheduler (arbitrary but stable)
        lock_id = 88442211

        engine = create_engine(db_url, pool_size=1)
        conn = engine.connect()
        result = conn.execute(text(f"SELECT pg_try_advisory_lock({lock_id})"))
        acquired = result.scalar()

        if acquired:
            # Keep connection alive — lock released on close
            _lock_connection = conn
            logger.info("Acquired scheduler advisory lock")
            return True
        else:
            conn.close()
            engine.dispose()
            logger.info(
                "Another process holds the scheduler lock, skipping scheduler init"
            )
            return False

    except Exception as e:
        if fail_open:
            logger.warning(
                "Could not acquire advisory lock (%s); starting scheduler "
                "anyway (fail-open, development)",
                e,
            )
            return True
        logger.error(
            "Could not acquire scheduler advisory lock (%s); skipping "
            "scheduler init to avoid duplicate schedulers running every job "
            "N times (fail-closed). Runs in development fail open.",
            e,
        )
        return False


def init_scheduler(app) -> Optional[BackgroundScheduler]:
    """
    Initialize and start the APScheduler BackgroundScheduler.

    Must be called from create_app() after all extensions are
    initialized.

    Args:
        app: The Flask application instance.

    Returns:
        The BackgroundScheduler instance, or None if disabled.
    """
    global _scheduler, _app

    if not app.config.get("SCHEDULER_ENABLED", True):
        logger.info("Scheduler disabled by configuration")
        return None

    # In development, Werkzeug reloader spawns two processes.
    # Only start the scheduler in the main process.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        logger.info("Skipping scheduler init in Werkzeug reloader child process")
        return None

    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler already running, skipping re-init")
        return _scheduler

    try:
        db_url = app.config.get("SQLALCHEMY_DATABASE_URI", "sqlite:///shuffify.db")

        # Advisory lock: prevent duplicate schedulers. Fail closed in
        # production (app.debug is False) so a transient DB blip skips init
        # rather than spawning duplicate schedulers; fail open in dev.
        if not _try_acquire_scheduler_lock(db_url, fail_open=app.debug):
            return None

        # Separate jobstore engine with small pool
        from sqlalchemy import create_engine

        jobstore_engine = create_engine(db_url, pool_size=2, pool_pre_ping=True)

        pool_size = app.config.get("SCHEDULER_THREAD_POOL_SIZE", 10)

        # SQLAlchemyJobStore serializes job state with pickle.
        # APScheduler 3.x has no config knob to swap serializers.
        # The risk is low: the pickle boundary is internal (this
        # app writes jobs to its own DB and reads them back), so
        # exploitation requires DB write access — at which point
        # the attacker already owns the application. This is a
        # known limitation; re-evaluate if upgrading to
        # APScheduler 4.x which redesigned serialization.
        jobstores = {
            "default": SQLAlchemyJobStore(engine=jobstore_engine),
        }
        executors = {
            "default": ThreadPoolExecutor(max_workers=pool_size),
        }
        job_defaults = {
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 3600,
        }

        _scheduler = BackgroundScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
        )

        # Add event listeners
        _scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)
        _scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
        _scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)

        _scheduler.start()
        _app = app
        logger.info(
            "APScheduler started successfully (pool_size=%d)",
            pool_size,
        )

        # Register existing enabled schedules from database
        with app.app_context():
            _register_existing_jobs()

        # Clean up stale execution records from prior crashes
        with app.app_context():
            _cleanup_stale_executions()

        return _scheduler

    except Exception as e:
        logger.error(
            f"Failed to initialize scheduler: {e}",
            exc_info=True,
        )
        _scheduler = None
        return None


def _register_existing_jobs():
    """
    Load enabled schedules from the database and register them
    with APScheduler.
    """
    try:
        from shuffify.models.db import Schedule

        enabled_schedules = Schedule.query.filter_by(is_enabled=True).all()
        registered = 0

        for schedule in enabled_schedules:
            try:
                add_job_for_schedule(schedule)
                registered += 1
            except Exception as e:
                logger.error(f"Failed to register job for schedule {schedule.id}: {e}")

        logger.info(
            f"Registered {registered}/"
            f"{len(enabled_schedules)} "
            f"scheduled jobs from database"
        )

    except Exception as e:
        logger.error(f"Failed to load schedules from database: {e}")


def get_scheduler() -> Optional[BackgroundScheduler]:
    """Get the global scheduler instance."""
    return _scheduler


def add_job_for_schedule(schedule):
    """
    Register an APScheduler job for a Schedule model instance.

    Args:
        schedule: A Schedule model instance.

    Raises:
        RuntimeError: If scheduler is not initialized.
    """
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialized")

    job_id = f"schedule_{schedule.id}"

    # Parse schedule_value into APScheduler trigger
    trigger, trigger_kwargs = _parse_schedule(
        schedule.schedule_type, schedule.schedule_value
    )
    if getattr(schedule, "timezone", None):
        trigger_kwargs["timezone"] = schedule.timezone

    existing = _scheduler.get_job(job_id)
    if (
        existing is not None
        and _job_matches(existing, trigger, trigger_kwargs, schedule.id)
        and getattr(existing, "next_run_time", None) is not None
    ):
        return

    _scheduler.add_job(
        func=_execute_scheduled_job,
        trigger=trigger,
        id=job_id,
        args=[schedule.id],
        replace_existing=True,
        **trigger_kwargs,
    )

    logger.info(
        f"Registered job {job_id} with trigger={trigger}, kwargs={trigger_kwargs}"
    )


def _job_matches(job, trigger_type, trigger_kwargs, schedule_id):
    """Compare actual trigger semantics, ignoring an interval's start instant."""
    args = getattr(job, "args", None)
    if not isinstance(args, (list, tuple)) or tuple(args) != (schedule_id,):
        return False
    actual = getattr(job, "trigger", None)
    expected_kwargs = dict(trigger_kwargs)
    if "timezone" not in expected_kwargs and _scheduler is not None:
        expected_kwargs["timezone"] = _scheduler.timezone
    if trigger_type == "interval" and isinstance(actual, IntervalTrigger):
        expected = IntervalTrigger(**expected_kwargs)
        return actual.interval == expected.interval and str(actual.timezone) == str(expected.timezone)
    if trigger_type == "cron" and isinstance(actual, CronTrigger):
        expected = CronTrigger(**expected_kwargs)
        return str(actual.timezone) == str(expected.timezone) and [
            str(field) for field in actual.fields
        ] == [str(field) for field in expected.fields]
    return False


def get_schedule_registration(schedule):
    """Read the actual scheduler job and its next fire time, without mutation."""
    if _scheduler is None or not _scheduler.running:
        return {
            "status": "unavailable", "next_run_at": None,
            "effective_timezone": getattr(schedule, "timezone", None),
        }
    try:
        job = _scheduler.get_job(f"schedule_{schedule.id}")
        zone = getattr(schedule, "timezone", None) or str(_scheduler.timezone)
        if not schedule.is_enabled:
            status = "needs_attention" if job is not None else "paused"
            return {"status": status, "next_run_at": None, "effective_timezone": zone}
        if job is None:
            return {"status": "needs_attention", "next_run_at": None, "effective_timezone": zone}
        trigger_type, kwargs = _parse_schedule(schedule.schedule_type, schedule.schedule_value)
        if getattr(schedule, "timezone", None):
            kwargs["timezone"] = schedule.timezone
        if not _job_matches(job, trigger_type, kwargs, schedule.id):
            return {"status": "needs_attention", "next_run_at": None, "effective_timezone": zone}
        next_run = getattr(job, "next_run_time", None)
        return {
            "status": "registered" if next_run is not None else "needs_attention",
            "next_run_at": next_run.isoformat() if next_run is not None else None,
            "effective_timezone": zone,
        }
    except Exception:
        logger.exception("Could not inspect scheduler registration for %s", schedule.id)
        return {
            "status": "needs_attention", "next_run_at": None,
            "effective_timezone": getattr(schedule, "timezone", None),
        }


def sync_schedule_registration(schedule):
    """Synchronize the committed exact ID, then report the observed job state."""
    from shuffify.models.db import Schedule, db

    schedule_id = schedule.id
    db.session.expire_all()
    current = db.session.get(Schedule, schedule_id)
    if current is None:
        return {"status": "needs_attention", "next_run_at": None, "effective_timezone": None}
    if _scheduler is None or not _scheduler.running:
        return {
            "status": "needs_attention", "next_run_at": None,
            "effective_timezone": current.timezone,
        }
    try:
        if current.is_enabled:
            add_job_for_schedule(current)
        else:
            remove_job_for_schedule(current.id)
    except Exception:
        logger.exception("Could not sync scheduler registration for %s", schedule_id)
        return {
            "status": "needs_attention", "next_run_at": None,
            "effective_timezone": current.timezone,
        }
    return get_schedule_registration(current)


def remove_job_for_schedule(schedule_id: int):
    """
    Remove an APScheduler job for a schedule.

    Args:
        schedule_id: The Schedule model ID.
    """
    if _scheduler is None:
        return

    job_id = f"schedule_{schedule_id}"
    try:
        _scheduler.remove_job(job_id)
        logger.info(f"Removed job {job_id}")
    except Exception:
        logger.debug(f"Job {job_id} not found in scheduler (already removed)")


def _parse_schedule(schedule_type: str, schedule_value: str) -> Tuple[str, Dict]:
    """
    Convert schedule configuration to APScheduler trigger args.

    Args:
        schedule_type: 'interval' or 'cron'.
        schedule_value: The schedule specification.

    Returns:
        Tuple of (trigger_type_string, trigger_kwargs_dict).
    """
    if schedule_type == ScheduleType.INTERVAL:
        interval_map = {
            IntervalValue.EVERY_6H: {"hours": 6},
            IntervalValue.EVERY_12H: {"hours": 12},
            IntervalValue.DAILY: {"days": 1},
            IntervalValue.EVERY_3D: {"days": 3},
            IntervalValue.WEEKLY: {"weeks": 1},
        }
        kwargs = interval_map.get(schedule_value, {"days": 1})
        return "interval", kwargs

    elif schedule_type == ScheduleType.CRON:
        parts = schedule_value.split()
        if len(parts) != 5:
            logger.warning(
                f"Invalid cron expression "
                f"'{schedule_value}', "
                f"defaulting to daily at midnight"
            )
            return "cron", {"hour": 0, "minute": 0}

        return "cron", {
            "minute": parts[0],
            "hour": parts[1],
            "day": parts[2],
            "month": parts[3],
            "day_of_week": parts[4],
        }

    else:
        logger.warning(f"Unknown schedule_type '{schedule_type}', defaulting to daily")
        return "interval", {"days": 1}


def _execute_scheduled_job(schedule_id: int):
    """
    Wrapper that executes a scheduled job within Flask app context.

    This is the function registered with APScheduler.  The Flask
    app is accessed via the module-level ``_app`` reference rather
    than being passed as an argument, because APScheduler's
    SQLAlchemyJobStore needs to pickle job args and Flask app
    objects are not picklable.

    Args:
        schedule_id: The Schedule model ID to execute.
    """
    with _app.app_context():
        try:
            from shuffify.services.executors import (
                JobExecutorService,
            )

            JobExecutorService.execute(schedule_id)
        except Exception as e:
            logger.error(
                f"Scheduled job for schedule {schedule_id} failed: {e}",
                exc_info=True,
            )


def _cleanup_stale_executions(max_age_minutes: int = 30):
    """
    Mark stuck 'running' execution records as failed.

    On startup, any execution still marked 'running' from a
    prior crash is stale. Mark it failed so the UI doesn't show
    perpetually-running jobs.
    """
    try:
        from shuffify.models.db import JobExecution

        stale = JobExecution.query.filter_by(status="running").all()

        if not stale:
            return

        now = datetime.now(timezone.utc)
        cleaned = 0
        for execution in stale:
            started = execution.started_at
            # Ensure timezone-aware comparison
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            age_minutes = (now - started).total_seconds() / 60
            if age_minutes > max_age_minutes:
                execution.status = "failed"
                execution.completed_at = now
                execution.error_message = (
                    "Marked as failed: stale execution from prior process"
                )
                cleaned += 1

        if cleaned:
            from shuffify.models.db import db

            db.session.commit()
            logger.info(
                "Cleaned up %d stale execution records",
                cleaned,
            )

    except Exception as e:
        logger.warning("Failed to clean up stale executions: %s", e)


def shutdown_scheduler():
    """Gracefully shut down the scheduler."""
    global _scheduler, _app, _lock_connection
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
    _scheduler = None
    _app = None

    # Release advisory lock
    if _lock_connection is not None:
        try:
            _lock_connection.close()
        except Exception:
            pass
        _lock_connection = None
