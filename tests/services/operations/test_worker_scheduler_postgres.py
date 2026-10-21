"""Real PostgreSQL fire uniqueness and scheduler session leadership handoff."""

import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from shuffify.models.db import JobExecution, Schedule, User
from shuffify.services.scheduled_admission import admit_scheduled, build_cadence
from shuffify.services.worker_scheduler import WorkerScheduler
from tests.services.operations.test_repository import postgres_repository as postgres_repository
from tests.services.operations.test_repository import race
from tests.services.operations.test_scheduled_admission import NOW


def make_schedule(repository):
    with Session(repository.engine, expire_on_commit=False) as storage, storage.begin():
        user = User(spotify_id="scheduled-pg-owner")
        storage.add(user)
        storage.flush()
        schedule = Schedule(
            user_id=user.id,
            job_type="shuffle",
            target_playlist_id="target",
            algorithm_name="BasicShuffle",
            schedule_type="interval",
            schedule_value="daily",
            timezone="UTC",
            created_at=NOW - timedelta(days=2),
        )
        storage.add(schedule)
        storage.flush()
        storage.expunge(schedule)
        return schedule


@pytest.mark.integration
def test_real_postgres_same_fire_race_and_replay_before_changed_configuration(postgres_repository):
    repository = postgres_repository
    schedule = make_schedule(repository)
    cadence = build_cadence(schedule).fingerprint
    results = race(lambda _: admit_scheduled(repository, schedule.id, cadence, NOW))
    assert results[0].operation_id == results[1].operation_id
    assert all(result.state == "admitted" for result in results)
    with Session(repository.engine) as storage, storage.begin():
        current = storage.get(Schedule, schedule.id)
        current.is_enabled = False
        current.target_playlist_id = "changed-target"
    replay = admit_scheduled(repository, schedule.id, cadence, NOW)
    assert replay.operation_id == results[0].operation_id
    with Session(repository.engine) as storage:
        assert storage.scalar(select(func.count()).select_from(JobExecution)) == 1
        assert storage.get(JobExecution, replay.operation_id).targets == ["target"]


@pytest.mark.integration
def test_real_postgres_contenders_retry_and_only_one_memory_scheduler_runs(postgres_repository, db_app):
    repository = postgres_repository
    schedule = make_schedule(repository)
    db_app.config["SCHEDULER_ENABLED"] = True
    options = dict(
        repository=repository, clock=lambda: NOW, retry_initial=0.01, retry_max=0.02, leader_check_interval=0.02
    )
    first, second = WorkerScheduler(db_app, **options), WorkerScheduler(db_app, **options)
    first.start()
    assert first.ready.wait(3)
    second.start()
    try:
        assert not second.ready.wait(0.08)
        assert first.registration_snapshot([schedule.id])["leader"]
        assert not second.registration_snapshot([schedule.id])["leader"]
        first.close()
        assert second.ready.wait(3)
        assert second.registration_snapshot([schedule.id])["registrations"][str(schedule.id)]["next_run_at"]
    finally:
        first.close()
        second.close()


@pytest.mark.integration
def test_real_postgres_killed_leader_session_reloads_under_new_generation(postgres_repository, db_app):
    repository = postgres_repository
    schedule = make_schedule(repository)
    db_app.config["SCHEDULER_ENABLED"] = True
    with WorkerScheduler(
        db_app, repository, clock=lambda: NOW, retry_initial=0.01, retry_max=0.02, leader_check_interval=0.01
    ) as runtime:
        assert runtime.ready.wait(3)
        old = runtime.registration_snapshot([schedule.id])
        with runtime._connection_lock:
            pid = runtime._driver(runtime._connection).get_backend_pid()
        with repository.engine.begin() as admin:
            assert admin.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = runtime.registration_snapshot([schedule.id])
            if current["leader"] and current["scheduler_running"] and current["generation"] != old["generation"]:
                break
            time.sleep(0.005)
        assert current["generation"] != old["generation"]
        assert current["scheduler_running"]
        assert runtime.metrics["leadership_losses"] == 1
        assert (
            runtime._fire(
                schedule.id,
                old["registrations"][str(schedule.id)]["cadence_fingerprint"],
                old["generation"],
                scheduled_for=NOW,
            ).code
            == "leadership_lost"
        )
        with Session(repository.engine) as storage:
            assert storage.scalar(select(func.count()).select_from(JobExecution)) == 0
