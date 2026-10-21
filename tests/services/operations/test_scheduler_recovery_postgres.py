"""PostgreSQL leadership replacement preserves one exact missed-fire receipt."""

import time
from datetime import timedelta
from threading import Event

import pytest
from sqlalchemy.orm import Session

from shuffify.models.db import Schedule, User
from shuffify.services.worker_scheduler import WorkerScheduler
from tests.services.operations.test_repository import postgres_repository as postgres_repository
from tests.services.operations.test_scheduled_admission import NOW
from tests.services.operations.test_scheduler_recovery import executions, wait_for_fire
from tests.services.operations.test_scheduler_recovery import scheduler_clock as scheduler_clock


@pytest.mark.integration
def test_postgres_leader_replacement_recovers_due_once_and_future_restarts_deduplicate(
    postgres_repository, db_app, scheduler_clock
):
    repository = postgres_repository
    with Session(repository.engine, expire_on_commit=False) as storage, storage.begin():
        owner = User(spotify_id="recovering-schedule-owner")
        storage.add(owner)
        storage.flush()
        schedule = Schedule(
            user_id=owner.id, job_type="shuffle", target_playlist_id="target", algorithm_name="BasicShuffle",
            schedule_type="interval", schedule_value="daily", timezone="UTC", created_at=NOW - timedelta(days=2),
        )
        storage.add(schedule)
        storage.flush()
        assert schedule.definition_changed_at == scheduler_clock[0]
        schedule_id = schedule.id
    db_app.config["SCHEDULER_ENABLED"] = True
    options = dict(
        clock=lambda: scheduler_clock[0], retry_initial=0.005, retry_max=0.01, leader_check_interval=0.01,
    )
    admitted = Event()

    def on_event(name, _value):
        if name == "admitted":
            admitted.set()

    first = WorkerScheduler(db_app, repository, **options)
    second = WorkerScheduler(db_app, repository, event_callback=on_event, **options)
    try:
        first.start()
        assert first.ready.wait(3)
        assert executions(repository) == []
        # Stop admissions before moving time across the due instant. The second
        # process cannot install its memory job until the first releases its lock.
        first.request_stop()
        scheduler_clock[0] = NOW + timedelta(minutes=5)
        second.start()
        first.close()
        assert second.ready.wait(3)
        rows = wait_for_fire(repository, admitted)
        assert len(rows) == 1
        operation_id = rows[0].id
        assert rows[0].scheduled_for == NOW
        assert rows[0].schedule_id == schedule_id
    finally:
        first.close()
        second.close()
    with WorkerScheduler(db_app, repository, **options) as restarted:
        assert restarted.ready.wait(3)
        time.sleep(0.03)
        assert [(row.id, row.scheduled_for) for row in executions(repository)] == [(operation_id, NOW)]
