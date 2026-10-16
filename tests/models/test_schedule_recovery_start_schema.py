"""A stable catch-up floor survives migration and run bookkeeping."""

import importlib.util
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.orm import Session

from shuffify.models.db import Schedule, UTCDateTime, db
from tests.services.operations.test_repository import postgres_url as postgres_url
from tests.services.operations.test_scheduled_admission import NOW
from tests.services.operations.test_scheduled_admission import schedule as schedule

ROOT = Path(__file__).resolve().parents[2]
REVISION = "c9d0e1f2a3b4"
MIGRATION = ROOT / "migrations/versions" / f"{REVISION}_schedule_recovery_start.py"


def migration():
    assert MIGRATION.is_file(), "Existing schedules need a stable-floor backfill migration"
    spec = importlib.util.spec_from_file_location("schedule_recovery_start", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_revision_extends_the_deployed_queue_head_without_rewriting_it():
    assert MIGRATION.is_file()
    script = ScriptDirectory(str(ROOT / "migrations"))
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == "b8c9d0e1f2a3"


def test_model_recovery_start_has_no_server_default_and_is_separate_from_bookkeeping(schedule):
    column = Schedule.__table__.c.recovery_start_at
    assert isinstance(column.type, UTCDateTime)
    assert column.nullable and column.server_default is None
    assert schedule.recovery_start_at == schedule.created_at
    initial = schedule.recovery_start_at
    schedule.last_run_at = NOW
    schedule.last_status = "success"
    db.session.commit()
    assert schedule.recovery_start_at == initial


def test_bulk_creation_anchors_each_recovery_start_to_its_own_creation_instant(schedule):
    values = [
        {"user_id": schedule.user_id, "job_type": "shuffle", "target_playlist_id": f"target-{index}",
         "created_at": NOW - timedelta(days=index + 2)}
        for index in range(2)
    ]
    with Session(db.engine) as storage, storage.begin():
        storage.execute(sa.insert(Schedule), values)
    with Session(db.engine) as storage:
        rows = storage.scalars(sa.select(Schedule).where(Schedule.id != schedule.id)).all()
        assert len(rows) == 2
        assert all(row.recovery_start_at == row.created_at for row in rows)
        assert rows[0].recovery_start_at != rows[1].recovery_start_at


def exercise_backfill_and_downgrade(connection):
    table = sa.Table(
        "schedules", sa.MetaData(), sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("definition_changed_at", UTCDateTime(), nullable=True),
    )
    table.create(connection)
    rows = [
        {"id": 1, "created_at": NOW - timedelta(days=2), "updated_at": NOW + timedelta(minutes=1),
         "definition_changed_at": None},
        {"id": 2, "created_at": NOW - timedelta(days=2), "updated_at": NOW + timedelta(minutes=2),
         "definition_changed_at": NOW + timedelta(minutes=1)},
        {"id": 3, "created_at": NOW - timedelta(days=2), "updated_at": NOW + timedelta(minutes=3),
         "definition_changed_at": NOW - timedelta(minutes=10)},
    ]
    connection.execute(table.insert(), rows)
    with Operations.context(MigrationContext.configure(connection)):
        migration().upgrade()
    reflected = {column["name"]: column for column in sa.inspect(connection).get_columns("schedules")}
    assert reflected["recovery_start_at"]["nullable"] is True
    assert reflected["recovery_start_at"]["default"] is None
    recovery_start = sa.column("recovery_start_at", UTCDateTime())
    saved = connection.execute(sa.select(table, recovery_start).order_by(table.c.id)).mappings().all()
    assert saved[0]["recovery_start_at"] == rows[0]["created_at"]
    assert saved[1]["recovery_start_at"] == rows[1]["definition_changed_at"]
    assert saved[2]["recovery_start_at"] == rows[2]["definition_changed_at"]
    assert [row["updated_at"] for row in saved] == [row["updated_at"] for row in rows]
    connection.execute(table.insert().values(
        id=4, created_at=NOW, updated_at=NOW, definition_changed_at=None,
    ))
    assert connection.scalar(sa.select(recovery_start).select_from(table).where(table.c.id == 4)) is None
    with Operations.context(MigrationContext.configure(connection)):
        migration().downgrade()
    assert {column["name"] for column in sa.inspect(connection).get_columns("schedules")} == set(table.c.keys())
    assert connection.scalar(sa.select(sa.func.count()).select_from(table)) == 4


def test_sqlite_upgrade_backfills_precise_or_creation_floor_and_downgrade_retains_rows():
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            exercise_backfill_and_downgrade(connection)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_postgres_upgrade_backfill_and_downgrade_are_transactional(postgres_url):
    engine = sa.create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                schema = "schedule_recovery_" + uuid4().hex
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                exercise_backfill_and_downgrade(connection)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
