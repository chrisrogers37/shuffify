"""Persist observed scheduler outcomes under the same fire lock as queue admission."""

from datetime import timedelta

from sqlalchemy import select

from shuffify.models.db import DeferredScheduleFire, JobExecution, Schedule
from shuffify.services.execution_result_service import SCHEDULE_SKIP_REASONS
from shuffify.services.scheduled_admission import (
    GRACE_SECONDS,
    AdmissionResult,
    AdmissionSkipped,
    admit_scheduled,
    ineligible_fire,
    utc,
)


def _record_skip(storage, schedule, due, result, now):
    # A history row has no queue kind or scheduled_for key. A conflicting command
    # keeps its own identity, and a later successful admission can still replay.
    already_recorded = storage.scalar(select(JobExecution.id).where(
        JobExecution.schedule_id == schedule.id, JobExecution.started_at == due,
        JobExecution.kind.is_(None), JobExecution.status == "skipped",
        JobExecution.result_json["schedule_skip_code"].as_string().is_not(None),
    ).limit(1))
    if already_recorded is None:
        storage.add(JobExecution(
            user_id=schedule.user_id, schedule_id=schedule.id, started_at=due, completed_at=now, status="skipped",
            error_message=SCHEDULE_SKIP_REASONS[result.code], result_json={
                "schedule_skip_code": result.code, "blocking_operation_id": result.blocking_operation_id,
                "metadata": {"triggered_by": "scheduler"},
            },
        ))


def process_scheduled_fire(repository, schedule_id, expected_cadence, scheduled_for, *, clock, is_leader,
                           default_timezone, revalidate_due=True, retry=False, history_only=False):
    due = utc(scheduled_for)
    deadline = due + timedelta(seconds=GRACE_SECONDS)

    def guard():
        if not is_leader():
            raise AdmissionSkipped("leadership_lost")
        if history_only or utc(clock()) > deadline:
            raise AdmissionSkipped("misfire")

    with repository.scheduled_fire_transaction(schedule_id, due) as storage:
        if not is_leader():
            return AdmissionResult("skipped", due, code="leadership_lost")
        # All history/deferral writers, including deletion, lock the parent first.
        schedule = storage.scalar(select(Schedule).where(Schedule.id == schedule_id).with_for_update(read=True))
        pending = storage.get(DeferredScheduleFire, (schedule_id, due))
        if retry and (pending is None or pending.cadence != expected_cadence):
            return AdmissionResult("skipped", due, code="retry_finished")
        accepted = storage.scalar(select(JobExecution.id).where(
            JobExecution.schedule_id == schedule_id, JobExecution.scheduled_for == due,
        ))
        if schedule is None and accepted is None:
            result = AdmissionResult("skipped", due, code="schedule_missing")
        elif accepted is None and (history_only or utc(clock()) > deadline):
            result = AdmissionResult("skipped", due, code="misfire")
        else:
            result = admit_scheduled(
                repository, schedule_id, expected_cadence, due, default_timezone=default_timezone,
                is_leader=is_leader, revalidate_due=revalidate_due, storage=storage, before_insert=guard,
            )
        if result.code == "leadership_lost":
            return result
        # Capacity, expiry and a conflicting fire key can bypass command capture.
        if result.code in {"outstanding_operation_limit", "misfire", "fire_conflict"}:
            reason = ineligible_fire(storage, schedule, expected_cadence, due, default_timezone, revalidate_due)
            if reason:
                result = AdmissionResult("skipped", due, code=reason)
        if result.code in SCHEDULE_SKIP_REASONS and schedule is not None:
            if result.code == "misfire" and pending is not None:
                # A release before the deadline followed by scheduler downtime
                # is a missed retry window, rather than a playlist still in use.
                blocker = storage.execute(select(JobExecution.id, JobExecution.completed_at).where(
                    JobExecution.id == pending.blocking_operation_id,
                )).one_or_none()
                if blocker is not None and (blocker.completed_at is None or blocker.completed_at > deadline):
                    result = AdmissionResult("skipped", due, code="busy", blocking_operation_id=blocker.id)
            elif result.code == "busy" and not history_only and utc(clock()) <= deadline:
                if pending is None:
                    storage.add(DeferredScheduleFire(
                        schedule_id=schedule_id, scheduled_for=due, cadence=expected_cadence,
                        blocking_operation_id=result.blocking_operation_id,
                    ))
                else:
                    pending.cadence = expected_cadence
                    pending.blocking_operation_id = result.blocking_operation_id
                return AdmissionResult("deferred", due, code="busy", blocking_operation_id=result.blocking_operation_id)
            _record_skip(storage, schedule, due, result, utc(clock()))
        if pending is not None and pending.cadence == expected_cadence:
            storage.delete(pending)
        return result
