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
    for _ in range(2):
        assert attempt(repo, schedule, NOW, NOW).code == reason
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
        with PostgresWakeListener(repo, lambda: None, schedule_callback=runtime.request_refresh) as listener:
            assert listener.ready.wait(3) and registered.wait(3)
            outcome = runtime._fire(schedule.id, build_cadence(schedule).fingerprint, runtime._generation,
                                    scheduled_for=due)
            assert outcome.state == "deferred" and deferred.is_set()
            repo.finish(repo.claim(operation_id=blocker.id), "succeeded")
            assert admitted.wait(3)
    with Session(repo.engine) as storage:
        slots = storage.scalars(select(JobExecution.scheduled_for).where(JobExecution.kind == "schedule_run"))
        assert slots.one() == due
