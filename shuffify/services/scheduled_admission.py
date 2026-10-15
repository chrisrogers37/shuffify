"""Deterministic worker-only schedule timing and local durable fire admission."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, Schedule
from shuffify.scheduler import _parse_schedule
from shuffify.services.live_review_bindings import fingerprint
from shuffify.services.operations.commands.routine_capture import CaptureNotFound, CaptureRejected, capture_schedule
from shuffify.services.operations.repository import BusyOperation, RequestKeyConflict


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("A scheduled fire requires an aware datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Cadence:
    fingerprint: str
    timezone: str
    trigger: object


def build_cadence(schedule, default_timezone="UTC"):
    """Execution bookkeeping cannot change a created-at-anchored interval phase."""
    zone = ZoneInfo(schedule.timezone or default_timezone)
    kind, options = _parse_schedule(schedule.schedule_type, schedule.schedule_value)
    anchor = utc(schedule.created_at)
    trigger = (
        IntervalTrigger(start_date=anchor, timezone=zone, **options)
        if kind == "interval"
        else CronTrigger(timezone=zone, **options)
    )
    identity = {
        "type": schedule.schedule_type,
        "value": schedule.schedule_value,
        "timezone": str(zone),
        "anchor": anchor.isoformat() if kind == "interval" else None,
        "incarnation": anchor.isoformat(),
    }
    return Cadence(fingerprint(identity), str(zone), trigger)


@dataclass(frozen=True)
class AdmissionResult:
    state: str
    scheduled_for: datetime
    code: str | None = None
    operation_id: int | None = None
    blocking_operation_id: int | None = None

    def to_dict(self):
        value = asdict(self)
        value["scheduled_for"] = self.scheduled_for.isoformat()
        return value


class _Skipped(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def admit_scheduled(
    repository, schedule_id, expected_cadence, scheduled_for, *, default_timezone="UTC", is_leader=lambda: True
):
    """Admit one exact due instant; replay accepted intent before volatile capture.

    There is no Flask request, provider construction or independent domain commit.
    Transient database errors propagate to the controller's bounded retry policy.
    """
    due = utc(scheduled_for)
    if type(schedule_id) is not int or schedule_id < 1 or not isinstance(expected_cadence, str):
        raise ValueError("A scheduled fire requires a schedule identity and cadence")

    def require_leader():
        if not is_leader():
            raise _Skipped("leadership_lost")

    try:
        require_leader()
        # The stable owner of an accepted fire lets enqueue replay it before
        # any fresh definition/state check, including a subsequent Pause.
        with Session(repository.engine) as storage:
            owner = storage.scalar(
                select(JobExecution.user_id).where(
                    JobExecution.schedule_id == schedule_id,
                    JobExecution.scheduled_for == due,
                )
            )
            if owner is None:
                owner = storage.scalar(select(Schedule.user_id).where(Schedule.id == schedule_id))
        if owner is None:
            raise _Skipped("schedule_missing")

        def capture(storage):
            require_leader()
            current = storage.scalar(select(Schedule).where(Schedule.id == schedule_id, Schedule.user_id == owner))
            if current is None:
                raise _Skipped("schedule_missing")
            if not current.is_enabled:
                raise _Skipped("schedule_disabled")
            if build_cadence(current, default_timezone).fingerprint != expected_cadence:
                raise _Skipped("cadence_changed")
            command = capture_schedule(storage, owner, schedule_id, triggered_by="scheduler")
            require_leader()
            return command.reserved_targets, {"version": 1, "command": command.model_dump(mode="json")}

        intent = {"purpose": "scheduled-fire.v1", "schedule_id": schedule_id, "scheduled_for": due.isoformat()}
        row = repository.enqueue(
            kind="schedule_run",
            user_id=owner,
            schedule_id=schedule_id,
            scheduled_for=due,
            intent=intent,
            command_factory=capture,
        )
        return AdmissionResult("admitted", due, operation_id=row.id)
    except _Skipped as skipped:
        return AdmissionResult("skipped", due, code=skipped.code)
    except BusyOperation as busy:
        return AdmissionResult("skipped", due, code="busy", blocking_operation_id=busy.operation_id)
    except RequestKeyConflict:
        return AdmissionResult("skipped", due, code="fire_conflict")
    except (CaptureNotFound, CaptureRejected, ValidationError, ValueError, ZoneInfoNotFoundError):
        return AdmissionResult("skipped", due, code="invalid_schedule")
