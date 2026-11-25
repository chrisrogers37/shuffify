"""Policy for saved routine definitions. Writers hold the destination configuration lock."""

from datetime import datetime, timedelta, timezone

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from shuffify.enums import ScheduleType
from shuffify.models.db import Schedule, db
from shuffify.services.base import lock_user_inventory

MAX_PER_USER = 20
MAX_PER_TARGET = 6
MIN_CRON_INTERVAL = timedelta(hours=1)


class RoutinePolicyError(ValueError):
    def __init__(self, code, status, message):
        super().__init__(message)
        self.code = code
        self.status = status


class RoutineCronTrigger(CronTrigger):
    """One selected minute per hour, with an elapsed-time floor across clock changes."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        minute = next(field for field in self.fields if field.name == "minute")
        # Five-field cron uses the same minute set in every eligible hour. Inspect
        # APScheduler's parsed field so lists, ranges and steps have its exact semantics.
        hour = datetime(2000, 1, 1)
        first = minute.get_next_value(hour)
        if first is not None and first < 59 and minute.get_next_value(hour.replace(minute=first + 1)) is not None:
            raise RoutinePolicyError("invalid_definition", 422, "Cron fires must be at least one hour apart.")

    def get_next_fire_time(self, previous_fire_time, now):
        candidate = super().get_next_fire_time(previous_fire_time, now)
        if candidate is None:
            return None
        due = candidate.astimezone(timezone.utc)
        # A fractional daylight-saving change (e.g. Lord Howe) can put two
        # matching clock minutes just 30 elapsed minutes apart. Check the raw
        # slot before the candidate even on a cold refresh with no prior fire.
        start = due - MIN_CRON_INTERVAL + timedelta(seconds=1)
        if start.astimezone(self.timezone).utcoffset() != due.astimezone(self.timezone).utcoffset():
            earlier = super().get_next_fire_time(None, start)
            if earlier and start <= earlier.astimezone(timezone.utc) < due:
                return super().get_next_fire_time(None, due + timedelta(seconds=1))
        return candidate


def uses_advanced_cron(schedule):
    """Old cron rows share a representation. Recognize exact preset forms emitted by the two writers."""
    if getattr(schedule, "advanced_cron", None):
        return True
    fields = schedule.schedule_value.split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    clock = all(part.isdecimal() and len(part) <= 2 for part in (minute, hour))
    daily_or_weekly = day == "*" and weekday in {"*", "mon", "tue", "wed", "thu", "fri", "sat", "sun", "0"}
    every_third_day = day == "*/3" and weekday == "*"
    return not (clock and month == "*" and (daily_or_weekly or every_third_day))


def check_timing(schedule_type, value):
    if schedule_type == ScheduleType.CRON:
        try:
            RoutineCronTrigger.from_crontab(value, timezone=timezone.utc)
        except RoutinePolicyError:
            raise
        except (AttributeError, ValueError) as error:
            raise RoutinePolicyError("invalid_definition", 422, "invalid cron expression") from error


def _check_capacity(user_id, target, existing_id):
    others = select(Schedule.id)
    if existing_id is None:
        # Distinct targets have distinct configuration locks. Raid auto-watch
        # already holds this same owner lock for the source it staged.
        lock_user_inventory(user_id)
    else:
        # Editing or resuming cannot grow the owner's inventory. The destination
        # configuration lock covers target moves as well as same-target edits.
        others = others.where(Schedule.id != existing_id)
    user_slots = others.where(Schedule.user_id == user_id).offset(MAX_PER_USER - 1).limit(1)
    if db.session.scalar(user_slots) is not None:
        raise RoutinePolicyError("routine_user_limit", 409,
                                 f"You can save up to {MAX_PER_USER} routines. Delete a routine to free a slot.")
    target_slots = others.where(Schedule.target_playlist_id == target).offset(MAX_PER_TARGET - 1).limit(1)
    if db.session.scalar(target_slots) is not None:
        raise RoutinePolicyError("routine_target_limit", 409,
                                 f"This playlist can have up to {MAX_PER_TARGET} routines across all users.")


def check_routine_write(user_id, target, schedule_type, value, *, existing_id=None):
    """Check the projected write. A refusal releases locks and discards staged source changes."""
    try:
        check_timing(schedule_type, value)
        _check_capacity(user_id, target, existing_id)
    except RoutinePolicyError:
        db.session.rollback()
        raise
