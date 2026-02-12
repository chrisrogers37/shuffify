"""
APScheduler integration for Shuffify.

Manages the background scheduler lifecycle: initialization,
job registration, and graceful shutdown. Jobs persist in SQLite
via SQLAlchemyJobStore.

IMPORTANT:
- Must be initialized AFTER Flask app creation (in create_app).
- In development with Werkzeug reloader, only start in main process.
- In production with Gunicorn, use --preload for single scheduler.
"""

import logging
import os
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


def _on_job_executed(event):
    """Listener for successful job execution."""
    logger.info(f"Job {event.job_id} executed successfully")


def _on_job_error(event):
    """Listener for failed job execution."""
    logger.error(
        f"Job {event.job_id} failed with exception: "
        f"{event.exception}",
        exc_info=event.traceback,
    )


def _on_job_missed(event):
    """Listener for missed job execution."""
    logger.warning(
        f"Job {event.job_id} missed its scheduled run time"
    )


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
    global _scheduler

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

        jobstores = {
            "default": SQLAlchemyJobStore(url=db_url),
        }
        executors = {
            "default": ThreadPoolExecutor(max_workers=3),
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
        logger.info("APScheduler started successfully")

        # Register existing enabled schedules from database
        with app.app_context():
            _register_existing_jobs(app)

        return _scheduler

    except Exception as e:
        logger.error(
            f"Failed to initialize scheduler: {e}",
            exc_info=True,
        )
        _scheduler = None
        return None


def _register_existing_jobs(app):
    """
    Load enabled schedules from the database and register them
    with APScheduler.

    Args:
        app: The Flask application instance.
    """
    try:
        from shuffify.models.db import Schedule

        enabled_schedules = Schedule.query.filter_by(
            is_enabled=True
        ).all()
        registered = 0

        for schedule in enabled_schedules:
            try:
                add_job_for_schedule(schedule, app)
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


def add_job_for_schedule(schedule, app):
    """
    Register an APScheduler job for a Schedule model instance.

    Args:
        schedule: A Schedule model instance.
        app: The Flask application instance.

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
        args=[app, schedule.id],
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


def _execute_scheduled_job(app, schedule_id: int):
    """
    Wrapper that executes a scheduled job within Flask app context.

    This is the function registered with APScheduler.

    Args:
        app: The Flask application instance.
        schedule_id: The Schedule model ID to execute.
    """
    with app.app_context():
        try:
            from shuffify.services.job_executor_service import (
                JobExecutorService,
            )

            JobExecutorService.execute(schedule_id)
        except Exception as e:
            logger.error(
                f"Scheduled job for schedule {schedule_id} "
                f"failed: {e}",
                exc_info=True,
            )


def shutdown_scheduler():
    """Gracefully shut down the scheduler."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler shut down")
    _scheduler = None
