"""API-safe scheduling facade and pure saved-trigger parsing.

Only OperationWorker constructs a scheduler. Web processes send bounded refresh
hints and read authenticated worker-memory snapshots; PostgreSQL notifications
cover lost hints.
"""

import logging
from datetime import datetime
from typing import Dict, Tuple

from shuffify.enums import IntervalValue, ScheduleType
from shuffify.services.schedule_utils import schedule_revision
from shuffify.services.worker_control import WorkerControlClient

logger = logging.getLogger(__name__)


def add_job_for_schedule(schedule):
    """Hint that a committed definition changed; never create a local job."""
    return WorkerControlClient().poke()


def remove_job_for_schedule(schedule_id):
    """Hint that committed state changed; worker refresh reads current rows."""
    return WorkerControlClient().poke()


def get_schedule_registrations(schedules):
    """One batched memory read, compared to the exact current DB definitions."""
    schedules = list(schedules)
    if not schedules:
        return {}
    snapshot = WorkerControlClient().registrations([row.id for row in schedules]) if len(schedules) <= 5000 else None
    available = bool(snapshot and snapshot.get('leader') is True and snapshot.get('scheduler_running') is True
                     and isinstance(snapshot.get('generation'), str) and snapshot['generation'])
    result = {}
    for row in schedules:
        zone = getattr(row, 'timezone', None)
        status, next_run = 'unavailable', None
        if available:
            zone = zone or snapshot.get('effective_timezone')
            job = snapshot['registrations'].get(str(row.id))
            if not row.is_enabled:
                status = 'paused' if job is None else 'needs_attention'
            else:
                status = 'needs_attention'
                if isinstance(job, dict) and job.get('installed_revision') == schedule_revision(row):
                    candidate = job.get('next_run_at')
                    try:
                        instant = datetime.fromisoformat(candidate)
                        if instant.utcoffset() is not None and job.get('effective_timezone') == zone:
                            next_run, status = candidate, 'registered'
                    except (ValueError, TypeError):
                        pass
        result[row.id] = {'status': status, 'next_run_at': next_run, 'effective_timezone': zone}
    return result


def get_schedule_registration(schedule):
    return get_schedule_registrations([schedule])[schedule.id]


def sync_schedule_registration(schedule):
    """Poke after commit, then report observed memory without claiming synchronization."""
    if schedule.is_enabled:
        add_job_for_schedule(schedule)
    else:
        remove_job_for_schedule(schedule.id)
    return get_schedule_registration(schedule)


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
