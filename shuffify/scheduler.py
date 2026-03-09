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
from typing import Optional, Tuple, Dict

from apscheduler.schedulers.background import BackgroundScheduler
from shuffify.enums import ScheduleType, IntervalValue
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)

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
        "scheduler_running": (
            _scheduler is not None and _scheduler.running
        ),
    }


def _on_job_executed(event):
    """Listener for successful job execution."""
    _metrics["jobs_executed"] += 1
    _metrics["last_execution_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    logger.info(f"Job {event.job_id} executed successfully")


def _on_job_error(event):
    """Listener for failed job execution."""
    _metrics["jobs_failed"] += 1
    _metrics["last_execution_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    logger.error(
        f"Job {event.job_id} failed with exception: "
        f"{event.exception}",
        exc_info=event.traceback,
    )


def _on_job_missed(event):
    """Listener for missed job execution."""
    _metrics["jobs_missed"] += 1
    logger.warning(
        f"Job {event.job_id} missed its scheduled run time"
    )


def _try_acquire_scheduler_lock(db_url: str) -> bool:
    """
    Attempt to acquire a PostgreSQL advisory lock to guarantee
    only one scheduler instance runs across all processes.

    Uses pg_try_advisory_lock with a fixed lock ID. The lock is
    held for the lifetime of the connection and released
    automatically when the connection closes.

    Returns True if lock acquired (or non-PostgreSQL DB).
    Returns True on any error (fail-open for safety).
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
        result = conn.execute(
            text(f"SELECT pg_try_advisory_lock({lock_id})")
        )
        acquired = result.scalar()

        if acquired:
            # Keep connection alive — lock released on close
            _lock_connection = conn
            logger.info(
                "Acquired scheduler advisory lock"
            )
            return True
        else:
            conn.close()
            engine.dispose()
            logger.info(
                "Another process holds the scheduler lock, "
                "skipping scheduler init"
            )
            return False

    except Exception as e:
        # Fail-open: if we can't check the lock, start anyway
        logger.warning(
            "Could not acquire advisory lock (%s), "
            "starting scheduler anyway (fail-open)",
            e,
        )
        return True


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
    if (
        app.debug
        and os.environ.get("WERKZEUG_RUN_MAIN") != "true"
    ):
        logger.info(
            "Skipping scheduler init in Werkzeug reloader "
            "child process"
        )
        return None

    if _scheduler is not None and _scheduler.running:
        logger.warning(
            "Scheduler already running, skipping re-init"
        )
        return _scheduler

    try:
        db_url = app.config.get(
            "SQLALCHEMY_DATABASE_URI", "sqlite:///shuffify.db"
        )

        # Advisory lock: prevent duplicate schedulers
        if not _try_acquire_scheduler_lock(db_url):
            return None

        # Separate jobstore engine with small pool
        from sqlalchemy import create_engine

        jobstore_engine = create_engine(
            db_url, pool_size=2, pool_pre_ping=True
        )

        pool_size = app.config.get(
            "SCHEDULER_THREAD_POOL_SIZE", 10
        )

        jobstores = {
            "default": SQLAlchemyJobStore(
                engine=jobstore_engine
            ),
        }
        executors = {
            "default": ThreadPoolExecutor(
                max_workers=pool_size
            ),
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
        _scheduler.add_listener(
            _on_job_executed, EVENT_JOB_EXECUTED
        )
        _scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
        _scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)

        _scheduler.start()
        _app = app
        logger.info(
            "APScheduler started successfully "
            "(pool_size=%d)",
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

        enabled_schedules = Schedule.query.filter_by(
            is_enabled=True
        ).all()
        registered = 0

        for schedule in enabled_schedules:
            try:
                add_job_for_schedule(schedule)
                registered += 1
            except Exception as e:
                logger.error(
                    f"Failed to register job for schedule "
                    f"{schedule.id}: {e}"
                )

        logger.info(
            f"Registered {registered}/"
            f"{len(enabled_schedules)} "
            f"scheduled jobs from database"
        )

    except Exception as e:
        logger.error(
            f"Failed to load schedules from database: {e}"
        )


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

    # Remove existing job if present (for updates)
    try:
        _scheduler.remove_job(job_id)
    except Exception:
        pass

    # Parse schedule_value into APScheduler trigger
    trigger, trigger_kwargs = _parse_schedule(
        schedule.schedule_type, schedule.schedule_value
    )

    _scheduler.add_job(
        func=_execute_scheduled_job,
        trigger=trigger,
        id=job_id,
        args=[schedule.id],
        replace_existing=True,
        **trigger_kwargs,
    )

    logger.info(
        f"Registered job {job_id} with trigger={trigger}, "
        f"kwargs={trigger_kwargs}"
    )


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
        logger.debug(
            f"Job {job_id} not found in scheduler "
            f"(already removed)"
        )


def _parse_schedule(
    schedule_type: str, schedule_value: str
) -> Tuple[str, Dict]:
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
        logger.warning(
            f"Unknown schedule_type '{schedule_type}', "
            f"defaulting to daily"
        )
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
                f"Scheduled job for schedule {schedule_id} "
                f"failed: {e}",
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

        stale = JobExecution.query.filter_by(
            status="running"
        ).all()

        if not stale:
            return

        now = datetime.now(timezone.utc)
        cleaned = 0
        for execution in stale:
            started = execution.started_at
            # Ensure timezone-aware comparison
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            age_minutes = (
                now - started
            ).total_seconds() / 60
            if age_minutes > max_age_minutes:
                execution.status = "failed"
                execution.completed_at = now
                execution.error_message = (
                    "Marked as failed: stale execution "
                    "from prior process"
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
        logger.warning(
            "Failed to clean up stale executions: %s", e
        )


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
