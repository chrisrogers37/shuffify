"""A busy slot survives leadership changes and returns to ordinary admission."""

from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, db
from shuffify.services.operations.repository import OperationRepository
from shuffify.services.scheduled_admission import build_cadence
from shuffify.services.worker_scheduler import WorkerScheduler
from tests.services.operations.test_repository import postgres_repository as postgres_repository
from tests.services.operations.test_scheduled_admission import NOW
from tests.services.operations.test_scheduled_admission import schedule as schedule
from tests.services.operations.test_scheduler_recovery import scheduler_clock as scheduler_clock


def prepare(schedule):
    schedule.definition_changed_at = schedule.created_at
    db.session.commit()
    return schedule.id, build_cadence(schedule).fingerprint


def worker(app, repo, clock, **kwargs):
    app.config["SCHEDULER_ENABLED"] = True
    return WorkerScheduler(app, repo, clock=lambda: clock[0], **kwargs)


def test_busy_fire_is_readmitted_at_original_slot_after_worker_restart(schedule, db_app, scheduler_clock, monkeypatch):
    monkeypatch.setattr("requests.sessions.Session.request", lambda *a, **kw: pytest.fail("Network during admission"))
    schedule_id, cadence = prepare(schedule)
    repo = OperationRepository()
    blocker = repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    # This observed slot is earlier than the next cadence fire. Only the durable retry can recover it.
    due = NOW - timedelta(minutes=5)
    with worker(db_app, repo, scheduler_clock) as first:
        assert first.ready.wait(2)
        result = first._fire(schedule_id, cadence, first._generation, scheduled_for=due)
        assert result.state == "deferred" and result.code == "busy"
    repo.finish(repo.claim(operation_id=blocker.id), "succeeded")
    scheduler_clock[0] = NOW - timedelta(seconds=1)
    completed = Event()
    with worker(db_app, repo, scheduler_clock, wake_callback=completed.set) as replacement:
        assert replacement.ready.wait(2)
        assert completed.wait(3)
    with Session(repo.engine) as storage:
        runs = storage.scalars(select(JobExecution).where(JobExecution.schedule_id == schedule_id)).all()
        assert [(run.kind, run.scheduled_for) for run in runs] == [("schedule_run", due)]


def attempt(repo, schedule, due, now, *, retry=False, leader=True):
    from shuffify.services.schedule_fire_outcomes import process_scheduled_fire

    return process_scheduled_fire(repo, schedule.id, build_cadence(schedule).fingerprint, due,
                                 clock=lambda: now, is_leader=lambda: leader, default_timezone="UTC", retry=retry)


def test_running_scheduler_readmits_after_busy_without_shifting_cadence(schedule, db_app, scheduler_clock):
    from shuffify.models.db import DeferredScheduleFire

    schedule_id, _ = prepare(schedule)
    repo = OperationRepository()
    blocker = repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    deferred, admitted = Event(), Event()

    def observe(name, _):
        if name == "deferred_busy":
            deferred.set()

    with worker(db_app, repo, scheduler_clock, event_callback=observe, wake_callback=admitted.set) as runner:
        assert runner.ready.wait(2)
        scheduler_clock[0] = NOW
        runner._scheduler.wakeup()
        assert deferred.wait(2)
        assert not admitted.is_set()
        assert runner._scheduler.get_job(f"schedule_{schedule_id}").next_run_time == NOW + timedelta(days=1)
        repo.finish(repo.claim(operation_id=blocker.id), "succeeded")
        scheduler_clock[0] += timedelta(seconds=5)
        runner._scheduler.wakeup()
        assert admitted.wait(2)
    assert JobExecution.query.filter_by(kind="schedule_run").one().scheduled_for == NOW
    assert DeferredScheduleFire.query.count() == 0


def test_busy_expiry_is_visible_once_to_owner_and_keeps_foreign_id_private(
    schedule, auth_client, db_app, sample_token, monkeypatch,
):
    from shuffify.models.db import DeferredScheduleFire, User

    foreign_owner = schedule.user_id
    schedule.user_id = User.query.filter_by(spotify_id="user123").one().id
    db.session.commit()
    prepare(schedule)
    repo = OperationRepository()
    blocker = repo.enqueue(kind="shuffle", user_id=foreign_owner, targets=["target"], payload={})
    lease = repo.claim(operation_id=blocker.id)
    repo.checkpoint(lease, {"plans": [{"resource": "target", "batches": [{"last_http_status": 204}]}]})
    assert attempt(repo, schedule, NOW, NOW).state == "deferred"
    assert attempt(repo, schedule, NOW, NOW + timedelta(hours=1)).state == "deferred"
    late = NOW + timedelta(hours=1, microseconds=1)
    assert attempt(repo, schedule, NOW, late, retry=True).code == "busy"
    attempt(repo, schedule, NOW, late)
    assert DeferredScheduleFire.query.count() == 0
    history = JobExecution.query.filter_by(status="skipped").one()
    assert history.scheduled_for is None and history.result_json["blocking_operation_id"] == blocker.id
    monkeypatch.setattr("shuffify.routes.require_auth", lambda *a, **kw: object())
    for path, key in [("/api/activity", "executions"), (f"/api/schedules/{schedule.id}/history", "history")]:
        response = auth_client.get(path)
        assert response.status_code == 200
        row, = response.json[key]
        assert row["status"] == "skipped"
        assert row["scheduled_skip"]["scheduled_for"] == NOW.isoformat()
        assert "using this playlist" in row["scheduled_skip"]["reason"]
        assert "blocking_operation_id" not in response.get_data(as_text=True)
    other = db_app.test_client()
    with other.session_transaction() as session:
        session["spotify_token"] = sample_token
        session["user_data"] = {"id": "scheduler-owner"}
    assert other.get("/api/activity").json["executions"] == []
    assert other.get(f"/api/schedules/{schedule.id}/history").status_code == 404


@pytest.mark.parametrize("change,code", [
    ("paused", "schedule_disabled"), ("edited", "eligibility_changed"), ("capacity", "outstanding_operation_limit"),
    ("attention", "needs_attention"), ("handoff", "leadership_lost"), ("released", None),
])
def test_retry_rechecks_current_policy_and_exact_grace_boundary(schedule, change, code):
    from shuffify.models.db import DeferredScheduleFire

    prepare(schedule)
    repo = OperationRepository()
    blocker = repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    assert attempt(repo, schedule, NOW, NOW).state == "deferred"
    repo.finish(repo.claim(operation_id=blocker.id), "needs_attention" if change == "attention" else "succeeded")
    if change == "paused":
        schedule.is_enabled = False
    if change == "edited":
        schedule.algorithm_params = {"keep_first": 1}
    db.session.commit()
    if change == "capacity":
        for n in range(5):
            repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=[f"other-{n}"], payload={})
    outcome = attempt(repo, schedule, NOW, NOW + timedelta(hours=1), retry=True, leader=change != "handoff")
    assert outcome.code == code
    assert DeferredScheduleFire.query.count() == int(change == "handoff")
    assert JobExecution.query.filter_by(kind="schedule_run").count() == int(change == "released")
    assert JobExecution.query.filter_by(status="skipped").count() == int(change in {"attention", "capacity"})


@pytest.mark.integration
def test_postgres_same_slot_contenders_share_deferral_and_one_queue_row(postgres_repository):
    from shuffify.models.db import DeferredScheduleFire
    from tests.services.operations.test_repository import race
    from tests.services.operations.test_worker_scheduler_postgres import make_schedule

    repo = postgres_repository
    schedule = make_schedule(repo)
    with Session(repo.engine) as storage, storage.begin():
        storage.get(type(schedule), schedule.id).definition_changed_at = schedule.created_at
    blocker = repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    assert [r.state for r in race(lambda _: attempt(repo, schedule, NOW, NOW))] == ["deferred", "deferred"]
    with Session(repo.engine) as storage:
        assert storage.scalars(select(DeferredScheduleFire)).one().blocking_operation_id == blocker.id
    repo.finish(repo.claim(operation_id=blocker.id), "succeeded")
    results = race(lambda _: attempt(repo, schedule, NOW, NOW))
    assert len({r.operation_id for r in results}) == 1 and all(r.state == "admitted" for r in results)
    with Session(repo.engine) as storage:
        assert storage.scalars(select(DeferredScheduleFire)).all() == []
        scheduled = storage.scalars(select(JobExecution).where(JobExecution.kind == "schedule_run")).one()
        assert scheduled.scheduled_for == NOW


@pytest.mark.parametrize("reason", ["fire_conflict", "invalid_schedule"])
def test_dropped_fire_records_one_history_row_without_taking_the_queue_key(schedule, reason):
    if reason == "invalid_schedule":
        schedule.target_playlist_id = ""
        db.session.commit()
    prepare(schedule)
    repo = OperationRepository()
    if reason == "fire_conflict":
        repo.enqueue(kind="different", user_id=schedule.user_id, schedule_id=schedule.id,
                     scheduled_for=NOW, targets=[], payload={})
    for offset in [timedelta(0), timedelta(hours=2) if reason == "fire_conflict" else timedelta(0)]:
        assert attempt(repo, schedule, NOW, NOW + offset).code == reason
    row = JobExecution.query.filter_by(status="skipped").one()
    assert row.result_json["schedule_skip_code"] == reason
    assert row.kind is None and row.scheduled_for is None and row.started_at == NOW


def test_executor_and_direct_misfire_share_owner_history_and_log_at_info(
    schedule, db_app, scheduler_clock, shuffify_logs,
):
    from apscheduler.events import EVENT_JOB_MISSED

    schedule_id, cadence = prepare(schedule)
    repo = OperationRepository()
    due = NOW - timedelta(hours=2)
    with worker(db_app, repo, scheduler_clock) as runtime:
        assert runtime.ready.wait(2)
        job = runtime._scheduler.get_job(f"schedule_{schedule_id}")
        executor = runtime._scheduler._executors["default"]
        event, = executor._run_due(job, [due])
        assert event.code == EVENT_JOB_MISSED
        assert runtime._fire(schedule_id, cadence, runtime._generation, scheduled_for=due).code == "misfire"
    row = JobExecution.query.one()
    assert row.result_json["schedule_skip_code"] == "misfire" and row.started_at == due
    logs = [record for record in shuffify_logs.records if "Scheduled fire skipped" in record.getMessage()]
    assert len(logs) == 2
    assert all(r.levelname == "INFO" and f"schedule_id={schedule_id}" in r.getMessage() and
               f"scheduled_for={due.isoformat()} code=misfire" in r.getMessage() for r in logs)


@pytest.mark.integration
def test_postgres_blocker_commit_wakes_leader_before_retry_timer(postgres_repository, db_app, scheduler_clock):
    from shuffify.services.operations.wake import PostgresWakeListener
    from tests.services.operations.test_worker_scheduler_postgres import make_schedule

    repo = postgres_repository
    schedule = make_schedule(repo)
    with Session(repo.engine) as storage, storage.begin():
        storage.get(type(schedule), schedule.id).definition_changed_at = schedule.created_at
    blocker = repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    admitted, deferred, registered = Event(), Event(), Event()

    def observe(name, _):
        if name == "deferred_busy":
            deferred.set()
        if name == "refreshes":
            registered.set()

    # Freeze the clock immediately before the deadline. Admission can only run
    # after the commit notification expedites the existing retry job.
    due = scheduler_clock[0] - timedelta(hours=1) + timedelta(milliseconds=1)
    with worker(db_app, repo, scheduler_clock, event_callback=observe, wake_callback=admitted.set) as runtime:
        assert runtime.ready.wait(3)
        registered.clear()
        with PostgresWakeListener(repo, lambda: None, schedule_callback=runtime.request_refresh,
                                  completion_callback=runtime.blockers_finished) as listener:
            assert listener.ready.wait(3) and registered.wait(3)
            outcome = runtime._fire(schedule.id, build_cadence(schedule).fingerprint, runtime._generation,
                                    scheduled_for=due)
            assert outcome.state == "deferred" and deferred.is_set()
            repo.finish(repo.claim(operation_id=blocker.id), "succeeded")
            assert admitted.wait(3)
    with Session(repo.engine) as storage:
        slots = storage.scalars(select(JobExecution.scheduled_for).where(JobExecution.kind == "schedule_run"))
        assert slots.one() == due


def test_sqlite_lost_wake_retries_before_four_second_grace_remainder(schedule, db_app, scheduler_clock):
    repo = OperationRepository()
    prepare(schedule)
    blocker = repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    due = NOW
    scheduler_clock[0] = due + timedelta(hours=1) - timedelta(seconds=4)
    admitted, waiting = Event(), Event()
    with worker(db_app, repo, scheduler_clock, wake_callback=admitted.set,
                event_callback=lambda name, _: waiting.set() if name == "deferred_busy" else None) as runtime:
        assert runtime.ready.wait(2) and waiting.wait(2)
        scheduler_clock[0] += timedelta(seconds=1)
        # A different SQLite worker has no in-process completion hint to send.
        other = OperationRepository()
        other.finish(other.claim(operation_id=blocker.id), "succeeded")
        scheduler_clock[0] += timedelta(seconds=1)
        runtime._scheduler.wakeup()
        assert admitted.wait(2)
    run = JobExecution.query.filter_by(kind="schedule_run").one()
    assert run.scheduled_for == due and JobExecution.query.filter_by(status="skipped").count() == 0


@pytest.mark.parametrize("executor_missed", [False, True])
def test_transient_history_write_recovers_without_admitting_expired_slot(
    schedule, db_app, scheduler_clock, monkeypatch, executor_missed,
):
    from contextlib import contextmanager

    from sqlalchemy.exc import OperationalError

    repo = OperationRepository()
    schedule_id, cadence = prepare(schedule)
    due = scheduler_clock[0] - timedelta(hours=2)
    recorded = Event()
    with worker(db_app, repo, scheduler_clock, event_callback=lambda name, _: recorded.set()
                if name == "skipped_misfire" else None) as runtime:
        assert runtime.ready.wait(2)
        job = runtime._scheduler.get_job(f"schedule_{schedule_id}")
        transaction = repo.scheduled_fire_transaction
        first = True

        @contextmanager
        def intermittent(*args):
            nonlocal first
            if first:
                first = False
                raise OperationalError("storage unavailable", {}, Exception())
            with transaction(*args) as storage:
                yield storage

        monkeypatch.setattr(repo, "scheduled_fire_transaction", intermittent)
        if executor_missed:
            runtime._scheduler._executors["default"]._run_due(job, [due])
        else:
            result = runtime._fire(schedule_id, cadence, runtime._generation, scheduled_for=due)
            assert result.code == "history_unavailable"
        assert runtime.metrics["history_retries"] == 1
        # Keep the regular daily registration in the future while its independent
        # history callback runs on the scheduler thread.
        runtime._scheduler.remove_job(job.id)
        scheduler_clock[0] += timedelta(seconds=5)
        runtime._scheduler.wakeup()
        assert recorded.wait(2)
    row = JobExecution.query.one()
    assert row.started_at == due and row.kind is None and row.result_json["schedule_skip_code"] == "misfire"


def test_capacity_skip_does_not_occupy_fire_key_on_readmission(schedule):
    prepare(schedule)
    repo = OperationRepository()
    active = [repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=[f"other-{n}"], payload={})
              for n in range(5)]
    assert attempt(repo, schedule, NOW, NOW).code == "outstanding_operation_limit"
    repo.finish(repo.claim(operation_id=active[0].id), "succeeded")
    admitted = attempt(repo, schedule, NOW, NOW)
    assert admitted.state == "admitted" and attempt(repo, schedule, NOW, NOW).operation_id == admitted.operation_id
    assert JobExecution.query.filter_by(status="skipped").one().scheduled_for is None
    assert JobExecution.query.filter_by(kind="schedule_run").one().scheduled_for == NOW


@pytest.mark.integration
def test_postgres_busy_capture_does_not_deadlock_with_routine_finalization(postgres_repository, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from tests.services.operations.test_worker_scheduler_postgres import make_schedule

    repo = postgres_repository
    schedule = make_schedule(repo)
    with Session(repo.engine) as storage, storage.begin():
        storage.get(type(schedule), schedule.id).definition_changed_at = schedule.created_at
    blocker = repo.enqueue(kind="schedule_run", user_id=schedule.user_id, targets=["target"], payload={},
                           schedule_id=schedule.id, scheduled_for=NOW - timedelta(days=1))
    lease = repo.claim(operation_id=blocker.id)
    execution_locked, schedule_locked = Event(), Event()
    find_blocker = repo._admission_blocker

    def signal_candidate(*args):
        match = find_blocker(*args)
        assert match.id == blocker.id
        schedule_locked.set()
        return match

    def finalize(storage):
        execution_locked.set()
        assert schedule_locked.wait(3)
        storage.get(type(schedule), schedule.id).last_run_at = NOW
        storage.flush()
        return {"status": "complete"}

    monkeypatch.setattr(repo, "_admission_blocker", signal_candidate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipt = pool.submit(repo.local_step, lease, "routine.finalize.succeeded.v1", finalize)
        assert execution_locked.wait(3)
        fire = pool.submit(attempt, repo, schedule, NOW, NOW)
        assert fire.result(timeout=5).state == "deferred"
        assert receipt.result(timeout=5) == {"status": "complete"}
    repo.finish(lease, "succeeded")
    with Session(repo.engine) as storage:
        assert storage.get(type(schedule), schedule.id).last_run_at == NOW


@pytest.mark.integration
def test_postgres_delete_waits_for_parent_before_removing_retry_or_history(
    postgres_repository, db_app, monkeypatch,
):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event

    from shuffify.models.db import DeferredScheduleFire
    from shuffify.services import scheduled_admission
    from shuffify.services.scheduler_service import SchedulerService
    from tests.services.operations.test_worker_scheduler_postgres import make_schedule

    repo = postgres_repository
    schedule = make_schedule(repo)
    with Session(repo.engine) as storage, storage.begin():
        storage.get(type(schedule), schedule.id).definition_changed_at = schedule.created_at
    blocker = repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    assert attempt(repo, schedule, NOW, NOW).state == "deferred"
    repo.finish(repo.claim(operation_id=blocker.id), "succeeded")
    db.session.remove()
    monkeypatch.setitem(db.engines, None, repo.engine)
    captured, deleting = Event(), Event()
    capture_schedule = scheduled_admission.capture_schedule

    def pause_capture(*args, **kwargs):
        captured.set()
        assert deleting.wait(4)
        return capture_schedule(*args, **kwargs)

    def observe_delete(_connection, _cursor, statement, *_):
        if "schedules" in statement and "FOR UPDATE" in statement:
            deleting.set()

    def delete():
        with db_app.app_context():
            SchedulerService.delete_schedule(schedule.id, schedule.user_id)

    monkeypatch.setattr(scheduled_admission, "capture_schedule", pause_capture)
    event.listen(repo.engine, "before_cursor_execute", observe_delete)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            retried = pool.submit(attempt, repo, schedule, NOW, NOW, retry=True)
            assert captured.wait(4)
            deleted = pool.submit(delete)
            assert retried.result(timeout=6).state == "admitted"
            deleted.result(timeout=6)
    finally:
        event.remove(repo.engine, "before_cursor_execute", observe_delete)
    with Session(repo.engine) as storage:
        assert storage.get(type(schedule), schedule.id) is None
        assert storage.scalars(select(DeferredScheduleFire)).all() == []
        saved_run = storage.scalars(select(JobExecution).where(JobExecution.kind == "schedule_run")).one()
        assert saved_run.schedule_id is None


def test_expired_retry_records_misfire_when_blocker_finished_inside_grace(schedule):
    prepare(schedule)
    repo = OperationRepository()
    blocker = repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    assert attempt(repo, schedule, NOW, NOW).state == "deferred"
    repo.finish(repo.claim(operation_id=blocker.id), "succeeded")
    db.session.get(JobExecution, blocker.id).completed_at = NOW + timedelta(minutes=2)
    db.session.commit()
    assert attempt(repo, schedule, NOW, NOW + timedelta(hours=2), retry=True).code == "misfire"
    assert JobExecution.query.filter_by(status="skipped").one().result_json["schedule_skip_code"] == "misfire"


def test_first_busy_attempt_crossing_deadline_keeps_observed_blocker(schedule, monkeypatch):
    from shuffify.services.schedule_fire_outcomes import process_scheduled_fire

    schedule_id, cadence = prepare(schedule)
    repo = OperationRepository()
    blocker = repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    now = [NOW + timedelta(hours=1)]
    find_blocker = repo._admission_blocker

    def cross_deadline(*args):
        candidate = find_blocker(*args)
        now[0] += timedelta(microseconds=1)
        return candidate

    monkeypatch.setattr(repo, "_admission_blocker", cross_deadline)
    result = process_scheduled_fire(repo, schedule_id, cadence, NOW, clock=lambda: now[0],
                                    is_leader=lambda: True, default_timezone="UTC")
    assert (result.state, result.code, result.blocking_operation_id) == ("skipped", "busy", blocker.id)
    assert JobExecution.query.filter_by(status="skipped").one().result_json["blocking_operation_id"] == blocker.id


@pytest.mark.integration
def test_leader_lost_while_expired_retry_waits_for_schedule_preserves_pending(postgres_repository):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event

    from shuffify.models.db import DeferredScheduleFire, Schedule
    from shuffify.services.schedule_fire_outcomes import process_scheduled_fire
    from tests.services.operations.test_worker_scheduler_postgres import make_schedule

    repo = postgres_repository
    schedule = make_schedule(repo)
    with Session(repo.engine) as storage, storage.begin():
        storage.get(Schedule, schedule.id).definition_changed_at = schedule.created_at
    repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    assert attempt(repo, schedule, NOW, NOW).state == "deferred"
    waiting, leader = Event(), Event()
    leader.set()

    def observe(_connection, _cursor, statement, *_):
        if "schedules" in statement and "FOR SHARE" in statement:
            waiting.set()

    event.listen(repo.engine, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(repo.engine) as holder, holder.begin():
                holder.scalar(select(Schedule.id).where(Schedule.id == schedule.id).with_for_update())
                retry = pool.submit(process_scheduled_fire, repo, schedule.id, build_cadence(schedule).fingerprint,
                                    NOW, clock=lambda: NOW + timedelta(hours=2), is_leader=leader.is_set,
                                    default_timezone="UTC", retry=True)
                assert waiting.wait(3)
                leader.clear()
            assert retry.result(timeout=3).code == "leadership_lost"
    finally:
        event.remove(repo.engine, "before_cursor_execute", observe)
    with Session(repo.engine) as storage:
        assert storage.get(DeferredScheduleFire, (schedule.id, NOW)) is not None
        assert storage.scalars(select(JobExecution.id).where(JobExecution.status == "skipped")).all() == []
    assert attempt(repo, schedule, NOW, NOW + timedelta(hours=2), retry=True).code == "busy"


def test_loss_of_leadership_during_busy_lookup_creates_no_deferral(schedule, monkeypatch):
    from shuffify.models.db import DeferredScheduleFire
    from shuffify.services.schedule_fire_outcomes import process_scheduled_fire

    schedule_id, cadence = prepare(schedule)
    repo = OperationRepository()
    repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    leader = Event()
    leader.set()
    find_blocker = repo._admission_blocker

    def lose_leader(*args):
        candidate = find_blocker(*args)
        leader.clear()
        return candidate

    monkeypatch.setattr(repo, "_admission_blocker", lose_leader)
    outcome = process_scheduled_fire(repo, schedule_id, cadence, NOW, clock=lambda: NOW,
                                     default_timezone="UTC", is_leader=leader.is_set)
    assert outcome.code == "leadership_lost" and DeferredScheduleFire.query.count() == 0


def test_expired_busy_slot_switches_to_one_history_only_job(schedule, db_app, scheduler_clock, monkeypatch):
    from sqlalchemy.exc import OperationalError

    repo = OperationRepository()
    schedule_id, cadence = prepare(schedule)
    blocker = repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    due = scheduler_clock[0] - timedelta(minutes=1)
    with worker(db_app, repo, scheduler_clock) as runtime:
        assert runtime.ready.wait(2)
        runtime._scheduler.pause()
        assert runtime._fire(schedule_id, cadence, runtime._generation, scheduled_for=due).state == "deferred"
        scheduler_clock[0] = due + timedelta(hours=2)

        def unavailable(*args):
            raise OperationalError("database unavailable", {}, Exception())

        with monkeypatch.context() as patch:
            patch.setattr(repo, "scheduled_fire_transaction", unavailable)
            outcome = runtime._fire(schedule_id, cadence, runtime._generation, due, scheduled_for=scheduler_clock[0])
            assert outcome.code == "history_unavailable"
        scheduler_clock[0] = due + timedelta(minutes=30)
        repo.finish(repo.claim(operation_id=blocker.id), "succeeded")
        retry, = [job for job in runtime._scheduler.get_jobs() if job.id.startswith("retry_")]
        assert retry.kwargs["history_only"] is True
        retry.func(*retry.args, scheduled_for=scheduler_clock[0], **retry.kwargs)
        assert not any(job.id.startswith("retry_") for job in runtime._scheduler.get_jobs())
    assert JobExecution.query.filter_by(kind="schedule_run").count() == 0
    assert JobExecution.query.filter_by(status="skipped").count() == 1


def test_leadership_loss_during_history_flush_rolls_back_and_preserves_waiter(schedule):
    from sqlalchemy import event

    from shuffify.models.db import DeferredScheduleFire
    from shuffify.services.schedule_fire_outcomes import process_scheduled_fire

    schedule_id, cadence = prepare(schedule)
    repo = OperationRepository()
    repo.enqueue(kind="shuffle", user_id=schedule.user_id, targets=["target"], payload={})
    assert attempt(repo, schedule, NOW, NOW).state == "deferred"
    leader = Event()
    leader.set()

    def on_insert(_connection, _cursor, statement, *_):
        if statement.startswith("INSERT INTO job_executions"):
            leader.clear()

    event.listen(repo.engine, "after_cursor_execute", on_insert)
    try:
        result = process_scheduled_fire(repo, schedule_id, cadence, NOW, clock=lambda: NOW + timedelta(hours=2),
                                        default_timezone="UTC", is_leader=leader.is_set, retry=True)
    finally:
        event.remove(repo.engine, "after_cursor_execute", on_insert)
    assert result.code == "leadership_lost" and DeferredScheduleFire.query.count() == 1
    assert JobExecution.query.filter_by(status="skipped").count() == 0
