"""Deterministic worker-only schedule timing and local durable fire admission."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import ValidationError
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, RoutineActivationReceipt, Schedule
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


def recovery_evidence(storage, schedules):
    """Batch durable fire watermarks and owned activation floors during refresh."""
    owners = {row.id: row for row in schedules}
    if not owners:
        return {}, {}
    latest = (
        select(JobExecution.schedule_id, func.max(JobExecution.scheduled_for).label("due"))
        .where(JobExecution.schedule_id.in_(owners), JobExecution.kind == "schedule_run")
        .group_by(JobExecution.schedule_id)
        .subquery()
    )
    fires = {}
    for row in storage.execute(select(
        JobExecution.schedule_id, JobExecution.user_id, JobExecution.scheduled_for,
    ).join(latest, and_(
        JobExecution.schedule_id == latest.c.schedule_id, JobExecution.scheduled_for == latest.c.due,
    ))):
        schedule = owners[row.schedule_id]
        if row.user_id == schedule.user_id and row.scheduled_for >= schedule.created_at:
            fires[row.schedule_id] = row
    activations = {}
    for schedule_id, user_id, incarnation, accepted in storage.execute(
        select(
            RoutineActivationReceipt.schedule_id, RoutineActivationReceipt.user_id,
            RoutineActivationReceipt.schedule_created_at, func.max(RoutineActivationReceipt.accepted_at),
        )
        .where(RoutineActivationReceipt.schedule_id.in_(owners))
        .group_by(
            RoutineActivationReceipt.schedule_id, RoutineActivationReceipt.user_id,
            RoutineActivationReceipt.schedule_created_at,
        )
    ):
        schedule = owners[schedule_id]
        if user_id == schedule.user_id and incarnation == schedule.created_at:
            activations[schedule_id] = accepted
    return fires, activations


def eligibility_floor(schedule, activation_at=None):
    changed = schedule.definition_changed_at or schedule.updated_at or schedule.created_at
    floor = max(utc(schedule.created_at), utc(changed))
    return max(floor, utc(activation_at)) if activation_at is not None else floor


def recoverable_fire(schedule, cadence, now, *, latest=None, activation_at=None, grace_seconds=3600):
    """Coalesce to the latest eligible slot, never to a new admission-time identity.

    Rows predating the precise definition floor use updated_at conservatively;
    their execution bookkeeping cannot prove when the definition last changed.
    """
    now = utc(now)
    floor = max(eligibility_floor(schedule, activation_at), now - timedelta(seconds=grace_seconds))
    if isinstance(cadence.trigger, IntervalTrigger):
        # The anchor defines cadence phase; the first run follows one full interval.
        floor = max(floor, utc(cadence.trigger.start_date) + cadence.trigger.interval)
    if latest is not None:
        floor = max(floor, utc(latest.scheduled_for) + timedelta(microseconds=1))
    due, current = None, cadence.trigger.get_next_fire_time(None, floor)
    while current is not None and utc(current) <= now:
        due = utc(current)
        current = cadence.trigger.get_next_fire_time(current, due + timedelta(microseconds=1))
    return due


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
    repository, schedule_id, expected_cadence, scheduled_for, *, default_timezone="UTC", is_leader=lambda: True,
    revalidate_due=False,
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
            if revalidate_due:
                activation = storage.scalar(select(func.max(RoutineActivationReceipt.accepted_at)).where(
                    RoutineActivationReceipt.schedule_id == current.id,
                    RoutineActivationReceipt.user_id == current.user_id,
                    RoutineActivationReceipt.schedule_created_at == current.created_at,
                ))
                if due < eligibility_floor(current, activation):
                    raise _Skipped("eligibility_changed")
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
