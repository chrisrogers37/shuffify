"""Schedule catch-up remains eligible after execution bookkeeping advances."""

import importlib
from datetime import datetime, timedelta
from threading import Event

from shuffify.models.db import db
from shuffify.services.operations.repository import OperationRepository
from tests.services.operations.test_scheduled_admission import NOW
from tests.services.operations.test_scheduled_admission import schedule as schedule
from tests.services.operations.test_scheduler_recovery import executions, runtime, wait_for_fire
from tests.services.operations.test_scheduler_recovery import scheduler_clock as scheduler_clock


def test_existing_schedule_bookkeeping_does_not_hide_a_missed_restart_fire(
    schedule, db_app, scheduler_clock, monkeypatch
):
    class BookkeepingDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return scheduler_clock[0].astimezone(tz)

    monkeypatch.setattr(importlib.import_module("shuffify.models.db"), "datetime", BookkeepingDateTime)
    schedule.definition_changed_at = None
    schedule.updated_at = schedule.created_at
    db.session.commit()
    repository = OperationRepository()
    with runtime(db_app, repository, scheduler_clock) as original:
        assert original.ready.wait(2)
        assert executions(repository) == []

    scheduler_clock[0] = NOW + timedelta(minutes=1)
    schedule.last_run_at = scheduler_clock[0]
    schedule.last_status = "success"
    db.session.commit()
    assert schedule.updated_at == scheduler_clock[0]
    assert schedule.definition_changed_at is None

    scheduler_clock[0] = NOW + timedelta(minutes=5)
    admitted = Event()
    with runtime(db_app, repository, scheduler_clock, admitted=admitted) as replacement:
        assert replacement.ready.wait(2)
        assert [row.scheduled_for for row in wait_for_fire(repository, admitted)] == [NOW]


def test_upgrade_backfills_a_legacy_schedule_even_after_bookkeeping_passed_the_due_time(
    schedule, db_app, scheduler_clock
):
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.orm import Session

    from shuffify.models.db import Schedule

    migration_path = (
        Path(__file__).resolve().parents[3] / "migrations/versions/c9d0e1f2a3b4_schedule_recovery_start.py"
    )
    assert migration_path.is_file(), "The stable recovery floor requires an upgrade for existing rows"
    spec = importlib.util.spec_from_file_location("schedule_recovery_start", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schedule_id, created = schedule.id, schedule.created_at
    db.session.remove()
    with db.engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE schedules DROP COLUMN recovery_start_at")
        connection.execute(sa.text(
            "UPDATE schedules SET definition_changed_at = NULL, updated_at = :bookkeeping, "
            "last_run_at = :bookkeeping, last_status = 'success' WHERE id = :id"
        ), {"bookkeeping": NOW + timedelta(minutes=1), "id": schedule_id})
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    with Session(db.engine) as storage:
        backfilled = storage.get(Schedule, schedule_id)
        assert backfilled.recovery_start_at == created
        assert backfilled.definition_changed_at is None
        assert backfilled.updated_at > NOW

    scheduler_clock[0] = NOW + timedelta(minutes=5)
    repository = OperationRepository()
    admitted = Event()
    with runtime(db_app, repository, scheduler_clock, admitted=admitted) as replacement:
        assert replacement.ready.wait(2)
        rows = wait_for_fire(repository, admitted)
        assert [row.scheduled_for for row in rows] == [NOW]
        operation_id = rows[0].id
    with runtime(db_app, repository, scheduler_clock) as restarted:
        assert restarted.ready.wait(2)
        assert [(row.id, row.scheduled_for) for row in executions(repository)] == [(operation_id, NOW)]
