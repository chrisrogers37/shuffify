"""Lost private pokes and LISTEN disconnects recover through committed PG state."""

import threading
import time

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from shuffify.models.db import Schedule
from shuffify.services.operations.wake import PostgresWakeListener
from shuffify.services.worker_scheduler import WorkerScheduler
from tests.services.operations.test_repository import postgres_repository as postgres_repository
from tests.services.operations.test_scheduled_admission import NOW
from tests.services.operations.test_worker_scheduler_postgres import make_schedule


def eventually(condition):
    limit = time.monotonic() + 4
    while time.monotonic() < limit:
        if condition():
            return
        time.sleep(0.005)
    assert condition()


@pytest.mark.integration
def test_schedule_notify_is_commit_only_and_bookkeeping_never_refreshes(postgres_repository, db_app, monkeypatch):
    import shuffify.services.schedule_notifications as notifications

    monkeypatch.setattr(notifications, "poke", lambda app: False)
    schedule = make_schedule(postgres_repository)
    refreshed = threading.Event()
    with PostgresWakeListener(postgres_repository, lambda: None, schedule_callback=refreshed.set) as listener:
        assert refreshed.wait(3) and listener.ready.is_set()
        refreshed.clear()
        with Session(postgres_repository.engine) as storage:
            row = storage.get(Schedule, schedule.id)
            row.is_enabled = False
            storage.flush()
            assert not refreshed.wait(0.05)
            storage.rollback()
        assert not refreshed.wait(0.05)
        with Session(postgres_repository.engine) as storage, storage.begin():
            row = storage.get(Schedule, schedule.id)
            row.last_status = "success"
        assert not refreshed.wait(0.05)
        with Session(postgres_repository.engine) as storage, storage.begin():
            storage.get(Schedule, schedule.id).is_enabled = False
        assert refreshed.wait(3)


@pytest.mark.integration
def test_lost_poke_and_notification_gap_refresh_after_listen_reconnect_without_idle_scan(
    postgres_repository,
    db_app,
    monkeypatch,
):
    import shuffify.services.schedule_notifications as notifications

    monkeypatch.setattr(notifications, "poke", lambda app: False)
    schedule = make_schedule(postgres_repository)
    db_app.config["SCHEDULER_ENABLED"] = True
    scans, calls = [], []
    reconnecting, allow_reconnect = threading.Event(), threading.Event()

    def connect():
        calls.append(True)
        if len(calls) > 1:
            reconnecting.set()
            assert allow_reconnect.wait(3)
        return postgres_repository.engine.raw_connection()

    def query(_connection, _cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT") and "schedules" in statement:
            scans.append(statement)

    event.listen(postgres_repository.engine, "before_cursor_execute", query)
    try:
        with WorkerScheduler(db_app, postgres_repository, clock=lambda: NOW) as controller:
            assert controller.ready.wait(3)
            with PostgresWakeListener(
                postgres_repository,
                lambda: None,
                schedule_callback=controller.request_refresh,
                connection_factory=connect,
                reconnect_delay=0.005,
            ) as listener:
                assert listener.ready.wait(3)
                eventually(lambda: controller._requested == controller._applied)
                baseline = len(scans)
                controller.registration_snapshot([schedule.id])
                time.sleep(0.05)
                assert len(scans) == baseline
                with postgres_repository.engine.begin() as admin:
                    assert admin.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": listener.backend_pid})
                assert reconnecting.wait(3)
                with Session(postgres_repository.engine) as storage, storage.begin():
                    storage.get(Schedule, schedule.id).is_enabled = False
                assert controller.registration_snapshot([schedule.id])["registrations"][str(schedule.id)] is not None
                allow_reconnect.set()
                eventually(lambda: len(calls) > 1 and listener.ready.is_set())
                eventually(
                    lambda: controller.registration_snapshot([schedule.id])["registrations"][str(schedule.id)] is None
                )
                baseline = len(scans)
                time.sleep(0.05)
                assert len(scans) == baseline
    finally:
        allow_reconnect.set()
        event.remove(postgres_repository.engine, "before_cursor_execute", query)


@pytest.mark.integration
def test_real_memory_fire_enqueues_exact_due_without_auth_or_legacy_execution(
    postgres_repository,
    db_app,
    monkeypatch,
):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from shuffify.models.db import JobExecution

    def forbidden(*args, **kwargs):
        raise AssertionError("Scheduled fire must only admit a command")

    monkeypatch.setattr("shuffify.spotify.api.SpotifyAPI.__init__", forbidden)
    monkeypatch.setattr("shuffify.services.worker_control.WorkerControlClient.poke", lambda self: False)
    schedule = make_schedule(postgres_repository)
    due = datetime.now(timezone.utc) + timedelta(milliseconds=100)
    db_app.config["SCHEDULER_ENABLED"] = True
    with WorkerScheduler(db_app, postgres_repository) as controller:
        assert controller.ready.wait(3)
        controller._scheduler.modify_job(f"schedule_{schedule.id}", next_run_time=due)

        def accepted():
            with Session(postgres_repository.engine) as storage:
                return storage.scalar(select(JobExecution.id)) is not None

        eventually(accepted)
        with Session(postgres_repository.engine) as storage:
            rows = storage.scalars(select(JobExecution)).all()
            assert len(rows) == 1
            assert rows[0].scheduled_for == due
            assert rows[0].state == "queued" and rows[0].kind == "schedule_run"
            assert rows[0].payload["command"]["triggered_by"] == "scheduler"
