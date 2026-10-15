"""Recent missed slots retain their exact durable identity after scheduler loss."""

import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, RoutineActivationReceipt, db
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.scheduled_admission import admit_scheduled, build_cadence, recoverable_fire
from shuffify.services.worker_scheduler import WorkerScheduler
from tests.services.operations.test_scheduled_admission import NOW
from tests.services.operations.test_scheduled_admission import schedule as schedule


@pytest.fixture
def scheduler_clock(monkeypatch):
    clock = [NOW - timedelta(seconds=1)]

    class ClockedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr("apscheduler.schedulers.base.datetime", ClockedDateTime)
    monkeypatch.setattr("shuffify.services.schedule_notifications.now_utc", lambda: clock[0])
    return clock


def executions(repository):
    with Session(repository.engine) as storage:
        return list(storage.scalars(select(JobExecution).order_by(JobExecution.id)))


def wait_for_fire(repository):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        rows = executions(repository)
        if rows:
            return rows
        time.sleep(0.005)
    return executions(repository)


def runtime(app, repository, clock, **kwargs):
    app.config["SCHEDULER_ENABLED"] = True
    return WorkerScheduler(app, repository, clock=lambda: clock[0], **kwargs)


@pytest.mark.parametrize("cadence", ["interval", "cron"])
def test_restart_recovers_one_daily_slot_inside_grace_and_deduplicates(
    schedule, db_app, scheduler_clock, monkeypatch, cadence
):
    schedule.schedule_type = cadence
    schedule.schedule_value = "daily" if cadence == "interval" else "30 12 * * *"
    schedule.updated_at = schedule.created_at
    schedule.definition_changed_at = schedule.created_at
    db.session.commit()
    repository = OperationRepository()

    def forbidden(*args, **kwargs):
        raise AssertionError("Schedule admission must remain local-only")

    monkeypatch.setattr("shuffify.spotify.api.SpotifyAPI.__init__", forbidden)
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    monkeypatch.setattr("shuffify.services.executors.base_executor.JobExecutorService.execute", forbidden)
    with runtime(db_app, repository, scheduler_clock) as first:
        assert first.ready.wait(2)
        assert executions(repository) == []

    scheduler_clock[0] = NOW + timedelta(minutes=5)
    with runtime(db_app, repository, scheduler_clock) as replacement:
        assert replacement.ready.wait(2)
        rows = wait_for_fire(repository)
        assert len(rows) == 1
        assert rows[0].kind == "schedule_run"
        assert rows[0].scheduled_for == NOW
        operation_id = rows[0].id

    with runtime(db_app, repository, scheduler_clock) as restarted:
        assert restarted.ready.wait(2)
        time.sleep(0.03)
        rows = executions(repository)
        assert [(row.id, row.scheduled_for) for row in rows] == [(operation_id, NOW)]


def test_restart_does_not_admit_a_slot_outside_grace(schedule, db_app, scheduler_clock):
    schedule.updated_at = schedule.created_at
    schedule.definition_changed_at = schedule.created_at
    db.session.commit()
    repository = OperationRepository()
    with runtime(db_app, repository, scheduler_clock) as first:
        assert first.ready.wait(2)
    scheduler_clock[0] = NOW + timedelta(seconds=3601)
    with runtime(db_app, repository, scheduler_clock) as replacement:
        assert replacement.ready.wait(2)
        time.sleep(0.03)
        assert executions(repository) == []
        registration = replacement.registration_snapshot([schedule.id])["registrations"][str(schedule.id)]
        assert datetime.fromisoformat(registration["next_run_at"]) == NOW + timedelta(days=1)


def test_latest_due_is_coalesced_without_changing_the_interval_phase(schedule, db_app, scheduler_clock):
    schedule.schedule_type = "cron"
    schedule.schedule_value = "* * * * *"
    schedule.updated_at = schedule.created_at
    schedule.definition_changed_at = schedule.created_at
    db.session.commit()
    scheduler_clock[0] = NOW + timedelta(minutes=5, seconds=23)
    repository = OperationRepository()
    with runtime(db_app, repository, scheduler_clock) as replacement:
        assert replacement.ready.wait(2)
        rows = wait_for_fire(repository)
        assert [row.scheduled_for for row in rows] == [NOW + timedelta(minutes=5)]


@pytest.mark.parametrize("floor_kind", ["created", "edited", "activation"])
def test_restart_never_backfills_a_slot_before_current_definition_became_eligible(
    schedule, db_app, scheduler_clock, floor_kind
):
    schedule.schedule_type = "cron"
    schedule.schedule_value = "30 12 * * *"
    schedule.updated_at = schedule.created_at
    schedule.definition_changed_at = schedule.created_at
    floor = NOW + timedelta(minutes=1)
    if floor_kind == "created":
        schedule.created_at = schedule.updated_at = floor
    elif floor_kind == "edited":
        schedule.definition_changed_at = floor
    else:
        db.session.add(RoutineActivationReceipt(
            user_id=schedule.user_id, request_key="activation", payload_hash="a" * 64,
            schedule_id=schedule.id, schedule_created_at=schedule.created_at, accepted_at=floor,
        ))
    db.session.commit()
    scheduler_clock[0] = NOW + timedelta(minutes=5)
    repository = OperationRepository()
    with runtime(db_app, repository, scheduler_clock) as replacement:
        assert replacement.ready.wait(2)
        time.sleep(0.03)
        assert executions(repository) == []


def test_manual_bookkeeping_during_downtime_does_not_hide_current_cadence_fire(
    schedule, db_app, scheduler_clock
):
    schedule.definition_changed_at = schedule.created_at
    db.session.commit()
    repository = OperationRepository()
    prior = NOW - timedelta(days=1)
    first = admit_scheduled(repository, schedule.id, build_cadence(schedule).fingerprint, prior)
    repository.finish(repository.claim(operation_id=first.operation_id), "succeeded")
    scheduler_clock[0] = NOW + timedelta(minutes=1)
    schedule.last_run_at, schedule.last_status = scheduler_clock[0], "success"
    db.session.commit()
    assert schedule.updated_at > schedule.definition_changed_at
    with runtime(db_app, repository, scheduler_clock) as replacement:
        assert replacement.ready.wait(2)
        deadline = time.monotonic() + 2
        while len(executions(repository)) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert [row.scheduled_for for row in executions(repository)] == [prior, NOW]


def test_pause_then_resume_after_due_does_not_backfill_even_with_prior_scheduled_history(
    schedule, db_app, scheduler_clock
):
    schedule.definition_changed_at = schedule.created_at
    db.session.commit()
    repository = OperationRepository()
    first = admit_scheduled(repository, schedule.id, build_cadence(schedule).fingerprint, NOW - timedelta(days=1))
    repository.finish(repository.claim(operation_id=first.operation_id), "succeeded")
    scheduler_clock[0] = NOW + timedelta(minutes=1)
    schedule.is_enabled = False
    db.session.commit()
    scheduler_clock[0] += timedelta(minutes=1)
    schedule.is_enabled = True
    db.session.commit()
    with runtime(db_app, repository, scheduler_clock) as replacement:
        assert replacement.ready.wait(2)
        time.sleep(0.03)
        assert [row.id for row in executions(repository)] == [first.operation_id]


def test_legacy_null_definition_floor_conservatively_uses_last_update(schedule, db_app, scheduler_clock):
    schedule.definition_changed_at = None
    schedule.updated_at = NOW + timedelta(minutes=1)
    db.session.commit()
    scheduler_clock[0] = NOW + timedelta(minutes=5)
    repository = OperationRepository()
    with runtime(db_app, repository, scheduler_clock) as replacement:
        assert replacement.ready.wait(2)
        time.sleep(0.03)
        assert executions(repository) == []


def test_resume_after_registration_before_catchup_admission_refuses_old_slot(
    schedule, db_app, scheduler_clock
):
    schedule.definition_changed_at = schedule.created_at
    db.session.commit()
    scheduler_clock[0] = NOW + timedelta(minutes=5)
    repository = OperationRepository()
    results = []

    def resume_before_admission(repository, schedule_id, cadence, due, **kwargs):
        with Session(repository.engine) as storage, storage.begin():
            current = storage.get(type(schedule), schedule_id)
            current.is_enabled = False
        with Session(repository.engine) as storage, storage.begin():
            current = storage.get(type(schedule), schedule_id)
            current.is_enabled = True
        result = admit_scheduled(repository, schedule_id, cadence, due, **kwargs)
        results.append(result)
        return result

    with runtime(db_app, repository, scheduler_clock, admission_callback=resume_before_admission) as replacement:
        assert replacement.ready.wait(2)
        deadline = time.monotonic() + 2
        while not results and time.monotonic() < deadline:
            time.sleep(0.005)
        assert len(results) == 1
        assert results[0].code == "eligibility_changed"
        assert executions(repository) == []


def test_recovery_at_exact_grace_boundary_admits_original_due(schedule, db_app, scheduler_clock):
    schedule.definition_changed_at = schedule.created_at
    db.session.commit()
    scheduler_clock[0] = NOW + timedelta(seconds=3600)
    repository = OperationRepository()
    with runtime(db_app, repository, scheduler_clock) as replacement:
        assert replacement.ready.wait(2)
        assert [row.scheduled_for for row in wait_for_fire(repository)] == [NOW]


def test_recovery_across_dst_fold_advances_by_utc_instant(schedule):
    schedule.schedule_type, schedule.schedule_value = "cron", "30 1 * * *"
    schedule.timezone = "America/New_York"
    schedule.definition_changed_at = schedule.created_at
    cadence = build_cadence(schedule)

    class BoundedTrigger:
        calls = 0

        def get_next_fire_time(self, previous, now):
            self.calls += 1
            assert self.calls < 65, "A one-hour recovery window must not repeat a DST fold forever"
            return cadence.trigger.get_next_fire_time(previous, now)

    now = datetime(2026, 11, 1, 6, 45, tzinfo=timezone.utc)
    bounded = replace(cadence, trigger=BoundedTrigger())
    assert recoverable_fire(schedule, bounded, now) == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)


def test_new_interval_anchor_is_phase_origin_and_first_period_can_recover(schedule, db_app, scheduler_clock):
    schedule.created_at = schedule.updated_at = schedule.definition_changed_at = NOW
    db.session.commit()
    scheduler_clock[0] = NOW + timedelta(minutes=5)
    cadence = build_cadence(schedule)
    assert recoverable_fire(schedule, cadence, scheduler_clock[0]) is None
    repository = OperationRepository()
    with runtime(db_app, repository, scheduler_clock) as replacement:
        assert replacement.ready.wait(2)
        time.sleep(0.03)
        assert executions(repository) == []
        registration = replacement.registration_snapshot([schedule.id])["registrations"][str(schedule.id)]
        assert datetime.fromisoformat(registration["next_run_at"]) == NOW + timedelta(days=1)
    scheduler_clock[0] = NOW + timedelta(days=1, minutes=5)
    with runtime(db_app, repository, scheduler_clock) as restarted:
        assert restarted.ready.wait(2)
        assert [row.scheduled_for for row in wait_for_fire(repository)] == [NOW + timedelta(days=1)]
