"""
Shared scheduling utilities.

Cron expression builders and schedule type helpers used by
both raid and rotation scheduling, and the words a routine's
operation and timing are shown in.
"""

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger

from shuffify.enums import IntervalValue, JobType, ScheduleType
from shuffify.services.live_review_bindings import fingerprint

# Frequencies that support a time-of-day picker
TIME_CAPABLE_FREQUENCIES = {"daily", "every_3d", "weekly"}

# HH:MM pattern
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def build_cron(schedule_value: str, schedule_time: str) -> str:
    """Convert frequency + HH:MM into a 5-field cron expression.

    Args:
        schedule_value: One of 'daily', 'every_3d', 'weekly'.
        schedule_time: Time in HH:MM format.

    Returns:
        A 5-field cron expression string.

    Raises:
        ValueError: If schedule_value is not time-capable.
    """
    hours, minutes = schedule_time.split(":")
    if schedule_value == "daily":
        return f"{minutes} {hours} * * *"
    if schedule_value == "every_3d":
        return f"{minutes} {hours} */3 * *"
    if schedule_value == "weekly":
        return f"{minutes} {hours} * * 0"
    raise ValueError(
        f"Cannot build cron for frequency: {schedule_value}"
    )


# A saved routine's operation, by job type.
OPERATIONS = {
    JobType.RAID: "Collect",
    JobType.RAID_AND_SHUFFLE: "Collect and shuffle",
    JobType.RAID_AND_DRIP: "Collect and add",
    JobType.DRIP: "Add collected tracks",
    JobType.SHUFFLE: "Shuffle",
    JobType.ROTATE: "Rotate",
}
INTERVALS = {
    "every_6h": "Every 6 hours",
    "every_12h": "Every 12 hours",
    "daily": "Every day",
    "every_3d": "Every 3 days",
    "weekly": "Every week",
}
# shuffify.scheduler hands a cron's fifth field to APScheduler 3 unchanged, and
# APScheduler numbers weekdays from Monday (0) to Sunday (6), unlike crontab.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_CLOCK_FIELD = re.compile(r"\d{1,2}")
_WEEKDAY_FIELD = re.compile(r"[0-6]")
_NAMED_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def normalize_timing(timing):
    """Validate typed timing and return the exact fields stored on Schedule."""
    if not isinstance(timing, dict):
        raise ValueError("timing must be an object")
    zone = timing.get("timezone", "UTC")
    if not isinstance(zone, str) or not zone:
        raise ValueError("timezone must be an IANA timezone")
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("timezone must be an IANA timezone") from error
    kind = timing.get("kind")
    if kind == "interval":
        interval = timing.get("interval")
        if interval not in {value.value for value in IntervalValue}:
            raise ValueError("invalid interval")
        value = interval
        schedule_type = ScheduleType.INTERVAL
    elif kind in {"daily", "weekly"}:
        clock = timing.get("time")
        if not isinstance(clock, str) or not TIME_RE.fullmatch(clock):
            raise ValueError("time must be HH:MM")
        hour, minute = (int(part) for part in clock.split(":"))
        if hour > 23 or minute > 59:
            raise ValueError("time must be HH:MM")
        weekday = "*"
        if kind == "weekly":
            weekday = timing.get("weekday")
            if weekday not in _NAMED_WEEKDAYS:
                raise ValueError("weekday must be mon through sun")
        value = f"{minute} {hour} * * {weekday}"
        schedule_type = ScheduleType.CRON
    elif kind == "advanced":
        value = timing.get("expression")
        if not isinstance(value, str) or len(value) > 100 or len(value.split()) != 5:
            raise ValueError("expression must be five cron fields")
        schedule_type = ScheduleType.CRON
    else:
        raise ValueError("invalid timing kind")
    if schedule_type == ScheduleType.CRON:
        try:
            CronTrigger.from_crontab(value, timezone=tz)
        except ValueError as error:
            raise ValueError("invalid cron expression") from error
    return {"schedule_type": schedule_type, "schedule_value": value, "timezone": zone}


def schedule_revision(schedule):
    """A definition/state token unaffected by execution bookkeeping."""
    return fingerprint({
        "id": schedule.id,
        "user_id": schedule.user_id,
        "job_type": schedule.job_type,
        "target_playlist_id": schedule.target_playlist_id,
        "target_playlist_name": schedule.target_playlist_name,
        "source_playlist_ids": schedule.source_playlist_ids or [],
        "algorithm_name": schedule.algorithm_name,
        "algorithm_params": schedule.algorithm_params or {},
        "schedule_type": schedule.schedule_type,
        "schedule_value": schedule.schedule_value,
        "timezone": getattr(schedule, "timezone", None),
        "is_enabled": schedule.is_enabled,
    })


def serialize_timing(schedule, *, effective_timezone=None):
    """Describe stored timing without changing legacy expressions or zones."""
    zone = getattr(schedule, "timezone", None)
    if zone is None:
        zone = effective_timezone
    kind = "advanced"
    editable = {}
    value = schedule.schedule_value
    if schedule.schedule_type == ScheduleType.INTERVAL and value in INTERVALS:
        kind = "interval"
        editable = {"interval": value}
    elif schedule.schedule_type == ScheduleType.CRON:
        parts = str(value).split()
        if len(parts) == 5 and parts[2:4] == ["*", "*"] and all(
            _CLOCK_FIELD.fullmatch(part) for part in parts[:2]
        ):
            minute, hour = int(parts[0]), int(parts[1])
            if minute < 60 and hour < 24:
                editable = {"time": f"{hour:02d}:{minute:02d}"}
                if parts[4] == "*":
                    kind = "daily"
                elif parts[4] in _NAMED_WEEKDAYS:
                    kind = "weekly"
                    editable["weekday"] = parts[4]
        if kind == "advanced":
            editable = {"expression": value}
    else:
        editable = {"expression": value}
    legacy = getattr(schedule, "timezone", None) is None
    return {
        "kind": kind,
        **editable,
        "timezone": getattr(schedule, "timezone", None),
        "text": timing_text(schedule.schedule_type, value, zone),
        "schedule_type": schedule.schedule_type,
        "schedule_value": value,
        "effective_timezone": zone,
        "legacy": legacy,
        "explanation": (
            "Legacy timing uses the scheduler timezone; it is unavailable."
            if legacy and zone is None
            else "Calendar day */3 is not a fixed 72-hour interval."
            if schedule.schedule_type == ScheduleType.CRON and "*/3" in str(value).split()
            else None
        ),
    }


def operation_label(job_type):
    """A routine's operation in words; an unknown job type is shown as itself."""
    return OPERATIONS.get(job_type, str(job_type))


def timing_text(schedule_type, schedule_value, timezone_name="UTC"):
    """When a routine runs, in words; timing this cannot state exactly is shown as custom."""
    value = str(schedule_value or "").strip()
    if schedule_type == ScheduleType.INTERVAL:
        return INTERVALS.get(value, f"Custom timing ({value})")
    if schedule_type != ScheduleType.CRON:
        return f"Custom timing ({value})"
    parts = value.split()
    if len(parts) == 5 and parts[3] == "*" and all(_CLOCK_FIELD.fullmatch(part) for part in parts[:2]):
        minute, hour = int(parts[0]), int(parts[1])
        if minute < 60 and hour < 24:
            at = f"at {hour:02d}:{minute:02d} {timezone_name or 'timezone unavailable'}"
            day, weekday = parts[2], parts[4]
            if day == "*" and weekday == "*":
                return f"Every day {at}"
            if day == "*/3" and weekday == "*":
                return f"Calendar day */3 {at}"
            if day == "*" and _WEEKDAY_FIELD.fullmatch(weekday):
                return f"Every {WEEKDAYS[int(weekday)]} {at}"
            if day == "*" and weekday in _NAMED_WEEKDAYS:
                return f"Every {WEEKDAYS[_NAMED_WEEKDAYS.index(weekday)]} {at}"
    return f"Custom timing (cron {value}, {timezone_name or 'timezone unavailable'})"
