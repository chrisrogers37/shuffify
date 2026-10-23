"""Memory scheduling has exact fire instants, retryable leadership and no idle table scans."""

import importlib
import threading
import time
from datetime import datetime, timedelta

import pytest
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import event

from shuffify.models.db import JobExecution, db
from tests.services.operations.test_scheduled_admission import NOW
from tests.services.operations.test_scheduled_admission import schedule as schedule


def module():
    return importlib.import_module("shuffify.services.worker_scheduler")


def controller(app, **kwargs):
    app.config["SCHEDULER_ENABLED"] = True
    return module().WorkerScheduler(app, clock=lambda: NOW, **kwargs)


def test_memory_scheduler_boot_and_snapshot_are_truthful_without_idle_table_poll(schedule, db_app):
    selects = []
    schedule_id = schedule.id

    def query(_connection, _cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT") and "schedules" in statement:
            selects.append(statement)

    event.listen(db.engine, "before_cursor_execute", query)
    runtime = controller(db_app)
    try:
        runtime.start()
        assert runtime.ready.wait(2)
        snapshot = runtime.registration_snapshot([schedule_id])
        assert snapshot["leader"] and snapshot["scheduler_running"]
        row = snapshot["registrations"][str(schedule_id)]
        assert row["next_run_at"] == (NOW + timedelta(days=1)).isoformat()
        assert row["effective_timezone"] == "UTC"
        assert runtime._scheduler._jobstores["default"].__class__.__name__ == "MemoryJobStore"
        count = len(selects)
        time.sleep(0.05)
        runtime.registration_snapshot([schedule_id])
        assert len(selects) == count == 1
    finally:
        runtime.close()
        event.remove(db.engine, "before_cursor_execute", query)
    assert not runtime.registration_snapshot([schedule_id])["leader"]


def test_refresh_preserves_unchanged_interval_and_tracks_definition_revision(schedule, db_app):
    from shuffify.services.schedule_utils import schedule_revision

    with controller(db_app) as runtime:
        assert runtime.ready.wait(2)
        job = runtime._scheduler.get_job(f"schedule_{schedule.id}")
        next_run = job.next_run_time
        schedule.algorithm_params = {"keep_first": 1}
        db.session.commit()
        runtime.request_refresh()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            actual = runtime.registration_snapshot([schedule.id])["registrations"][str(schedule.id)]
            if actual["installed_revision"] == schedule_revision(schedule):
                break
            time.sleep(0.005)
        assert runtime._scheduler.get_job(job.id) is job
        assert job.next_run_time == next_run
        assert actual["installed_revision"] == schedule_revision(schedule)
        schedule.is_enabled = False
        db.session.commit()
        runtime.request_refresh()
        deadline = time.monotonic() + 2
        while runtime._scheduler.get_job(job.id) is not None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert runtime.registration_snapshot([schedule.id])["registrations"][str(schedule.id)] is None


def test_due_executor_receives_exact_times_coalesces_and_respects_grace():
    received, events, finished = [], [], threading.Event()
    executor = module().DurableAdmissionExecutor(clock=lambda: NOW)
    scheduler = BackgroundScheduler(executors={"default": executor}, timezone="UTC")
    scheduler.add_listener(lambda event: (events.append(event), finished.set()), EVENT_JOB_EXECUTED | EVENT_JOB_MISSED)
    scheduler.start(paused=True)
    try:
        job = scheduler.add_job(
            lambda **kwargs: received.append(kwargs["scheduled_for"]),
            "date",
            run_date=NOW + timedelta(days=1),
            coalesce=True,
            misfire_grace_time=3600,
        )
        executor.submit_job(job, [NOW - timedelta(minutes=30), NOW - timedelta(minutes=1)])
        assert finished.wait(2)
        assert received == [NOW - timedelta(minutes=1)]
        assert events[0].scheduled_run_time == received[0]
        finished.clear()
        job.modify(coalesce=False)
        executor.submit_job(job, [NOW - timedelta(seconds=3601), NOW - timedelta(seconds=3600)])
        deadline = time.monotonic() + 2
        while len(events) < 3 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert events[1].code == EVENT_JOB_MISSED
        assert received[-1] == NOW - timedelta(seconds=3600)
    finally:
        scheduler.shutdown(wait=True)


class Connection:
    def __init__(self, acquired=True):
        self.acquired, self.autocommit, self.closed, self.lost = acquired, False, False, False
        self.statements = []

    def cursor(self):
        connection = self

        class Cursor:
            def execute(self, statement):
                connection.statements.append(statement)
                if connection.lost:
                    raise OSError("private-password")

            def fetchone(self):
                return (connection.acquired,)

            def close(self):
                pass

        return Cursor()

    def close(self):
        self.closed = True


def test_leader_retries_held_and_failed_connections_and_old_generation_cannot_admit(schedule, db_app, caplog):
    first, leader, replacement = Connection(False), Connection(), Connection()
    calls = []

    def connect():
        calls.append(True)
        if len(calls) == 1:
            return first
        if len(calls) == 2:
            raise OSError("password=private-password")
        return leader if len(calls) == 3 else replacement

    with controller(
        db_app, connection_factory=connect, retry_initial=0.005, retry_max=0.01, leader_check_interval=0.01
    ) as runtime:
        assert runtime.ready.wait(2)
        old = runtime.registration_snapshot([schedule.id])
        leader.lost = True
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = runtime.registration_snapshot([schedule.id])
            if current["leader"] and current["generation"] != old["generation"]:
                break
            time.sleep(0.005)
        assert current["generation"] != old["generation"]
        value = runtime._fire(
            schedule.id,
            old["registrations"][str(schedule.id)]["cadence_fingerprint"],
            old["generation"],
            scheduled_for=NOW,
        )
        assert value.code == "leadership_lost"
        assert JobExecution.query.count() == 0
    assert first.closed and leader.closed and replacement.closed
    assert "private-password" not in caplog.text


def test_shutdown_interrupts_lock_retry_without_starting_scheduler(schedule, db_app):
    attempted = threading.Event()

    def held():
        attempted.set()
        return Connection(False)

    runtime = controller(db_app, connection_factory=held, retry_initial=10, retry_max=10)
    runtime.start()
    assert attempted.wait(1)
    before = time.monotonic()
    runtime.close()
    assert time.monotonic() - before < 0.5
    assert runtime._scheduler is None


def test_transient_admission_retries_keep_due_identity_and_stop_at_grace(schedule, db_app):
    from sqlalchemy.exc import OperationalError

    clock = [NOW]
    calls = []

    def unavailable(repository, schedule_id, cadence, due, **kwargs):
        calls.append(due)
        clock[0] += timedelta(seconds=1801)
        raise OperationalError("private SQL", {}, Exception("private password"))

    app = db_app
    app.config["SCHEDULER_ENABLED"] = True
    with module().WorkerScheduler(
        app, clock=lambda: clock[0], admission_callback=unavailable, retry_initial=0.001, retry_max=0.001
    ) as runtime:
        assert runtime.ready.wait(2)
        snap = runtime.registration_snapshot([schedule.id])
        result = runtime._fire(
            schedule.id,
            snap["registrations"][str(schedule.id)]["cadence_fingerprint"],
            snap["generation"],
            scheduled_for=NOW,
        )
        assert result.code == "misfire"
        assert calls == [NOW, NOW]
        assert runtime.metrics["admission_retries"] == 2
        assert runtime.metrics["missed"] == 1
        assert JobExecution.query.count() == 0


@pytest.mark.parametrize("invalidated_interface", [False, True])
def test_transient_success_keeps_exact_fire_and_wakes_queue_once(schedule, db_app, invalidated_interface):
    from sqlalchemy.exc import InterfaceError, OperationalError

    from shuffify.services.scheduled_admission import AdmissionResult

    calls, wakes = [], []

    def admit(repository, schedule_id, cadence, due, **kwargs):
        calls.append(due)
        if len(calls) == 1:
            if invalidated_interface:
                raise InterfaceError("private", {}, Exception("private"), connection_invalidated=True)
            raise OperationalError("private", {}, Exception("private"))
        return AdmissionResult("admitted", due, operation_id=77)

    with controller(
        db_app, admission_callback=admit, wake_callback=lambda: wakes.append(True), retry_initial=0.001, retry_max=0.001
    ) as runtime:
        assert runtime.ready.wait(2)
        snap = runtime.registration_snapshot([schedule.id])
        due = NOW - timedelta(seconds=12, microseconds=12345)
        result = runtime._fire(
            schedule.id,
            snap["registrations"][str(schedule.id)]["cadence_fingerprint"],
            snap["generation"],
            scheduled_for=due,
        )
        assert result.operation_id == 77
        assert calls == [due, due] and wakes == [True]


def test_stop_drains_an_inflight_local_admission_without_submitting_more_work(schedule, db_app):
    from shuffify.services.scheduled_admission import AdmissionResult

    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def admit(repository, schedule_id, cadence, due, **kwargs):
        entered.set()
        assert release.wait(2)
        return AdmissionResult("admitted", due, operation_id=77)

    runtime = controller(db_app, admission_callback=admit)
    runtime.start()
    assert runtime.ready.wait(2)
    snap = runtime.registration_snapshot([schedule.id])
    job = runtime._scheduler.get_job(f"schedule_{schedule.id}")
    executor = runtime._scheduler._executors["default"]
    executor.submit_job(job, [NOW])
    assert entered.wait(2)
    closing = threading.Thread(target=lambda: (runtime.close(), closed.set()))
    closing.start()
    assert not closed.wait(0.03)
    assert (
        runtime._fire(
            schedule.id,
            snap["registrations"][str(schedule.id)]["cadence_fingerprint"],
            snap["generation"],
            scheduled_for=NOW,
        ).code
        == "leadership_lost"
    )
    release.set()
    assert closed.wait(2)
    closing.join()


def test_disabled_scheduler_never_starts_or_touches_database(db_app, monkeypatch):
    db_app.config["SCHEDULER_ENABLED"] = False
    runtime = module().WorkerScheduler(db_app)
    monkeypatch.setattr(module(), "Session", lambda *args, **kwargs: pytest.fail("Disabled scheduler queried"))
    with runtime:
        assert not runtime.ready.is_set()
        snapshot = runtime.registration_snapshot([1])
        assert snapshot["leader"] is snapshot["scheduler_running"] is False
        assert snapshot["registrations"] == {"1": None}


@pytest.mark.parametrize("ids", [[True], [-1], [0], ["1"], list(range(1, 5002))])
def test_memory_snapshot_rejects_invalid_or_unbounded_id_requests(db_app, ids):
    runtime = controller(db_app)
    with pytest.raises(ValueError):
        runtime.registration_snapshot(ids)
    runtime.close()


def test_snapshot_never_reports_a_paused_scheduler_or_tampered_trigger_as_registered(schedule, db_app):
    from apscheduler.triggers.interval import IntervalTrigger

    with controller(db_app) as runtime:
        assert runtime.ready.wait(2)
        job = runtime._scheduler.get_job(f"schedule_{schedule.id}")
        job.modify(trigger=IntervalTrigger(days=12, timezone="UTC"))
        row = runtime.registration_snapshot([schedule.id])["registrations"][str(schedule.id)]
        assert row["installed_revision"] is None
        assert row["cadence_fingerprint"] is None
        assert row["next_run_at"] is None
        runtime._scheduler.pause()
        assert runtime.registration_snapshot([schedule.id])["scheduler_running"] is False


def test_failed_boot_refresh_retries_without_running_jobs_then_becomes_ready(schedule, db_app, monkeypatch):
    session = module().Session
    calls = []

    def intermittent(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("private-dsn")
        return session(*args, **kwargs)

    monkeypatch.setattr(module(), "Session", intermittent)
    with controller(db_app, retry_initial=0.005, retry_max=0.01) as runtime:
        assert runtime.ready.wait(2)
        assert len(calls) == 2
        assert runtime.metrics["refresh_failures"] == 1
        assert runtime.registration_snapshot([schedule.id])["scheduler_running"]


def test_one_invalid_timezone_does_not_block_other_enabled_or_disabled_rows(schedule, db_app):
    from shuffify.models.db import Schedule

    invalid = Schedule(
        user_id=schedule.user_id,
        job_type="shuffle",
        target_playlist_id="invalid",
        algorithm_name="BasicShuffle",
        timezone="Not/A/RealZone",
        schedule_type="cron",
        schedule_value="0 1 * * *",
        created_at=NOW,
    )
    paused = Schedule(
        user_id=schedule.user_id,
        job_type="shuffle",
        target_playlist_id="paused",
        algorithm_name="BasicShuffle",
        timezone="UTC",
        is_enabled=False,
        created_at=NOW,
    )
    db.session.add_all([invalid, paused])
    db.session.commit()
    ids = [schedule.id, invalid.id, paused.id]
    with controller(db_app) as runtime:
        assert runtime.ready.wait(2)
        rows = runtime.registration_snapshot(ids)["registrations"]
        assert rows[str(schedule.id)] is not None
        assert rows[str(invalid.id)] is rows[str(paused.id)] is None
        assert runtime.metrics["invalid_schedules"] == 1


def test_wake_callback_failure_does_not_invalidate_committed_admission(schedule, db_app):
    from shuffify.services.scheduled_admission import AdmissionResult

    def unavailable():
        raise RuntimeError("private callback detail")

    with controller(
        db_app,
        wake_callback=unavailable,
        admission_callback=lambda *args, **kwargs: AdmissionResult(
            "admitted", kwargs.get("scheduled_for", NOW), operation_id=77
        ),
    ) as runtime:
        assert runtime.ready.wait(2)
        snap = runtime.registration_snapshot([schedule.id])
        result = runtime._fire(
            schedule.id,
            snap["registrations"][str(schedule.id)]["cadence_fingerprint"],
            snap["generation"],
            scheduled_for=NOW,
        )
        assert result.state == "admitted" and result.operation_id == 77
        assert runtime.metrics["wake_failures"] == 1


def test_a_fire_refused_for_an_unresolved_needs_attention_run_is_reported_like_a_busy_fire(schedule, db_app):
    from shuffify.services.scheduled_admission import AdmissionResult

    events = []
    with controller(
        db_app,
        event_callback=lambda name, value: events.append((name, value)),
        admission_callback=lambda *args, **kwargs: AdmissionResult(
            "skipped", NOW, code="needs_attention", blocking_operation_id=41
        ),
    ) as runtime:
        assert runtime.ready.wait(2)
        snap = runtime.registration_snapshot([schedule.id])
        result = runtime._fire(
            schedule.id,
            snap["registrations"][str(schedule.id)]["cadence_fingerprint"],
            snap["generation"],
            scheduled_for=NOW,
        )
    assert result.code == "needs_attention"
    assert runtime.metrics["skipped_needs_attention"] == 1
    assert ("skipped_needs_attention", result.to_dict()) in events
    assert result.to_dict()["blocking_operation_id"] == 41


def test_boot_is_future_only_and_never_instantiates_a_persistent_jobstore(schedule, db_app, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Persistent/pickled jobstore is forbidden")

    monkeypatch.setattr("apscheduler.jobstores.sqlalchemy.SQLAlchemyJobStore.__init__", forbidden)
    with controller(db_app) as runtime:
        assert runtime.ready.wait(2)
        snapshot = runtime.registration_snapshot([schedule.id])
        next_run = datetime.fromisoformat(snapshot["registrations"][str(schedule.id)]["next_run_at"])
        assert next_run > NOW
        assert JobExecution.query.count() == 0


@pytest.mark.parametrize("pool_size", [2, 10])
def test_worker_uses_configured_admission_pool(schedule, db_app, pool_size):
    db_app.config["SCHEDULER_THREAD_POOL_SIZE"] = pool_size
    with controller(db_app) as runtime:
        assert runtime.ready.wait(2)
        assert runtime._scheduler._executors["default"]._pool._max_workers == pool_size


def test_admission_failure_event_is_counted_without_logging_private_error(schedule, db_app, caplog):
    def rejected(*args, **kwargs):
        raise RuntimeError("private-provider-secret")

    with controller(db_app, admission_callback=rejected) as runtime:
        assert runtime.ready.wait(2)
        job = runtime._scheduler.get_job(f"schedule_{schedule.id}")
        runtime._scheduler._executors["default"].submit_job(job, [NOW])
        deadline = time.monotonic() + 2
        while runtime.metrics.get("admission_errors") != 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert runtime.metrics["admission_errors"] == 1
    assert "private-provider-secret" not in caplog.text
